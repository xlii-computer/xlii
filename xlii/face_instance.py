"""One face per environment — lock, liveness, resume-or-replace.

Product law: a single desk (window + ``serve --face``) on this machine/VM/USB.
Project switch is in-session ("change clothes"), not a second process.
Operational hygiene: upgrades and crashes must not leave ghost faces.

User-facing word is **resume** (continue the live desk). Internal/env still
accepts ``attach`` as a synonym. Not a security boundary — same-user processes
can ignore the lock.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional


_LOCK_DIR_NAME = "locks"
_LOCK_FILE_NAME = "face.json"
_SCHEMA = 1


@dataclass
class FaceRecord:
    """On-disk record of the live face stack (serve --face is the owner pid)."""

    pid: int
    port: int
    token: str
    host: str = "127.0.0.1"
    started_at: float = 0.0
    schema: int = _SCHEMA

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/?token={self.token}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Optional["FaceRecord"]:
        try:
            return cls(
                pid=int(data["pid"]),
                port=int(data["port"]),
                token=str(data["token"]),
                host=str(data.get("host") or "127.0.0.1"),
                started_at=float(data.get("started_at") or 0.0),
                schema=int(data.get("schema") or _SCHEMA),
            )
        except (KeyError, TypeError, ValueError):
            return None


def locks_dir() -> Path:
    override = (os.environ.get("XLII_FACE_LOCK_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    from xlii.config import GLOBAL_CONFIG_DIR
    from xlii.project_paths import xlii_user_root
    # Prefer ~/.xlii/locks (user runtime); fall back next to config if needed.
    home_xlii = xlii_user_root() / _LOCK_DIR_NAME
    if home_xlii.parent.is_dir() or not GLOBAL_CONFIG_DIR:
        return home_xlii
    return Path(GLOBAL_CONFIG_DIR) / _LOCK_DIR_NAME


def lock_path() -> Path:
    return locks_dir() / _LOCK_FILE_NAME


def read_record() -> Optional[FaceRecord]:
    path = lock_path()
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return FaceRecord.from_dict(data)


def write_record(rec: FaceRecord) -> None:
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    text = json.dumps(rec.to_dict(), indent=2) + "\n"
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def clear_record(*, only_if_pid: Optional[int] = None) -> bool:
    """Remove the lock file. If *only_if_pid* is set, only when it matches."""
    path = lock_path()
    try:
        if not path.is_file():
            return False
        if only_if_pid is not None:
            rec = read_record()
            if rec is None or rec.pid != only_if_pid:
                return False
        path.unlink()
        return True
    except OSError:
        return False


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, not ours to signal
    except OSError:
        return False
    return True


def port_open(host: str, port: int, *, timeout: float = 0.8) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_face_alive(rec: FaceRecord, *, timeout: float = 1.5) -> bool:
    """Best-effort: the face HTTP port answers (token not strictly required for GET /)."""
    if not port_open(rec.host, rec.port, timeout=min(timeout, 0.8)):
        return False
    url = f"http://{rec.host}:{rec.port}/"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= int(getattr(resp, "status", 200) or 200) < 500
    except Exception:  # noqa: BLE001
        # Port open is enough — assets may redirect or require WS for full UI.
        return port_open(rec.host, rec.port, timeout=0.5)


def is_live(rec: Optional[FaceRecord]) -> bool:
    if rec is None:
        return False
    if not pid_alive(rec.pid):
        return False
    return http_face_alive(rec) or port_open(rec.host, rec.port)


def discover_live() -> Optional[FaceRecord]:
    """Return a live face record, clearing stale locks."""
    rec = read_record()
    if rec is None:
        return None
    if is_live(rec):
        return rec
    # Stale (dead pid or dead port) — reaping ghosts.
    clear_record()
    return None


def _term_pid(pid: int, *, timeout: float = 4.0) -> None:
    if not pid_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        # Best-effort kill; process may already be gone or unsignalable.
        pass


def _desktop_pids() -> list[int]:
    """PIDs of xlii-desktop processes (best-effort; Linux /proc)."""
    out: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return out
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            )
        except OSError:
            continue
        if "xlii-desktop" in cmd:
            out.append(int(entry.name))
    return out


def stop_face(rec: Optional[FaceRecord] = None, *, also_desktop: bool = True) -> None:
    """Gracefully stop the face serve pid and, by default, any xlii-desktop."""
    rec = rec or read_record()
    if rec is not None:
        _term_pid(rec.pid)
    if also_desktop:
        for pid in _desktop_pids():
            if rec is not None and pid == rec.pid:
                continue
            _term_pid(pid)
    clear_record()


def claim(*, port: int, token: str, host: str = "127.0.0.1") -> FaceRecord:
    """Write the lock for *this* process (the live ``serve --face``)."""
    rec = FaceRecord(
        pid=os.getpid(),
        port=int(port),
        token=str(token),
        host=host or "127.0.0.1",
        started_at=time.time(),
    )
    write_record(rec)
    return rec


def release() -> None:
    """Drop the lock if we own it."""
    clear_record(only_if_pid=os.getpid())


def resolve_policy(
    *,
    prefer: Optional[str] = None,
    interactive: Optional[bool] = None,
) -> str:
    """Return ``resume`` | ``replace`` | ``cancel`` | ``ok`` (no live face).

    *prefer*: ``resume`` / ``replace`` / ``cancel`` from CLI flags
    (``attach`` is accepted as a synonym for ``resume``).
    Env ``XLII_FACE_INSTANCE`` overrides when *prefer* is None.
    Non-interactive default when a face is live: **resume** (safer than replace).
    """
    live = discover_live()
    if live is None:
        return "ok"

    choice = (prefer or "").strip().lower()
    if not choice:
        choice = (os.environ.get("XLII_FACE_INSTANCE") or "").strip().lower()

    if choice in ("resume", "attach", "a"):
        return "resume"
    if choice in ("replace", "r", "new"):
        return "replace"
    if choice in ("cancel", "c", "abort"):
        return "cancel"

    if interactive is None:
        interactive = False
    if not interactive:
        return "resume"

    age = ""
    if live.started_at:
        mins = max(0, int((time.time() - live.started_at) / 60))
        age = f" · started ~{mins}m ago" if mins else " · just started"
    print(
        f"A face is already running (pid {live.pid} · port {live.port}{age}).\n"
        f"  [r] resume  — continue that session\n"
        f"  [n] new     — stop it and start fresh\n"
        f"  [c] cancel",
        file=sys.stderr,
    )
    try:
        raw = input("face [r/n/c]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return "cancel"
    # resume is default on bare Enter; "a" kept for muscle memory
    if raw in ("r", "resume", "a", "attach", ""):
        return "resume"
    if raw in ("n", "new", "replace"):
        return "replace"
    return "cancel"


def prepare_launch(
    *,
    prefer: Optional[str] = None,
    interactive: Optional[bool] = None,
) -> tuple[str, Optional[FaceRecord]]:
    """Gate a face launch. Returns (action, record).

    action:
      - ``ok``: no live face (or cleared); caller may start
      - ``resume``: live face; caller should open record.url and not spawn
      - ``cancel``: user aborted
    """
    live = discover_live()
    if live is None:
        return "ok", None
    policy = resolve_policy(prefer=prefer, interactive=interactive)
    if policy == "resume":
        return "resume", live
    if policy == "replace":
        stop_face(live, also_desktop=True)
        return "ok", None
    if policy == "ok":
        return "ok", None
    return "cancel", live


def prepare_serve(
    *,
    replace: bool = False,
) -> tuple[bool, str]:
    """Called by ``serve --face`` before claiming the lock.

    Returns (may_start, message). If another live face exists and *replace*
    is false, refuses. If replace, stops the other first.
    """
    live = discover_live()
    if live is None:
        return True, ""
    if live.pid == os.getpid():
        return True, ""
    env_rep = (os.environ.get("XLII_FACE_INSTANCE") or "").strip().lower() in (
        "replace", "r", "new",
    )
    if replace or env_rep:
        stop_face(live, also_desktop=True)
        return True, f"replaced previous face (was pid {live.pid})"
    return (
        False,
        f"face already running (pid {live.pid} · port {live.port}). "
        "Resume via `xlii scratch --tauri` / `xlii code --tauri`, or "
        "start fresh with --replace / XLII_FACE_INSTANCE=replace.",
    )
