"""Throne housekeep — collections, empty/test leftovers, registry ghosts, dead keys.

Read-only by default. Destructive flags only touch *untracked* cloud rows,
dead registry paths, and server keys that are not this machine's pool.
Never deletes a Collection a live project.json still claims.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from xlii.config import JOURNAL_COLLECTION_PREFIX

# Last path segment looks like pytest / tmp / a one-letter leftover.
_TESTY = re.compile(
    r"(pytest|tmp[_-]|_test(?:$|[-_])|^test[-_]|guardtest|faceproj"
    r"|^proj\d*$|^t$|^tmp$|^repo$)",
    re.I,
)


def looks_testy(name: str) -> bool:
    leaf = (name or "").rsplit("/", 1)[-1]
    return bool(_TESTY.search(leaf))


@dataclass
class CollRow:
    cid: str
    name: str
    docs: Optional[int]  # 0 = empty; None = unknown
    claimed_by: Optional[str] = None
    path: Optional[str] = None
    journal: bool = False
    empty: bool = False
    testy: bool = False
    orphan: bool = False  # untracked or tracked-dead
    untracked: bool = False  # absent from the registry entirely


@dataclass
class KeyRow:
    label: str
    name: str
    days_left: Optional[int] = None
    expired: bool = False
    disabled: bool = False
    orphan: bool = False


@dataclass
class GhostRow:
    name: str
    path: str


@dataclass
class SweepReport:
    collections: list[CollRow] = field(default_factory=list)
    ghosts: list[GhostRow] = field(default_factory=list)
    keys: list[KeyRow] = field(default_factory=list)
    error: str = ""

    def sweepable_empty(self) -> list[CollRow]:
        return [c for c in self.collections if c.untracked and c.empty and not c.claimed_by]

    def sweepable_test(self) -> list[CollRow]:
        return [
            c for c in self.collections
            if c.untracked and c.empty and c.testy and not c.claimed_by
        ]


def gather(
    *,
    cloud: dict[str, str],
    registry_entries: list,
    empty_of: Optional[Callable[[str], Optional[bool]]] = None,
    claimed_of: Optional[Callable[[str], Optional[str]]] = None,
    key_rows: Optional[list[KeyRow]] = None,
    ghost_check: Optional[Callable[[Any], bool]] = None,
) -> SweepReport:
    """Build a report from already-fetched cloud + registry (test seam)."""
    from pathlib import Path

    from xlii.project_resolver import project_is_alive

    by_id: dict[str, Any] = {}
    for e in registry_entries:
        cid = getattr(e, "collection_id", "") or ""
        if cid:
            by_id[cid] = e

    rows: list[CollRow] = []
    for cid, name in sorted(cloud.items(), key=lambda kv: (kv[1] or "").lower()):
        entry = by_id.get(cid)
        claimed = None
        path = None
        if entry is not None:
            path = getattr(entry, "path", None)
            disk_id = None
            if claimed_of is not None:
                disk_id = claimed_of(path or "")
            elif path:
                from xlii.cmds.project._collections import _claimed_collection

                disk_id = _claimed_collection(Path(path))
            if disk_id == cid:
                claimed = getattr(entry, "name", None)
        empty: Optional[bool] = None
        if empty_of is not None:
            empty = empty_of(cid)
        docs = 0 if empty is True else (None if empty is None else 1)
        journal = (name or "").startswith(JOURNAL_COLLECTION_PREFIX)
        orphan = entry is None or claimed is None
        untracked = entry is None
        rows.append(CollRow(
            cid=cid,
            name=name or cid,
            docs=docs,
            claimed_by=claimed,
            path=path,
            journal=journal,
            empty=bool(empty),
            testy=looks_testy(name or ""),
            orphan=orphan and not journal,
            untracked=untracked and not journal,
        ))

    ghosts: list[GhostRow] = []
    for e in registry_entries:
        alive = ghost_check(e) if ghost_check is not None else project_is_alive(e)
        if not alive:
            ghosts.append(GhostRow(
                name=getattr(e, "name", "") or "?",
                path=getattr(e, "path", "") or "",
            ))

    return SweepReport(collections=rows, ghosts=ghosts, keys=list(key_rows or []))


def live_empty_check(clients) -> Callable[[str], Optional[bool]]:
    """One cheap list_documents(limit=1) per collection."""

    def _empty(cid: str) -> Optional[bool]:
        try:
            resp = clients.xai.collections.list_documents(
                collection_id=cid, limit=1,
            )
            docs = getattr(resp, "documents", None) or []
            return len(list(docs)) == 0
        except Exception:
            return None

    return _empty


def gather_live(cfg=None) -> SweepReport:
    """Hit the management/collections APIs. Needs XAI_MANAGEMENT_API_KEY."""
    from xlii.client import Clients, MissingCredentials
    from xlii.config import GlobalConfig
    from xlii.registry import Registry

    cfg = cfg or GlobalConfig.load()
    try:
        clients = Clients.from_config(cfg)
    except MissingCredentials as e:
        return SweepReport(error=str(e))
    from xlii.cmds.project._collections import _list_cloud_collections

    try:
        cloud = _list_cloud_collections(clients)
    except Exception as e:
        return SweepReport(error=f"collections list failed: {type(e).__name__}: {e}")

    keys = _live_key_rows(cfg)
    return gather(
        cloud=cloud,
        registry_entries=list(Registry.load().entries),
        empty_of=live_empty_check(clients),
        key_rows=keys,
    )


def _live_key_rows(cfg) -> list[KeyRow]:
    from datetime import datetime, timezone

    from xlii.bootstrap import (
        BootstrapError,
        discover_team_id,
        list_api_keys,
        plan_prune,
        reconcile_local_keys,
        require_management_key,
    )

    try:
        require_management_key(cfg)
        team_id = discover_team_id(cfg)
        remote = list_api_keys(cfg.management_api_key, team_id)
    except (BootstrapError, Exception):
        return []
    now = datetime.now(timezone.utc)
    rows: list[KeyRow] = []
    seen: set[str] = set()
    for st in reconcile_local_keys(getattr(cfg, "keys", None) or [], remote, now):
        seen.add(st.name or st.label)
        expired = bool(st.days_left is not None and st.days_left < 0 and not st.parse_failed)
        rows.append(KeyRow(
            label=st.label or "?",
            name=st.name or "",
            days_left=st.days_left if not st.parse_failed else None,
            expired=expired,
            disabled=bool(st.disabled),
            orphan=not st.found,
        ))
    try:
        plan = plan_prune(cfg, team_id)
    except Exception:
        return rows
    for k in plan.candidates:
        nm = k.get("name") or ""
        if nm in seen:
            continue
        rows.append(KeyRow(
            label="(server)",
            name=nm,
            expired=False,
            disabled=bool(k.get("disabled")),
            orphan=True,
        ))
    return rows


def render_report(report: SweepReport, out) -> None:
    if report.error:
        out.print(f"[red]{report.error}[/red]")
        return
    out.print("[bold]throne sweep[/bold] [dim](inventory — nothing deleted)[/dim]")

    out.print(f"\n[bold]collections[/bold] ({len(report.collections)})")
    if not report.collections:
        out.print("  [dim](none on this team)[/dim]")
    for c in report.collections:
        flags = []
        if c.empty:
            flags.append("[yellow]empty[/yellow]")
        if c.testy:
            flags.append("[yellow]test[/yellow]")
        if c.journal:
            flags.append("journal")
        if c.claimed_by:
            flags.append(f"claimed {c.claimed_by}")
        elif c.untracked:
            flags.append("[yellow]untracked[/yellow]")
        elif c.orphan:
            flags.append("[yellow]registry-unclaimed[/yellow]")
        docs = "empty" if c.docs == 0 else ("? docs" if c.docs is None else "has docs")
        flag = ("  " + " ".join(flags)) if flags else ""
        out.print(f"  · {c.name:<32} {docs}{flag}")

    out.print(f"\n[bold]registry ghosts[/bold] ({len(report.ghosts)})")
    if not report.ghosts:
        out.print("  [dim](none)[/dim]")
    for g in report.ghosts:
        out.print(f"  · {g.name:<32} [dim]{g.path}[/dim]")

    out.print(f"\n[bold]keys[/bold] ({len(report.keys)})")
    if not report.keys:
        out.print("  [dim](none listed)[/dim]")
    for k in report.keys:
        bits = []
        if k.expired:
            bits.append("[red]EXPIRED[/red]")
        elif k.days_left is not None:
            bits.append(f"{k.days_left}d")
        if k.disabled:
            bits.append("[red]disabled[/red]")
        if k.orphan:
            bits.append("orphan")
        extra = ("  " + " ".join(bits)) if bits else ""
        out.print(f"  · {k.label:<14} {k.name or '—'}{extra}")

    n_empty = len(report.sweepable_empty())
    n_test = len(report.sweepable_test())
    out.print(
        f"\n[dim]sweepable: {n_empty} empty untracked · {n_test} empty test-named untracked · "
        f"{len(report.ghosts)} ghosts · "
        f"{sum(1 for k in report.keys if k.expired or (k.orphan and k.disabled))} dead keys[/dim]"
    )
    out.print("[dim]xlii sweep --empty|--test|--ghosts|--keys  (add --yes to apply)[/dim]")


def apply_sweep(
    report: SweepReport,
    *,
    empty: bool = False,
    test: bool = False,
    ghosts: bool = False,
    keys: bool = False,
    yes: bool = False,
    delete_collection: Optional[Callable[[str], None]] = None,
    prune_ghosts: Optional[Callable[[], int]] = None,
    prune_keys: Optional[Callable[[], int]] = None,
    confirm: Optional[Callable[[str], bool]] = None,
    out=None,
) -> int:
    """Apply selected sweeps. Returns 0 on success / nothing-to-do."""
    todo_c: list[CollRow] = []
    if empty:
        todo_c.extend(report.sweepable_empty())
    if test:
        for c in report.sweepable_test():
            if c not in todo_c:
                todo_c.append(c)
    n_g = len(report.ghosts) if ghosts else 0
    n_k = sum(1 for k in report.keys if k.expired or k.orphan) if keys else 0
    if not todo_c and not n_g and not n_k:
        if out:
            out.print("[dim]nothing selected to sweep[/dim]")
        return 0
    msg = (
        f"sweep {len(todo_c)} collection(s), {n_g} ghost row(s), {n_k} key(s)?"
    )
    if confirm is not None and not yes and not confirm(msg):
        if out:
            out.print("[dim]cancelled[/dim]")
        return 1
    rc = 0
    for c in todo_c:
        if delete_collection is None:
            continue
        try:
            delete_collection(c.cid)
            if out:
                out.print(f"  [green]✓[/green] deleted collection {c.name}")
        except Exception as e:
            rc = 1
            if out:
                out.print(f"  [red]✗[/red] {c.name}: {e}")
    if ghosts and prune_ghosts is not None:
        n = prune_ghosts()
        if out:
            out.print(f"  [green]✓[/green] forgot {n} ghost registry row(s)")
    if keys and prune_keys is not None:
        n = prune_keys()
        if out:
            out.print(f"  [green]✓[/green] pruned {n} key(s)")
    return rc
