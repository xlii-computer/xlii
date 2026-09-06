"""JRN-2 — bash-wide journal capture daemon (Project Shadow).

A standalone, mostly-sleeping daemon that tails an append-only feed written by the
opt-in bash hook (`xlii journal install`) and routes each captured command into the
right project's journal — reusing ProjectJournal's batched summarize + archive
(JRN-1). It is deliberately **separate** from `xlii/daemon.py` (the XMPP/OMEMO
fabric): bash-wide journaling must work without the experimental fabric. The ingest
core (`tick` / `ingest_lines`) is reusable, so the fabric daemon can *host* it too
("take your desktop with you" — your journal rides the fabric across machines)
without forcing XMPP on anyone.

Transport (decided, proposals/lifecycle-and-journal.md §B4): **append-only file +
tail**. The bash hook does ONE fork-free append per command; this daemon does all
the summarization async, so the shell never blocks and a down daemon just leaves
lines waiting in the file.

Privacy gate: a command is journaled only when its project has **opted in**
(`read_journal_auto` — i.e. `/journal --code-auto` in that project). Commands run
outside any opted-in project are parsed and dropped. The hook itself is a no-op
unless `XLII_JOURNAL` is exported, and nothing is installed without the explicit
`xlii journal install`.
"""

from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

from xlii.config import PROJECT_CONFIG_FILE, PROJECT_DIR_NAME, ProjectConfig
from xlii.journal import ProjectJournal, journal_batch_size, read_journal_auto

# Runtime state lives under XDG data home (same family as daemon.py's audit log),
# overridable for tests. The feed + pid + offset are GLOBAL: the bash hook is one
# writer for the whole machine/user session, so a single daemon tails one feed and
# fans out per project. Per-project privacy is enforced by the opt-in gate, not by
# the path.
_DEFAULT_RUNTIME_DIR = Path.home() / ".local" / "share" / "xlii"
_FEED_NAME = "journal-feed.log"
_PID_NAME = "journal-daemon.pid"
_OFFSET_NAME = "journal-daemon.offset"
_MAX_PROJECT_SEARCH_DEPTH = 64  # defensive: don't walk forever on a pathological path
# Minimum polling floor: prevents tight-loop wakeups if callers pass 0/negative or
# extremely small intervals, balancing responsiveness with idle CPU usage.
_MIN_POLL_INTERVAL_SECONDS = 0.2


def runtime_dir() -> Path:
    return Path(os.environ.get("XLII_STATE_DIR", str(_DEFAULT_RUNTIME_DIR)))


def feed_path() -> Path:
    env = os.environ.get("XLII_JOURNAL_FEED", "").strip()
    return Path(env) if env else runtime_dir() / _FEED_NAME


def pidfile_path() -> Path:
    return runtime_dir() / _PID_NAME


def offset_path() -> Path:
    return runtime_dir() / _OFFSET_NAME


# --------------------------------------------------------------------------- #
#  feed parsing + project routing
# --------------------------------------------------------------------------- #

def parse_feed_line(line: str) -> Optional[tuple[str, str, str]]:
    """Parse one ``ts \\t cwd \\t command`` feed line. Returns (ts, cwd, cmd) or
    None for blank/malformed lines or empty commands."""
    if not line:
        return None
    parts = line.rstrip("\n").split("\t", 2)
    if len(parts) != 3:
        return None
    ts, cwd, cmd = parts[0].strip(), parts[1].strip(), parts[2].strip()
    if not cmd or not cwd:
        return None
    return ts, cwd, cmd


def find_project_root(cwd: str | Path) -> Optional[Path]:
    """Nearest ancestor of ``cwd`` that is an xlii project (has
    ``.xlii/project.json``), or None."""
    try:
        p = Path(cwd).expanduser()
    except (OSError, ValueError):
        return None
    seen = 0
    for d in [p, *p.parents]:
        if (d / PROJECT_DIR_NAME / PROJECT_CONFIG_FILE).is_file():
            return d
        seen += 1
        if seen > _MAX_PROJECT_SEARCH_DEPTH:  # defensive: don't walk forever on a pathological path
            break
    return None


def _load_project(root: Path) -> Optional[ProjectConfig]:
    try:
        return ProjectConfig.load(root)
    except Exception:
        return None


