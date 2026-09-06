"""Last-face receipt — a file the daemon can query when the tab is gone.

Not a live wire. The face (and public serve) write a small JSON note: which
desk, which grant, why it ended (alive / exit / idle / revoke / crash). Phone
Mojo reads the note. The process itself is not haunted.

Lives in the shared serve state dir (body contract #1) so daemon and serve
see the same file.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from xlii.atomicio import write_text_atomic

RECEIPT_NAME = "last_face.json"


def receipt_path(state_dir: "Path | None" = None) -> Path:
    if state_dir is None:
        from xlii.serve_spool import default_state_dir

        state_dir = default_state_dir()
    return Path(state_dir) / RECEIPT_NAME


def write_face_receipt(
    *,
    reason: str,
    project: str = "",
    path: str = "",
    grant: str = "",
    extra: str = "",
    state_dir: "Path | None" = None,
    now: Optional[float] = None,
) -> Path:
    """Overwrite the receipt. Best-effort — must never raise into the face."""
    dest = receipt_path(state_dir)
    payload = {
        "reason": (reason or "unknown").strip() or "unknown",
        "project": (project or "").strip(),
        "path": (path or "").strip(),
        "grant": (grant or "").strip(),
        "extra": (extra or "").strip(),
        "at": float(now if now is not None else time.time()),
    }
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(
            dest,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )
    except OSError:
        # Best-effort receipt; do not fail the caller on missing dirs or I/O.
        pass
    return dest


def note_project(project: Any = None, **kw: Any) -> Path:
    """Write a receipt using a live project object (land / exit)."""
    name = ""
    root = ""
    if project is not None:
        name = (getattr(project, "name", None) or "").strip()
        raw = getattr(project, "project_root", None)
        if raw is not None:
            try:
                root = str(Path(raw).expanduser())
            except (TypeError, ValueError):
                root = str(raw)
    return write_face_receipt(project=name, path=root, **kw)


def read_face_receipt(state_dir: "Path | None" = None) -> dict[str, Any]:
    """Load the receipt. Missing / corrupt → empty dict."""
    path = receipt_path(state_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def format_face_receipt(data: dict[str, Any] | None = None, *, now: Optional[float] = None) -> str:
    """Human reply for ``webcode last``."""
    row = data if data is not None else read_face_receipt()
    if not row or not row.get("reason"):
        return "[daemon] no face receipt — no desk has landed on this node"
    reason = str(row.get("reason") or "unknown")
    name = str(row.get("project") or "")
    grant = str(row.get("grant") or "")
    extra = str(row.get("extra") or "")
    when = row.get("at")
    age = ""
    try:
        ts = float(when)
        stamp = int((now if now is not None else time.time()) - ts)
        if stamp < 0:
            stamp = 0
        age = f"  {stamp}s ago"
    except (TypeError, ValueError):
        age = ""
    bits = [f"reason={reason}"]
    if name:
        bits.append(f"desk={name}")
    if grant:
        bits.append(f"grant={grant}")
    if extra:
        bits.append(extra)
    return "[daemon] last face: " + "  ".join(bits) + age
