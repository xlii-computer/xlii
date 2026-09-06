"""run_headless_turn (xlii/agent_dispatch.py) — THE headless body primitive
(GVM B5). Pins the engine directly at its kernel home: event stream shapes,
confirm auto-deny, error paths, seeding, and persistence — all headless, no
tty, no stdin. The WS façade path (cmds/serve_ws.run_ws_turn, an alias) is
covered by the pre-existing tests/test_serve_ws.py suite.
"""

from __future__ import annotations

from types import SimpleNamespace

import xlii.tools as tools_mod
from tests.helpers import make_project
from xlii.agent_dispatch import run_headless_turn


class _FakeAgent:
    instances: list["_FakeAgent"] = []

    def __init__(self, **kw):
        self.history = [{"role": "system", "content": "SYS"}]
        self.console = kw["console"]
        self._renderer_cache = None
        _FakeAgent.instances.append(self)

    def _renderer(self):
        from xlii.tui.renderer import Renderer

        if self._renderer_cache is None or not isinstance(self._renderer_cache, Renderer):
            # a tap may already be installed — wrap-check by attribute
            if self._renderer_cache is not None and hasattr(self._renderer_cache, "_inner"):
                return self._renderer_cache
            self._renderer_cache = Renderer(self.console, plain=True)
        return self._renderer_cache

    def run_turn(self, prompt):
        cb = getattr(self.console, "on_content_chunk", None)
        if cb:
            cb("hel")
            cb("lo")
        self.history.append({"role": "user", "content": prompt})
        self.history.append({"role": "assistant", "content": f"reply to: {prompt}"})
        self._renderer().emit(self._answer())
        return ("", set(), None)  # streamed: empty text, reply only in history

    def _answer(self):
        from xlii.turn_events import AssistantAnswer

        return AssistantAnswer("reply", streamed=True)


def _wire(monkeypatch, agent_cls=_FakeAgent):
    _FakeAgent.instances = []
    monkeypatch.setattr("xlii.config.GlobalConfig", SimpleNamespace(load=SimpleNamespace))
    monkeypatch.setattr("xlii.pool.ClientPool", SimpleNamespace(from_config=lambda cfg: object()))
    monkeypatch.setattr("xlii.agent.Agent", agent_cls)
    monkeypatch.setattr("xlii.agent.SessionState", SimpleNamespace(from_flat=lambda **kw: object()))


def test_turn_runs_with_styled_events_enabled(tmp_path, monkeypatch):
    """B1 regression: the real Agent gates every renderer.emit() on
    styled_enabled(), so run_headless_turn must render STYLED for the turn or
    the sink gets only chunks + done. Pin that styled is on during the turn and
    restored (to the conftest 'raw' default) after."""
    from xlii.shell_run import styled_enabled

    seen: dict[str, bool] = {}

    class _StyleProbe(_FakeAgent):
        def run_turn(self, prompt):
            seen["styled"] = styled_enabled()
            return super().run_turn(prompt)

    _wire(monkeypatch, _StyleProbe)
    project = make_project(tmp_path)
    assert styled_enabled() is False  # baseline: conftest forces raw
    rc = run_headless_turn(project=project, session_id="s1", prompt="hi",
                           sink=lambda e: None)
    assert rc == 0
    assert seen["styled"] is True     # styled during the turn → events emit
    assert styled_enabled() is False  # restored, not left flipped


def test_passed_pool_is_reused_not_rebuilt(tmp_path, monkeypatch):
    """B3 regression: a caller-supplied pool (the WS server's per-connection
    pool) is reused across turns, never rebuilt — a rebuild per turn leaks an
    httpx client set on a long-lived connection."""
    builds = {"n": 0}

    def _from_config(cfg):
        builds["n"] += 1
        return object()

    _FakeAgent.instances = []
    monkeypatch.setattr("xlii.config.GlobalConfig", SimpleNamespace(load=SimpleNamespace))
    monkeypatch.setattr("xlii.pool.ClientPool", SimpleNamespace(from_config=_from_config))
    monkeypatch.setattr("xlii.agent.Agent", _FakeAgent)
    monkeypatch.setattr("xlii.agent.SessionState", SimpleNamespace(from_flat=lambda **kw: object()))
    project = make_project(tmp_path)

    pool, cfg = object(), object()
    for _ in range(3):
        rc = run_headless_turn(project=project, session_id="s1", prompt="hi",
                               sink=lambda e: None, pool=pool, cfg=cfg)
        assert rc == 0
    assert builds["n"] == 0  # never rebuilt — the supplied pool was reused