def _iso_from_epoch(raw: str) -> str:
    try:
        return datetime.fromtimestamp(int(raw), timezone.utc).astimezone().isoformat(timespec="seconds")
    except (ValueError, OSError, OverflowError):
        return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _build_journal(project: ProjectConfig, cfg: Any, pool: Any) -> ProjectJournal:
    return ProjectJournal(
        project=project, pool=pool, cfg=cfg, agent=None, console=None,
        code_on=True, batch_size=journal_batch_size(),
    )


@dataclass(frozen=True)
class _IngestOutcome:
    counts: dict[str, int]
    retryable_failure: bool = False
    committed_offset: Optional[int] = None


def ingest_lines(
    lines: list[str],
    *,
    cfg: Any = None,
    pool: Any = None,
    journal_factory: Optional[Callable[[ProjectConfig], Optional[ProjectJournal]]] = None,
) -> dict[str, int]:
    """Route feed lines into the right project journals and flush each batch.

    Groups commands by project root, drops any whose project hasn't opted in
    (`read_journal_auto`), and rides ProjectJournal's existing observe→summarize→
    archive path. Fully best-effort: a bad project never aborts the others.
    Returns {project_root: entries_journaled}. ``journal_factory`` is a test seam.
    """
    return _ingest_lines_outcome(
        lines, cfg=cfg, pool=pool, journal_factory=journal_factory,
    ).counts


def _ingest_lines_outcome(
    lines: list[str],
    *,
    cfg: Any = None,
    pool: Any = None,
    journal_factory: Optional[Callable[[ProjectConfig], Optional[ProjectJournal]]] = None,
) -> _IngestOutcome:
    """Ingest lines plus whether any opted-in entry needs to be retried."""
    by_root: dict[Path, list[tuple[str, str, str]]] = {}
    for line in lines:
        parsed = parse_feed_line(line)
        if parsed is None:
            continue
        ts, cwd, cmd = parsed
        root = find_project_root(cwd)
        if root is None:
            continue
        by_root.setdefault(root, []).append((ts, cwd, cmd))

    counts: dict[str, int] = {}
    retryable_failure = False
    for root, items in by_root.items():
        project = _load_project(root)
        if project is None:
            continue
        try:
            if not read_journal_auto(project):
                continue  # privacy gate: only opted-in projects are journaled
        except Exception:
            continue
        journal = journal_factory(project) if journal_factory else _build_journal(project, cfg, pool)
        if journal is None:
            continue
        journal.code_on = True
        n = 0
        for ts, cwd, cmd in items:
            try:
                # Bash feed entries are plain shell commands (no tool outputs/calls):
                # [] = no tool-result payloads, and tool_calls=0 keeps the expected
                # observe_turn metadata shape for ProjectJournal's pipeline.
                recorded = journal.observe_turn(
                    cmd, [], SimpleNamespace(tool_calls=0), cwd=cwd,
                )
                if recorded is False:
                    retryable_failure = True
                    continue
                n += 1
            except Exception:
                # best-effort: one bad entry must not abort the batch
                retryable_failure = True
        try:
            journal.flush()
        except Exception:
            # best-effort daemon behavior: a flush failure in one project
            # must not abort ingestion for other projects/batches.
            pass
        counts[str(root)] = n
    return _IngestOutcome(counts=counts, retryable_failure=retryable_failure)


