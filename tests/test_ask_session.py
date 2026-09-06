"""`xlii ask --session` (xmpp-next X1) — the multi-turn primitive, offline.

Pins the state round-trip the daemon's per-JID sessions and the future serve
head both ride on: same id ⇒ shared context, different id ⇒ isolation,
--new-session ⇒ reset, no --session ⇒ the historical stateless one-shot.
The LLM/network layer is faked; the transcript machinery on disk is real.
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

import xlii.cmds.sessions.ask as ask_mod
from tests.helpers import make_project


class _FakeAgent:
    """Captures the seeded history and replies deterministically."""

    instances: list["_FakeAgent"] = []

    def __init__(self, **kw):
        self.history = [{"role": "system", "content": "SYS"}]
        self.kw = kw
        _FakeAgent.instances.append(self)

    def run_turn(self, prompt, attachments=None, tier_text=None, cancelled=None):
        # Snapshot what the model would actually see for this turn.
        self.seeded = [dict(h) for h in self.history]
        self.attachments = attachments
        self.tier_text = tier_text
        return (f"reply to: {prompt}", set(), None)


class _StreamingFakeAgent(_FakeAgent):
    def run_turn(self, prompt, attachments=None, tier_text=None, cancelled=None):
        self.seeded = [dict(h) for h in self.history]
        self.attachments = attachments
        self.tier_text = tier_text
        self.history.append({"role": "user", "content": prompt})
        self.history.append({"role": "assistant", "content": f"streamed reply to: {prompt}"})
        return ("", set(), None)


class _EmptyStreamingFakeAgent(_FakeAgent):
    def run_turn(self, prompt, attachments=None, cancelled=None):
        self.seeded = [dict(h) for h in self.history]
        self.attachments = attachments
        self.history.append({"role": "user", "content": prompt})
        return ("", set(), None)


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """cmd_ask with everything but the transcript layer faked out."""
    _FakeAgent.instances = []
    project = make_project(tmp_path)
    monkeypatch.setattr(ask_mod, "_resolve_project_target", lambda ws: tmp_path)
    monkeypatch.setattr(ask_mod, "ProjectConfig",
                        SimpleNamespace(load=lambda p: project))
    monkeypatch.setattr(ask_mod, "GlobalConfig",
                        SimpleNamespace(load=lambda: SimpleNamespace()))
    monkeypatch.setattr(ask_mod, "ClientPool",
                        SimpleNamespace(from_config=lambda cfg: SimpleNamespace()))
    monkeypatch.setattr(ask_mod, "Agent", _FakeAgent)
    return project


def _args(prompt, session=None, new_session=False):
    return argparse.Namespace(prompt=prompt, workspace=None, yolo=False,
                              session=session, new_session=new_session)


def test_same_session_id_shares_context(wired, capsys):
    assert ask_mod.cmd_ask(_args("first question", session="s1")) == 0
    assert ask_mod.cmd_ask(_args("second question", session="s1")) == 0
    second = _FakeAgent.instances[1]
    seeded = "\n".join(str(h) for h in second.seeded)
    assert "first question" in seeded
    assert "reply to: first question" in seeded
    out = capsys.readouterr().out
    assert "reply to: second question" in out


def test_distinct_session_ids_are_isolated(wired):
    ask_mod.cmd_ask(_args("alice's secret", session="xmpp:alice@x"))
    ask_mod.cmd_ask(_args("hi", session="xmpp:bob@x"))
    bob = _FakeAgent.instances[1]
    assert "alice's secret" not in "\n".join(str(h) for h in bob.seeded)


def test_new_session_resets_the_conversation(wired):
    ask_mod.cmd_ask(_args("remember the number 41", session="s1"))
    ask_mod.cmd_ask(_args("fresh start", session="s1", new_session=True))
    fresh = _FakeAgent.instances[1]
    assert "remember the number 41" not in "\n".join(str(h) for h in fresh.seeded)


def test_new_session_without_session_id_is_an_error(wired):
    assert ask_mod.cmd_ask(_args("hi", new_session=True)) == 1
    assert _FakeAgent.instances == []  # refused before any agent work


def test_no_session_flag_stays_stateless(wired, tmp_path):
    ask_mod.cmd_ask(_args("one"))
    ask_mod.cmd_ask(_args("two"))
    second = _FakeAgent.instances[1]
    assert "one" not in "\n".join(str(h) for h in second.seeded)
    assert not (tmp_path / ".xlii" / "ask-sessions").exists()


def test_session_state_lands_under_the_project_xli_dir(wired, tmp_path):
    ask_mod.cmd_ask(_args("hello", session="xmpp:me@host:lab"))
    root = tmp_path / ".xlii" / "ask-sessions"
    dirs = list(root.iterdir())
    assert len(dirs) == 1
    turns = list((dirs[0] / "turns").glob("*.md"))
    assert len(turns) == 1
    assert "hello" in turns[0].read_text()


def test_streamed_reply_is_printed_and_persisted(wired, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(ask_mod, "Agent", _StreamingFakeAgent)

    assert ask_mod.cmd_ask(_args("stream me", session="s1")) == 0

    out = capsys.readouterr().out
    assert "streamed reply to: stream me" in out
    turn_files = list((tmp_path / ".xlii" / "ask-sessions").glob("*/turns/*.md"))
    assert len(turn_files) == 1
    saved = turn_files[0].read_text()
    assert "streamed reply to: stream me" in saved
    assert "(no reply)" not in saved


def test_empty_streamed_turn_does_not_persist_previous_reply(wired, monkeypatch, tmp_path):
    ask_mod.cmd_ask(_args("first question", session="s1"))
    monkeypatch.setattr(ask_mod, "Agent", _EmptyStreamingFakeAgent)

    assert ask_mod.cmd_ask(_args("silent turn", session="s1")) == 0

    saved = "\n".join(
        p.read_text()
        for p in sorted((tmp_path / ".xlii" / "ask-sessions").glob("*/turns/*.md"))
    )
    assert "reply to: first question" in saved
    assert "silent turn" not in saved
    assert "(no reply)" not in saved


def test_ask_attach_is_forwarded_to_the_turn(wired):
    # Media-in (#3): --attach paths reach the agent turn (→ multimodal vision/PDF).
    ask_mod.cmd_ask(argparse.Namespace(
        prompt="what is this?", workspace=None, yolo=False, session=None,
        new_session=False, attach=["/tmp/photo.png"]))
    assert _FakeAgent.instances[0].attachments == ["/tmp/photo.png"]


def test_session_key_is_safe_and_collision_resistant():
    k1 = ask_mod._session_key("xmpp:me@example.org:lab")
    k2 = ask_mod._session_key("xmpp:me@example.org_lab")  # slug-collides with k1
    assert k1 != k2  # digest keeps trust boundaries apart
    assert "/" not in k1 and ":" not in k1
    # deterministic: the same id always lands in the same directory
    assert k1 == ask_mod._session_key("xmpp:me@example.org:lab")


# --------------------------------------------------------------------------- #
#  --persona (fabric F2 / the mojo): run AS the persona over its own memory
# --------------------------------------------------------------------------- #

def test_ask_persona_runs_over_the_persona_and_recalls_its_prompt(tmp_path, monkeypatch, capsys):
    """--persona routes the turn through the persona: the agent is seeded with
    the persona's system prompt (its identity + memory), NOT the project one."""
    _FakeAgent.instances = []
    persona = SimpleNamespace(name="ixaac", system_prompt=lambda: "I AM IXAAC")
    persona_project = make_project(tmp_path)
    captured = {}

    monkeypatch.setattr(ask_mod, "GlobalConfig",
                        SimpleNamespace(load=lambda: SimpleNamespace(management_api_key="mgmt")))
    monkeypatch.setattr(ask_mod, "ClientPool",
                        SimpleNamespace(from_config=lambda cfg, **kw: captured.setdefault("pool_kw", kw) or SimpleNamespace()))
    monkeypatch.setattr(ask_mod, "Agent", _FakeAgent)
    monkeypatch.setattr("xlii.cmds.sessions.resolve._lookup_persona",
                        lambda name: persona if name == "ixaac" else None)
    monkeypatch.setattr("xlii.session_boot.ensure_persona_project",
                        lambda p, pool, console=None, local_only=False:
                            captured.setdefault("local_only", local_only) is None or persona_project)
    mem = SimpleNamespace(
        persist=lambda history, user_input, dirty: captured.setdefault("persist", (user_input, history)) or set())
    monkeypatch.setattr("xlii.profile.chat_profile",
                        lambda persona, project, *, seed_limit: SimpleNamespace(memory=mem))
    monkeypatch.setattr(
        "xlii.session_boot.seed_chat_history",
        lambda profile, persona: ([{"role": "system", "content": persona.system_prompt()}], 0, 0),
    )

    args = argparse.Namespace(prompt="who are you?", workspace=None, yolo=False,
                              session=None, new_session=False, persona="ixaac")
    assert ask_mod.cmd_ask(args) == 0
    assert capsys.readouterr().out.strip() == "reply to: who are you?"
    # The agent was CONSTRUCTED with the persona's system prompt as its seed
    # history (its identity + memory), over the persona's own project.
    built = _FakeAgent.instances[0]
    assert built.kw["history"][0]["content"] == "I AM IXAAC"
    assert built.kw["project"] is persona_project
    assert built.kw["session"].conversational is True
    # The mojo read never demands the management key (nodes read; center writes).
    assert captured["pool_kw"] == {"require_management": False}
    # With a management key present, the persona project is Collection-backed.
    assert captured["local_only"] is False
    # F3: the turn accrues into the persona's memory (journal-local-first).
    assert captured["persist"][0] == "who are you?"


