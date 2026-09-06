"""The LOCAL delivery channel — the desktop/inline mouth (tui-media-delivery P0).

Media-out shipped for the daemon (XEP-0363 → the phone renders inline); the
local surfaces were mouths that couldn't deliver — "send it as an image"
generated the file and showed it nowhere (the elephant bug). This module makes
every local surface a real mouth by the SAME seam the phone uses:

- ``grant_local_outbox`` gives the session a temp ``outbox_dir`` → the shipped
  ``apply_outbox_gate`` advertises ``send_file`` + ``generate_image`` exactly as
  it does for the daemon (phone parity through one gate, zero new plumbing).
- ``drain_outbox`` runs after each turn: images render through the same
  ``maybe_preview`` cascade ``/imagine`` uses (renderable sink in the TUI,
  chafa/sixel/path-line inline); other files print a delivery line. Drained
  files MOVE to ``delivered/`` (never unlink — textual-image widgets mount
  lazily on the main thread; a vanished path would race), so a second drain is
  a no-op and nothing leaks into the next turn.
- ``release_local_outbox`` tears the dir down at surface exit.

The grant is LOCAL-ONLY by marker: a caller-granted dir (``xlii ask --outbox``,
the daemon's) is never touched — the daemon drains its own outbox its own way.
Kernel-leaf: stdlib + terminal_image only.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_DELIVERED = "delivered"


def grant_local_outbox(session: Any) -> "Path | None":
    """Grant the session a LOCAL delivery dir; return it ONLY when this call
    did the granting (None when one already exists — a caller-granted
    ``--outbox`` dir, or an enclosing surface's grant). Callers release only
    what they granted, so `/tui` from the inline REPL can't tear down the
    inline loop's channel on exit."""
    if getattr(session, "outbox_dir", None) is not None:
        return None
    path = Path(tempfile.mkdtemp(prefix="xlii-outbox-"))
    session.outbox_dir = path
    session._local_outbox = True
    return path


def drain_outbox(session: Any, console: Any, *, backend: str = "auto") -> int:
    """Deliver everything the turn queued: render images, announce files, move
    all of it to ``delivered/``. Locally-granted outboxes only; never raises
    (a delivery hiccup must not sink the turn's reply)."""
    if not getattr(session, "_local_outbox", False):
        return 0
    outbox = getattr(session, "outbox_dir", None)
    if outbox is None:
        return 0
    outbox = Path(outbox)
    try:
        files = sorted(p for p in outbox.iterdir() if p.is_file())
    except OSError:
        return 0
    if not files:
        return 0

    done = outbox / _DELIVERED
    n = 0
    for src in files:
        try:
            done.mkdir(exist_ok=True)
            dest = done / src.name
            i = 1
            while dest.exists():
                dest = done / f"{i}-{src.name}"
                i += 1
            # Move FIRST, preview the delivered/ path — it stays stable for the
            # session, so a lazily-mounted graphics widget never loses its file.
            shutil.move(str(src), str(dest))
            if dest.suffix.lower() in _IMAGE_SUFFIXES:
                from xlii.terminal_image import maybe_preview
                maybe_preview(dest, enabled=True, backend=backend,
                              console=console, force=True)
            elif console is not None:
                console.print(f"[dim]delivered: {dest.name} — {dest}[/dim]")
            n += 1
        except Exception:  # noqa: BLE001 — best-effort delivery, per file
            continue
    return n


def release_local_outbox(session: Any) -> None:
    """Tear down a locally-granted outbox at surface exit (best-effort)."""
    if not getattr(session, "_local_outbox", False):
        return
    outbox = getattr(session, "outbox_dir", None)
    session.outbox_dir = None
    session._local_outbox = False
    if outbox is not None:
        shutil.rmtree(str(outbox), ignore_errors=True)
