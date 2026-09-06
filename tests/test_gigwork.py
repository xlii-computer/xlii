"""Gigwork (proposals/gigwork.md G0–G2): swappable worker brains.

G0 — the ChatBackend seam: no backend = today's path untouched; a backend
without the xAI server plane never sees search_project/web_search/x_search.
G1 — provider registry (env-only keys, friendly errors) + /gigwork command.
G2 — dispatch_subagent gig= pin: allowlist-gated, no pool draw, honest badge.
All offline; foreign endpoints are faked at the backend seam.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import xlii.agent as A
import xlii.config as C
from xlii.chat_backend import (
    CAP_XAI_SERVER,
    GIG_CAPABILITIES,
    GIG_PRESETS,
    GigError,
    HomeBackend,
    OpenAICompatBackend,
    XAI_SERVER_TOOL_NAMES,
    gig_allowlist,
    gig_providers,
    resolve_gig_backend,
)
from xlii.worker_agent import WorkerAgent
from tests.helpers import make_agent, make_msg


def _resp(msg):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0),
    )


def _fake_clients(scripted):
    it = iter(scripted)
    completions = SimpleNamespace(create=lambda **kw: next(it))
    return SimpleNamespace(
        chat=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        label="primary",
    )


class FakeBackend:
    """Scripted gig brain: records every create() kwargs for assertions."""

    def __init__(self, scripted, *, label="kimi", model="moonshot-v1-128k",
                 capabilities=GIG_CAPABILITIES):
        self._it = iter(scripted)
        self.calls: list[dict] = []
        self.label = label
        self.model = model
        self.capabilities = capabilities

    def allows_tool(self, name):
        if name in XAI_SERVER_TOOL_NAMES:
            return CAP_XAI_SERVER in self.capabilities
        return True

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return next(self._it)


def _cfg_with_gig(**extra):
    cfg = C.GlobalConfig()
    cfg.gigwork = {
        "providers": {
            "kimi": {
                "kind": "openai_compat",
                "base_url": "https://api.moonshot.test/v1",
                "api_key_env": "KIMI_TEST_KEY",
                "model": "moonshot-v1-128k",
            }
        },
        **extra,
    }
    return cfg


# --------------------------------------------------------------------------- #
#  G0 — seam behavior
# --------------------------------------------------------------------------- #


def test_no_backend_is_todays_path(tmp_path):
    """chat_backend=None → the clients.chat call runs exactly as before."""
    agent = make_agent(tmp_path)
    agent.cfg.max_worker_iterations = 2
    scripted = [_resp(make_msg("done", None))]
    w = WorkerAgent(clients=_fake_clients(scripted), project=agent.project,
                    cfg=agent.cfg, role="explore")
    text, call = w.run("look")
    assert text == "done"
    assert call.iterations == 1


def test_gig_backend_gets_model_and_call(tmp_path):
    agent = make_agent(tmp_path)
    agent.cfg.max_worker_iterations = 2
    backend = FakeBackend([_resp(make_msg("gig says hi", None))])
    w = WorkerAgent(clients=_fake_clients([]), project=agent.project,
                    cfg=agent.cfg, role="explore", chat_backend=backend)
    text, call = w.run("opinion?")
    assert text == "gig says hi"
    assert call.model == "moonshot-v1-128k"        # backend model, not role map
    assert len(backend.calls) == 1                 # brain was the backend, not clients


def test_explicit_model_override_beats_backend_model(tmp_path):
    agent = make_agent(tmp_path)
    agent.cfg.max_worker_iterations = 1
    backend = FakeBackend([_resp(make_msg("ok", None))])
    w = WorkerAgent(clients=_fake_clients([]), project=agent.project,
                    cfg=agent.cfg, chat_backend=backend, model="special-model")
    _, call = w.run("t")
    assert call.model == "special-model"


def test_xai_server_tools_stripped_for_gig_capabilities(tmp_path):
    """A brain without CAP_XAI_SERVER must never even SEE the xAI-plane tools
    in its schema list — capability flags, not prompt theater."""
    agent = make_agent(tmp_path)
    agent.cfg.max_worker_iterations = 1
    backend = FakeBackend([_resp(make_msg("ok", None))])
    w = WorkerAgent(clients=_fake_clients([]), project=agent.project,
                    cfg=agent.cfg, role="explore", chat_backend=backend)
    w.run("t")
    offered = {s["function"]["name"] for s in backend.calls[0]["tools"]}
    assert offered.isdisjoint(XAI_SERVER_TOOL_NAMES)
    assert "read_file" in offered                  # local tools stay
    assert "xai_docs" in offered                   # DNA read: HTTP docs, not Responses

    # Same pass with the home capability set keeps search_project.
    home_like = FakeBackend([_resp(make_msg("ok", None))],
                            capabilities=frozenset({*GIG_CAPABILITIES, CAP_XAI_SERVER}))
    w2 = WorkerAgent(clients=_fake_clients([]), project=agent.project,
                     cfg=agent.cfg, role="explore", chat_backend=home_like)
    w2.run("t")
    offered2 = {s["function"]["name"] for s in home_like.calls[0]["tools"]}
    assert "search_project" in offered2


def test_home_backend_wraps_clients(tmp_path):
    scripted = [_resp(make_msg("via home", None))]
    clients = _fake_clients(scripted)
    hb = HomeBackend(clients)
    assert hb.label == "primary"
    assert hb.model == ""                          # per-role resolution stays home
    assert hb.allows_tool("search_project")
    assert hb.create(messages=[]).choices[0].message.content == "via home"


def test_openai_compat_backend_strips_xai_cache_headers(monkeypatch):
    seen = {}

    class _FakeOpenAI:
        def __init__(self, *, api_key, base_url):
            seen["ctor"] = (api_key, base_url)
            def _create(**kw):
                seen["kwargs"] = kw
                return "resp"

            self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))

    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)
    b = OpenAICompatBackend(label="kimi", model="m", api_key="sk-x",
                               base_url="https://api.moonshot.test/v1")
    out = b.create(messages=[], extra_headers={"x-grok-cache": "on"})
    assert out == "resp"
    assert seen["ctor"] == ("sk-x", "https://api.moonshot.test/v1")
    assert "extra_headers" not in seen["kwargs"]   # home-plane plumbing stays home
    b.create(messages=[], stream=True, stream_options={"include_usage": True})
    assert "stream_options" not in seen["kwargs"]
    assert seen["kwargs"].get("stream") is True


def test_openai_compat_backend_omits_temperature_by_default(monkeypatch):
    """Foreign brains have their own parameter rules (Kimi K2 allows ONLY
    temperature=1): the caller's worker_temp must NEVER cross the seam unless
    the provider pins one."""
    seen = {}

    class _FakeOpenAI:
        def __init__(self, *, api_key, base_url):
            def _create(**kw):
                seen["kwargs"] = kw
                return "resp"

            self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))

    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)
    b = OpenAICompatBackend(label="kimi", model="m", api_key="sk-x",
                            base_url="https://api.kimi.test/v1")
    b.create(messages=[], temperature=0.2)
    assert "temperature" not in seen["kwargs"]     # endpoint default applies

    b2 = OpenAICompatBackend(label="kimi", model="m", api_key="sk-x",
                             base_url="https://api.kimi.test/v1", temperature=0.7)
    b2.create(messages=[], temperature=0.2)
    assert seen["kwargs"]["temperature"] == 0.7    # provider pin wins


def test_gigwork_status_suffixes_render_providers(monkeypatch):
    """/status gig lines: model, endpoint, key state, agent-allowed marker."""
    monkeypatch.setenv("KIMI_API_KEY", "sk-x")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from xlii.chat_backend import gigwork_status_suffixes

    cfg = SimpleNamespace(gigwork={
        "providers": {
            "kimi": {"kind": "openai_compat", "base_url": "https://api.kimi.test/v1",
                     "api_key_env": "KIMI_API_KEY", "model": "k3"},
            "deepseek": {"kind": "openai_compat", "base_url": "https://api.deepseek.com/v1",
                         "api_key_env": "DEEPSEEK_API_KEY", "model": "deepseek-chat"},
        },
        "defaults": {"allow": ["kimi"]},
    })
    lines = gigwork_status_suffixes(cfg)
    assert any("gig/kimi:" in ln and "k3" in ln and "key set" in ln and "agent-allowed" in ln
               for ln in lines)
    assert any("gig/deepseek:" in ln and "$DEEPSEEK_API_KEY unset" in ln
               and "agent-allowed" not in ln for ln in lines)


def test_gig_providers_parses_optional_temperature(monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "sk-x")
    cfg = SimpleNamespace(gigwork={"providers": {
        "kimi": {"kind": "openai_compat", "base_url": "https://api.kimi.test/v1",
                 "api_key_env": "KIMI_API_KEY", "model": "kimi-k2", "temperature": 0.6},
        "plain": {"kind": "openai_compat", "base_url": "https://p.test/v1",
                  "api_key_env": "KIMI_API_KEY", "model": "m"},
        "bad": {"kind": "openai_compat", "base_url": "https://b.test/v1",
                "api_key_env": "KIMI_API_KEY", "model": "m", "temperature": "hot"},
    }})
    import pytest

    from xlii.chat_backend import GigError, gig_providers
    with pytest.raises(GigError, match="temperature must be a number"):
        gig_providers(cfg)
    cfg.gigwork["providers"].pop("bad")
    providers = gig_providers(cfg)
    assert providers["kimi"].temperature == 0.6
    assert providers["plain"].temperature is None


# --------------------------------------------------------------------------- #
#  G1 — provider registry + config
# --------------------------------------------------------------------------- #


def test_gig_providers_parse_and_key_status(monkeypatch):
    cfg = _cfg_with_gig()
    providers = gig_providers(cfg)
    assert set(providers) == {"kimi"}
    p = providers["kimi"]
    assert (p.kind, p.model) == ("openai_compat", "moonshot-v1-128k")
    monkeypatch.delenv("KIMI_TEST_KEY", raising=False)
    assert p.key_set is False
    monkeypatch.setenv("KIMI_TEST_KEY", "sk-test")
    assert p.key_set is True


def test_inline_api_key_is_refused():
    cfg = C.GlobalConfig()
    cfg.gigwork = {"providers": {"leaky": {
        "kind": "openai_compat", "base_url": "https://x/v1",
        "api_key": "sk-oops", "api_key_env": "E", "model": "m"}}}
    with pytest.raises(GigError, match="inline api_key is refused"):
        gig_providers(cfg)


def test_bad_entries_raise_actionable_errors():
    cfg = C.GlobalConfig()
    cfg.gigwork = {"providers": {"weird": {"kind": "bogus_backend",
                                           "base_url": "https://x/v1",
                                           "api_key_env": "E", "model": "m"}}}
    with pytest.raises(GigError, match="unknown kind"):
        gig_providers(cfg)
    cfg.gigwork = {"providers": {"partial": {"kind": "openai_compat"}}}
    with pytest.raises(GigError, match="missing base_url, api_key_env, model"):
        gig_providers(cfg)


def test_empty_or_absent_block_means_no_providers():
    assert gig_providers(C.GlobalConfig()) == {}
    assert gig_allowlist(C.GlobalConfig()) == []


def test_resolve_backend_errors_are_the_fix(monkeypatch):
    cfg = _cfg_with_gig()
    with pytest.raises(GigError, match="unknown gig provider 'nope'"):
        resolve_gig_backend(cfg, "nope")
    monkeypatch.delenv("KIMI_TEST_KEY", raising=False)
    with pytest.raises(GigError, match="KIMI_TEST_KEY is not set"):
        resolve_gig_backend(cfg, "kimi")


def test_config_round_trips_gigwork_block(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(C, "GLOBAL_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(C, "GLOBAL_CONFIG_FILE", tmp_path / "config.json")
    cfg = _cfg_with_gig()
    cfg.save()
    loaded = C.GlobalConfig.load()
    assert gig_providers(loaded).keys() == {"kimi"}


# --------------------------------------------------------------------------- #
#  G1 — /gigwork command
# --------------------------------------------------------------------------- #


class _Console:
    def __init__(self):
        self.lines: list[str] = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    def text(self):
        return "\n".join(self.lines)


def _run_cmd(line, agent):
    from xlii.repl_cmds.gigwork import _gigwork_handler
    console = _Console()
    handled = _gigwork_handler(line, {"console": console, "agent": agent, "state": None})
    return handled, console.text()


def test_gigwork_command_registered_with_gig_alias():
    from xlii.commands import iter_repl_commands
    from xlii.repl_cmds import register_all
    register_all()
    cmds = {c.name: c for c in iter_repl_commands()}
    assert "gigwork" in cmds
    assert "gig" in cmds["gigwork"].aliases


def test_gigwork_ls_lists_providers_and_key_state(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg_with_gig()
    monkeypatch.delenv("KIMI_TEST_KEY", raising=False)
    handled, out = _run_cmd("/gigwork ls", agent)
    assert handled
    assert "kimi" in out and "KIMI_TEST_KEY unset" in out


def test_gigwork_hire_runs_one_pass(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg_with_gig()
    agent.cfg.max_worker_iterations = 2
    agent.pool = SimpleNamespace(primary=lambda: _fake_clients([]))
    backend = FakeBackend([_resp(make_msg("second opinion: fine", None))])
    monkeypatch.setattr("xlii.chat_backend.resolve_gig_backend",
                        lambda cfg, name: backend)
    handled, out = _run_cmd("/gigwork kimi is this race real?", agent)
    assert handled
    assert "gigwork[kimi]" in out
    assert "second opinion: fine" in out
    assert len(backend.calls) == 1


def test_gigwork_unknown_provider_prints_fix(tmp_path):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg_with_gig()
    handled, out = _run_cmd("/gigwork deepseek hello", agent)
    assert handled
    assert "unknown gig provider" in out and "kimi" in out


def test_gigwork_bare_prints_usage(tmp_path):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg_with_gig()
    handled, out = _run_cmd("/gigwork", agent)
    assert handled
    assert "usage:" in out


# --------------------------------------------------------------------------- #
#  G2 — dispatch pin
# --------------------------------------------------------------------------- #


def _dispatch(agent, args):
    return A.Agent._run_worker(agent, args)


def test_dispatch_gig_not_allowlisted_is_refused(tmp_path):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg_with_gig()          # defaults.allow absent → empty
    text, call = _dispatch(agent, {"task": "t", "gig": "kimi"})
    assert "not in gigwork.defaults.allow" in text
    assert call.iterations == 0


def test_dispatch_allowlisted_gig_hires_and_badges(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg_with_gig(defaults={"allow": ["kimi"]})
    agent.cfg.max_worker_iterations = 2
    agent.pool = SimpleNamespace(
        primary=lambda: _fake_clients([]),
        acquire=lambda: (_ for _ in ()).throw(AssertionError("pool drawn for a gig")),
    )
    backend = FakeBackend([_resp(make_msg("kimi verdict", None))])
    monkeypatch.setattr("xlii.chat_backend.resolve_gig_backend",
                        lambda cfg, name: backend)
    text, call = _dispatch(agent, {"task": "compare", "gig": "kimi"})
    assert text.startswith("--- gigwork[kimi]")
    assert "kimi verdict" in text
    assert call.model == "moonshot-v1-128k"


def test_dispatch_gig_env_missing_returns_tool_text(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg_with_gig(defaults={"allow": ["kimi"]})
    monkeypatch.delenv("KIMI_TEST_KEY", raising=False)
    text, call = _dispatch(agent, {"task": "t", "gig": "kimi"})
    assert "KIMI_TEST_KEY is not set" in text
    assert call.iterations == 0


def test_dispatch_gig_failure_never_touches_xai_quarantine(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg_with_gig(defaults={"allow": ["kimi"]})
    agent.cfg.max_worker_iterations = 1
    reports = []
    agent.pool = SimpleNamespace(
        primary=lambda: _fake_clients([]),
        acquire=lambda: (_ for _ in ()).throw(AssertionError("pool drawn for a gig")),
        report_auth_failure=lambda c: reports.append("auth"),
        report_success=lambda c: reports.append("ok"),
    )

    class _Boom(FakeBackend):
        def create(self, **kwargs):
            raise RuntimeError("401 from moonshot")

    monkeypatch.setattr("xlii.chat_backend.resolve_gig_backend",
                        lambda cfg, name: _Boom([]))
    text, _ = _dispatch(agent, {"task": "t", "gig": "kimi"})
    assert "gigwork[kimi] failed" in text and "401" in text
    assert reports == []                           # xAI key map untouched


def test_dispatch_schema_documents_gig():
    from xlii.tools import dispatch_subagent_schema
    props = dispatch_subagent_schema()["function"]["parameters"]["properties"]
    assert "gig" in props
    assert "allow" in props["gig"]["description"]


# --------------------------------------------------------------------------- #
#  Preset catalog + add/rm
# --------------------------------------------------------------------------- #


def test_preset_catalog_is_sane():
    """Every preset is a complete, inert recipe: real URL shape, UPPER_SNAKE
    env, non-empty model. Nothing is contacted at import."""

    assert {"kimi", "deepseek", "anthropic", "gemini", "huggingface",
            "openrouter", "groq", "together", "mistral", "fireworks",
            "openai", "ollama"} <= set(GIG_PRESETS)
    for name, p in GIG_PRESETS.items():
        assert p["base_url"].startswith(("https://", "http://127.0.0.1")), name
        assert p["api_key_env"].isupper() and " " not in p["api_key_env"], name
        assert p["model"], name
        assert p["note"], name
    assert GIG_PRESETS["ollama"].get("key_optional") is True
    assert GIG_PRESETS["anthropic"]["api_key_env"] == "ANTHROPIC_API_KEY"


def _global_cfg_sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(C, "GLOBAL_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(C, "GLOBAL_CONFIG_FILE", tmp_path / "config.json")
    C.GlobalConfig().save()


def test_add_preset_persists_and_hints_env(tmp_path, monkeypatch):
    _global_cfg_sandbox(monkeypatch, tmp_path)
    agent = make_agent(tmp_path)
    agent.cfg = C.GlobalConfig.load()
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    handled, out = _run_cmd("/gigwork add deepseek", agent)
    assert handled
    assert "added" in out and "deepseek-chat" in out
    assert "export DEEPSEEK_API_KEY" in out          # activation hint

    # Persisted to the global file AND live on the session cfg.
    assert gig_providers(C.GlobalConfig.load()).keys() == {"deepseek"}
    assert gig_providers(agent.cfg).keys() == {"deepseek"}


def test_add_preset_with_as_and_model_overrides(tmp_path, monkeypatch):
    _global_cfg_sandbox(monkeypatch, tmp_path)
    agent = make_agent(tmp_path)
    agent.cfg = C.GlobalConfig.load()
    handled, out = _run_cmd(
        "/gigwork add openrouter --as router --model qwen/qwen3-coder", agent)
    assert handled and "gigwork[router]" in out
    p = gig_providers(C.GlobalConfig.load())["router"]
    assert p.model == "qwen/qwen3-coder"
    assert p.base_url == "https://openrouter.ai/api/v1"


def test_add_custom_and_unknown_preset(tmp_path, monkeypatch):
    _global_cfg_sandbox(monkeypatch, tmp_path)
    agent = make_agent(tmp_path)
    agent.cfg = C.GlobalConfig.load()
    handled, out = _run_cmd(
        "/gigwork add --custom myhost https://llm.lan/v1 MYHOST_KEY my-model", agent)
    assert handled and "gigwork[myhost]" in out
    assert gig_providers(C.GlobalConfig.load())["myhost"].api_key_env == "MYHOST_KEY"

    handled, out = _run_cmd("/gigwork add nosuch", agent)
    assert "unknown preset" in out and "kimi" in out


def test_rm_removes_provider_and_scrubs_allowlist(tmp_path, monkeypatch):
    _global_cfg_sandbox(monkeypatch, tmp_path)
    agent = make_agent(tmp_path)
    agent.cfg = C.GlobalConfig.load()
    _run_cmd("/gigwork add kimi", agent)
    # Put it on the allowlist, then rm must scrub it there too.
    cfg = C.GlobalConfig.load()
    cfg.gigwork["defaults"]["allow"] = ["kimi"]
    cfg.save()
    agent.cfg = C.GlobalConfig.load()

    handled, out = _run_cmd("/gigwork rm kimi", agent)
    assert handled and "removed" in out
    reloaded = C.GlobalConfig.load()
    assert gig_providers(reloaded) == {}
    assert gig_allowlist(reloaded) == []

    handled, out = _run_cmd("/gigwork rm kimi", agent)
    assert "no provider" in out


def test_key_optional_resolves_without_env(tmp_path, monkeypatch):
    """Ollama: no env var needed — resolve mints the backend with a placeholder
    instead of refusing (local endpoints ignore the key)."""
    _global_cfg_sandbox(monkeypatch, tmp_path)
    agent = make_agent(tmp_path)
    agent.cfg = C.GlobalConfig.load()
    _run_cmd("/gigwork add ollama", agent)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

    seen = {}

    class _FakeOpenAI:
        def __init__(self, *, api_key, base_url):
            seen["ctor"] = (api_key, base_url)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: None))

    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)
    backend = resolve_gig_backend(C.GlobalConfig.load(), "ollama")
    assert backend.label == "ollama"
    assert seen["ctor"][0] == "unused"
    assert seen["ctor"][1].startswith("http://127.0.0.1:11434")

    # And ls reports it ready rather than nagging for a key.
    handled, out = _run_cmd("/gigwork ls", agent)
    assert "no key needed" in out


def test_presets_verb_lists_catalog(tmp_path):
    agent = make_agent(tmp_path)
    agent.cfg = C.GlobalConfig()
    handled, out = _run_cmd("/gigwork presets", agent)
    assert handled
    for name in ("kimi", "anthropic", "gemini", "huggingface", "ollama"):
        assert name in out


def test_gigwork_kit_flag_selects_palette(tmp_path, monkeypatch):
    """--kit picks the worker tool palette; 'role' stays the persona concept
    (/role) and is not a gigwork flag."""
    agent = make_agent(tmp_path)
    agent.cfg = _cfg_with_gig()
    agent.cfg.max_worker_iterations = 2
    agent.pool = SimpleNamespace(primary=lambda: _fake_clients([]))
    backend = FakeBackend([_resp(make_msg("ran it", None))])
    monkeypatch.setattr("xlii.chat_backend.resolve_gig_backend",
                        lambda cfg, name: backend)
    handled, out = _run_cmd("/gigwork kimi --kit bash run the tests", agent)
    assert handled and "kit bash" in out
    offered = {s["function"]["name"] for s in backend.calls[0]["tools"]}
    assert offered == {"read_file", "bash"}          # the bash kit, exactly

    handled, out = _run_cmd("/gigwork kimi --kit pilot x", agent)
    assert "unknown kit" in out


# --------------------------------------------------------------------------- #
#  allow / deny — the dispatch allow list as a verb
# --------------------------------------------------------------------------- #


def test_set_provider_allowed_engine():
    from xlii.chat_backend import set_provider_allowed

    cfg = _cfg_with_gig(defaults={"allow": []})
    assert set_provider_allowed(cfg, "kimi", True) == ["kimi"]
    assert set_provider_allowed(cfg, "kimi", True) == ["kimi"]      # idempotent
    assert gig_allowlist(cfg) == ["kimi"]
    assert set_provider_allowed(cfg, "kimi", False) == []
    assert gig_allowlist(cfg) == []
    with pytest.raises(GigError, match="unknown gig provider 'ghost'"):
        set_provider_allowed(cfg, "ghost", True)


def test_allow_deny_commands_persist(tmp_path, monkeypatch):
    _global_cfg_sandbox(monkeypatch, tmp_path)
    agent = make_agent(tmp_path)
    agent.cfg = C.GlobalConfig.load()
    _run_cmd("/gigwork add kimi", agent)

    handled, out = _run_cmd("/gigwork allow kimi", agent)
    assert handled and "allowed" in out and "agent-hireable: kimi" in out
    assert gig_allowlist(C.GlobalConfig.load()) == ["kimi"]
    assert gig_allowlist(agent.cfg) == ["kimi"]             # live mirror

    handled, out = _run_cmd("/gigwork deny kimi", agent)
    assert "denied" in out and "only /gigwork can hire" in out
    assert gig_allowlist(C.GlobalConfig.load()) == []

    handled, out = _run_cmd("/gigwork allow ghost", agent)
    assert "unknown gig provider" in out
    handled, out = _run_cmd("/gigwork allow", agent)
    assert "pass a provider name" in out


# --------------------------------------------------------------------------- #
#  Cache marks — the per-provider cache knob (auto | true | false)
# --------------------------------------------------------------------------- #


def test_cache_knob_parses_and_defaults():
    cfg = _cfg_with_gig()
    assert gig_providers(cfg)["kimi"].cache == "auto"

    cfg.gigwork["providers"]["kimi"]["cache"] = True
    assert gig_providers(cfg)["kimi"].cache == "on"
    cfg.gigwork["providers"]["kimi"]["cache"] = False
    assert gig_providers(cfg)["kimi"].cache == "off"
    cfg.gigwork["providers"]["kimi"]["cache"] = "sometimes"
    with pytest.raises(GigError, match='cache must be true, false, or "auto"'):
        gig_providers(cfg)


def test_cache_auto_is_anthropic_only():
    cfg = C.GlobalConfig()
    cfg.gigwork = {"providers": {
        "haiku": {"kind": "openai_compat", "base_url": "https://api.anthropic.com/v1/",
                  "api_key_env": "A_KEY", "model": "claude-haiku-4-5"},
        "kimi": {"kind": "openai_compat", "base_url": "https://api.moonshot.test/v1",
                 "api_key_env": "K_KEY", "model": "m"},
        "router": {"kind": "openai_compat", "base_url": "https://openrouter.test/v1",
                   "api_key_env": "R_KEY", "model": "m", "cache": True},
        "quiet": {"kind": "openai_compat", "base_url": "https://api.anthropic.com/v1/",
                  "api_key_env": "A_KEY", "model": "m", "cache": False},
    }}
    ps = gig_providers(cfg)
    assert ps["haiku"].cache_effective is True      # auto → marks for api.anthropic.com
    assert ps["kimi"].cache_effective is False      # auto → none elsewhere
    assert ps["router"].cache_effective is True     # true forces marks on
    assert ps["quiet"].cache_effective is False     # false forces them off


def test_with_cache_marks_shapes_and_purity():
    from xlii.chat_backend import _with_cache_marks

    messages = [
        {"role": "system", "content": "HARNESS"},
        {"role": "user", "content": "TASK"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "result", "tool_call_id": "1"},
    ]
    out = _with_cache_marks(messages)
    assert out[0]["content"] == [{"type": "text", "text": "HARNESS",
                                  "cache_control": {"type": "ephemeral"}}]
    assert out[1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert out[2] is messages[2] and out[3] is messages[3]   # only system+user marked
    # Purity: the worker loop reuses its list across iterations — never mutated.
    assert messages[0]["content"] == "HARNESS"
    assert messages[1]["content"] == "TASK"

    # Parts-form user content: the LAST text part carries the mark.
    parts = [{"role": "user", "content": [{"type": "text", "text": "a"},
                                          {"type": "text", "text": "b"}]}]
    out = _with_cache_marks(parts)
    assert "cache_control" not in out[0]["content"][0]
    assert out[0]["content"][1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in parts[0]["content"][1]     # source untouched


def test_backend_injects_marks_only_when_on(monkeypatch):
    seen = {}

    class _FakeOpenAI:
        def __init__(self, *, api_key, base_url):
            def _create(**kw):
                seen["kwargs"] = kw
                return "resp"

            self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))

    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]

    on = OpenAICompatBackend(label="haiku", model="m", api_key="k",
                             base_url="https://api.anthropic.com/v1/", cache_marks=True)
    on.create(messages=msgs)
    marked = seen["kwargs"]["messages"]
    assert marked[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert msgs[0]["content"] == "S"                          # caller's list untouched

    off = OpenAICompatBackend(label="kimi", model="m", api_key="k",
                              base_url="https://api.moonshot.test/v1")
    off.create(messages=msgs)
    assert seen["kwargs"]["messages"] is msgs                 # zero transformation


def test_resolve_wires_cache_marks(monkeypatch):
    class _FakeOpenAI:
        def __init__(self, *, api_key, base_url):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: "r"))

    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)
    monkeypatch.setenv("A_KEY", "sk-a")
    monkeypatch.setenv("KIMI_TEST_KEY", "sk-k")
    cfg = _cfg_with_gig()
    cfg.gigwork["providers"]["haiku"] = {
        "kind": "openai_compat", "base_url": "https://api.anthropic.com/v1/",
        "api_key_env": "A_KEY", "model": "claude-haiku-4-5"}
    assert resolve_gig_backend(cfg, "haiku").cache_marks is True
    assert resolve_gig_backend(cfg, "kimi").cache_marks is False