def test_ask_persona_keyless_node_is_local_only(tmp_path, monkeypatch, capsys):
    """A fabric node holds only the inference key — the persona turn must run
    (identity + local memory) without demanding management: local-only create."""
    _FakeAgent.instances = []
    persona = SimpleNamespace(name="ixaac", system_prompt=lambda: "I AM IXAAC")
    persona_project = make_project(tmp_path)
    captured = {}

    monkeypatch.setattr(ask_mod, "GlobalConfig",
                        SimpleNamespace(load=lambda: SimpleNamespace(management_api_key=None)))
    monkeypatch.setattr(ask_mod, "ClientPool",
                        SimpleNamespace(from_config=lambda cfg, **kw: SimpleNamespace()))
    monkeypatch.setattr(ask_mod, "Agent", _FakeAgent)
    monkeypatch.setattr("xlii.cmds.sessions.resolve._lookup_persona", lambda name: persona)
    monkeypatch.setattr("xlii.session_boot.ensure_persona_project",
                        lambda p, pool, console=None, local_only=False:
                            captured.setdefault("local_only", local_only) is None or persona_project)
    mem = SimpleNamespace(
        persist=lambda history, user_input, dirty: captured.setdefault("persisted", True) or set())
    monkeypatch.setattr("xlii.profile.chat_profile",
                        lambda persona, project, *, seed_limit: SimpleNamespace(memory=mem))
    monkeypatch.setattr(
        "xlii.session_boot.seed_chat_history",
        lambda profile, persona: ([{"role": "system", "content": "I AM IXAAC"}], 0, 0))

    args = argparse.Namespace(prompt="hi", workspace=None, yolo=False,
                              session=None, new_session=False, persona="ixaac")
    assert ask_mod.cmd_ask(args) == 0
    # No management key → the persona project is created local-only.
    assert captured["local_only"] is True
    # It still accrues locally (identity + growing memory on a keyless node).
    assert captured.get("persisted") is True