def _ingest_line_records_outcome(
    records: list[tuple[str, int]],
    *,
    cfg: Any = None,
    pool: Any = None,
    journal_factory: Optional[Callable[[ProjectConfig], Optional[ProjectJournal]]] = None,
) -> _IngestOutcome:
    """Ingest feed lines in offset order and report the durable offset prefix.

    When one opted-in raw entry cannot be written, the failing line must remain
    queued for retry, but earlier successful lines should not be replayed into
    duplicate journal entries. Processing stops at the first retryable failure so
    the caller can commit only the contiguous prefix that is safe to forget.
    """
    counts: dict[str, int] = {}
    retryable_failure = False
    committed_offset: Optional[int] = None
    journals: dict[Path, ProjectJournal] = {}
    skipped_roots: set[Path] = set()

    for line, end_offset in records:
        parsed = parse_feed_line(line)
        if parsed is None:
            committed_offset = end_offset
            continue
        _ts, cwd, cmd = parsed
        root = find_project_root(cwd)
        if root is None or root in skipped_roots:
            committed_offset = end_offset
            continue

        journal = journals.get(root)
        if journal is None:
            project = _load_project(root)
            if project is None:
                skipped_roots.add(root)
                committed_offset = end_offset
                continue
            try:
                if not read_journal_auto(project):
                    skipped_roots.add(root)
                    committed_offset = end_offset
                    continue
            except Exception:
                skipped_roots.add(root)
                committed_offset = end_offset
                continue
            journal = journal_factory(project) if journal_factory else _build_journal(project, cfg, pool)
            if journal is None:
                skipped_roots.add(root)
                committed_offset = end_offset
                continue
            journal.code_on = True
            journals[root] = journal

        try:
            recorded = journal.observe_turn(
                cmd, [], SimpleNamespace(tool_calls=0), cwd=cwd,
            )
        except Exception:
            retryable_failure = True
            break
        if recorded is False:
            retryable_failure = True
            break

        key = str(root)
        counts[key] = counts.get(key, 0) + 1
        committed_offset = end_offset

    for journal in journals.values():
        try:
            journal.flush()
        except Exception:
            # One journal that won't flush must not stop the others from flushing.
            pass

    return _IngestOutcome(
        counts=counts,
        retryable_failure=retryable_failure,
        committed_offset=committed_offset,
    )


# --------------------------------------------------------------------------- #
#  tail offset (resume where we left off, don't re-ingest on restart)
# --------------------------------------------------------------------------- #

def _read_offset() -> int:
    try:
        return int(offset_path().read_text().strip())
    except (OSError, ValueError):
        return 0


def _write_offset(n: int) -> None:
    try:
        runtime_dir().mkdir(parents=True, exist_ok=True)
        offset_path().write_text(str(int(n)))
    except OSError:
        # Best-effort persistence: failing to save the tail offset must not
        # stop ingestion; on restart we may re-read some lines.
        return


def _read_new_lines_with_offset() -> tuple[list[str], int]:
    """New feed lines since the saved offset, plus the offset to commit after
    those lines have been durably handled. Handles truncation/rotation
    (offset past EOF -> restart from 0)."""
    fp = feed_path()
    try:
        size = fp.stat().st_size
    except OSError:
        return [], _read_offset()
    offset = _read_offset()
    if offset > size:
        offset = 0  # feed was rotated/truncated
    if offset == size:
        return [], offset
    try:
        with fp.open("r", errors="replace") as f:
            f.seek(offset)
            data = f.read()
            new_offset = f.tell()
    except OSError:
        return [], offset
    return data.splitlines(), new_offset


def _read_new_line_records_with_offset() -> tuple[list[tuple[str, int]], int]:
    """New feed lines paired with their post-line file offsets."""
    fp = feed_path()
    try:
        size = fp.stat().st_size
    except OSError:
        return [], _read_offset()
    offset = _read_offset()
    if offset > size:
        offset = 0
    if offset == size:
        return [], offset
    records: list[tuple[str, int]] = []
    try:
        with fp.open("r", errors="replace") as f:
            f.seek(offset)
            while True:
                raw = f.readline()
                if raw == "":
                    break
                line = raw.rstrip("\n")
                if line.endswith("\r"):
                    line = line[:-1]
                records.append((line, f.tell()))
            new_offset = f.tell()
    except OSError:
        return [], offset
    return records, new_offset


def read_new_lines() -> list[str]:
    """New feed lines since the saved offset; advances the offset. Handles
    truncation/rotation (offset past EOF -> restart from 0)."""
    lines, new_offset = _read_new_lines_with_offset()
    if lines:
        _write_offset(new_offset)
    return lines


def tick(*, cfg: Any = None, pool: Any = None) -> int:
    """One ingest pass: consume new feed lines, journal them. Returns entry count.
    This is the reusable seam the XMPP/OMEMO fabric daemon can call on its own
    schedule to host journaling without a second process."""
    records, new_offset = _read_new_line_records_with_offset()
    if not records:
        return 0
    outcome = _ingest_line_records_outcome(records, cfg=cfg, pool=pool)
    if outcome.committed_offset is not None:
        _write_offset(outcome.committed_offset)
    elif not outcome.retryable_failure:
        _write_offset(new_offset)
    return sum(outcome.counts.values())


# --------------------------------------------------------------------------- #
#  PID supervision + liveness (status reads this)
# --------------------------------------------------------------------------- #

