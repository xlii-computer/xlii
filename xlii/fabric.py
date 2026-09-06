"""Fabric: the mojo's node→center transport (F3 pull, SFTP MVP).

A keyless NODE accrues persona turns locally — it holds only the capped
inference key, so its persona project is local-only and its memory never leaves
the box (the F2/F3 local-write path). The CENTER — the throne, which holds the
management key — PULLS those accrued turns down and archives them to the
persona's shared Collection (the F3 center-half drain, ``end_of_turn_sync`` /
``sync_project``). Then every surface recalls what was said to any body: one
persona, many named surfaces, memory converging on the throne.

This module is the pure transport core: given a connected remote-fs handle (the
shipped ``xlii.remotefs`` sftp/ftp provider) and a local turns dir, it mirrors a
node's turn files down — idempotently, with node-namespaced filenames so a
node's ``-NNNN`` sequence can't collide with the throne's own and a re-pull is a
no-op (provenance lives in the name). The CLI face (``xlii/cmds/fabric.py``)
wires config → connection → this → the drain.

The throne also pulls on a timer (``maybe_auto_pull``) so you don't have to
remember the CLI. Pulled turns keep the body in the filename; recall reads it
back (inline history ``[via node]``, search-hit ``(via node)``).

Deferred to the XMPP-native phase (F5): pull over the OMEMO link instead of SSH
(NAT-friendly, no remote-fs dependency), per-node watermarks, and the poison /
quota / provenance-trust guards.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, runtime_checkable

# A node's persona turns live under the remote CHAT_STATE_DIR (persona.py:25,
# ``~/.xlii/chat``). Over sftp, paths are relative to the login home, so the
# default is home-relative (no leading slash).
DEFAULT_REMOTE_CHAT_STATE = ".xlii/chat"

# ``20260429T123456Z-0042.md`` — transcript.turn_filename's shape. Capture the
# sortable UTC timestamp stem + the node-local sequence so ingest can re-key the
# sequence under the node name while preserving chronological sort order.
_TURN_RE = re.compile(r"^(\d{8}T\d{6}Z)-(\d+)\.md$")

# After ingest: ``20260429T123456Z-node1-0042.md``. The node group is greedy so
# a hyphenated body (``usb-stick``) still yields the trailing ``-NNNN`` as the
# sequence. Local (unpulled) writes stay on ``_TURN_RE`` and have no body tag.
_INGESTED_RE = re.compile(
    r"^(\d{8}T\d{6}Z)-([A-Za-z0-9][A-Za-z0-9_.-]*)-(\d+)\.md$"
)

# How often the throne gathers other bodies' diary without being asked.
# 0 disables. Manual ``xlii fabric pull`` always runs and refreshes the stamp.
DEFAULT_PULL_INTERVAL_S = 900


@runtime_checkable
class ReadableConn(Protocol):
    """The slice of ``xlii.remotefs.RemoteFsConnection`` the pull needs."""

    def listdir(self, path: str = "") -> list[tuple[str, bool, Optional[int]]]:
        raise NotImplementedError

    def read(self, path: str) -> bytes:
        raise NotImplementedError


@dataclass
class PullResult:
    """What one node's pull moved. ``pulled`` = new turn files written to the
    throne; ``skipped`` = already present (idempotent); ``errors`` = per-file
    transfer failures (never fatal — a partial pull still lands what it got)."""

    node: str
    pulled: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        s = f"{self.node}: {self.pulled} pulled, {self.skipped} skipped"
        if self.errors:
            s += f", {len(self.errors)} errors"
        return s


def source_from_name(filename: str) -> Optional[str]:
    """Body that wrote this turn, from the throne-local filename.

    Pulled turns are ``TS-<node>-NNNN.md`` (see :func:`ingest_name`). Local
    writes stay ``TS-NNNN.md`` and return ``None`` (this box). A name that
    isn't either shape is untagged rather than guessed.
    """
    name = Path(filename).name
    if _TURN_RE.match(name):
        return None
    m = _INGESTED_RE.match(name)
    return m.group(2) if m else None


def via_tag(source: Optional[str]) -> str:
    """Inline-history prefix when a turn arrived from another body."""
    node = (source or "").strip()
    return f"[via {node}]" if node else ""


def recall_label(source_path: str) -> str:
    """Search-hit header: path unchanged, body appended when the name is tagged."""
    raw = source_path or ""
    node = source_from_name(Path(raw).name)
    if node:
        return f"{raw}  (via {node})"
    return raw


def ingest_name(remote_name: str, node: str) -> str:
    """The throne-local filename for a node's turn file.

    Keep the sortable timestamp stem so pulled turns interleave chronologically
    with the throne's own; re-key the node-local ``-NNNN`` sequence under the
    node name so it can't collide with the throne's sequence; and stay a pure
    function of (name, node) so a re-pull maps to the same target and is skipped
    (idempotent). Provenance is in the name.

    ``20260429T123456Z-0042.md`` + ``node1`` → ``20260429T123456Z-node1-0042.md``.
    A name that doesn't match the turn pattern is defensively node-prefixed so it
    still lands uniquely rather than being dropped or colliding."""
    m = _TURN_RE.match(remote_name)
    if m:
        return f"{m.group(1)}-{node}-{m.group(2)}.md"
    return f"{node}-{remote_name}"


def remote_turns_dir(chat_state: str, persona: str) -> str:
    """Login-home-relative path to a persona's ``turns/`` on a node."""
    base = (chat_state or DEFAULT_REMOTE_CHAT_STATE).strip("/")
    return f"{base}/{persona}/turns"