def test_ask_persona_no_accrue_skips_the_write(tmp_path, monkeypatch, capsys):
    """--no-accrue runs the persona turn without writing to its memory (a
    read-only one-shot for scripts that shouldn't pollute the conversation)."""
    _FakeAgent.instances = []
    persona = SimpleNamespace(name="ixaac", system_prompt=lambda: "I AM IXAAC")
    persona_project = make_project(tmp_path)
    seen = {"persisted": False}

    monkeypatch.setattr(ask_mod, "GlobalConfig",
                        SimpleNamespace(load=lambda: SimpleNamespace(management_api_key=None)))
    monkeypatch.setattr(ask_mod, "ClientPool",
                        SimpleNamespace(from_config=lambda cfg, **kw: SimpleNamespace()))
    monkeypatch.setattr(ask_mod, "Agent", _FakeAgent)
    monkeypatch.setattr("xlii.cmds.sessions.resolve._lookup_persona", lambda name: persona)
    monkeypatch.setattr("xlii.session_boot.ensure_persona_project",
                        lambda p, pool, console=None, local_only=False: persona_project)
    mem = SimpleNamespace(
        persist=lambda history, user_input, dirty: seen.update(persisted=True) or set())
    monkeypatch.setattr("xlii.profile.chat_profile",
                        lambda persona, project, *, seed_limit: SimpleNamespace(memory=mem))
    monkeypatch.setattr("xlii.session_boot.seed_chat_history",
                        lambda profile, persona: ([{"role": "system", "content": "x"}], 0, 0))

    args = argparse.Namespace(prompt="hi", workspace=None, yolo=False, session=None,
                              new_session=False, persona="ixaac", no_accrue=True)
    assert ask_mod.cmd_ask(args) == 0
    assert seen["persisted"] is False    # opted out of the memory write