def read_pid() -> Optional[int]:
    try:
        return int(pidfile_path().read_text().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    except OSError:
        return False
    return True


def daemon_running() -> bool:
    """Whether a journal daemon is live (PID file + the process is alive). The
    status strip reads this so the badge reflects reality, not a stale flag."""
    pid = read_pid()
    return bool(pid and _pid_alive(pid))


def _write_pid() -> None:
    runtime_dir().mkdir(parents=True, exist_ok=True)
    pidfile_path().write_text(str(os.getpid()))


def _remove_pid() -> None:
    try:
        pidfile_path().unlink()
    except OSError:
        # Best-effort cleanup: PID file may already be gone or temporarily
        # unavailable due to races/FS state; failure here is non-fatal.
        return


def _load_cfg_pool() -> tuple[Any, Any]:
    """Best-effort cfg + pool for summarize/archive. None pool → entries are still
    written raw locally; only the LLM summary + remote archive are skipped."""
    try:
        from xlii.config import GlobalConfig
        cfg = GlobalConfig.load()
    except Exception:
        return None, None
    try:
        from xlii.pool import ClientPool
        pool = ClientPool.from_config(cfg, require_management=False)
    except Exception:
        pool = None
    return cfg, pool


def serve(*, poll_interval: float = 2.0, run_once: bool = False) -> int:
    """Run the journal daemon. Idempotent: if one is already live, this is a no-op
    (returns 0). Mostly sleeps; wakes only to drain the feed. Clean exit on
    SIGTERM/SIGINT (final drain + PID removal)."""
    if daemon_running():
        return 0
    cfg, pool = _load_cfg_pool()
    stop = {"flag": False}

    def _handle(signum, _frame):  # noqa: ANN001
        stop["flag"] = True

    try:
        signal.signal(signal.SIGTERM, _handle)
        signal.signal(signal.SIGINT, _handle)
    except (ValueError, OSError):
        pass  # not on the main thread (e.g. hosted by the fabric daemon) — fine

    _write_pid()
    try:
        # Drain whatever is already queued, then loop.
        tick(cfg=cfg, pool=pool)
        if run_once:
            return 0
        while not stop["flag"]:
            time.sleep(max(_MIN_POLL_INTERVAL_SECONDS, poll_interval))
            tick(cfg=cfg, pool=pool)
        tick(cfg=cfg, pool=pool)  # final drain before exit
    finally:
        _remove_pid()
    return 0


def ensure_daemon() -> bool:
    """Spawn `xlii journal serve` detached if no daemon is live. Returns True if a
    daemon is running afterward (already-live or freshly spawned). Best-effort."""
    if daemon_running():
        return True
    import subprocess
    import sys
    try:
        subprocess.Popen(
            [sys.executable, "-m", "xlii", "journal", "serve"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # detach from the controlling terminal
        )
    except (OSError, ValueError):
        return False
    return True


def stop_daemon() -> bool:
    """Signal a running daemon to exit. Returns True if a signal was sent."""
    pid = read_pid()
    if not pid or not _pid_alive(pid):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


# --------------------------------------------------------------------------- #
#  bash hook (installed into ~/.config/xlii/journal.sh by `xlii journal install`)
# --------------------------------------------------------------------------- #

def hook_script() -> str:
    """The bash snippet that captures commands. Fork-free: a single builtin append
    per command, gated on XLII_JOURNAL, with a recursion guard. No `ls` per command
    (that would fork every time — the daemon samples lazily instead)."""
    return (
        "# xlii journal (JRN-2) bash-wide capture — managed by `xlii journal install`.\n"
        "# Fork-free: one builtin append per command. No-op unless XLII_JOURNAL is set.\n"
        '__xlii_jrnl_feed="${XLII_JOURNAL_FEED:-$HOME/.local/share/xlii/journal-feed.log}"\n'
        "__xlii_jrnl() {\n"
        '  [ -n "${XLII_JOURNAL:-}" ] || return 0\n'
        '  case "$BASH_COMMAND" in __xlii_jrnl*) return 0 ;; esac\n'
        "  printf '%s\\t%s\\t%s\\n' \"${EPOCHSECONDS:-0}\" \"$PWD\" \"$BASH_COMMAND\" "
        '>> "$__xlii_jrnl_feed" 2>/dev/null\n'
        "  return 0\n"
        "}\n"
        "trap '__xlii_jrnl' DEBUG\n"
    )
