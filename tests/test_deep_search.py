"""Heavy tier — the deep-search coordinator (chat-tiers Vector C).

Every model / search / agent seam is injected as a fake, so the fan-out, wave,
budget, and citation logic are exercised with zero network and zero subprocess.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.agent_stats import CallStats
from xlii.chat_backend import GigError
from xlii.config import GlobalConfig
from xlii.deep_search import (
    KIND_INVESTIGATE,
    KIND_SEARCH,
    DeepSearchProgress,
    DeepSearchResult,
    Finding,
    SubQuery,
    resolve_investigate_hire,
    run_deep_search,
    split_citations,
)
from tests.helpers import make_agent, script_iterations


# --- helpers -------------------------------------------------------------- #

def _finding(sq: SubQuery, text="body", cites=None):
    return Finding(subquery=sq, text=text, citations=cites or [])


def _search(sq: SubQuery) -> Finding:
    return _finding(sq, text=f"result for {sq.query}", cites=[f"http://src/{sq.query}"])


# --- unit ----------------------------------------------------------------- #

def test_split_citations():
    body, cites = split_citations("the answer\n\n--- citations ---\nhttp://a\nhttp://b")
    assert body == "the answer"
    assert cites == ["http://a", "http://b"]
    body2, cites2 = split_citations("no marker here")
    assert body2 == "no marker here" and cites2 == []


def test_subquery_label():
    assert SubQuery("weather", KIND_SEARCH, "web").label == "web:weather"
    assert SubQuery("gossip", KIND_SEARCH, "x").label == "x:gossip"
    assert SubQuery("analyze", KIND_INVESTIGATE).label.startswith("agent:")


# --- the coordinator ------------------------------------------------------ #

def test_basic_plan_fanout_synth():
    plan = lambda q, prior: [] if prior else [SubQuery("a"), SubQuery("b")]
    synth = lambda q, findings: ("ANSWER [1][2]", [])
    res = run_deep_search(
        "why", plan_fn=plan, search_fn=_search, synth_fn=synth,
    )
    assert res.answer == "ANSWER [1][2]"
    assert len(res.findings) == 2
    assert res.rounds == 1
    assert res.citations == ["http://src/a", "http://src/b"]
    assert res.partial is False


def test_second_wave_fills_gaps():
    def plan(q, prior):
        return [SubQuery("q3")] if prior else [SubQuery("q1"), SubQuery("q2")]

    res = run_deep_search(
        "q", plan_fn=plan, search_fn=_search,
        synth_fn=lambda q, f: ("done", []), max_rounds=2,
    )
    assert res.rounds == 2
    assert [f.subquery.query for f in res.findings] == ["q1", "q2", "q3"]


def test_breadth_cap_is_logged_not_silent():
    logs: list[str] = []
    plan = lambda q, prior: [] if prior else [SubQuery(f"q{i}") for i in range(10)]
    res = run_deep_search(
        "q", plan_fn=plan, search_fn=_search,
        synth_fn=lambda q, f: ("x", []),
        max_subqueries=3, on_log=logs.append,
    )
    assert len(res.findings) == 3
    assert any("capping" in m for m in logs)


def test_partial_failure_still_synthesizes():
    def flaky(sq: SubQuery) -> Finding:
        if sq.query == "boom":
            raise RuntimeError("network down")
        return _search(sq)

    captured = {}

    def synth(q, findings):
        captured["ok"] = [f.subquery.query for f in findings if f.ok]
        return ("partial answer", [])

    res = run_deep_search(
        "q", plan_fn=lambda q, p: [] if p else [SubQuery("ok1"), SubQuery("boom")],
        search_fn=flaky, synth_fn=synth,
    )
    assert res.partial is True
    assert res.answer == "partial answer"
    # the failed sub-query is present as an errored finding, excluded from synth
    assert any(not f.ok for f in res.findings)
    assert captured["ok"] == ["ok1"]


def test_investigate_routes_to_investigate_fn():
    seen = {"search": 0, "investigate": 0}

    def search(sq):
        seen["search"] += 1
        return _finding(sq)

    def investigate(sq):
        seen["investigate"] += 1
        return _finding(sq, text="deep")

    plan = lambda q, prior: [] if prior else [
        SubQuery("look", KIND_SEARCH), SubQuery("dig", KIND_INVESTIGATE),
    ]
    run_deep_search("q", plan_fn=plan, search_fn=search, investigate_fn=investigate,
                    synth_fn=lambda q, f: ("a", []))
    assert seen == {"search": 1, "investigate": 1}


def test_cancellation_stops_and_marks():
    res = run_deep_search(
        "q", plan_fn=lambda q, p: [SubQuery("a")], search_fn=_search,
        synth_fn=lambda q, f: ("a", []), cancelled=lambda: True,
    )
    assert res.cancelled is True
    assert res.findings == []
    assert "cancelled" in res.answer


def test_warm_start_seeds_first_wave():
    called = {"plan": 0}

    def plan(q, prior):
        called["plan"] += 1
        return []

    res = run_deep_search(
        "q", warm_start=["seed one", "seed two"],
        plan_fn=plan, search_fn=_search, synth_fn=lambda q, f: ("a", []),
        max_rounds=1,
    )
    assert [f.subquery.query for f in res.findings] == ["seed one", "seed two"]
    assert called["plan"] == 0  # wave 1 used the warm-start, not the planner


def test_on_progress_reaches_full():
    snaps: list[DeepSearchProgress] = []
    plan = lambda q, prior: [] if prior else [SubQuery("a"), SubQuery("b")]
    run_deep_search("q", plan_fn=plan, search_fn=_search,
                    synth_fn=lambda q, f: ("a", []), on_progress=snaps.append)
    assert snaps  # progress was reported
    last = snaps[-1]
    assert last.done == last.total == 2
    assert all(st == "done" for _label, st in last.items)


def test_no_results_answer():
    res = run_deep_search("q", plan_fn=lambda q, p: [], search_fn=_search,
                          synth_fn=lambda q, f: ("unused", []))
    assert res.findings == []
    assert "no usable results" in res.answer


def test_synthesize_false_skips_synth():
    calls = {"synth": 0}

    def synth(q, f):
        calls["synth"] += 1
        return ("x", [])

    res = run_deep_search(
        "q", plan_fn=lambda q, p: [] if p else [SubQuery("a")],
        search_fn=_search, synth_fn=synth, synthesize=False,
    )
    assert calls["synth"] == 0
    assert res.answer == ""
    assert len(res.findings) == 1  # findings still gathered for the caller


# --- activation: heavy auto-runs and expert can escalate ------------------ #

def _fake_ds_result(question, **kw):
    sq = SubQuery("q1")
    return DeepSearchResult(
        question=question,
        findings=[Finding(subquery=sq, text="found stuff", citations=["http://a"])],
        citations=["http://a"],
    )


def test_heavy_tier_auto_runs_deep_search_before_synthesis(tmp_path, monkeypatch):
    captured = {}

    def fake_run(question, **kw):
        captured["question"] = question
        captured["warm_start"] = kw.get("warm_start")
        return _fake_ds_result(question, **kw)

    monkeypatch.setattr("xlii.deep_search.run_deep_search", fake_run)

    agent = make_agent(tmp_path)
    agent.session.conversational = True
    agent.session.chat_tier = "heavy"
    script_iterations(
        agent,
        ("final answer [1]", None),
    )
    answer, _, stats = agent.run_turn("who won last night")

    # heavy runs the coordinator before the first synthesis model call.
    assert captured["question"] == "who won last night"
    assert captured["warm_start"] is None
    assert answer == "final answer [1]"
    assert stats.tool_calls == 1
    # the findings digest reached history as the tool result
    tool_msgs = [h for h in agent.history if h.get("role") == "tool"]
    assert any("found stuff" in m["content"] for m in tool_msgs)
    assert any("http://a" in m["content"] for m in tool_msgs)


def test_sigil_heavy_runs_deep_search_with_stripped_question(tmp_path, monkeypatch):
    # `>>h …` (Vector D) outranks a cheap sticky pick for one message, and the
    # coordinator gets the question with the sigil already stripped.
    captured = {}

    def fake_run(question, **kw):
        captured["question"] = question
        return _fake_ds_result(question, **kw)

    monkeypatch.setattr("xlii.deep_search.run_deep_search", fake_run)

    agent = make_agent(tmp_path)
    agent.session.conversational = True
    agent.session.chat_tier = "fast"
    script_iterations(agent, ("final answer [1]", None))
    agent.run_turn(">>h who won last night")

    assert captured["question"] == "who won last night"
    assert agent.session.chat_tier == "fast"  # one-shot, sticky untouched


def test_auto_heavy_auto_runs_deep_search(tmp_path, monkeypatch):
    captured = {}

    def fake_run(question, **kw):
        captured["question"] = question
        return _fake_ds_result(question, **kw)

    monkeypatch.setattr("xlii.deep_search.run_deep_search", fake_run)

    agent = make_agent(tmp_path)
    agent.session.conversational = True
    agent.session.chat_tier = "auto"
    script_iterations(agent, ("final answer [1]", None))
    agent.run_turn("what's the latest news on the merger")

    assert captured["question"] == "what's the latest news on the merger"


def test_deep_search_tool_runs_on_expert_tier_with_raw_question(tmp_path, monkeypatch):
    captured = {}

    def fake_run(question, **kw):
        captured["question"] = question
        captured["warm_start"] = kw.get("warm_start")
        return _fake_ds_result(question, **kw)

    monkeypatch.setattr("xlii.deep_search.run_deep_search", fake_run)

    agent = make_agent(tmp_path)
    agent.session.conversational = True
    agent.session.chat_tier = "expert"
    agent.session.user_shell_cwd = "/home/alice/private"
    script_iterations(
        agent,
        ("", [("request_deep_search",
               {"reason": "needs live data", "subqueries": ["a", "b"]})]),
        ("final answer [1]", None),
    )
    agent.run_turn("who won last night")

    assert captured["question"] == "who won last night"
    assert captured["warm_start"] == ["a", "b"]


def test_deep_search_tool_gated_off_fast_tier(tmp_path, monkeypatch):
    ran = {"called": False}

    def fake_run(question, **kw):
        ran["called"] = True
        return _fake_ds_result(question, **kw)

    monkeypatch.setattr("xlii.deep_search.run_deep_search", fake_run)

    agent = make_agent(tmp_path)
    agent.session.conversational = True
    agent.session.chat_tier = "fast"  # fast never gets the escape hatch
    script_iterations(
        agent,
        ("", [("request_deep_search", {"reason": "x"})]),
        ("done", None),
    )
    agent.run_turn("thanks")

    assert ran["called"] is False  # tool was never advertised, so never executed
    tool_msgs = [h for h in agent.history if h.get("role") == "tool"]
    assert any("advertised palette" in m["content"] for m in tool_msgs)


def test_deep_search_tool_gated_off_auto_fast_turn(tmp_path, monkeypatch):
    ran = {"called": False}

    def fake_run(question, **kw):
        ran["called"] = True
        return _fake_ds_result(question, **kw)

    monkeypatch.setattr("xlii.deep_search.run_deep_search", fake_run)

    agent = make_agent(tmp_path)
    agent.session.conversational = True
    agent.session.chat_tier = "auto"
    script_iterations(
        agent,
        ("", [("request_deep_search", {"reason": "x"})]),
        ("done", None),
    )
    agent.run_turn("thanks")

    assert ran["called"] is False
    tool_msgs = [h for h in agent.history if h.get("role") == "tool"]
    assert any("advertised palette" in m["content"] for m in tool_msgs)


# --- mid-wave abort (tauri-face live-fire: stop must reach a running wave) - #


def test_fan_out_abort_returns_partial_without_waiting():
    """`should_abort` frees the TURN mid-wave: queued sub-queries are
    cancelled and control returns in ~a poll interval — previously the wave
    blocked on every in-flight future and a stop took minutes."""
    import threading
    import time

    from xlii.deep_search import _fan_out

    release = threading.Event()

    def slow_search(sq: SubQuery) -> Finding:
        release.wait(10)
        return _finding(sq)

    subs = [SubQuery(f"q{i}", KIND_SEARCH, "web") for i in range(3)]
    start = time.monotonic()
    try:
        out = _fan_out(
            subs, slow_search, slow_search, 1,
            base_done=0, grand_total=3, on_progress=None,
            should_abort=lambda: True,
        )
    finally:
        release.set()  # let the one in-flight worker finish and die
    assert out == []
    assert time.monotonic() - start < 5


def test_run_deep_search_cancel_mid_wave_marks_cancelled():
    """The turn-level `cancelled` hook reaches INTO the wave (not just the
    between-waves poll): a cancel during fan-out still yields the honest
    cancelled result instead of hanging on the remaining sub-queries."""
    import threading

    from xlii.deep_search import run_deep_search

    cancel = threading.Event()
    started = threading.Event()

    def slow_search(sq: SubQuery) -> Finding:
        started.set()
        cancel.wait(10)  # the searches only end once cancel fires
        return _finding(sq)

    def plan(question, findings):
        if findings:
            return []
        return [SubQuery(f"q{i}", KIND_SEARCH, "web") for i in range(3)]

    canceller = threading.Timer(0.2, cancel.set)
    canceller.start()
    try:
        res = run_deep_search(
            "anything",
            plan_fn=plan,
            search_fn=slow_search,
            investigate_fn=slow_search,
            synth_fn=lambda q, f: ("synth", []),
            max_parallel=1,
            cancelled=cancel.is_set,
        )
    finally:
        canceller.cancel()
        cancel.set()
    assert started.is_set()
    assert res.cancelled is True


# --- G4: optional gig / gaggle on investigate hops ------------------------ #


def _gig_cfg(*, allow=None, investigate_gig="", investigate_gaggle="",
             providers=("kimi",)):
    cfg = GlobalConfig()
    defaults = {"allow": list(allow if allow is not None else [])}
    if investigate_gig:
        defaults["investigate_gig"] = investigate_gig
    if investigate_gaggle:
        defaults["investigate_gaggle"] = investigate_gaggle
    cfg.gigwork = {
        "providers": {
            p: {
                "kind": "openai_compat",
                "base_url": "https://x.test/v1",
                "api_key_env": f"{p.upper()}_TEST_KEY",
                "model": f"{p}-model",
            }
            for p in providers
        },
        "defaults": defaults,
    }
    cfg.max_worker_iterations = 1
    return cfg


class _RecordingWorker:
    """Stand-in WorkerAgent: records construction kwargs, returns canned text."""

    last: dict | None = None

    def __init__(self, **kw):
        type(self).last = kw

    def run(self, query, **kw):
        return (f"worker:{query}", CallStats())


class _StubGigBackend:
    def __init__(self, label="kimi"):
        self.label = label
        self.model = "stub-model"
        self.capabilities = frozenset({"chat", "tools"})


def _investigate_only():
    return lambda q, p: [] if p else [SubQuery("dig", KIND_INVESTIGATE)]


def _run_investigate(tmp_path, cfg, **kw):
    return run_deep_search(
        "q",
        clients=object(),
        cfg=cfg,
        project=make_agent(tmp_path).project,
        plan_fn=_investigate_only(),
        max_rounds=1,
        synthesize=False,
        **kw,
    )


def test_resolve_investigate_hire_home_when_unset():
    assert resolve_investigate_hire(_gig_cfg()) == (None, None)


def test_resolve_investigate_hire_defaults_and_call_site_wins():
    cfg = _gig_cfg(investigate_gaggle="second-opinion", allow=["kimi"])
    assert resolve_investigate_hire(cfg) == (None, "second-opinion")
    assert resolve_investigate_hire(cfg, gig="kimi") == ("kimi", None)
    assert resolve_investigate_hire(cfg, gaggle="debate") == (None, "debate")


def test_resolve_investigate_hire_conflicts_fail_closed():
    with pytest.raises(GigError, match="not both"):
        resolve_investigate_hire(_gig_cfg(), gig="kimi", gaggle="second-opinion")
    cfg = _gig_cfg(investigate_gig="kimi", investigate_gaggle="second-opinion")
    with pytest.raises(GigError, match="investigate_gig or investigate_gaggle"):
        resolve_investigate_hire(cfg)
    # Call-site still wins over a conflicting defaults pair.
    assert resolve_investigate_hire(cfg, gig="kimi") == ("kimi", None)


def test_investigate_default_home(tmp_path, monkeypatch):
    _RecordingWorker.last = None
    monkeypatch.setattr("xlii.worker_agent.WorkerAgent", _RecordingWorker)
    res = _run_investigate(tmp_path, _gig_cfg())
    assert res.findings[0].ok
    assert res.findings[0].text == "worker:dig"
    assert _RecordingWorker.last["role"] == "general"
    assert _RecordingWorker.last.get("chat_backend") is None


def test_investigate_gig_default(tmp_path, monkeypatch):
    backend = _StubGigBackend("kimi")
    hired: dict = {}

    def _resolve(cfg, name):
        hired["name"] = name
        return backend

    monkeypatch.setattr("xlii.chat_backend.resolve_gig_backend", _resolve)
    _RecordingWorker.last = None
    monkeypatch.setattr("xlii.worker_agent.WorkerAgent", _RecordingWorker)
    pool = SimpleNamespace(
        acquire=lambda: (_ for _ in ()).throw(AssertionError("pool drawn for a gig")),
        report_success=lambda c: (_ for _ in ()).throw(AssertionError("pool success")),
        report_auth_failure=lambda c: (_ for _ in ()).throw(AssertionError("pool auth")),
    )
    res = _run_investigate(
        tmp_path,
        _gig_cfg(allow=["kimi"], investigate_gig="kimi"),
        pool=pool,
    )
    assert hired["name"] == "kimi"
    assert res.findings[0].ok
    assert res.findings[0].text == "worker:dig"
    assert _RecordingWorker.last["role"] == "explore"
    assert _RecordingWorker.last["chat_backend"] is backend
    assert _RecordingWorker.last["worker_writes"] is False


def test_investigate_gaggle_default(tmp_path, monkeypatch):
    seen: dict = {}

    def _fake_run(name, question, **kw):
        seen["name"] = name
        seen["question"] = question
        return SimpleNamespace(merged="## Agreements\nx")

    monkeypatch.setattr("xlii.jam.run_jam", _fake_run)
    res = _run_investigate(
        tmp_path,
        _gig_cfg(allow=["kimi"], investigate_gaggle="second-opinion"),
    )
    assert seen["name"] == "second-opinion"
    assert seen["question"] == "dig"
    assert res.findings[0].ok
    assert "## Agreements" in res.findings[0].text


def test_investigate_call_site_override(tmp_path, monkeypatch):
    backend = _StubGigBackend("kimi")
    monkeypatch.setattr(
        "xlii.chat_backend.resolve_gig_backend", lambda cfg, name: backend
    )
    jam_calls: list = []

    def _boom_jam(*a, **k):
        jam_calls.append((a, k))
        raise AssertionError("gaggle default must not run when gig= is set")

    monkeypatch.setattr("xlii.jam.run_jam", _boom_jam)
    _RecordingWorker.last = None
    monkeypatch.setattr("xlii.worker_agent.WorkerAgent", _RecordingWorker)
    res = _run_investigate(
        tmp_path,
        _gig_cfg(allow=["kimi"], investigate_gaggle="second-opinion"),
        gig="kimi",
    )
    assert jam_calls == []
    assert res.findings[0].ok
    assert _RecordingWorker.last["chat_backend"] is backend

    seen: dict = {}

    def _fake_run(name, question, **kw):
        seen["name"] = name
        return SimpleNamespace(merged="gaggle wins")

    monkeypatch.setattr("xlii.jam.run_jam", _fake_run)
    res = _run_investigate(
        tmp_path,
        _gig_cfg(allow=["kimi"], investigate_gig="kimi"),
        gaggle="second-opinion",
    )
    assert seen["name"] == "second-opinion"
    assert res.findings[0].text == "gaggle wins"


def test_investigate_allowlist_refusal(tmp_path, monkeypatch):
    def _resolve(cfg, name):
        raise AssertionError("must not mint a backend off the allowlist")

    monkeypatch.setattr("xlii.chat_backend.resolve_gig_backend", _resolve)
    _RecordingWorker.last = None
    monkeypatch.setattr("xlii.worker_agent.WorkerAgent", _RecordingWorker)
    res = _run_investigate(
        tmp_path,
        _gig_cfg(allow=[], investigate_gig="kimi"),
    )
    assert not res.findings[0].ok
    assert "not in gigwork.defaults.allow" in (res.findings[0].error or "")
    assert _RecordingWorker.last is None

    res = _run_investigate(
        tmp_path,
        _gig_cfg(allow=[]),
        gig="kimi",
    )
    assert not res.findings[0].ok
    assert "not in gigwork.defaults.allow" in (res.findings[0].error or "")
    assert _RecordingWorker.last is None


def test_investigate_gig_hire_failure_no_home_fallback(tmp_path, monkeypatch):
    def _resolve(cfg, name):
        raise GigError("gig provider 'kimi': environment variable KIMI_TEST_KEY is not set")

    monkeypatch.setattr("xlii.chat_backend.resolve_gig_backend", _resolve)
    _RecordingWorker.last = None
    monkeypatch.setattr("xlii.worker_agent.WorkerAgent", _RecordingWorker)
    res = _run_investigate(
        tmp_path,
        _gig_cfg(allow=["kimi"], investigate_gig="kimi"),
    )
    assert not res.findings[0].ok
    assert "KIMI_TEST_KEY is not set" in (res.findings[0].error or "")
    assert _RecordingWorker.last is None  # home worker never constructed


def test_investigate_gig_and_gaggle_conflict(tmp_path, monkeypatch):
    _RecordingWorker.last = None
    monkeypatch.setattr("xlii.worker_agent.WorkerAgent", _RecordingWorker)
    res = _run_investigate(
        tmp_path,
        _gig_cfg(allow=["kimi"]),
        gig="kimi",
        gaggle="second-opinion",
    )
    assert not res.findings[0].ok
    assert "not both" in (res.findings[0].error or "")
    assert _RecordingWorker.last is None

    res = _run_investigate(
        tmp_path,
        _gig_cfg(
            allow=["kimi"],
            investigate_gig="kimi",
            investigate_gaggle="second-opinion",
        ),
    )
    assert not res.findings[0].ok
    assert "investigate_gig or investigate_gaggle" in (res.findings[0].error or "")
    assert _RecordingWorker.last is None


def test_request_deep_search_passes_call_site_pins(tmp_path, monkeypatch):
    captured: dict = {}

    def fake_run(question, **kw):
        captured["gig"] = kw.get("gig")
        captured["gaggle"] = kw.get("gaggle")
        return DeepSearchResult(question=question)

    monkeypatch.setattr("xlii.deep_search.run_deep_search", fake_run)
    import xlii.agent as A

    agent = make_agent(tmp_path)
    agent.cfg = _gig_cfg(allow=["kimi"])
    A.Agent._run_deep_search(agent, {"reason": "x", "gig": "kimi"})
    assert captured["gig"] == "kimi"
    assert captured["gaggle"] is None

    A.Agent._run_deep_search(
        agent, {"reason": "x", "gaggle": "second-opinion"},
    )
    assert captured["gaggle"] == "second-opinion"
    assert captured["gig"] is None


def test_request_deep_search_gig_and_gaggle_tool_text(tmp_path, monkeypatch):
    ran = {"called": False}

    def fake_run(question, **kw):
        ran["called"] = True
        return DeepSearchResult(question=question)

    monkeypatch.setattr("xlii.deep_search.run_deep_search", fake_run)
    import xlii.agent as A

    agent = make_agent(tmp_path)
    agent.cfg = _gig_cfg(allow=["kimi"])
    text = A.Agent._run_deep_search(
        agent,
        {"reason": "x", "gig": "kimi", "gaggle": "second-opinion"},
    )
    assert text.startswith("request_deep_search:")
    assert "not both" in text
    assert ran["called"] is False

    agent.cfg = _gig_cfg(
        allow=["kimi"],
        investigate_gig="kimi",
        investigate_gaggle="second-opinion",
    )
    text = A.Agent._run_deep_search(agent, {"reason": "x"})
    assert "investigate_gig or investigate_gaggle" in text
    assert ran["called"] is False
