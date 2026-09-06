"""`xlii job` — post / list / watch named advisory farm jobs.

Cancel rungs: ``cancel`` (withdraw, cooperative), ``bench`` (pickup→observe,
cooperative), ``evict`` (XEP-0045 membership revoke, enforced). ``kill``
remains the process rung.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from xlii.config import GlobalConfig
from xlii.job_board import JobBoard
from xlii.farm import (
    KIND_ADVISORY,
    Budget,
    JobError,
    Ticket,
    Workplace,
    WORKPLACE_BRIEF,
    WORKPLACE_LOCAL,
    job_board_root,
    job_muc_room,
    job_node_name,
    job_offers,
)


def _board(cfg: GlobalConfig) -> JobBoard:
    board = JobBoard(job_board_root(cfg))
    board.ensure()
    return board


def _read_context(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if text == "-":
        return sys.stdin.read()
    path = Path(text).expanduser()
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return raw


def cmd_job_post(args: argparse.Namespace) -> int:
    cfg = GlobalConfig.load()
    job = (args.job or "").strip()
    task = (args.task or "").strip()
    if args.project:
        workplace = Workplace(
            mode=WORKPLACE_LOCAL,
            project=args.project.strip(),
            rev=(args.rev or "").strip(),
        )
    else:
        workplace = Workplace(mode=WORKPLACE_BRIEF)
    try:
        budget = Budget(
            max_usd=float(args.budget) if args.budget is not None else None,
            max_iters=int(args.max_iters),
        )
        ticket = Ticket.make(
            job=job,
            task=task,
            context=_read_context(getattr(args, "context", "") or ""),
            accept=(args.accept or "").strip(),
            workplace=workplace,
            budget=budget,
            posted_by=job_node_name(cfg) if job_node_name(cfg) != "node" else "throne",
            kind=str(getattr(args, "kind", "") or KIND_ADVISORY),
        )
    except JobError as e:
        print(f"job post: {e}", file=sys.stderr)
        return 1
    board = _board(cfg)
    board.post(ticket)
    print(ticket.id)
    if job_muc_room(cfg):
        try:
            from xlii.farm_xmpp import publish_ad
            if publish_ad(cfg, ticket) != 0:
                print("job post: on file board; MUC publish failed", file=sys.stderr)
        except ImportError:
            print(
                "job post: on file board; MUC needs xlii[daemon] extra",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"job post: on file board; MUC failed: {e}", file=sys.stderr)
    return 0


def cmd_job_ls(args: argparse.Namespace) -> int:
    cfg = GlobalConfig.load()
    board = _board(cfg)
    rows: list[tuple[str, Ticket]] = []
    for t in board.list_open():
        rows.append(("open", t))
    for t in board.list_claimed():
        rows.append(("claimed", t))
    if board.done_dir.is_dir():
        for path in sorted(board.done_dir.glob("*.json")):
            try:
                found = board.get(path.stem)
            except JobError:
                continue
            if found:
                rows.append(("done", found[1]))
    if not rows:
        print("(no jobs)")
        return 0
    for lane, t in rows:
        task = " ".join(t.task.split())
        if len(task) > 60:
            task = task[:57] + "…"
        who = t.claimed_by or t.posted_by
        print(f"{t.id}  {lane:7}  {t.job:8}  {who:12}  {task}")
    return 0


def cmd_job_show(args: argparse.Namespace) -> int:
    cfg = GlobalConfig.load()
    found = _board(cfg).get(args.id)
    if found is None:
        print(f"job show: no such job {args.id}", file=sys.stderr)
        return 1
    lane, ticket, result = found
    print(f"# {lane}")
    print(ticket.dump(), end="")
    if result is not None:
        print("# result")
        print(result.dump(), end="")
    return 0


def cmd_job_result(args: argparse.Namespace) -> int:
    cfg = GlobalConfig.load()
    found = _board(cfg).get(args.id)
    if found is None:
        print(f"job result: no such job {args.id}", file=sys.stderr)
        return 1
    _lane, ticket, result = found
    if result is None:
        print(f"job result: {args.id} is {_lane} (no result yet)", file=sys.stderr)
        return 1
    if args.json:
        print(result.dump(), end="")
        return 0
    if result.status != "done":
        print(f"{result.status}: {result.reason or result.text}", file=sys.stderr)
        return 1
    print(result.text)
    return 0


def cmd_job_cancel(args: argparse.Namespace) -> int:
    cfg = GlobalConfig.load()
    job_id = (args.id or "").strip()
    if not job_id:
        print("job cancel: id is required", file=sys.stderr)
        return 1
    board = _board(cfg)
    found = board.get(job_id)
    if found is None:
        print(f"job cancel: no such job {job_id}", file=sys.stderr)
        return 1
    lane, _ticket, _result = found
    if lane == "done":
        print(f"job cancel: {job_id} is already done", file=sys.stderr)
        return 1
    if not board.cancel(job_id):
        print(f"job cancel: could not cancel {job_id}", file=sys.stderr)
        return 1
    print(job_id)
    if job_muc_room(cfg):
        try:
            from xlii.farm_xmpp import publish_cancel
            if publish_cancel(cfg, job_id, by=job_node_name(cfg)) != 0:
                print("job cancel: on file board; MUC publish failed", file=sys.stderr)
        except ImportError:
            print(
                "job cancel: on file board; MUC needs xlii[daemon] extra",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"job cancel: on file board; MUC failed: {e}", file=sys.stderr)
    return 0


def cmd_job_bench(args: argparse.Namespace) -> int:
    cfg = GlobalConfig.load()
    node = (args.node or "").strip()
    if not node:
        print("job bench: node is required", file=sys.stderr)
        return 1
    until = (getattr(args, "until", "") or "").strip()
    if not job_muc_room(cfg):
        print(
            "job bench: jobs.muc is empty — bench is a cooperative room op",
            file=sys.stderr,
        )
        return 1
    try:
        from xlii.farm_xmpp import publish_bench
    except ImportError:
        print("job bench: MUC needs xlii[daemon] extra (slixmpp)", file=sys.stderr)
        return 1
    try:
        rc = publish_bench(cfg, node, by=job_node_name(cfg), until=until)
    except Exception as e:
        print(f"job bench: {e}", file=sys.stderr)
        return 1
    if rc == 0:
        print(node)
    return rc


def cmd_job_evict(args: argparse.Namespace) -> int:
    cfg = GlobalConfig.load()
    node = (args.node or "").strip()
    if not node:
        print("job evict: node is required", file=sys.stderr)
        return 1
    if not job_muc_room(cfg):
        print(
            "job evict: jobs.muc is empty — evict is a MUC admin act (XEP-0045)",
            file=sys.stderr,
        )
        return 1
    try:
        from xlii.farm_xmpp import evict_node
    except ImportError:
        print(
            "job evict: needs xlii[daemon] extra (slixmpp) and MUC owner affiliation",
            file=sys.stderr,
        )
        return 1
    try:
        return evict_node(cfg, node)
    except Exception as e:
        print(f"job evict: {e}", file=sys.stderr)
        return 1


def cmd_job_accept(args: argparse.Namespace) -> int:
    """Poster accept of a quarantined foreign result — bumps rep, does not fuse."""
    from xlii.farm_market import RepStore, accept_quarantined, rep_path

    cfg = GlobalConfig.load()
    job_id = (args.id or "").strip()
    fp = (getattr(args, "fp", "") or "").strip()
    if not job_id:
        print("job accept: id is required", file=sys.stderr)
        return 1
    if not fp:
        print("job accept: --fp <fingerprint> is required", file=sys.stderr)
        return 1
    board = _board(cfg)
    try:
        counts = accept_quarantined(
            board, job_id, fingerprint=fp, rep=RepStore(rep_path(board.root)),
        )
    except JobError as e:
        print(f"job accept: {e}", file=sys.stderr)
        return 1
    print(f"{fp}  accepted={counts['accepted']} disputed={counts['disputed']} "
          f"abandoned={counts['abandoned']}")
    return 0


def cmd_job_invite(args: argparse.Namespace) -> int:
    cfg = GlobalConfig.load()
    fp = (args.fp or "").strip()
    if not fp:
        print("job invite: fingerprint is required", file=sys.stderr)
        return 1
    venue = (getattr(args, "venue", "") or "").strip() or job_muc_room(cfg)
    if not venue:
        print(
            "job invite: jobs.muc is empty — pass --venue host or set jobs.muc",
            file=sys.stderr,
        )
        return 1
    try:
        from xlii.farm_xmpp import _creds
        from xlii.farm_market import create_deal_room
    except ImportError:
        print("job invite: needs xlii[daemon] extra (slixmpp)", file=sys.stderr)
        return 1
    try:
        dcfg, password = _creds()
    except Exception as e:
        print(f"job invite: {e}", file=sys.stderr)
        return 1

    async def _go() -> int:
        import asyncio
        from slixmpp import ClientXMPP

        xmpp = ClientXMPP(dcfg.jid, password)
        xmpp.register_plugin("xep_0030")
        xmpp.register_plugin("xep_0045")
        done = asyncio.Event()
        rc = 1
        room_out = ""

        async def _start(_e=None) -> None:
            nonlocal rc, room_out
            try:
                xmpp.send_presence()
                room_out = await create_deal_room(
                    xmpp, venue, fp, nick=job_node_name(cfg, dcfg.node_name),
                )
                rc = 0
            except Exception as e:
                print(f"job invite: {type(e).__name__}: {e}", file=sys.stderr)
                rc = 1
            finally:
                xmpp.disconnect()
                done.set()

        xmpp.add_event_handler("session_start", _start)
        xmpp.connect()
        await done.wait()
        if rc == 0:
            print(room_out)
        return rc

    import asyncio
    return asyncio.run(_go())


def cmd_job_watch(args: argparse.Namespace) -> int:
    cfg = GlobalConfig.load()
    offers = job_offers(cfg)
    muc = job_muc_room(cfg)
    if not offers and not muc:
        print(
            "job watch: jobs.offers is empty — this box will not pick up work",
            file=sys.stderr,
        )
        return 1
    if muc:
        try:
            from xlii.farm_xmpp import run_watch
        except ImportError:
            print(
                "job watch: MUC needs xlii[daemon] extra (slixmpp)",
                file=sys.stderr,
            )
            return 1
        try:
            return run_watch(cfg, once=bool(getattr(args, "once", False)))
        except Exception as e:
            print(f"job watch: MUC failed: {e}", file=sys.stderr)
            return 1

    board = _board(cfg)
    from xlii.job_run import pick_and_run

    poll = max(0.5, float(getattr(args, "poll", 2.0) or 2.0))
    once = bool(getattr(args, "once", False))
    while True:
        result = pick_and_run(cfg, board)
        if result is not None:
            print(
                f"{result.status} {result.id} {result.job} "
                f"({result.reason or 'ok'})",
                file=sys.stderr,
            )
            if once:
                return 0 if result.status == "done" else 1
        elif once:
            print("job watch: nothing eligible", file=sys.stderr)
            return 0
        if once:
            break
        time.sleep(poll)
    return 0


def register(sub) -> None:
    p = sub.add_parser(
        "job",
        help="Named advisory farm jobs: post an ad, watch as a node, read results.",
    )
    jsub = p.add_subparsers(dest="job_cmd", required=True)

    p_post = jsub.add_parser("post", help="Post an advisory job ad (throne).")
    p_post.add_argument("--job", default="explore", help="Named job (v0: explore)")
    p_post.add_argument("--task", required=True, help="What the node should answer")
    p_post.add_argument(
        "--context", default="",
        help="Pasted snippets, a file path, or - for stdin (brief workplace)",
    )
    p_post.add_argument("--accept", default="", help="What 'done' looks like")
    p_post.add_argument(
        "--project", default="",
        help="local-project name (must exist in the node's jobs.projects)",
    )
    p_post.add_argument("--rev", default="", help="Optional git rev hint")
    p_post.add_argument("--budget", type=float, default=None, help="Soft USD cap")
    p_post.add_argument("--max-iters", type=int, default=8, dest="max_iters")
    p_post.add_argument(
        "--kind", default=KIND_ADVISORY,
        help="advisory (house) or market (cross-throne; refuses context / stage / lab)",
    )
    p_post.set_defaults(func=cmd_job_post)

    p_ls = jsub.add_parser("ls", help="List open / claimed / done jobs.")
    p_ls.set_defaults(func=cmd_job_ls)

    p_show = jsub.add_parser("show", help="Print one ticket (and result if done).")
    p_show.add_argument("id")
    p_show.set_defaults(func=cmd_job_show)

    p_res = jsub.add_parser("result", help="Print a done job's text.")
    p_res.add_argument("id")
    p_res.add_argument("--json", action="store_true")
    p_res.set_defaults(func=cmd_job_result)

    p_watch = jsub.add_parser(
        "watch",
        help="Node loop: claim the next eligible ad and run it (explore, no bash).",
    )
    p_watch.add_argument("--once", action="store_true", help="One pick or idle, then exit")
    p_watch.add_argument("--poll", type=float, default=2.0, help="Seconds between scans")
    p_watch.set_defaults(func=cmd_job_watch)

    p_cancel = jsub.add_parser(
        "cancel",
        help="Withdraw a ticket (cooperative). Running node aborts within one iteration.",
    )
    p_cancel.add_argument("id", help="Ticket id")
    p_cancel.set_defaults(func=cmd_job_cancel)

    p_bench = jsub.add_parser(
        "bench",
        help="Flip a node pickup → observe (cooperative). Daemon stays up.",
    )
    p_bench.add_argument("node", help="Node nick to bench")
    p_bench.add_argument("--until", default="", help="Optional until stamp (informational)")
    p_bench.set_defaults(func=cmd_job_bench)

    p_evict = jsub.add_parser(
        "evict",
        help="Kick / revoke MUC membership (enforced, XEP-0045). Requires owner affiliation.",
    )
    p_evict.add_argument("node", help="Node nick to evict")
    p_evict.set_defaults(func=cmd_job_evict)

    p_accept = jsub.add_parser(
        "accept",
        help="Accept a quarantined market result (bumps fingerprint rep; never auto-fuses).",
    )
    p_accept.add_argument("id", help="Ticket id")
    p_accept.add_argument("--fp", required=True, help="OMEMO fingerprint to credit")
    p_accept.set_defaults(func=cmd_job_accept)

    p_invite = jsub.add_parser(
        "invite",
        help="Open a deal room and invite a market badge (fingerprint). Terms first.",
    )
    p_invite.add_argument("fp", help="Invitee's OMEMO fingerprint")
    p_invite.add_argument("--venue", default="", help="Venue host or room JID")
    p_invite.set_defaults(func=cmd_job_invite)
