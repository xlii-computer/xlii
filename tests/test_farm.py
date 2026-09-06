"""Farm v0 — named advisory jobs on a file board. No network, no MUC."""

from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import pytest

from xlii.farm import (
    PUBLISHED_JOBS,
    REFUSED_JOBS,
    JobError,
    JobResult,
    Ticket,
    Workplace,
    WORKPLACE_LOCAL,
    job_offers,
    skip_reason,
    utc_now,
    valid_job_id,
)
from xlii.job_board import JobBoard
from xlii.job_run import pick_and_run, run_ticket


def _cfg(tmp_path, **jobs):
    block = {
        "offers": ["explore"],
        "gig": "kimi",
        "node": "kimi-laptop",
        "board": str(tmp_path / "board"),
        **jobs,
    }
    return SimpleNamespace(jobs=block, max_worker_iterations=4)


def _ticket(**kw) -> Ticket:
    return Ticket.make(job="explore", task="is this racy?", **kw)


def test_published_jobs_are_explore_only():
    assert PUBLISHED_JOBS == {"explore"}
    assert "bash" in REFUSED_JOBS
    assert "lab" in REFUSED_JOBS


def test_make_refuses_bash_and_empty_task():
    with pytest.raises(JobError, match="not a published"):
        Ticket.make(job="bash", task="rm -rf /")
    with pytest.raises(JobError, match="task is required"):
        Ticket.make(job="explore", task="  ")


def test_offers_strip_refused_names():
    cfg = SimpleNamespace(jobs={"offers": ["explore", "bash", "lab", "nope"]})
    assert job_offers(cfg) == {"explore"}
    cfg2 = SimpleNamespace(jobs={"offers": []})
    assert job_offers(cfg2) == frozenset()


def test_skip_reason_unknown_job_and_empty_offers(tmp_path):
    t = _ticket()
    t.job = "bash"
    assert "never offerable" in (skip_reason(t, _cfg(tmp_path)) or "")
    t.job = "consult"
    assert "not published" in (skip_reason(t, _cfg(tmp_path)) or "")
    t.job = "explore"
    empty = _cfg(tmp_path, offers=[])
    assert "not in this box's offers" in (skip_reason(t, empty) or "")
    assert skip_reason(t, _cfg(tmp_path)) is None


def test_skip_reason_local_project_missing(tmp_path):
    t = Ticket.make(
        job="explore", task="look at auth",
        workplace=Workplace(mode=WORKPLACE_LOCAL, project="iXaac-lab"),
    )
    cfg = _cfg(tmp_path)
    assert "not in jobs.projects" in (skip_reason(t, cfg) or "")
    cfg = _cfg(tmp_path, projects={"iXaac-lab": str(tmp_path / "missing")})
    assert "does not exist" in (skip_reason(t, cfg) or "")
    lab = tmp_path / "lab"
    lab.mkdir()
    cfg = _cfg(tmp_path, projects={"iXaac-lab": str(lab)})
    assert skip_reason(t, cfg) is None


def test_board_post_claim_complete(tmp_path):
    board = JobBoard(tmp_path / "jobs")
    t = _ticket()
    board.post(t)
    assert board.list_open()[0].id == t.id
    claimed = board.claim(t.id, "kimi-laptop")
    assert claimed is not None and claimed.claimed_by == "kimi-laptop"
    assert board.list_open() == []
    assert board.list_claimed()[0].id == t.id
    result = JobResult(
        id=t.id, status="done", node="kimi-laptop", job="explore",
        text="not racy", finished_at=utc_now(),
    )
    board.complete(claimed, result)
    lane, ticket, res = board.get(t.id)
    assert lane == "done" and ticket.task == t.task
    assert res is not None and res.text == "not racy"


def test_claim_is_exclusive(tmp_path):
    board = JobBoard(tmp_path / "jobs")
    t = _ticket()
    board.post(t)
    a = board.claim(t.id, "a")
    b = board.claim(t.id, "b")
    assert a is not None and a.claimed_by == "a"
    assert b is None
    assert board.get(t.id)[0] == "claimed"


def test_expire_stale_returns_to_open(tmp_path):
    from datetime import datetime, timedelta, timezone

    board = JobBoard(tmp_path / "jobs")
    t = _ticket()
    t.claim_ttl_s = 30
    board.post(t)
    claimed = board.claim(t.id, "slow")
    assert claimed is not None
    claimed.claimed_at = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    (board.claimed_dir / f"{t.id}.json").write_text(claimed.dump())
    reopened = board.expire_stale()
    assert t.id in reopened
    assert board.get(t.id)[0] == "open"
    assert board.get(t.id)[1].claimed_by == ""


def test_valid_job_id_is_a_filename_stem():
    assert valid_job_id("a" * 32)
    assert valid_job_id(_ticket().id)
    assert not valid_job_id("")
    assert not valid_job_id("../secrets")
    assert not valid_job_id("abc/../../etc/passwd")
    assert not valid_job_id("foo\\bar")
    assert not valid_job_id("a..b")
    assert not valid_job_id("bad id")


