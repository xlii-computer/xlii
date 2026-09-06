"""File-board bus for farm tickets.

``~/.xlii/jobs/{open,claimed,done}/`` — claim is an exclusive create
(``O_CREAT|O_EXCL``), not a rename-over, so two watchers cannot both win.
The same JSON documents will ride MUC later; this is the testable bus.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from xlii.atomicio import write_text_atomic
from xlii.farm import JobError, JobResult, Ticket, utc_now, valid_job_id


class JobBoard:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.open_dir = self.root / "open"
        self.claimed_dir = self.root / "claimed"
        self.done_dir = self.root / "done"
        self.cancelled_dir = self.root / "cancelled"
        self.quarantine_dir = self.root / "quarantine"

    def ensure(self) -> None:
        for d in (
            self.open_dir, self.claimed_dir, self.done_dir,
            self.cancelled_dir, self.quarantine_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def _job_file(self, folder: Path, job_id: str) -> Path:
        """`{id}.json` inside *folder*. Raises if *job_id* can escape the lane."""
        if not valid_job_id(job_id):
            raise JobError(f"invalid job id {job_id!r}")
        root = folder.resolve()
        path = (root / f"{job_id}.json").resolve()
        if path.parent != root:
            raise JobError("job path escaped the board")
        return path

    def _open_path(self, job_id: str) -> Path:
        return self._job_file(self.open_dir, job_id)

    def _claimed_path(self, job_id: str) -> Path:
        return self._job_file(self.claimed_dir, job_id)

    def _done_path(self, job_id: str) -> Path:
        return self._job_file(self.done_dir, job_id)

    def _cancelled_path(self, job_id: str) -> Path:
        return self._job_file(self.cancelled_dir, job_id)

    def post(self, ticket: Ticket) -> Path:
        self.ensure()
        path = self._open_path(ticket.id)
        if (
            path.exists()
            or self._claimed_path(ticket.id).exists()
            or self._done_path(ticket.id).exists()
            or self._cancelled_path(ticket.id).exists()
        ):
            raise JobError(f"job {ticket.id} already exists on the board")
        write_text_atomic(path, ticket.dump())
        return path

    def list_open(self) -> list[Ticket]:
        return self._list(self.open_dir)

    def list_claimed(self) -> list[Ticket]:
        return self._list(self.claimed_dir)

    def _list(self, folder: Path) -> list[Ticket]:
        if not folder.is_dir():
            return []
        out: list[Ticket] = []
        for path in sorted(folder.glob("*.json")):
            try:
                out.append(Ticket.from_json(path.read_text(encoding="utf-8")))
            except JobError:
                continue
        return out

    def get(self, job_id: str) -> Optional[tuple[str, Ticket, Optional[JobResult]]]:
        """Return (lane, ticket, result?) or None."""
        if not valid_job_id(job_id):
            return None
        for lane, folder in (
            ("open", self.open_dir),
            ("claimed", self.claimed_dir),
            ("done", self.done_dir),
            ("cancelled", self.cancelled_dir),
        ):
            try:
                path = self._job_file(folder, job_id)
            except JobError:
                return None
            if not path.is_file():
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            result = None
            if lane == "done" and raw.get("status"):
                result = JobResult.from_dict(raw)
            ticket_raw = raw.get("ticket") if lane == "done" else raw
            if not isinstance(ticket_raw, dict):
                ticket_raw = raw
            ticket = Ticket.from_dict(ticket_raw)
            return lane, ticket, result
        return None

    def is_cancelled(self, job_id: str) -> bool:
        if not valid_job_id(job_id):
            return False
        try:
            return self._cancelled_path(job_id).is_file()
        except JobError:
            return False

    def cancel(self, job_id: str) -> bool:
        """Mark *job_id* cancelled so it is not open. False if missing or already done."""
        if not valid_job_id(job_id):
            return False
        self.ensure()
        found = self.get(job_id)
        if found is None:
            return False
        lane, ticket, _result = found
        if lane == "done":
            return False
        if lane == "cancelled":
            return True
        dest = self._cancelled_path(job_id)
        write_text_atomic(dest, ticket.dump())
        for p in (self._open_path(job_id), self._claimed_path(job_id)):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        return True

    def claim(self, job_id: str, node: str) -> Optional[Ticket]:
        """Atomically take *job_id* from open → claimed. None if lost the race."""
        if not valid_job_id(job_id):
            return None
        self.ensure()
        try:
            src = self._open_path(job_id)
            dest = self._claimed_path(job_id)
        except JobError:
            return None
        if not src.is_file():
            return None
        try:
            ticket = Ticket.from_json(src.read_text(encoding="utf-8"))
        except JobError:
            return None
        ticket.claimed_by = node
        ticket.claimed_at = utc_now()
        payload = ticket.dump().encode("utf-8")
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(dest, flags, 0o600)
        except FileExistsError:
            return None
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            src.unlink()
        except FileNotFoundError:
            # The source ticket is already gone -- the claim itself succeeded.
            pass
        return ticket

    def complete(self, ticket: Ticket, result: JobResult) -> Path:
        self.ensure()
        dest = self._done_path(ticket.id)
        doc = result.to_dict()
        doc["ticket"] = ticket.to_dict()
        write_text_atomic(dest, json.dumps(doc, indent=2) + "\n")
        for p in (self._claimed_path(ticket.id), self._open_path(ticket.id)):
            try:
                p.unlink()
            except FileNotFoundError:
                # Already-removed ticket files are exactly the desired end state.
                pass
        return dest

    def expire_stale(self, now: Optional[datetime] = None) -> list[str]:
        """Move expired claims back to open. Returns reopened ids."""
        self.ensure()
        now = now or datetime.now(timezone.utc)
        reopened: list[str] = []
        for ticket in self.list_claimed():
            if not _expired(ticket, now):
                continue
            ticket.claimed_by = ""
            ticket.claimed_at = ""
            dest = self._open_path(ticket.id)
            write_text_atomic(dest, ticket.dump())
            try:
                self._claimed_path(ticket.id).unlink()
            except FileNotFoundError:
                # The claimed file is already gone; the ticket is back on the open queue either way.
                pass
            reopened.append(ticket.id)
        return reopened


def _expired(ticket: Ticket, now: datetime) -> bool:
    if not ticket.claimed_at:
        return True
    try:
        claimed = datetime.fromisoformat(ticket.claimed_at)
        if claimed.tzinfo is None:
            claimed = claimed.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    age = (now - claimed).total_seconds()
    return age > ticket.claim_ttl_s
