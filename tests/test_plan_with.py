"""Plan-surface T2: /plan --with <provider> — a hired planner drives plan-mode
orchestrator turns (and ONLY those). Offline — backends are faked at the chat
seam; the real OpenAI client is stubbed where resolve_gig_backend mints one."""

from __future__ import annotations

from types import SimpleNamespace

from xlii.chat_backend import GIG_CAPABILITIES, XAI_SERVER_TOOL_NAMES
from xlii.mode_controller import PlanController
from tests.helpers import make_agent, make_msg


class _FakeBackend:
    label = "haiku"
    model = "claude-haiku-4-5"
    capabilities = GIG_CAPABILITIES

    def __init__(self, chunks=None):
        self.calls: list[dict] = []
        self._chunks = chunks or []

    def allows_tool(self, name):
        return name not in XAI_SERVER_TOOL_NAMES

    def create(self, **kw):
        self.calls.append(kw)
        return iter(self._chunks)


def _content_chunk(text):
    delta = SimpleNamespace(content=text, tool_calls=None, reasoning_content=None)
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=delta)])


def _usage_chunk():
    usage = SimpleNamespace(prompt_tokens=5, completion_tokens=2, prompt_tokens_details=None)
    return SimpleNamespace(usage=usage, choices=[])


class _Console:
    def __init__(self):
        self.lines: list[str] = []
        self.supports_live = False

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    def text(self):
        return "\n".join(self.lines)


# --- the controller carries the hire -----------------------------------------

def test_plan_controller_carries_backend_tag_and_directive():
    b = _FakeBackend()
    c = PlanController(chat_backend=b)
    assert c.chat_backend is b
    assert c.status_tag() == ("PLAN·gigwork[haiku]", "PLAN")
    assert "hired non-xAI brain" in c.get_system_directive()

    home = PlanController()
    assert home.chat_backend is None
    assert home.status_tag() == ("PLAN", "PLAN")
    assert "hired non-xAI brain" not in home.get_system_directive()


# --- run_turn plumbing: model, schemas, backend all reach the call -----------

def test_plan_turn_routes_model_schemas_and_backend(tmp_path):
    agent = make_agent(tmp_path)
    b = _FakeBackend()
    agent.set_mode(PlanController(chat_backend=b))
    seen: dict = {}

    def fake(**kw):
        seen.update(kw)
        return (make_msg("the plan", None), None, False)

    agent._stream_orchestrator_iteration = fake
    agent.run_turn("plan it")
    assert seen["backend"] is b
    assert seen["model"] == "claude-haiku-4-5"          # backend model wins the turn
    names = {s["function"]["name"] for s in seen["schemas"]}
    assert XAI_SERVER_TOOL_NAMES.isdisjoint(names)      # capability strip at schema time
    assert {"grep", "glob", "read_file", "write_file"} <= names  # degraded search, not blindness


def test_home_plan_turn_is_untouched(tmp_path):
    agent = make_agent(tmp_path)
    agent.set_mode(PlanController())
    seen: dict = {}

    def fake(**kw):
        seen.update(kw)
        return (make_msg("the plan", None), None, False)

    agent._stream_orchestrator_iteration = fake
    agent.run_turn("plan it")
    assert seen["backend"] is None
    names = {s["function"]["name"] for s in seen["schemas"]}
    assert "search_project" in names                    # home planner keeps the xAI plane


def test_backend_dies_with_the_controller(tmp_path):
    agent = make_agent(tmp_path)
    agent.set_mode(PlanController(chat_backend=_FakeBackend()))
    assert getattr(agent.active_mode, "chat_backend", None) is not None
    agent.set_mode(None)                                # /execute · /cancel path
    assert getattr(agent.active_mode, "chat_backend", None) is None


# --- the stream call itself routes through the backend ------------------------

def test_stream_iteration_uses_backend_not_home(tmp_path):
    agent = make_agent(tmp_path)
    agent.console = _Console()
    b = _FakeBackend(chunks=[_content_chunk("the plan"), _usage_chunk()])

    def touched():
        raise AssertionError("home client touched during a hired plan turn")

    agent.pool = SimpleNamespace(primary=touched)
    msg, usage, _streamed = agent._stream_orchestrator_iteration(
        model="claude-haiku-4-5", schemas=[], temperature=0.1,
        cache_hdrs={"x-grok-cache": "k"}, backend=b,
    )
    assert msg.content == "the plan"
    assert usage is not None and usage.prompt_tokens == 5
    call = b.calls[0]
    assert call["model"] == "claude-haiku-4-5" and call["stream"] is True
    assert call["messages"] is agent.history


def test_stream_iteration_home_path_unchanged(tmp_path):
    agent = make_agent(tmp_path)
    agent.console = _Console()
    calls: list[dict] = []

    def create(**kw):
        calls.append(kw)
        return iter([_content_chunk("home"), _usage_chunk()])

    clients = SimpleNamespace(chat=SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create))))
    agent.pool = SimpleNamespace(primary=lambda: clients)
    msg, usage, _streamed = agent._stream_orchestrator_iteration(
        model="grok-4", schemas=[], temperature=0.1, cache_hdrs=None, backend=None,
    )
    assert msg.content == "home" and calls


# --- /plan --with parsing ------------------------------------------------------

def _gig_cfg(agent, monkeypatch):
    agent.cfg.gigwork = {
        "providers": {"kimi": {"kind": "openai_compat",
                               "base_url": "https://api.kimi.test/v1",
                               "api_key_env": "KIMI_WITH_KEY",
                               "model": "k3"}},
        "defaults": {"allow": []},
    }
    monkeypatch.setenv("KIMI_WITH_KEY", "sk-x")

    class _FakeOpenAI:
        def __init__(self, *, api_key, base_url):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: None))

    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)


def _plan_cmd(line, agent):
    from xlii.repl_cmds.mode import h_plan

    console = _Console()
    handled = h_plan(line, {"console": console, "agent": agent, "state": None,
                            "project": agent.project})
    return handled, console.text()


def test_plan_with_hires_and_banners(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    _gig_cfg(agent, monkeypatch)
    handled, out = _plan_cmd("/plan --with kimi", agent)
    assert handled and agent.plan_mode
    backend = agent.active_mode.chat_backend
    assert backend is not None and backend.label == "kimi" and backend.model == "k3"
    assert "planner: gigwork[kimi]" in out
    assert "/execute runs on the home plane" in out
    assert "PLAN·gigwork[kimi]" == agent.active_mode.status_tag()[0]


def test_plan_with_refuses_before_mode_flips(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    _gig_cfg(agent, monkeypatch)

    handled, out = _plan_cmd("/plan --with ghost", agent)
    assert "unknown gig provider 'ghost'" in out and not agent.plan_mode

    handled, out = _plan_cmd("/plan --with", agent)
    assert "pass a provider name" in out and not agent.plan_mode

    monkeypatch.delenv("KIMI_WITH_KEY", raising=False)
    handled, out = _plan_cmd("/plan --with kimi", agent)
    assert "KIMI_WITH_KEY is not set" in out and not agent.plan_mode


def test_bare_plan_is_home_and_unchanged(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    _gig_cfg(agent, monkeypatch)
    handled, out = _plan_cmd("/plan", agent)
    assert handled and agent.plan_mode
    assert agent.active_mode.chat_backend is None
    assert "plan mode ON" in out and "planner: gigwork" not in out
