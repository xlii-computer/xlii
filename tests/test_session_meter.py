"""Tests for session cost meter (terminal-native-toolkit Phase 4)."""

from __future__ import annotations

from types import SimpleNamespace

from tests.helpers import FakeConsole
from xlii.agent_stats import CallStats, TurnStats
from xlii.repl_cmds.session import h_budget, h_cost
from xlii.session_meter import (
    budget_warning,
    context_meter_text,
    context_window,
    format_session_cost_line,
    init_budget_from_env,
    ktok,
    record_turn,
    session_usage_text,
    turn_total_tokens,
)
from xlii.tui.status import session_cost as status_session_cost


def _stats(*, prompt: int = 100, completion: int = 50, cost: float = 0.01) -> TurnStats:
    ts = TurnStats()
    ts.orch.model = "grok-x"
    ts.orch.prompt_tokens = prompt
    ts.orch.completion_tokens = completion
    ts.orch.cost_usd = cost
    return ts


def test_turn_total_tokens_includes_judges():
    ts = TurnStats()
    ts.orch.prompt_tokens = 10
    ts.orch.completion_tokens = 5
    ts.judges = CallStats(prompt_tokens=20, completion_tokens=3)
    assert turn_total_tokens(ts) == 38


def test_turn_total_tokens_tolerates_partial_stats():
    # A turn path that returns a minimal stats stub (cost only, no orch/workers
    # call-stats — e.g. the policy-hooks driver's fake) must not crash the meter.
    partial = SimpleNamespace(tool_calls=0, total_cost=0.0)
    assert turn_total_tokens(partial) == 0


def test_record_turn_accumulates_cost_and_tokens():
    session = SimpleNamespace(session_cost=0.0, session_tokens=0, last_turn_stats=None)
    record_turn(session, _stats(prompt=200, completion=100, cost=0.02))
    record_turn(session, _stats(prompt=300, completion=150, cost=0.03))
    assert session.session_tokens == 750
    assert abs(session.session_cost - 0.05) < 1e-9
    assert session.last_turn_stats is not None


def test_budget_warning_when_exceeded():
    session = SimpleNamespace(session_cost=1.25, budget_usd=1.0)
    msg = budget_warning(session)
    assert msg is not None and "exceeds budget" in msg
    session.session_cost = 0.5
    assert budget_warning(session) is None


def test_init_budget_from_env(monkeypatch):
    session = SimpleNamespace(budget_usd=None)
    monkeypatch.setenv("XLII_BUDGET", "2.5")
    init_budget_from_env(session)
    assert session.budget_usd == 2.5


def test_init_budget_skips_when_already_set(monkeypatch):
    session = SimpleNamespace(budget_usd=9.0)
    monkeypatch.setenv("XLII_BUDGET", "2.5")
    init_budget_from_env(session)
    assert session.budget_usd == 9.0


def test_session_cost_label_shows_budget_cap():
    state = SimpleNamespace(
        session_cost=0.042,
        budget_usd=1.0,
        agent=SimpleNamespace(session=SimpleNamespace(last_turn_stats=_stats())),
    )
    assert status_session_cost(state) == "$0.042/$1.000"


def test_h_cost_session_only():
    console = FakeConsole()
    agent = SimpleNamespace(
        session=SimpleNamespace(
            session_cost=0.05,
            session_tokens=1200,
            budget_usd=None,
            last_turn_stats=_stats(),
        )
    )
    h_cost("/cost --session", {"console": console, "agent": agent, "cfg": SimpleNamespace()})
    text = console.text
    assert "session:" in text and "1.2k tok" in text


def test_h_cost_shows_turn_and_pricing_when_no_turn():
    console = FakeConsole()
    agent = SimpleNamespace(
        session=SimpleNamespace(
            session_cost=0.0,
            session_tokens=0,
            budget_usd=None,
            last_turn_stats=None,
        )
    )
    cfg = SimpleNamespace(
        pricing={"grok-x": {"input_per_million": 1.0, "output_per_million": 2.0}},
        get_model_for_role=lambda role: "grok-x",
    )
    h_cost("/cost", {"console": console, "agent": agent, "cfg": cfg})
    text = console.text
    assert "session: no turns yet" in text
    assert "pricing" in text and "grok-x" in text


def test_h_cost_turn_without_pricing_table():
    console = FakeConsole()
    agent = SimpleNamespace(
        session=SimpleNamespace(
            session_cost=0.01,
            session_tokens=150,
            budget_usd=None,
            last_turn_stats=_stats(),
        )
    )
    h_cost("/cost", {"console": console, "agent": agent, "cfg": SimpleNamespace(pricing={})})
    text = console.text
    assert "this turn:" in text
    assert "rate table" in text
    assert "pricing (USD" not in text


def test_h_budget_set_and_clear():
    console = FakeConsole()
    agent = SimpleNamespace(
        session=SimpleNamespace(
            session_cost=0.0,
            session_tokens=0,
            budget_usd=None,
            last_turn_stats=None,
        )
    )
    ctx = {"console": console, "agent": agent}
    h_budget("/budget 1.5", ctx)
    assert agent.session.budget_usd == 1.5
    h_budget("/budget --clear", ctx)
    assert agent.session.budget_usd is None