# --------------------------------------------------------------------------- #
#  F3 the center's half — "drain from the throne": the body that holds the
#  management key archives the accrued turn to the persona's SHARED Collection,
#  so every other surface recalls it. Nodes read; the center writes.
# --------------------------------------------------------------------------- #

def _persona_drain_setup(tmp_path, monkeypatch, *, mgmt, local_only):
    """Wire cmd_ask's --persona path with the REAL end_of_turn_sync but a recorded
    sync_project, so a test can assert whether the drain-to-Collection fires."""
    _FakeAgent.instances = []
    persona = SimpleNamespace(name="ixaac", system_prompt=lambda: "I AM IXAAC")
    persona_project = make_project(tmp_path, local_only=local_only)
    synced: list = []

    monkeypatch.setattr(ask_mod, "GlobalConfig",
                        SimpleNamespace(load=lambda: SimpleNamespace(management_api_key=mgmt)))
    monkeypatch.setattr(ask_mod, "ClientPool",
                        SimpleNamespace(from_config=lambda cfg, **kw: SimpleNamespace(primary=lambda: object())))
    monkeypatch.setattr(ask_mod, "Agent", _FakeAgent)
    monkeypatch.setattr("xlii.cmds.sessions.resolve._lookup_persona", lambda name: persona)
    monkeypatch.setattr("xlii.session_boot.ensure_persona_project",
                        lambda p, pool, console=None, local_only=False: persona_project)
    # A written turn returns the __rescan__ sentinel (the real chat TurnStore
    # contract, profile.py) — that truthy dirty set is what drives the drain.
    mem = SimpleNamespace(persist=lambda history, user_input, dirty: {"__rescan__"})
    monkeypatch.setattr("xlii.profile.chat_profile",
                        lambda persona, project, *, seed_limit: SimpleNamespace(memory=mem))
    monkeypatch.setattr("xlii.session_boot.seed_chat_history",
                        lambda profile, persona: ([{"role": "system", "content": "x"}], 0, 0))
    monkeypatch.setattr(
        "xlii.sync.sync_project",
        lambda clients, project, cfg, **kw: synced.append(project) or SimpleNamespace(
            uploaded=1, updated=0, deleted=0, summary=lambda: "1 uploaded"))
    return persona_project, synced


def test_ask_persona_center_drains_to_shared_collection(tmp_path, monkeypatch, capsys):
    """The center holds the management key → a Collection-backed persona project,
    so the accrued turn is archived to the persona's shared Collection (the
    mgmt-key write that lets every other surface recall it)."""
    persona_project, synced = _persona_drain_setup(
        tmp_path, monkeypatch, mgmt="mgmt", local_only=False)
    args = argparse.Namespace(prompt="hi", workspace=None, yolo=False, session=None,
                              new_session=False, persona="ixaac")
    assert ask_mod.cmd_ask(args) == 0
    assert synced == [persona_project]    # the turn was drained to the Collection


def test_ask_persona_node_keeps_memory_local(tmp_path, monkeypatch, capsys):
    """A keyless node (local-only persona project) must NOT drain — its memory
    stays local until the center pulls it. The mgmt-key write is the center's."""
    _project, synced = _persona_drain_setup(
        tmp_path, monkeypatch, mgmt=None, local_only=True)
    args = argparse.Namespace(prompt="hi", workspace=None, yolo=False, session=None,
                              new_session=False, persona="ixaac")
    assert ask_mod.cmd_ask(args) == 0
    assert synced == []    # local-only node: no Collection write attempted


