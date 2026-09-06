"""Open-stream catalog, peeks, and stream_sync.

Mixin extracted from :mod:`xlii.serve_face.server`.
"""
from __future__ import annotations

import re
import time


class FaceStreamsMixin:
    """Open-stream catalog, peeks, and stream_sync."""

    _STREAM_SYNC_MAX = 80  # last N user/assistant lines (not tools)

    @staticmethod
    def _history_text(content) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            bits = []
            for part in content:
                if isinstance(part, str):
                    bits.append(part)
                elif isinstance(part, dict) and part.get("type") == "text":
                    bits.append(str(part.get("text") or ""))
            return "\n".join(b for b in bits if b)
        return str(content)

    def _ensure_talk_tape_seeded_from_disk(self) -> bool:
        """Load persona turns into the in-memory tape once when empty.

        Returns True only when this call populated an empty tape from disk.
        """
        if self._talk_tape or self._talk_in_flight is not None:
            return False
        seeded = self._seed_talk_from_disk()
        if seeded:
            self._talk_tape.extend(seeded)
            return True
        return False

    def stream_turns(self) -> list[dict[str, str]]:
        """This project's tape: user/assistant lines from the live history.

        Tools stay off the replay. Caps at ``_STREAM_SYNC_MAX`` so a long
        room doesn't flood the wire. Phone/chat posture merges the in-memory
        talk tape (and persona turns on disk when the tape is empty).
        """
        code_turns = self._code_agent_turns()
        if not (self._phone_glass() or self.posture == "chat"):
            return code_turns
        self._ensure_talk_tape_seeded_from_disk()
        talk_turns = list(self._talk_tape)
        if self._talk_in_flight is not None:
            talk_turns.append(dict(self._talk_in_flight))
        merged = talk_turns + code_turns
        return merged[-self._STREAM_SYNC_MAX:]

    def _code_agent_turns(self) -> list[dict[str, str]]:
        agent = getattr(self.state, "agent", None)
        hist = list(getattr(agent, "history", None) or [])
        out: list[dict[str, str]] = []
        for msg in hist:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            text = self._history_text(msg.get("content")).strip()
            if not text:
                continue
            if len(text) > 8000:
                text = text[:8000] + "\n…"
            out.append({"role": role, "text": text})
        return out[-self._STREAM_SYNC_MAX:]

    def _append_talk_turn(self, role: str, text: str) -> None:
        line = (text or "").strip()
        if not line:
            return
        if len(line) > 8000:
            line = line[:8000] + "\n…"
        self._talk_tape.append({"role": role, "text": line})

    def _begin_talk_turn(self, text: str) -> None:
        line = (text or "").strip()
        if not line:
            return
        if len(line) > 8000:
            line = line[:8000] + "\n…"
        self._talk_in_flight = {"role": "user", "text": line, "in_flight": True}

    def _finish_talk_turn(self, reply: str) -> None:
        if self._talk_in_flight is not None:
            self._talk_tape.append({
                k: v for k, v in self._talk_in_flight.items() if k != "in_flight"
            })
            self._talk_in_flight = None
        self._append_talk_turn("assistant", reply)

    def _seed_talk_from_disk(self) -> list[dict[str, str]]:
        """Load recent persona talk turns when the in-memory tape is empty."""
        try:
            from xlii.cmds.sessions.resolve import _lookup_persona, ensure_default_persona
            from xlii.persona import DEFAULT_PERSONA_ID, talk_persona_id
            from xlii.transcript import load_recent_turns

            state = self.state
            persona_id = talk_persona_id(
                state=state, project=state.project, cfg=state.cfg,
            )
            persona = _lookup_persona(persona_id)
            if persona is None and persona_id == DEFAULT_PERSONA_ID:
                persona = ensure_default_persona()
            if persona is None:
                return []
            turns_dir = persona.turns_dir
            out: list[dict[str, str]] = []
            for t in load_recent_turns(turns_dir, 40):
                if getattr(t, "user", ""):
                    out.append({"role": "user", "text": str(t.user)[:8000]})
                if getattr(t, "assistant", ""):
                    out.append({"role": "assistant", "text": str(t.assistant)[:8000]})
            return out[-self._STREAM_SYNC_MAX:]
        except Exception:
            return []

    def _emit_resume_meta_from_disk(self) -> None:
        """Honest floor: disk-seeded tape after a dead backend respawn."""
        if not (self._phone_glass() or self.posture == "chat"):
            return
        if self._disk_resume_announced:
            return
        if not self._ensure_talk_tape_seeded_from_disk():
            return
        self._disk_resume_announced = True
        seeded = list(self._talk_tape)
        if not seeded:
            return
        try:
            from xlii.face_receipt import read_face_receipt

            rec = read_face_receipt()
            reason = str(rec.get("reason") or "")
            if reason not in {"stdin", "idle", "ttl", "linger", "crash", "revoke"}:
                return
            when = rec.get("at")
            age = ""
            try:
                stamp = int(time.time() - float(when))
                if stamp < 0:
                    stamp = 0
                age = f"{stamp}s ago"
            except (TypeError, ValueError):
                age = ""
            n = len(seeded)
            self.send({
                "type": "meta_message",
                "level": "info",
                "text": (
                    f"resumed from disk · {n} turns · background jobs from the "
                    f"previous session are not recoverable · last ended: "
                    f"{reason} {age}".strip()
                ),
            })
        except Exception:
            pass

    def live_stream_id(self) -> str:
        row = self.live_stream_row()
        return str((row or {}).get("id") or "")

    def live_stream_row(self) -> dict[str, str] | None:
        try:
            from xlii.project_paths import is_home_desk_project

            proj = getattr(self.state, "project", None)
            if proj is None or is_home_desk_project(proj):
                return None
        except Exception:
            proj = getattr(self.state, "project", None)
            if proj is None:
                return None
        return self._stream_row_from_project(proj)

    def open_streams(self) -> list[dict[str, str]]:
        return list(self._open_streams)

    def stream_by_id(self, sid: str) -> dict[str, str] | None:
        want = (sid or "").strip()
        for row in self._open_streams:
            if row.get("id") == want:
                return row
        return None

    def _stream_row_from_project(self, project) -> dict[str, str] | None:
        from pathlib import Path as P

        name = (getattr(project, "name", "") or "").strip()
        root = getattr(project, "project_root", None)
        if not name or root is None:
            return None
        try:
            path = str(P(root).expanduser().resolve())
        except OSError:
            path = str(root)
        sid = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")[:40] or "proj"
        taken = {r["id"] for r in self._open_streams if r.get("path") != path}
        if sid in taken:
            sid = f"{sid}-{abs(hash(path)) % 10000}"
        kind = "lab"
        try:
            from xlii.recent_desks import _kind_of

            kind = _kind_of(project)
        except Exception:
            # Kind is cosmetic — fall back to "lab".
            pass
        label = name.split("/", 1)[-1] if name.startswith("chat/") else name
        return {"id": sid, "name": name, "path": path, "label": label, "kind": kind}

    def note_open_stream(self, project) -> None:
        try:
            from xlii.project_paths import is_home_desk_project

            if is_home_desk_project(project):
                return
        except Exception:
            # Best-effort filter — a failed check still lists the stream.
            pass
        row = self._stream_row_from_project(project)
        if not row:
            return
        rest = [r for r in self._open_streams if r.get("path") != row["path"]]
        self._open_streams = [row] + rest

    def forget_open_stream(self, sid: str) -> None:
        want = (sid or "").strip()
        if not want or want == self.live_stream_id():
            return
        self._open_streams = [r for r in self._open_streams if r.get("id") != want]

    def enter_open_stream(self, sid: str) -> bool:
        row = self.stream_by_id(sid)
        if not row:
            return False
        return self.join_project_at(row["path"])

    def stream_turns_for(self, sid: str) -> list[dict[str, str]]:
        if not sid or sid == self.live_stream_id():
            return self.stream_turns()
        row = self.stream_by_id(sid)
        if not row:
            return []
        name = row.get("name") or ""
        stash = getattr(self.state, "_history_stash", None) or {}
        hist = stash.get(f"code:{name}") or stash.get(f"chat:{name}")
        if hist:
            return self._msgs_to_turns(hist)
        from pathlib import Path as P

        from xlii.transcript import load_recent_turns

        turns_dir = P(row["path"]) / ".xlii" / "turns"
        out: list[dict[str, str]] = []
        try:
            for t in load_recent_turns(turns_dir, 40):
                if getattr(t, "user", ""):
                    out.append({"role": "user", "text": str(t.user)[:8000]})
                if getattr(t, "assistant", ""):
                    out.append({"role": "assistant", "text": str(t.assistant)[:8000]})
        except Exception:
            return []
        return out[-self._STREAM_SYNC_MAX:]

    def _msgs_to_turns(self, hist) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for msg in hist or []:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            text = self._history_text(msg.get("content")).strip()
            if not text:
                continue
            if len(text) > 8000:
                text = text[:8000] + "\n…"
            out.append({"role": role, "text": text})
        return out[-self._STREAM_SYNC_MAX:]

    def emit_stream_peek(self, slot: str, sid: str) -> None:
        row = self.stream_by_id(sid) or {}
        try:
            self.send({
                "type": "stream_peek",
                "slot": slot,
                "id": sid,
                "project": row.get("name") or sid,
                "turns": self.stream_turns_for(sid),
            })
        except Exception:
            # Best-effort peek — the next snapshot repaints the slot.
            pass

    def emit_open_peeks(self) -> None:
        """Re-paint any slot that is looking at a parked tape."""
        deck = getattr(self, "_deck", None)
        if deck is None:
            return
        for slot in ("a", "b"):
            view = deck._slot_get(slot) or ""
            if view.startswith("stream:"):
                self.emit_stream_peek(slot, view.split(":", 1)[1])

    def _bind_open_stream(self, project) -> None:
        """Remember this folder as an open tape; remap if a slot was peeking it."""
        prev_id = getattr(self, "_stream_live", "") or ""
        self.note_open_stream(project)
        new_id = self.live_stream_id()
        self._stream_live = new_id
        deck = getattr(self, "_deck", None)
        if deck is not None:
            deck.remap_streams(prev_id, new_id)
        self.sync_stream()
        self.emit_open_peeks()

    def sync_stream(self) -> None:
        """Push the current project's history onto the Face stream.

        Stream **is** project history. Switch / Home / connect must replace
        the pixels, not keep the last room rolling.
        """
        proj = getattr(getattr(self.state, "project", None), "name", "") or ""
        try:
            self.send({
                "type": "stream_sync",
                "project": proj,
                "turns": self.stream_turns(),
            })
        except Exception:  # noqa: BLE001 — a tape miss must not kill the land
            pass
