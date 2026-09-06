"""Chat tiers — the per-turn reasoning-depth selector (chat-tiers Vector A).

A tier overlays the `chat` model role: fast→economy, expert/heavy→reason,
auto→routed (placeholder = expert until Vector B). It is inert unless the turn
resolves to the `chat` role, and a sticky model_override always wins over it.
"""

from __future__ import annotations

from xlii.agent import resolve_orchestrator_model
from xlii.chat_tiers import (
    AUTO,
    STRATEGY_DEEP_SEARCH,
    STRATEGY_SINGLE,
    normalize_tier,
    resolve_tier,
    resolve_tier_model,
    split_tier_sigil,
    tier_strategy,
)
from xlii.config import GlobalConfig
from xlii.repl_cmds.code import _tier_handler
from tests.helpers import FakeConsole, make_agent, script_iterations


# --- the module ----------------------------------------------------------- #

def test_normalize_tier_accepts_valid_rejects_junk():
    assert normalize_tier("fast") == "fast"
    assert normalize_tier("  EXPERT ") == "expert"
    assert normalize_tier("auto") == "auto"
    assert normalize_tier("nope") is None
    assert normalize_tier(None) is None


def test_resolve_tier_maps_concrete_bundles():
    assert resolve_tier("fast").profile == "economy"
    assert resolve_tier("expert").profile == "reason"
    heavy = resolve_tier("heavy")
    assert heavy.profile == "reason"
    assert heavy.strategy == STRATEGY_DEEP_SEARCH
    assert resolve_tier(None) is None
    assert resolve_tier("bogus") is None


def test_auto_no_message_defaults_to_expert():
    # With no message to classify (status probe), auto resolves to expert —
    # never auto-spend on heavy without a signal.
    assert resolve_tier(AUTO).name == "expert"


def test_auto_is_message_aware():
    # The Vector B router classifies the message: fresh-data → heavy, casual →
    # fast, ambiguous → expert.
    assert resolve_tier(AUTO, user_message="what's the latest news on X").name == "heavy"
    assert resolve_tier(AUTO, user_message="thanks!").name == "fast"
    assert resolve_tier(
        AUTO, user_message="tell me about the history of the roman empire",
    ).name == "expert"


def test_resolve_tier_model_uses_profiles():
    cfg = GlobalConfig()
    # fast → economy profile's chat model (the cheap fast model).
    assert resolve_tier_model(cfg, "fast") == "grok-build-0.1"
    # expert/heavy → reason profile's chat model.
    assert resolve_tier_model(cfg, "expert") == "grok-4.20-reasoning"
    assert resolve_tier_model(cfg, "heavy") == "grok-4.20-reasoning"
    # auto → the routed tier's model (expert placeholder).
    assert resolve_tier_model(cfg, "auto") == "grok-4.20-reasoning"
    # unset/unknown → None (caller falls back to the plain chat role).
    assert resolve_tier_model(cfg, None) is None
    assert resolve_tier_model(cfg, "bogus") is None


def test_tier_strategy():
    assert tier_strategy("heavy") == STRATEGY_DEEP_SEARCH
    assert tier_strategy("fast") == STRATEGY_SINGLE
    assert tier_strategy("expert") == STRATEGY_SINGLE
    assert tier_strategy("auto") == STRATEGY_SINGLE  # routes to expert
    assert tier_strategy("auto", user_message="who won last night") == STRATEGY_DEEP_SEARCH
    assert tier_strategy(None) == STRATEGY_SINGLE


# --- resolution path ------------------------------------------------------ #

def test_session_chat_tier_reads_agent_session():
    from types import SimpleNamespace

    from xlii.chat_tiers import session_chat_tier

    assert session_chat_tier(None) is None
    st = SimpleNamespace(agent=SimpleNamespace(session=SimpleNamespace(chat_tier="expert")))
    assert session_chat_tier(st) == "expert"
    assert session_chat_tier(SimpleNamespace(chat_tier="fast")) == "fast"


