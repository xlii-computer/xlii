"""Job board, task pills, and task-maker save.

Mixin extracted from :mod:`xlii.serve_face.server`.
"""
from __future__ import annotations


class FaceJobsMixin:
    """Job board, task pills, and task-maker save."""

    def _bind_job_listener(self) -> None:
        """Job state changes must refresh the strip (task pills, not only journal)."""
        try:
            from xlii.jobs import set_job_listener

            set_job_listener(self._on_job_change)
        except Exception:  # noqa: BLE001
            pass

    def _on_job_change(self) -> None:
        try:
            self.send(self.chrome_state())
        except Exception:  # noqa: BLE001 — a strip miss must not kill a job
            pass

    def jobs_open(self, job_id: str = "") -> bool:
        """Open the jobs board; mark a finished pill seen when *job_id* is given."""
        jid = (job_id or "").strip()
        if jid:
            try:
                from xlii.jobs import _peek_registry

                reg = _peek_registry(self.state)
                if reg is not None:
                    reg.mark_seen(jid)
            except Exception:
                # Best-effort — a registry miss must not block the jobs board.
                pass
        self._prefer_home_pack()
        if jid:
            try:
                self.deck.ensure_pane("jobs")
                self.deck.open_scheme("jobs")
                pane = None
                dock = getattr(self.deck, "_dock", None)
                if dock is not None:
                    pane = dock.slots.get("jobs")
                if pane is not None and hasattr(pane, "mount"):
                    pane.mount(f"jobs://{jid}")
            except Exception:
                # Pane mount is best-effort — the board still opens below.
                pass
        ok = bool(self.deck.open_pane("jobs"))
        try:
            self.send(self.chrome_state())
        except Exception:
            # Chrome refresh is best-effort.
            pass
        return ok

    def write_task(self, spec: dict, *, run: bool = False) -> bool:
        """Task maker Save — write ``.xlii/tasks/<name>.toml`` from the step list."""
        from xlii import tasks as T

        project = getattr(self.state, "project", None)
        xli = getattr(project, "xli_dir", None) if project is not None else None
        if xli is None:
            self.send({"type": "meta_message", "level": "warn",
                       "text": "no project — land a folder before saving a task"})
            return False
        try:
            path = T.write_pipeline_spec(xli, spec or {})
        except T.TaskError as e:
            self.send({"type": "meta_message", "level": "warn", "text": str(e)})
            return False
        except Exception as e:
            self.send({"type": "meta_message", "level": "error",
                       "text": f"could not write task: {type(e).__name__}: {e}"})
            return False
        name = str((spec or {}).get("name") or path.stem)
        self.send({"type": "meta_message", "level": "success",
                   "text": f"wrote {path} — /tasks run {name}"})
        try:
            self.deck._taskmake_address = f"taskmake://{name}"
            self.deck.send_snapshot()
        except Exception:
            # Best-effort UI sync — the save already succeeded.
            pass
        if run:
            self.send({"type": "prefill", "text": f"/tasks run {name}"})
        return True

    def jobs_clear(self) -> int:
        """Drop finished jobs (and their pills). Live work stays."""
        n = 0
        try:
            from xlii.jobs import _peek_registry

            reg = _peek_registry(self.state)
            if reg is not None:
                n = reg.clear_finished()
        except Exception:
            n = 0
        try:
            self.send(self.chrome_state())
            self.deck.send_snapshot()
        except Exception:
            # Best-effort UI refresh — the registry is already cleared.
            pass
        self.send({"type": "meta_message",
                   "text": f"cleared {n} finished job{'' if n == 1 else 's'}",
                   "level": "info"})
        return n