def test_event_stream_shapes_and_order(tmp_path, monkeypatch):
    _wire(monkeypatch)
    project = make_project(tmp_path)
    events: list[dict] = []

    rc = run_headless_turn(project=project, session_id="s1", prompt="say hi",
                           sink=events.append)

    assert rc == 0
    types = [e["type"] for e in events]
    assert types[0] == "user_turn"
    assert "assistant_chunk" in types
    assert "assistant_answer" in types
    assert events[-1] == {"type": "turn_done", "ok": True, "exit_code": 0}


def test_streamed_reply_is_recovered_and_persisted(tmp_path, monkeypatch):
    _wire(monkeypatch)
    project = make_project(tmp_path)

    assert run_headless_turn(project=project, session_id="s1", prompt="q1",
                             sink=lambda e: None) == 0

    from xlii.turn_store import ask_session_turns_dir
    from xlii.transcript import load_recent_turns
    turns = load_recent_turns(ask_session_turns_dir(project, "s1"), 5)
    assert len(turns) == 1
    assert turns[0].user == "q1"
    assert turns[0].assistant == "reply to: q1"


def test_session_seeds_from_prior_turns(tmp_path, monkeypatch):
    _wire(monkeypatch)
    project = make_project(tmp_path)
    sink = lambda e: None
    run_headless_turn(project=project, session_id="s1", prompt="first", sink=sink)
    run_headless_turn(project=project, session_id="s1", prompt="second", sink=sink)

    second = _FakeAgent.instances[1]
    seeded = "\n".join(str(h) for h in second.history)
    assert "first" in seeded and "reply to: first" in seeded


def test_non_yolo_turn_holds_auto_deny_and_restores(tmp_path, monkeypatch):
    seen: dict = {}

    class _ConfirmProbe(_FakeAgent):
        def run_turn(self, prompt):
            seen["during"] = tools_mod._confirm
            return ("ok", set(), None)

    _wire(monkeypatch, _ConfirmProbe)
    project = make_project(tmp_path)
    before = tools_mod._confirm

    rc = run_headless_turn(project=project, session_id="s1", prompt="risky",
                           sink=lambda e: None, yolo=False)

    assert rc == 0
    assert seen["during"] is tools_mod.auto_deny
    assert seen["during"]("approve? [y/N] ") == ""  # declines, never touches stdin
    assert tools_mod._confirm is before  # restored after the turn


def test_missing_credentials_is_a_clean_error_event(tmp_path, monkeypatch):
    from xlii.client import MissingCredentials

    _wire(monkeypatch)
    monkeypatch.setattr(
        "xlii.pool.ClientPool",
        SimpleNamespace(from_config=lambda cfg: (_ for _ in ()).throw(
            MissingCredentials("no keys"))),
    )
    project = make_project(tmp_path)
    events: list[dict] = []

    rc = run_headless_turn(project=project, session_id="s1", prompt="hi",
                           sink=events.append)

    assert rc == 1
    assert events[-2] == {"type": "error", "message": "no keys"}
    assert events[-1] == {"type": "turn_done", "ok": False, "exit_code": 1}
    assert _FakeAgent.instances == []  # refused before any agent work


def test_turn_exception_is_sunk_not_raised(tmp_path, monkeypatch):
    class _Boom(_FakeAgent):
        def run_turn(self, prompt):
            raise RuntimeError("model exploded")

    _wire(monkeypatch, _Boom)
    project = make_project(tmp_path)
    events: list[dict] = []

    rc = run_headless_turn(project=project, session_id="s1", prompt="hi",
                           sink=events.append)

    assert rc == 1
    assert events[-2]["type"] == "error" and "model exploded" in events[-2]["message"]
    assert events[-1] == {"type": "turn_done", "ok": False, "exit_code": 1}