def remote_media_dir(chat_state: str, persona: str) -> str:
    """Login-home-relative path to a persona's ``media/`` on a node — the
    media inbox the daemon persists inbound files into (sibling of turns/)."""
    base = (chat_state or DEFAULT_REMOTE_CHAT_STATE).strip("/")
    return f"{base}/{persona}/media"


def pull_node_turns(
    conn: ReadableConn,
    remote_dir: str,
    local_dir: Path,
    node: str,
    *,
    dry_run: bool = False,
) -> PullResult:
    """Mirror one node's persona turn files down into the throne's local turns
    dir. Idempotent: a turn already present (by its node-namespaced name) is
    skipped, so repeated pulls fetch only what's new. Per-file transfer failures
    are collected, never raised — a partial pull still lands what it got. A node
    with no memory for this persona yet (missing remote dir) is an empty pull,
    not an error."""
    result = PullResult(node=node)
    try:
        entries = conn.listdir(remote_dir)
    except (FileNotFoundError, OSError) as e:
        # Missing dir = node hasn't talked as this persona yet → empty pull.
        if isinstance(e, FileNotFoundError):
            return result
        result.errors.append(f"listdir {remote_dir}: {type(e).__name__}: {e}")
        return result

    for name, is_dir, _size in entries:
        if is_dir or not name.endswith(".md"):
            continue
        target_name = ingest_name(name, node)
        if Path(target_name).name != target_name:
            result.errors.append(f"{name}: unsafe ingest name {target_name!r}")
            continue
        target = local_dir / target_name
        if target.exists():
            result.skipped += 1
            continue
        if dry_run:
            result.pulled += 1
            continue
        try:
            data = conn.read(f"{remote_dir}/{name}")
        except OSError as e:
            result.errors.append(f"{name}: {type(e).__name__}: {e}")
            continue
        from xlii.atomicio import write_bytes_atomic

        try:
            write_bytes_atomic(target, data)
        except OSError as e:
            result.errors.append(f"{name}: {type(e).__name__}: {e}")
            continue
        result.pulled += 1
    return result


def pull_node_media(
    conn: ReadableConn,
    remote_dir: str,
    local_dir: Path,
    node: str,
    *,
    dry_run: bool = False,
) -> PullResult:
    """Mirror one node's persona MEDIA store (the media inbox) to the throne.

    Same discipline as :func:`pull_node_turns` — idempotent name-based skip,
    per-file errors collected never raised, missing dir = empty pull — minus
    the ``.md`` filter (media is binary) and with a simpler ingest name:
    ``<node>-<name>``. Media filenames already carry the persist-time UTC
    stamp, and the plain prefix keeps a file paired with its metadata sidecar
    (``<node>-X`` ↔ ``<node>-X.meta.json``) — pairing IS the contract here,
    so never re-key file and sidecar differently."""
    result = PullResult(node=node)
    try:
        entries = conn.listdir(remote_dir)
    except (FileNotFoundError, OSError) as e:
        if isinstance(e, FileNotFoundError):
            return result
        result.errors.append(f"listdir {remote_dir}: {type(e).__name__}: {e}")
        return result

    for name, is_dir, _size in entries:
        if is_dir:
            continue
        target_name = f"{node}-{name}"
        if Path(target_name).name != target_name:
            result.errors.append(f"{name}: unsafe ingest name {target_name!r}")
            continue
        target = local_dir / target_name
        if target.exists():
            result.skipped += 1
            continue
        if dry_run:
            result.pulled += 1
            continue
        try:
            data = conn.read(f"{remote_dir}/{name}")
        except OSError as e:
            result.errors.append(f"{name}: {type(e).__name__}: {e}")
            continue
        from xlii.atomicio import write_bytes_atomic

        try:
            local_dir.mkdir(parents=True, exist_ok=True)
            write_bytes_atomic(target, data)
        except OSError as e:
            result.errors.append(f"{name}: {type(e).__name__}: {e}")
            continue
        result.pulled += 1
    return result