def test_format_session_cost_line():
    session = SimpleNamespace(session_tokens=500, session_cost=0.01, budget_usd=2.0)
    line = format_session_cost_line(session)
    assert "500 tok" in line and "$0.010" in line and "budget $2.000" in line


def test_budget_clear_sticks_with_env_after_meter_begin(monkeypatch):
    from xlii.agent import SessionState
    from xlii.repl import _session_meter_begin

    session = SessionState()
    monkeypatch.setenv("XLII_BUDGET", "5.0")
    init_budget_from_env(session)
    assert session.budget_usd == 5.0

    h_budget("/budget --clear", {"console": FakeConsole(), "agent": SimpleNamespace(session=session)})
    assert session.budget_usd is None
    assert session.budget_env_cleared

    state = SimpleNamespace(agent=SimpleNamespace(session=session), console=FakeConsole())
    _session_meter_begin(state)
    assert session.budget_usd is None


def test_policy_followup_accumulates_session_meter(monkeypatch, tmp_path):
    from xlii.agent import SessionState
    from xlii.repl import _drive_policy_hooks, _session_meter_end

    session = SessionState()
    state = SimpleNamespace(
        agent=SimpleNamespace(session=session, history=[]),
        console=FakeConsole(),
        project=SimpleNamespace(xli_dir=tmp_path),
    )
    tmp_path.mkdir(exist_ok=True)

    calls = {"n": 0}

    def fake_control_hooks(xli_dir, data, console=None, control_enabled=False):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"followup": "keep going"}
        return {}

    monkeypatch.setattr("xlii.repl._control_hooks_enabled", lambda s: True)
    monkeypatch.setattr("xlii.hooks.run_control_hooks", fake_control_hooks)
    monkeypatch.setattr("xlii.hooks.run_hooks", lambda *a, **k: None)
    monkeypatch.setattr("xlii.repl._checkpoint_begin", lambda s: None)
    monkeypatch.setattr("xlii.repl._checkpoint_end", lambda s, t, d: None)

    _session_meter_end(state, _stats(cost=0.05))
    _drive_policy_hooks(
        state,
        lambda msg: ("ok", set(), _stats(cost=0.01)),
        lambda result, prompt: None,     # render-only slice (Phase 5b spine)
        "first",
        set(),
        _stats(),
    )
    assert abs(session.session_cost - 0.06) < 1e-9


def test_ktok_and_context_window():
    from xlii.session_meter import reset_model_window_cache

    reset_model_window_cache()
    assert ktok(270_000) == "270K"
    assert ktok(500_000) == "500K"
    assert ktok(1_000_000) == "1M"
    assert context_window("grok-4.3") == 1_000_000
    assert context_window("grok-4.6") == 500_000
    assert context_window("grok-build-0.1") == 256_000
    assert context_window("mystery") is None


def test_context_meter_and_session_usage_text():
    stats = TurnStats(context_tokens=270_000, cached_tokens=0)
    stats.orch.model = "grok-4.3"
    state = SimpleNamespace(
        agent=SimpleNamespace(session=SimpleNamespace(
            last_turn_stats=stats, session_tokens=84_000, session_cost=0.12,
        )),
    )
    assert context_meter_text(state) == "270K / 1M"
    assert session_usage_text(state).startswith("sess 84K")
    empty = SimpleNamespace(agent=SimpleNamespace(session=SimpleNamespace(
        last_turn_stats=None, session_tokens=0, session_cost=0.0,
    )))
    assert context_meter_text(empty, model="grok-3") == "0K / 131K"
    assert session_usage_text(empty) == ""


def test_extract_context_windows_from_models_payload():
    from xlii.bootstrap import extract_context_windows

    payload = {
        "object": "list",
        "data": [
            {
                "id": "grok-4.6",
                "aliases": ["grok-4.6-latest"],
                "context_length": 500_000,
            },
            {"id": "grok-4.3", "context_length": 1_000_000},
            {"id": "grok-imagine-image", "context_length": 1024},
            {"id": "broken", "context_length": None},
        ],
    }
    got = extract_context_windows(payload)
    assert got["grok-4.6"] == 500_000
    assert got["grok-4.6-latest"] == 500_000
    assert got["grok-4.3"] == 1_000_000
    assert got["grok-imagine-image"] == 1024
    assert "broken" not in got


def test_live_catalog_overrides_baked_prefix(tmp_path, monkeypatch):
    from xlii.session_meter import (
        _save_live_windows,
        context_meter_text,
        reset_model_window_cache,
    )

    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path))
    reset_model_window_cache()
    _save_live_windows({"grok-4.6": 500_000, "custom-lab": 128_000})
    assert context_window("grok-4.6") == 500_000
    assert context_window("custom-lab") == 128_000
    assert context_meter_text(
        SimpleNamespace(agent=SimpleNamespace(session=None)),
        model="grok-4.6",
    ) == "0K / 500K"