def test_tier_steers_chat_slot_model():
    cfg = GlobalConfig()
    # No tier → plain chat role model, unchanged behavior.
    assert resolve_orchestrator_model(cfg=cfg, conversational=True) == (
        "grok-4.3", "chat",
    )
    # fast → economy chat model.
    assert resolve_orchestrator_model(
        cfg=cfg, conversational=True, chat_tier="fast",
    ) == ("grok-build-0.1", "chat")
    # expert → reason chat model.
    assert resolve_orchestrator_model(
        cfg=cfg, conversational=True, chat_tier="expert",
    ) == ("grok-4.20-reasoning", "chat")


def test_tier_inert_off_the_chat_role():
    cfg = GlobalConfig()
    # Non-conversational = orchestrator role: the tier does not apply.
    model, role = resolve_orchestrator_model(
        cfg=cfg, conversational=False, chat_tier="expert",
    )
    assert role == "orchestrator"
    assert model == cfg.get_model_for_role("orchestrator")


def test_model_override_beats_tier():
    cfg = GlobalConfig()
    model, role = resolve_orchestrator_model(
        cfg=cfg, conversational=True, model_override="pinned-x", chat_tier="fast",
    )
    assert (model, role) == ("pinned-x", "chat")


def test_resolve_orchestrator_model_routes_auto_by_message():
    cfg = GlobalConfig()
    # auto + fresh-data message → heavy → reason model.
    assert resolve_orchestrator_model(
        cfg=cfg, conversational=True, chat_tier="auto",
        user_message="what's the latest news",
    ) == ("grok-4.20-reasoning", "chat")
    # auto + casual message → fast → economy model.
    assert resolve_orchestrator_model(
        cfg=cfg, conversational=True, chat_tier="auto", user_message="thanks!",
    ) == ("grok-build-0.1", "chat")


# --- >>tier sigil (Vector D) ------------------------------------------------ #

def test_split_tier_sigil_full_names_and_shorthands():
    assert split_tier_sigil(">>heavy what changed?") == ("heavy", "what changed?")
    assert split_tier_sigil(">>fast quick one") == ("fast", "quick one")
    assert split_tier_sigil(">>E why does this hang?") == ("expert", "why does this hang?")
    assert split_tier_sigil(">>a route this one") == ("auto", "route this one")
    # whitespace after >> is tolerated (mobile keyboards autocorrect a space in)
    assert split_tier_sigil(">> heavy spaced form") == ("heavy", "spaced form")


def test_split_tier_sigil_never_eats_non_tier_text():
    # A false positive would eat the user's words — anything that isn't exactly
    # `>>` + tier + message passes through untouched.
    for text in (
        "plain message",
        ">>notatier hello",
        ">>file.txt append target",
        ">>heavy",          # bare sigil, no message — not a /tier synonym
        ">>h   ",           # shorthand + whitespace only
        ">>",
        "",
    ):
        assert split_tier_sigil(text) == (None, text)


# --- agent integration ---------------------------------------------------- #

def test_fresh_session_defaults_to_auto(tmp_path):
    # Vector D (proposal-locked once B/C landed): fresh sessions start on auto.
    agent = make_agent(tmp_path)
    assert agent.session.chat_tier == "auto"


def test_agent_reads_session_tier(tmp_path):
    agent = make_agent(tmp_path)
    agent.session.conversational = True
    # The auto default routes: no message in hand → expert (reason model).
    assert agent.orchestrator_model_and_role() == ("grok-4.20-reasoning", "chat")
    # /tier off → the plain chat role, today's pre-tier behavior.
    agent.session.chat_tier = None
    assert agent.orchestrator_model_and_role() == ("chat-model", "chat")
    # Setting a tier steers the chat slot via the built-in profiles.
    agent.session.chat_tier = "expert"
    assert agent.orchestrator_model_and_role() == ("grok-4.20-reasoning", "chat")
    agent.session.chat_tier = "fast"
    assert agent.orchestrator_model_and_role() == ("grok-build-0.1", "chat")