def test_ticket_from_dict_rejects_path_escape():
    raw = _ticket().to_dict()
    raw["id"] = "../secrets"
    with pytest.raises(JobError, match="invalid ticket id"):
        Ticket.from_dict(raw)
    raw["id"] = "abc/../../etc/passwd"
    with pytest.raises(JobError, match="invalid ticket id"):
        Ticket.from_dict(raw)
    raw["id"] = _ticket().id
    assert Ticket.from_dict(raw).id == raw["id"]


def test_board_get_and_claim_refuse_traversal(tmp_path):
    board = JobBoard(tmp_path / "jobs")
    board.ensure()
    (tmp_path / "pwned.json").write_text("nope")
    assert board.get("../pwned") is None
    assert board.get("..") is None
    assert board.claim("../pwned", "n") is None
    with pytest.raises(JobError, match="invalid job id"):
        board._open_path("../pwned")
    t = _ticket()
    posted = board.post(t)
    assert posted.parent == board.open_dir.resolve()
    assert posted.name == f"{t.id}.json"


class _FakeWorker:
    def __init__(self, reply="ok", error=None):
        self.reply = reply
        self.error = error
        self.seen = {}

    def run(self, task, context=None, max_iterations=None, should_stop=None, **kw):
        self.seen = dict(task=task, context=context, max_iterations=max_iterations,
                         should_stop=should_stop)
        if self.error:
            raise self.error
        return self.reply, SimpleNamespace(cost_usd=0.02, iterations=1)


def test_pick_and_run_explore(tmp_path):
    cfg = _cfg(tmp_path)
    board = JobBoard(tmp_path / "board")
    t = _ticket(accept="verdict only")
    board.post(t)
    fake = _FakeWorker(reply="verdict: no")

    def factory(_cfg, _project, _backend):
        return fake

    result = pick_and_run(cfg, board, worker_factory=factory)
    assert result is not None and result.status == "done"
    assert result.text == "verdict: no"
    assert "Acceptance:" in fake.seen["task"]
    assert fake.seen["max_iterations"] == 8
    assert board.get(t.id)[0] == "done"


def test_watch_skips_jobs_it_does_not_offer(tmp_path):
    cfg = _cfg(tmp_path, offers=[])
    board = JobBoard(tmp_path / "board")
    board.post(_ticket())
    fake = _FakeWorker()
    result = pick_and_run(
        cfg, board, worker_factory=lambda *a: fake,
    )
    assert result is None
    assert board.list_open()  # still sitting there


def test_run_ticket_skips_bash_even_if_forged(tmp_path):
    cfg = _cfg(tmp_path)
    t = _ticket()
    t.job = "bash"
    result = run_ticket(t, cfg, worker_factory=lambda *a: _FakeWorker())
    assert result.status == "skipped"
    assert "never offerable" in result.reason


def test_run_ticket_uses_passed_node_over_jobs_default(tmp_path):
    cfg = _cfg(tmp_path, node="")
    t = _ticket()
    result = run_ticket(
        t, cfg, worker_factory=lambda *a: _FakeWorker(), node="shop-a",
    )
    assert result.status == "done"
    assert result.node == "shop-a"
    defaulted = run_ticket(t, cfg, worker_factory=lambda *a: _FakeWorker())
    assert defaulted.node == "node"


def test_cli_post_ls_show_result(tmp_path, monkeypatch):
    from xlii.cmds import jobs as job_cmd
    from xlii.config import GlobalConfig

    cfg = GlobalConfig()
    cfg.jobs = {
        "offers": ["explore"],
        "node": "throne",
        "board": str(tmp_path / "board"),
    }
    monkeypatch.setattr(job_cmd, "GlobalConfig", SimpleNamespace(load=lambda: cfg))

    rc = job_cmd.cmd_job_post(Namespace(
        job="explore", task="look at this", context="", accept="",
        project="", rev="", budget=None, max_iters=8,
    ))
    assert rc == 0
    board = JobBoard(tmp_path / "board")
    open_tickets = board.list_open()
    assert len(open_tickets) == 1
    job_id = open_tickets[0].id

    rc = job_cmd.cmd_job_ls(Namespace())
    assert rc == 0

    rc = job_cmd.cmd_job_show(Namespace(id=job_id))
    assert rc == 0

    rc = job_cmd.cmd_job_result(Namespace(id=job_id, json=False))
    assert rc == 1  # not done yet