def test_ask_persona_no_sync_keeps_the_turn_private(tmp_path, monkeypatch, capsys):
    """--no-sync accrues the turn locally but skips the shared drain, even on the
    center (a private turn that never leaves this body)."""
    _project, synced = _persona_drain_setup(
        tmp_path, monkeypatch, mgmt="mgmt", local_only=False)
    args = argparse.Namespace(prompt="hi", workspace=None, yolo=False, session=None,
                              new_session=False, persona="ixaac", no_sync=True)
    assert ask_mod.cmd_ask(args) == 0
    assert synced == []    # opted out of the shared Collection write


# --------------------------------------------------------------------------- #
#  run_persona_oneshot — the shared engine (CLI + daemon + REPL /mojo). The
#  fusion seam: ambient_context is what the MODEL sees; the turn store gets the
#  RAW prompt (so injected journal/wiki never pollutes the persona's memory).
# --------------------------------------------------------------------------- #

class _PromptCapturingAgent(_FakeAgent):
    def run_turn(self, prompt, attachments=None, tier_text=None, cancelled=None):
        self.turn_prompt = prompt
        self.turn_tier_text = tier_text
        self.history.append({"role": "user", "content": prompt})
        self.history.append({"role": "assistant", "content": "ANSWER"})
        return ("ANSWER", set(), None)


def _oneshot_wiring(tmp_path, monkeypatch, *, mgmt=None):
    persona = SimpleNamespace(name="ixaac", system_prompt=lambda: "I AM IXAAC")
    # Collection-backed (not local-only) so the center's drain can fire when asked.
    persona_project = make_project(tmp_path, local_only=False)
    cap = {}
    monkeypatch.setattr(ask_mod, "Agent", _PromptCapturingAgent)
    monkeypatch.setattr("xlii.session_boot.ensure_persona_project",
                        lambda p, pool, console=None, local_only=False:
                            cap.setdefault("local_only", local_only) is None or persona_project)
    # __setitem__ returns None, so the `or` yields the truthy dirty set that
    # drives the drain — while still recording the raw user_input we assert on.
    mem = SimpleNamespace(
        persist=lambda history, user_input, dirty: cap.__setitem__("persist_input", user_input) or {"__rescan__"})
    monkeypatch.setattr("xlii.profile.chat_profile",
                        lambda persona, project, *, seed_limit: SimpleNamespace(memory=mem))
    monkeypatch.setattr("xlii.session_boot.seed_chat_history",
                        lambda profile, persona: ([{"role": "system", "content": "I AM IXAAC"}], 0, 0))
    monkeypatch.setattr("xlii.sync.sync_project",
                        lambda clients, project, cfg, **kw: cap.setdefault("synced", True) or SimpleNamespace(
                            uploaded=1, updated=0, deleted=0, summary=lambda: "1"))
    import contextlib
    console = SimpleNamespace(print=lambda *a, **k: None,
                             status=lambda *a, **k: contextlib.nullcontext())
    cfg = SimpleNamespace(management_api_key=mgmt)
    pool = SimpleNamespace(primary=lambda: object())
    return persona, cfg, pool, console, cap


def test_oneshot_injects_ambient_but_persists_raw_prompt(tmp_path, monkeypatch):
    _FakeAgent.instances = []
    persona, cfg, pool, console, cap = _oneshot_wiring(tmp_path, monkeypatch, mgmt="mgmt")
    text = ask_mod.run_persona_oneshot(
        persona, "what did I do?", pool=pool, cfg=cfg, console=console,
        ambient_context="JOURNAL+WIKI DUMP", persist=True, drain=False)
    assert text == "ANSWER"
    built = _FakeAgent.instances[0]
    # The MODEL saw ambient + separator + prompt...
    assert built.turn_prompt == "JOURNAL+WIKI DUMP\n\n---\n\nwhat did I do?"
    # ...but the turn store records only the RAW question (clean memory)...
    assert cap["persist_input"] == "what did I do?"
    # ...and the chat-tier router classifies the RAW question too — never the
    # ambient (whose journal/wiki URLs read as a fresh-data ask and mis-routed
    # even a greeting into a heavy deep-search fan-out).
    assert built.turn_tier_text == "what did I do?"