def test_run_turn_sigil_overrides_sticky_for_one_message(tmp_path):
    agent = make_agent(tmp_path)
    agent.session.conversational = True
    agent.session.chat_tier = "fast"
    script_iterations(agent, ("ok", None))
    _, _, stats = agent.run_turn(">>e explain the borrow checker")
    # The sigil steered THIS turn to expert's reason model…
    assert stats.orch.model == "grok-4.20-reasoning"
    # …the model never saw the sigil…
    user_msgs = [h for h in agent.history if h.get("role") == "user"]
    assert user_msgs[-1]["content"] == "explain the borrow checker"
    # …and the sticky pick is untouched.
    assert agent.session.chat_tier == "fast"


def test_run_turn_sigil_ignored_outside_chat(tmp_path):
    # In a non-conversational turn the tier machinery never runs: the text
    # passes through intact (a code question may legitimately start with >>).
    agent = make_agent(tmp_path)
    agent.session.conversational = False
    script_iterations(agent, ("ok", None))
    agent.run_turn(">>h explain this shell redirect")
    user_msgs = [h for h in agent.history if h.get("role") == "user"]
    assert user_msgs[-1]["content"] == ">>h explain this shell redirect"


def test_agent_auto_routes_by_message(tmp_path):
    agent = make_agent(tmp_path)
    agent.session.conversational = True
    agent.session.chat_tier = "auto"
    # heavy signal → reason model; casual → economy; ambiguous → expert (reason).
    m, _ = agent.orchestrator_model_and_role("who won the game last night")
    assert m == "grok-4.20-reasoning"
    m, _ = agent.orchestrator_model_and_role("thanks!")
    assert m == "grok-build-0.1"
    m, _ = agent.orchestrator_model_and_role()  # no message → expert
    assert m == "grok-4.20-reasoning"


# --- /tier command -------------------------------------------------------- #

def test_tier_command_sets_and_clears(tmp_path):
    con = FakeConsole()
    agent = make_agent(tmp_path, cfg=GlobalConfig(), console=con)
    ctx = {"agent": agent, "console": con}

    assert _tier_handler("/tier expert", ctx) is True
    assert agent.session.chat_tier == "expert"
    assert agent.cfg.chat_tier == "expert"

    assert _tier_handler("/tier auto", ctx) is True
    assert agent.session.chat_tier == "auto"

    assert _tier_handler("/tier off", ctx) is True
    assert agent.session.chat_tier is None
    assert agent.cfg.chat_tier == "off"


def test_tier_command_rejects_unknown(tmp_path):
    con = FakeConsole()
    agent = make_agent(tmp_path, cfg=GlobalConfig(), console=con)
    ctx = {"agent": agent, "console": con}
    assert _tier_handler("/tier turbo", ctx) is True
    assert agent.session.chat_tier == "auto"  # unchanged (the fresh-session default)


def test_tier_command_bare_shows_menu(tmp_path):
    con = FakeConsole()
    agent = make_agent(tmp_path, cfg=GlobalConfig(), console=con)
    ctx = {"agent": agent, "console": con}
    assert _tier_handler("/tier", ctx) is True
    blob = "\n".join(con.lines)
    for name in ("fast", "expert", "heavy", "auto"):
        assert name in blob


def test_restore_chat_tier_from_config():
    from types import SimpleNamespace

    from xlii.session_boot import _restore_chat_tier

    sess = SimpleNamespace(chat_tier="auto")
    state = SimpleNamespace(agent=SimpleNamespace(session=sess))
    _restore_chat_tier(state, SimpleNamespace(chat_tier="expert"))
    assert sess.chat_tier == "expert"
    _restore_chat_tier(state, SimpleNamespace(chat_tier="off"))
    assert sess.chat_tier is None
    sess.chat_tier = "fast"
    _restore_chat_tier(state, SimpleNamespace(chat_tier=""))
    assert sess.chat_tier == "fast"
