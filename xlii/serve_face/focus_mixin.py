"""Outbox drain, uploads, and feed-view focus.

Mixin extracted from :mod:`xlii.serve_face.server`.
"""
from __future__ import annotations

import base64
import binascii
import sys as _sys
import time
from pathlib import Path
from typing import Any, Optional

from xlii.serve_face.wire import MAX_INLINE_IMAGE_BYTES, _IMAGE_SUFFIXES
from xlii.turn_events import FileOut
from xlii.ws_protocol import serialize_event

_sf = _sys.modules["xlii.serve_face"]


class FaceFocusMixin:
    """Outbox drain, uploads, and feed-view focus."""

    # ------------------------------------------------------------ outbox

    def drain_outbox_to_wire(self) -> int:
        """The face's outbox drain: same move-to-``delivered/``-FIRST
        discipline as ``outbox.drain_outbox``, delivery as ``file_out`` events
        instead of terminal previews. Locally-granted outboxes only."""
        import shutil

        sess = self.state.agent.session
        if not getattr(sess, "_local_outbox", False):
            return 0
        outbox = getattr(sess, "outbox_dir", None)
        if outbox is None:
            return 0
        outbox = Path(outbox)
        try:
            files = sorted(p for p in outbox.iterdir() if p.is_file())
        except OSError:
            return 0
        done = outbox / "delivered"
        n = 0
        for src in files:
            try:
                done.mkdir(exist_ok=True)
                dest = done / src.name
                i = 1
                while dest.exists():
                    dest = done / f"{i}-{src.name}"
                    i += 1
                shutil.move(str(src), str(dest))
                kind = ("image" if dest.suffix.lower() in _IMAGE_SUFFIXES
                        else dest.suffix.lstrip(".").lower() or "file")
                size = dest.stat().st_size
                from xlii.artifacts import locate_made_file

                root = getattr(getattr(self.state, "project", None), "project_root", None)
                durable, address = locate_made_file(dest, root)
                b64 = ""
                if kind == "image" and size <= MAX_INLINE_IMAGE_BYTES:
                    b64 = base64.b64encode(dest.read_bytes()).decode()
                self.send(serialize_event(FileOut(
                    name=durable.name, kind=kind, b64=b64,
                    path=str(durable), address=address,
                )))
                n += 1
            except Exception:  # noqa: BLE001 — best-effort delivery, per file
                continue
        return n

    # ------------------------------------------------------------ uploads

    def handle_upload(self, msg: dict[str, Any]) -> None:
        from xlii.atomicio import write_bytes_atomic

        name = Path(str(msg.get("name") or "")).name
        if not name:
            self.send({"type": "error", "message": "upload requires name"})
            return
        try:
            data = base64.b64decode(msg.get("b64") or "", validate=True)
        except (binascii.Error, ValueError):
            self.send({"type": "error", "message": "upload: invalid base64"})
            return
        if not data or len(data) > _sf.MAX_UPLOAD_BYTES:
            self.send({"type": "error",
                       "message": f"upload: empty or over {_sf.MAX_UPLOAD_BYTES // (1024*1024)} MiB"})
            return
        uploads = Path(self.state.project.xli_dir) / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        dest = uploads / f"{stamp}-{name}"
        i = 1
        while dest.exists():
            dest = uploads / f"{stamp}-{i}-{name}"
            i += 1
        write_bytes_atomic(dest, data)
        if self.posture == "code":
            self.state.attach_file(str(dest))
            self.send({"type": "meta_message", "text": f"attached: {dest.name}",
                       "level": "success"})
        else:
            self.pending_uploads.append(str(dest))
            self.send({"type": "meta_message",
                       "text": f"staged for the next ask: {dest.name}",
                       "level": "success"})

    # -------------------------------------------------------------- focus

    def _chat_attachments(self) -> Optional[list[str]]:
        """Uploads + locker paths (incl. F4 once-focus) for the next [M] turn."""
        paths = list(self.pending_uploads)
        self.pending_uploads = []
        live = getattr(self.state, "live_attachment_paths", None)
        if callable(live):
            try:
                for p in live() or []:
                    if p and p not in paths:
                        paths.append(p)
            except Exception:  # noqa: BLE001
                pass
        return paths or None

    def handle_focus(self, msg: dict[str, Any]) -> None:
        """Pin a feed-view / pane address as next-turn context (not a rewind)."""
        from xlii.attach import focus_address, unfocus_address

        if msg.get("clear"):
            for item in list(self._focus):
                try:
                    unfocus_address(self.state, item)
                except Exception:  # noqa: BLE001
                    pass
            self._focus = []
            self._emit_focus_state()
            self.send({"type": "meta_message", "level": "info",
                       "text": "focus cleared"})
            return
        address = str(msg.get("address") or "").strip()
        if not address:
            self.send({"type": "error", "message": "focus requires address"})
            return
        if self._turn_mutation_busy():
            self.send({"type": "error",
                       "message": self._turn_mutation_busy_message("focus")})
            return
        once = True if msg.get("once") is None else bool(msg.get("once"))
        item = focus_address(self.state, address, once=once)
        if item is None:
            self.send({"type": "meta_message", "level": "warn",
                       "text": f"cannot focus {address}"})
            return
        # Replace an existing pin of the same address; otherwise append.
        self._focus = [i for i in self._focus if i.get("address") != item["address"]]
        self._focus.append(item)
        self._emit_focus_state()
        if msg.get("quiet"):
            return
        title = item.get("title") or address
        self.send({"type": "meta_message", "level": "success",
                   "text": f"focus · {title} — next turn (not a rewind)"})

    def pin_canvas_work(self, address: str) -> None:
        """Hold the canvas make as a durable attachment until the work changes."""
        from xlii.attach import focus_address, unfocus_address

        address = (address or "").strip()
        if not address:
            return
        if not address.startswith("canvas://"):
            address = f"canvas://{address}"
        kept: list[dict[str, Any]] = []
        for item in list(self._focus):
            prev = str(item.get("address") or "")
            if not prev.startswith("canvas://"):
                kept.append(item)
                continue
            try:
                unfocus_address(self.state, item)
            except Exception:
                # Unfocusing is tidy-up; the pin below is what has to land.
                pass
            path = str(item.get("path") or "")
            rm = getattr(self.state, "remove_file", None)
            files = getattr(self.state, "attached_files", None) or []
            if callable(rm) and path:
                for e in files:
                    if e.get("path") == path and e.get("role") == "canvas":
                        try:
                            rm(path)
                        except Exception:
                            # The entry is being replaced anyway; a failed
                            # remove leaves a stale duplicate at worst.
                            pass
                        break
        self._focus = kept
        item = focus_address(self.state, address, once=False)
        if item is None:
            self._emit_focus_state()
            return
        self._focus = [i for i in self._focus if i.get("address") != item["address"]]
        self._focus.append(item)
        self._emit_focus_state()

    def _emit_focus_state(self, *, even_empty: bool = True) -> None:
        """Drop consumed once-files, then tell the face what is still pinned."""
        files = getattr(self.state, "attached_files", None) or []
        live_paths = {
            e.get("path") for e in files
            if isinstance(e, dict) and e.get("enabled")
        }
        had = bool(self._focus)
        kept: list[dict[str, Any]] = []
        for item in self._focus:
            path = item.get("path") or ""
            if path and path not in live_paths:
                continue
            kept.append(item)
        self._focus = kept
        if not kept and not even_empty and not had:
            return
        self.send({
            "type": "focus_state",
            "items": [
                {
                    "address": i.get("address") or "",
                    "title": i.get("title") or "",
                    "kind": i.get("kind") or "file",
                    "once": bool(i.get("once", True)),
                }
                for i in kept
            ],
        })

    def _exit_overlay(self) -> None:
        """Leave howto / ops / plan / harness — same work as ``/off``."""
        try:
            from xlii.repl_cmds.off import _off_handler
            ctx = self.state.as_context_dict() if hasattr(self.state, "as_context_dict") else {
                "console": getattr(self.state, "console", None),
                "agent": getattr(self.state, "agent", None),
                "state": self.state,
            }
            # /off's howto detach walks attached_docs — fakes/partial states
            # may not have the list. Don't let that block leaving the mode.
            st = ctx.get("state")
            if st is not None and not hasattr(st, "attached_docs"):
                try:
                    st.attached_docs = []
                except Exception as e:
                    self.send({
                        "type": "meta_message",
                        "level": "warn",
                        "text": f"could not prepare attached_docs ({type(e).__name__}); continuing /off",
                    })
            _off_handler("/off", ctx)
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "warn",
                       "text": f"could not leave mode ({type(e).__name__})"})