def test_oneshot_defaults_hire_to_read(tmp_path, monkeypatch):
    """K1: a persona oneshot advertises worker hire unless the caller says none."""
    _FakeAgent.instances = []
    persona, cfg, pool, console, _cap = _oneshot_wiring(tmp_path, monkeypatch)
    ask_mod.run_persona_oneshot(persona, "hi", pool=pool, cfg=cfg, console=console)
    assert _FakeAgent.instances[0].kw["session"].hire == "read"
    _FakeAgent.instances = []
    ask_mod.run_persona_oneshot(
        persona, "hi", pool=pool, cfg=cfg, console=console, hire="write")
    assert _FakeAgent.instances[0].kw["session"].hire == "write"


def test_oneshot_carries_chat_tier_onto_the_transient_agent(tmp_path, monkeypatch):
    """Face [M] /tier must ride the oneshot — a fresh session defaults to auto."""
    _FakeAgent.instances = []
    persona, cfg, pool, console, _cap = _oneshot_wiring(tmp_path, monkeypatch)
    ask_mod.run_persona_oneshot(
        persona, "hi", pool=pool, cfg=cfg, console=console, chat_tier="expert")
    sess = _FakeAgent.instances[0].kw["session"]
    assert sess.chat_tier == "expert"
    _FakeAgent.instances = []
    ask_mod.run_persona_oneshot(persona, "hi", pool=pool, cfg=cfg, console=console)
    assert _FakeAgent.instances[0].kw["session"].chat_tier is None


def test_oneshot_no_ambient_is_the_bare_prompt(tmp_path, monkeypatch):
    _FakeAgent.instances = []
    persona, cfg, pool, console, cap = _oneshot_wiring(tmp_path, monkeypatch)
    ask_mod.run_persona_oneshot(persona, "hi", pool=pool, cfg=cfg, console=console)
    assert _FakeAgent.instances[0].turn_prompt == "hi"


def test_oneshot_drain_false_skips_the_shared_write(tmp_path, monkeypatch):
    _FakeAgent.instances = []
    persona, cfg, pool, console, cap = _oneshot_wiring(tmp_path, monkeypatch, mgmt="mgmt")
    # /mojo's posture: persist locally, do NOT force the remote push.
    ask_mod.run_persona_oneshot(persona, "hi", pool=pool, cfg=cfg, console=console,
                                persist=True, drain=False)
    assert cap.get("persist_input") == "hi"     # accrued locally
    assert "synced" not in cap                   # but not drained


def test_oneshot_drain_true_writes_the_shared_collection(tmp_path, monkeypatch):
    _FakeAgent.instances = []
    persona, cfg, pool, console, cap = _oneshot_wiring(tmp_path, monkeypatch, mgmt="mgmt")
    ask_mod.run_persona_oneshot(persona, "hi", pool=pool, cfg=cfg, console=console,
                                persist=True, drain=True)
    assert cap.get("synced") is True             # the center's mgmt-key drain fired


def test_oneshot_carries_desk_plugin_dir(tmp_path, monkeypatch):
    """Face / /mojo pass this folder's .xlii so talk sees the pane's ● list."""
    _FakeAgent.instances = []
    persona, cfg, pool, console, _cap = _oneshot_wiring(tmp_path, monkeypatch)
    desk = tmp_path / "desk" / ".xlii"
    desk.mkdir(parents=True)
    ask_mod.run_persona_oneshot(
        persona, "hi", pool=pool, cfg=cfg, console=console, desk_xli_dir=desk)
    built = _FakeAgent.instances[0]
    sess = built.kw.get("session")
    assert sess is not None
    assert sess.plugin_xli_dirs == (desk,)


def test_oneshot_threads_gig_backend_onto_agent(tmp_path, monkeypatch):
    """A keyless limb speaks through jobs.gig — same Mojo, foreign larynx."""
    _FakeAgent.instances = []
    persona, cfg, pool, console, _cap = _oneshot_wiring(tmp_path, monkeypatch)
    backend = object()
    ask_mod.run_persona_oneshot(
        persona, "hi", pool=pool, cfg=cfg, console=console, chat_backend=backend)
    assert _FakeAgent.instances[0].kw.get("chat_backend") is backend


