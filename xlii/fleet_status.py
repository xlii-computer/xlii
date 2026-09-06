"""The /status fleet section — the downstream stack, read from the /remote roster.

`status` = everything that can break between you and the work, up AND down the
chain: ▲ vendor (deferred) · ● self (the session) · ▼ fleet (this module). The
fleet IS the `/remote` roster — a downstream box is reachable *because* you
added it as a connection, so the roster is already the honest map; no separate
registry. Two layers over it:

- REACH  — every configured connection (protocol-agnostic: whatever the manager
  reports renders; s3 rows appear automatically when its handler lands).
- ROLE   — node (referenced by ``cfg.fabric_nodes`` — a body running xlii) ·
  machine (a plain reachable box) · storage (bytes in/out, not a body).

The cost asymmetry that shapes the API: **the default view never opens a
socket** (``fleet_rows`` reads specs + the probe cache only); reachability
requires a real connect per host, which is slow and can hang — so probing is
always opt-in (``/status --probe``), concurrent, and hard-capped per host.

Presence (is xlii live on that node?) has NO zero-socket source on a REPL seat
today — the daemon broadcasts it on the XMPP bus, which only bus holders see —
so nodes render presence ``unknown``, never a claimed ``offline``. A live
column arrives when the fabric bus work lands a presence spool.

Kernel module: pure data + pure string-building (the git_status.py split); the
face prints. NOT xlii/status.py — that is the frame/status-bar kernel.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as _FuturesTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

# Role inference by protocol (menu of installed handlers; unknown/future
# protocols default to machine — never a crash, never a promise).
STORAGE_PROTOCOLS = frozenset({"webdav", "s3"})

PROBE_TIMEOUT_S = 5.0
PROBE_CACHE_NAME = "fleet-probe.json"
STALE_AFTER_S = 3600.0

_ROLE_GLYPH = {"node": "●", "machine": "○", "storage": "▪"}


@dataclass
class FleetRow:
    name: str
    protocol: str
    scheme: str
    role: str                       # node | machine | storage
    fabric_node: Optional[str]      # fabric_nodes roster key, when role == node
    presence: str                   # "unknown" for nodes, "—" otherwise (v1)
    reach: Optional[str] = None     # "ok" | "fail" | "timeout" | None (never probed)
    reach_ts: Optional[float] = None
    reach_error: Optional[str] = None


@dataclass
class ProbeResult:
    ok: bool
    error: Optional[str] = None
    latency_ms: Optional[int] = None


def classify_role(name: str, spec: dict, fabric_nodes: Optional[dict]) -> tuple[str, Optional[str]]:
    """(role, fabric_node_key). A connection is a NODE iff a fabric_nodes entry
    references it by remote name (the only honest zero-socket node signal —
    presence itself is unreadable here). Else protocol infers storage/machine."""
    for key, entry in (fabric_nodes or {}).items():
        if isinstance(entry, dict) and entry.get("remote") == name:
            return "node", str(key)
    proto = str(spec.get("protocol") or "ftp").lower()
    if proto in STORAGE_PROTOCOLS:
        return "storage", None
    return "machine", None


def fleet_rows(*, manager: Any = None, fabric_nodes: Optional[dict] = None,
               cache: Optional[dict] = None) -> list[FleetRow]:
    """The roster as rows — ZERO-SOCKET by contract: reads ``manager.names()``
    + ``manager.spec()`` (config dicts) and the probe cache only. Never
    ``manager.get()`` (that connects)."""
    if manager is None:
        from xlii.remotefs import manager as _mgr
        manager = _mgr
    from xlii.remotefs import scheme_for_protocol

    probes = (cache or {}).get("probes", {})
    rows: list[FleetRow] = []
    for name in manager.names():
        spec = manager.spec(name)
        if not isinstance(spec, dict):
            continue
        proto = str(spec.get("protocol") or "ftp").lower()
        role, fkey = classify_role(name, spec, fabric_nodes)
        row = FleetRow(
            name=name, protocol=proto, scheme=scheme_for_protocol(proto),
            role=role, fabric_node=fkey,
            presence="unknown" if role == "node" else "—",
        )
        p = probes.get(name)
        if isinstance(p, dict) and "ok" in p:
            row.reach = "ok" if p.get("ok") else ("timeout" if "timeout" in str(p.get("error") or "") else "fail")
            row.reach_ts = p.get("ts")
            row.reach_error = p.get("error")
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
#  the probe — opt-in, concurrent, hard-capped; never touches the manager cache
# --------------------------------------------------------------------------- #

def _default_connector(name: str, timeout: float) -> None:
    """One throwaway connect+listdir+close proving the host actually answers.

    ``connect()`` alone is not proof on webdav (its session does no I/O until
    the first request), so ``listdir("")`` — one PROPFIND / one post-login RTT —
    is the uniform cheap verb. A spec-copy timeout override wins in the
    constructor, so every handler honors the cap at setup. Deliberately NOT
    ``manager.get()``: the probe must not pollute the socket cache or hold
    connections open."""
    from xlii.remotefs import RemoteFsConnection, _vault_secret
    from xlii.remotefs import manager as _mgr

    spec = _mgr.spec(name)
    if not isinstance(spec, dict):
        raise RuntimeError("no such remote")
    secret = _vault_secret(spec.get("vault_ref"))
    conn = RemoteFsConnection(name, {**spec, "timeout": timeout}, secret=secret)
    try:
        conn.connect()
        conn.listdir("")
    finally:
        conn.close()


def probe_fleet(names: list[str], *, connector: Optional[Callable[[str, float], None]] = None,
                timeout: float = PROBE_TIMEOUT_S, max_workers: int = 8) -> dict[str, ProbeResult]:
    """Connect-check every name concurrently; per-host ``timeout`` cap, plus an
    overall deadline (timeout + 2s grace) so a hanging protocol stack (smb has
    no per-op deadline) can't wedge the REPL — stragglers report ``timeout``
    and their worker threads are abandoned to die with the socket."""
    if not names:
        return {}
    connector = connector or _default_connector
    results: dict[str, ProbeResult] = {}

    def _one(n: str) -> ProbeResult:
        t0 = time.monotonic()
        try:
            connector(n, timeout)
        except Exception as e:  # noqa: BLE001 — a probe failure is a RESULT
            return ProbeResult(ok=False, error=f"{type(e).__name__}: {e}"[:200])
        return ProbeResult(ok=True, latency_ms=int((time.monotonic() - t0) * 1000))

    with ThreadPoolExecutor(max_workers=min(max_workers, len(names))) as ex:
        futs = {ex.submit(_one, n): n for n in names}
        try:
            for fut in as_completed(futs, timeout=timeout + 2.0):
                results[futs[fut]] = fut.result()
        except _FuturesTimeout:
            # Slow nodes stay absent from results; the probe returns whatever came back in time.
            pass
    for n in names:
        results.setdefault(n, ProbeResult(ok=False, error=f"timeout >{timeout:.0f}s"))
    return results


# --------------------------------------------------------------------------- #
#  the cache — global (remotes are global config), atomic, corrupt-tolerant
# --------------------------------------------------------------------------- #

def _cache_path() -> Path:
    from xlii.journal_daemon import runtime_dir
    return runtime_dir() / PROBE_CACHE_NAME


def load_probe_cache() -> dict:
    """Tolerant load: missing/corrupt → {} (the sessions-mirror discipline)."""
    try:
        data = json.loads(_cache_path().read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_probe_cache(results: dict[str, ProbeResult], roster_names: list[str]) -> None:
    """Merge new results over the cache; prune names no longer in the roster."""
    from xlii.atomicio import write_text_atomic

    cache = load_probe_cache()
    probes = cache.get("probes")
    if not isinstance(probes, dict):
        probes = {}
    now = time.time()
    for name, r in results.items():
        probes[name] = {"ok": r.ok, "ts": now, "error": r.error, "latency_ms": r.latency_ms}
    keep = set(roster_names)
    cache["probes"] = {n: p for n, p in probes.items() if n in keep}
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, json.dumps(cache, indent=1))
    except OSError:
        pass  # a cache write failure must never sink /status


# --------------------------------------------------------------------------- #
#  rendering — pure string-building; the face prints
# --------------------------------------------------------------------------- #

def format_age(ts: Optional[float], now: Optional[float] = None) -> str:
    if ts is None:
        return ""
    now = time.time() if now is None else now
    secs = max(0, int(now - ts))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def format_fleet_lines(rows: list[FleetRow], *, now: Optional[float] = None) -> list[str]:
    """The ▼ fleet section as rich-markup lines. Dry, factual, columnar."""
    now = time.time() if now is None else now
    nodes = sum(1 for r in rows if r.role == "node")
    head = f"[bold]▼ fleet[/bold]    {len(rows)} remote(s)"
    if nodes:
        head += f" · {nodes} node(s)"
    lines = [head]
    if not rows:
        return lines
    width = max(len(r.name) for r in rows)
    for r in rows:
        glyph = _ROLE_GLYPH.get(r.role, "○")
        presence = r.presence if r.role == "node" else "—"
        if r.reach is None:
            reach = "[dim]— (never probed)[/dim]"
        else:
            age = format_age(r.reach_ts, now)
            stale = (r.reach_ts is not None and (now - r.reach_ts) > STALE_AFTER_S)
            body = f"{r.reach} ({age})" if age else r.reach
            reach = f"[dim]{body} stale[/dim]" if stale else body
        label = f"{r.fabric_node} · " if (r.fabric_node and r.fabric_node != r.name) else ""
        lines.append(
            f"   {glyph} [cyan]{r.name:<{width}}[/cyan]  {r.protocol:<6} "
            f"{r.role:<8} [dim]{label}presence {presence}[/dim]  reach: {reach}"
        )
    return lines