# --------------------------------------------------------------------------- #
#  roster pull + throne auto-pull
# --------------------------------------------------------------------------- #


@dataclass
class RosterPull:
    """One pass over the fabric roster (turns + media + Collection drain)."""

    nodes: list[PullResult] = field(default_factory=list)
    media: list[PullResult] = field(default_factory=list)
    drains: list[tuple[str, str, bool]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def pulled_turns(self) -> int:
        return sum(r.pulled for r in self.nodes)

    def ok(self) -> bool:
        if self.errors:
            return False
        if any(r.errors for r in self.nodes) or any(r.errors for r in self.media):
            return False
        return all(ok for _name, _msg, ok in self.drains)


def default_pull_state_path() -> Path:
    """Watermark file: last auto/manual pull. ``XLII_STATE_DIR`` for tests."""
    env = (os.environ.get("XLII_STATE_DIR") or "").strip()
    if env:
        return Path(env) / "fabric-last-pull.json"
    from xlii.project_paths import xlii_user_root

    return xlii_user_root() / "fabric-last-pull.json"


def last_pull_epoch(path: Optional[Path] = None) -> float:
    p = path or default_pull_state_path()
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return 0.0
    try:
        return float(data.get("epoch") or 0)
    except (TypeError, ValueError):
        return 0.0


def last_sync_stamp(path: Optional[Path] = None) -> Optional[float]:
    """Newest fabric pull/push watermark as unix epoch, or ``None`` if never.

    Reads the marker ``maybe_auto_pull`` / ``stamp_pull`` already write.
    Bearings treat ``None`` as ``last pull never`` — never probed live.
    """
    epoch = last_pull_epoch(path)
    if epoch <= 0:
        return None
    return epoch


def pull_due(
    *,
    interval_s: int,
    now: Optional[float] = None,
    path: Optional[Path] = None,
) -> bool:
    """True when auto-pull should run. ``interval_s <= 0`` is off."""
    if interval_s <= 0:
        return False
    last = last_pull_epoch(path)
    return ((now if now is not None else time.time()) - last) >= interval_s


def stamp_pull(*, now: Optional[float] = None, path: Optional[Path] = None) -> None:
    p = path or default_pull_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    from xlii.atomicio import write_text_atomic

    payload = json.dumps({"epoch": now if now is not None else time.time()})
    write_text_atomic(p, payload)


def pull_interval_s(cfg: Any) -> int:
    raw = getattr(cfg, "fabric_pull_interval_s", DEFAULT_PULL_INTERVAL_S)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PULL_INTERVAL_S


def pull_roster(
    nodes: dict,
    *,
    persona_override: str = "",
    dry_run: bool = False,
    connect: Callable[[str], ReadableConn],
    resolve_persona: Callable[[str], Any],
    drain: Optional[Callable[[Any], tuple[str, bool]]] = None,
    require_remote: Optional[Callable[[str], Optional[str]]] = None,
    default_persona: str = "",
    only: Optional[str] = None,
) -> RosterPull:
    """Mirror every (or one) roster node's turns + media, then drain each persona.

    I/O is injected so the CLI, auto-pull, and tests share one loop. Per-node
    failures are collected; a dead box does not abort the rest.
    """
    from xlii.persona import DEFAULT_PERSONA_ID, journal_knob_id

    journal = journal_knob_id(default_persona) or DEFAULT_PERSONA_ID
    out = RosterPull()
    roster = dict(nodes or {})
    if only:
        if only not in roster:
            out.errors.append(f"no such node: {only}")
            return out
        roster = {only: roster[only]}
    if not roster:
        out.errors.append("no nodes configured")
        return out

    touched: dict[str, Any] = {}
    for node_name in sorted(roster):
        spec = roster[node_name] or {}
        if not isinstance(spec, dict):
            out.errors.append(f"{node_name}: malformed roster entry")
            continue
        raw_persona = (persona_override or spec.get("persona") or journal).strip()
        persona_name = journal_knob_id(raw_persona) or journal
        persona = resolve_persona(persona_name)
        if persona is None:
            out.errors.append(f"{node_name}: no such persona {persona_name!r}")
            continue
        remote_name = spec.get("remote") or ""
        if require_remote is not None:
            err = require_remote(remote_name)
            if err:
                out.errors.append(f"{node_name}: {err}")
                continue
        try:
            conn = connect(remote_name)
        except Exception as e:
            out.errors.append(f"{node_name}: connect failed ({type(e).__name__}: {e})")
            continue
        on_disk = getattr(persona, "name", None) or persona_name
        rdir = remote_turns_dir(spec.get("chat_state", ""), on_disk)
        res = pull_node_turns(conn, rdir, persona.turns_dir, node_name, dry_run=dry_run)
        out.nodes.append(res)
        mdir = remote_media_dir(spec.get("chat_state", ""), on_disk)
        mres = pull_node_media(
            conn, mdir, persona.project_root / "media", node_name, dry_run=dry_run
        )
        out.media.append(mres)
        touched.setdefault(persona_name, persona)

    if drain is not None and not dry_run:
        for persona_name, persona in touched.items():
            msg, ok = drain(persona)
            out.drains.append((persona_name, msg, ok))
    return out


def drain_persona(persona: Any, cfg: Any, console: Any = None) -> tuple[str, bool]:
    """Archive a persona's turns to its Collection (mgmt-key write).

    Keyless boxes ingest locally and skip the archive — same gate as the CLI.
    Lazy-imports session_boot so fabric can run at chat boot without a
    circular import at module load.
    """
    from xlii.client import MissingCredentials
    from xlii.pool import ClientPool
    from xlii.session_boot import ensure_persona_project
    from xlii.sync import sync_project

    no_mgmt = not (getattr(cfg, "management_api_key", None) or "")
    try:
        pool = ClientPool.from_config(cfg, require_management=False)
    except MissingCredentials as e:
        return f"(no credentials: {e})", False
    if no_mgmt:
        return (
            "ingested locally; NOT archived (run on the throne — needs the "
            "management key)"
        ), True
    project = ensure_persona_project(persona, pool, console=console, local_only=False)
    if project is None:
        return "(could not open persona project)", False
    if project.local_only:
        return (
            "ingested locally; NOT archived (run on the throne — needs the "
            "management key)"
        ), True
    try:
        stats = sync_project(pool.primary(), project, cfg)
    except Exception as e:
        return f"archive failed ({type(e).__name__}: {e})", False
    if stats.failed > 0:
        return f"archive partial failure ({stats.summary()})", False
    return f"archived → Collection ({stats.summary()})", True


def _default_connect(remote_name: str) -> ReadableConn:
    from xlii.remotefs import manager

    return manager.get(remote_name)


def _default_require_remote(remote_name: str) -> Optional[str]:
    from xlii.remotefs import manager

    spec = manager.spec(remote_name)
    if spec is None:
        return (
            f"no such remote connection: {remote_name!r} — add one with "
            f"`xlii remote add` (have: {', '.join(manager.names()) or 'none'})"
        )
    if (spec.get("protocol") or "").lower() != "sftp":
        return (
            f"remote {remote_name!r} must use sftp (have: {spec.get('protocol', '?')!r}) "
            "— fabric pull is SFTP-only"
        )
    return None


def _default_resolve_persona(name: str) -> Any:
    from xlii.persona import Persona, canonicalize_persona_id, is_valid_name

    n = canonicalize_persona_id(name)
    if not is_valid_name(n):
        return None
    p = Persona(n)
    return p if p.exists() else None


def maybe_auto_pull(
    cfg: Any,
    *,
    now: Optional[float] = None,
    state_path: Optional[Path] = None,
    console: Any = None,
    connect: Optional[Callable[[str], ReadableConn]] = None,
    resolve_persona: Optional[Callable[[str], Any]] = None,
    drain: Optional[Callable[[Any], tuple[str, bool]]] = None,
    require_remote: Optional[Callable[[str], Optional[str]]] = None,
    default_persona: str = "",
) -> Optional[RosterPull]:
    """Pull the roster when the interval has elapsed. ``None`` = skipped.

    Stamps the watermark before the wire so two chat/mojo turns racing do not
    both open SFTP. A failed pass still counts as an attempt (retry next interval;
    ``xlii fabric pull`` forces now). Never raises — a dead node must not block
    talk.
    """
    nodes = getattr(cfg, "fabric_nodes", None) or {}
    if not nodes:
        return None
    path = state_path or default_pull_state_path()
    interval = pull_interval_s(cfg)
    if not pull_due(interval_s=interval, now=now, path=path):
        return None
    try:
        stamp_pull(now=now, path=path)
    except OSError:
        return None

    def _drain(persona: Any, _cfg: Any = cfg, _con: Any = console) -> tuple[str, bool]:
        if drain is not None:
            return drain(persona)
        return drain_persona(persona, _cfg, _con)

    try:
        return pull_roster(
            nodes,
            connect=connect or _default_connect,
            resolve_persona=resolve_persona or _default_resolve_persona,
            drain=_drain,
            require_remote=require_remote or _default_require_remote,
            default_persona=default_persona,
        )
    except Exception as e:
        batch = RosterPull()
        batch.errors.append(f"auto-pull failed ({type(e).__name__}: {e})")
        return batch