def test_cli_watch_once_with_fake_worker(tmp_path, monkeypatch):
    from xlii.cmds import jobs as job_cmd
    from xlii.config import GlobalConfig
    from xlii.job_run import pick_and_run as real_pick

    cfg = GlobalConfig()
    cfg.jobs = {
        "offers": ["explore"],
        "gig": "kimi",
        "node": "kimi-laptop",
        "board": str(tmp_path / "board"),
    }
    monkeypatch.setattr(job_cmd, "GlobalConfig", SimpleNamespace(load=lambda: cfg))
    board = JobBoard(tmp_path / "board")
    board.post(_ticket())
    fake = _FakeWorker(reply="from kimi")

    def fake_pick(cfg_arg, board_arg, worker_factory=None):
        return real_pick(cfg_arg, board_arg, worker_factory=lambda *a: fake)

    monkeypatch.setattr("xlii.job_run.pick_and_run", fake_pick)

    rc = job_cmd.cmd_job_watch(Namespace(once=True, poll=0.1))
    assert rc == 0
    done = list((tmp_path / "board" / "done").glob("*.json"))
    assert len(done) == 1
    _lane, ticket_done, res = board.get(done[0].stem)
    assert ticket_done.id
    assert res is not None and res.text == "from kimi"


def test_parser_registers_job():
    from xlii.cli import build_parser

    p = build_parser()
    args = p.parse_args(["job", "ls"])
    assert args.command == "job" and args.job_cmd == "ls"


def test_muc_envelope_roundtrip():
    from xlii.farm_muc import (
        OP_AD, RoomLedger, encode_ad, encode_claim, parse_farm_body,
    )

    t = _ticket()
    op, data = parse_farm_body(encode_ad(t))
    assert op == OP_AD and data["ticket"]["id"] == t.id
    assert parse_farm_body("hello") is None
    assert parse_farm_body('{"xlii":"nope","op":"ad"}') is None

    from datetime import datetime, timedelta, timezone

    led = RoomLedger()
    led.apply_body(encode_ad(t), owner=True)
    base = datetime.now(timezone.utc)
    led.apply_body(encode_claim(
        job_id=t.id, node="b", at=(base + timedelta(seconds=2)).isoformat(),
    ))
    led.apply_body(encode_claim(
        job_id=t.id, node="a", at=(base + timedelta(seconds=1)).isoformat(),
    ))
    assert led.winner(t.id) == "a"
    assert led.open_tickets() == []


def test_muc_same_timestamp_node_name_tiebreak():
    from xlii.farm_muc import RoomLedger, encode_ad, encode_claim

    from xlii.farm import utc_now

    t = _ticket()
    led = RoomLedger()
    led.apply_body(encode_ad(t), owner=True)
    at = utc_now()
    led.apply_body(encode_claim(job_id=t.id, node="zeta", at=at))
    led.apply_body(encode_claim(job_id=t.id, node="acer", at=at))
    assert led.winner(t.id) == "acer"


def test_farm_runtime_lost_claim(tmp_path, monkeypatch):
    import asyncio

    from xlii.farm_muc import encode_claim
    from xlii.farm_xmpp import FarmRuntime
    from xlii.job_board import JobBoard

    monkeypatch.setattr("xlii.farm_xmpp.CLAIM_GRACE_S", 0)
    sent: list[str] = []
    cfg = _cfg(tmp_path)
    board = JobBoard(tmp_path / "board")
    rt = FarmRuntime(
        cfg, node="acer", board=board, send_body=sent.append, pickup=True,
    )
    from datetime import datetime, timedelta, timezone

    from xlii.farm_muc import encode_ad as _encode_ad

    t = _ticket()
    rt.ingest(_encode_ad(t), owner=True)
    earlier = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    rt.ledger.apply_body(encode_claim(job_id=t.id, node="other", at=earlier))

    def boom(*a, **k):
        raise AssertionError("should not run after losing the claim")

    monkeypatch.setattr("xlii.farm_xmpp.run_ticket", boom)
    result = asyncio.run(rt.consider(t))
    assert result is None


def test_farm_runtime_runs_when_claim_wins(tmp_path, monkeypatch):
    import asyncio

    from xlii.farm import JobResult, utc_now
    from xlii.farm_muc import encode_ad
    from xlii.farm_xmpp import FarmRuntime
    from xlii.job_board import JobBoard

    monkeypatch.setattr("xlii.farm_xmpp.CLAIM_GRACE_S", 0)
    sent: list[str] = []
    cfg = _cfg(tmp_path)
    board = JobBoard(tmp_path / "board")
    rt = FarmRuntime(
        cfg, node="acer", board=board, send_body=sent.append, pickup=True,
    )
    t = _ticket()
    rt.ingest(encode_ad(t), owner=True)

    def fake_run(ticket, cfg_arg, **kw):
        return JobResult(
            id=ticket.id, status="done", node="acer", job="explore",
            text="from acer", finished_at=utc_now(),
        )

    monkeypatch.setattr("xlii.farm_xmpp.run_ticket", fake_run)
    result = asyncio.run(rt.consider(t))
    assert result is not None and result.status == "done"
    assert any('"op": "claim"' in s or '"op":"claim"' in s for s in sent)
    assert any('"op": "result"' in s or '"op":"result"' in s for s in sent)