def test_oneshot_limb_addendum_names_this_body(tmp_path, monkeypatch):
    """Mojo on a named node knows which limb it is wearing."""
    _FakeAgent.instances = []
    persona, cfg, pool, console, _cap = _oneshot_wiring(tmp_path, monkeypatch)
    cfg.jobs = {"node": "acer", "gig": "kimi"}
    ask_mod.run_persona_oneshot(persona, "hi", pool=pool, cfg=cfg, console=console)
    sys_txt = _FakeAgent.instances[0].kw["history"][0]["content"]
    assert "[BODY]" in sys_txt
    assert "`acer`" in sys_txt
    assert "`kimi`" in sys_txt
    assert "second throne" in sys_txt


def test_limb_addendum_silent_when_unnamed():
    from types import SimpleNamespace

    from xlii.persona import limb_addendum

    assert limb_addendum(SimpleNamespace(jobs={}, node_name="")) == ""
    text = limb_addendum(SimpleNamespace(jobs={"node": "acer"}))
    assert "`acer`" in text
    assert "larynx" not in text


def test_ask_persona_gig_larynx_when_no_xai_pool(tmp_path, monkeypatch, capsys):
    """Phone/daemon `xlii ask --persona` on a gig-only box must not die of
    MissingCredentials — the named gig is the mouth, still Mojo."""
    from xlii.client import MissingCredentials

    persona = SimpleNamespace(name="mojo", system_prompt=lambda: "I AM MOJO")
    captured = {}

    monkeypatch.setattr(ask_mod, "GlobalConfig",
                        SimpleNamespace(load=lambda: SimpleNamespace(
                            management_api_key=None, jobs={"gig": "kimi"})))
    monkeypatch.setattr(ask_mod, "ClientPool", SimpleNamespace(
        from_config=lambda cfg, **kw: (_ for _ in ()).throw(
            MissingCredentials("no API keys configured"))))
    monkeypatch.setattr("xlii.cmds.sessions.resolve._lookup_persona",
                        lambda name: persona)
    monkeypatch.setattr("xlii.farm.job_gig", lambda cfg: "kimi")
    backend = object()

    def _resolve(cfg, name):
        captured["gig"] = name
        return backend

    monkeypatch.setattr("xlii.chat_backend.resolve_gig_backend", _resolve)

    def fake_oneshot(*_a, **kw):
        captured["backend"] = kw.get("chat_backend")
        captured["pool"] = kw.get("pool")
        return "hello from the hand"

    monkeypatch.setattr(ask_mod, "run_persona_oneshot", fake_oneshot)
    monkeypatch.setattr(ask_mod, "_resolve_voice",
                        lambda prompt, attachments, **k: (prompt, attachments))

    args = argparse.Namespace(prompt="hello", workspace=None, yolo=False,
                              session=None, new_session=False, persona="mojo",
                              attach=None, no_accrue=False, no_sync=False)
    assert ask_mod.cmd_ask(args) == 0
    assert capsys.readouterr().out.strip() == "hello from the hand"
    assert captured["gig"] == "kimi"
    assert captured["backend"] is backend
    assert captured["pool"] is None


def test_oneshot_raises_persona_project_error_on_open_failure(tmp_path, monkeypatch):
    persona = SimpleNamespace(name="ixaac", system_prompt=lambda: "x")
    monkeypatch.setattr("xlii.session_boot.ensure_persona_project",
                        lambda p, pool, console=None, local_only=False: None)
    with pytest.raises(ask_mod.PersonaProjectError):
        ask_mod.run_persona_oneshot(
            persona, "hi", pool=SimpleNamespace(), cfg=SimpleNamespace(management_api_key=None),
            console=SimpleNamespace(print=lambda *a, **k: None))


def test_ask_persona_unknown_name_errors(tmp_path, monkeypatch, capsys):
    import xlii.persona

    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", tmp_path / "personas")
    monkeypatch.setattr(ask_mod, "GlobalConfig",
                        SimpleNamespace(load=SimpleNamespace))
    args = argparse.Namespace(prompt="hi", workspace=None, yolo=False,
                              session=None, new_session=False, persona="ghost")
    assert ask_mod.cmd_ask(args) == 1
    assert "no such persona" in capsys.readouterr().err
