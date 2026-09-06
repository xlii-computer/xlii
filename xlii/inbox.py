"""Goal inbox — drop a markdown task into `.xlii/inbox/` and let
`xlii loop --drain-inbox` run it unattended (cursor-workflows.md B2).

Each inbox file is a markdown doc with optional frontmatter:

    ---
    goal: make the failing auth tests pass
    judge: tests, anthropic        # comma string or a YAML list
    max_cycles: 8
    test: pytest -q tests/test_auth.py
    budget: 2.50
    commit: each
    ---
    Longer context for the agent. Used as the goal when there is no
    `goal:` key — so a plain markdown task with no frontmatter works too.

Files are processed in sorted (filename) order and moved to `inbox/done/` after
each run. This is the automation-lite path: a cron job runs `xlii loop
--drain-inbox` on a schedule; a webhook (or the XMPP fabric) just writes a file
here. No VM fleet, no event bus.
"""

from __future__ import annotations

import fcntl
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from xlii.frontmatter import parse_frontmatter

_STEM_RE = re.compile(r"[^a-z0-9_-]+")

INBOX_DIR = "inbox"
INBOX_DONE_DIR = "done"


@dataclass
class InboxDefaults:
    """Fallbacks for fields an inbox file omits — sourced from the CLI flags so
    `xlii loop --drain-inbox --judge tests,anthropic` sets the default judge for
    files that don't specify their own."""
    judge: str = "tests"
    max_cycles: int = 5
    test_command: str = "pytest -q"
    budget_usd: Optional[float] = None
    commit_mode: str = "never"


@dataclass
class InboxItem:
    path: Path
    goal: str
    judges: list[str]
    max_cycles: int
    test_command: str
    budget_usd: Optional[float]
    commit_mode: str
    # Injection-class Unicode count on the goal text (0 = clean). Ingress only —
    # never auto-strips; drain prints a take-note when > 0.
    credibility: int = 0
    source: str = "local"
    # Seventh key (pr-watch): drain skips without archiving when the tree is
    # not on this branch or is dirty. Empty = no restriction (serve-inbox).
    branch: str = ""
    # No `push:` key. Push is watcher-side only (pr-watch P1). A frontmatter
    # push field would let any serve-inbox POST grant itself push authority.


def inbox_dir(xli_dir: Path) -> Path:
    return xli_dir / INBOX_DIR


def list_inbox(xli_dir: Path) -> list[Path]:
    """Pending inbox files: top-level `*.md`, sorted by name. The `done/`
    subdir is excluded (a non-recursive glob never descends into it)."""
    d = inbox_dir(xli_dir)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.md") if p.is_file())


def _as_judge_list(raw: Any, default: str) -> list[str]:
    if raw is None or raw == "":
        raw = default
    items = raw if isinstance(raw, list) else str(raw).split(",")
    return [str(j).strip() for j in items if str(j).strip()]


def _as_int(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _as_float_or_none(raw: Any, default: Optional[float]) -> Optional[float]:
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def parse_inbox_item(path: Path, defaults: Optional[InboxDefaults] = None) -> InboxItem:
    """Parse one inbox file into loop parameters.

    Frontmatter keys (all optional except the goal): `goal`, `judge`,
    `max_cycles`, `test`, `budget`, `commit`, `branch`. There is no `push`
    key — push is watcher-side only. Anything omitted falls back to
    `defaults`. The goal is the `goal:` key, or — if absent — the markdown
    body.

    Raises ValueError if neither a `goal:` key nor a body is present."""
    defaults = defaults or InboxDefaults()
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    goal = str(meta.get("goal") or "").strip() or body.strip()
    if not goal:
        raise ValueError(f"{path.name}: no goal (set frontmatter 'goal:' or add a body)")

    source = str(meta.get("source") or "local").strip().lower() or "local"

    commit = str(meta.get("commit", defaults.commit_mode) or defaults.commit_mode).strip()
    if commit not in ("never", "each", "final"):
        commit = defaults.commit_mode
    if source == "webhook":
        # Webhook frontmatter is attacker-controlled — never auto-commit.
        commit = "never"

    test_command = str(meta.get("test") or "").strip() or defaults.test_command
    try:
        from xlii.shellgate import NETWORK, MODIFIES_SYSTEM, classify_command

        classified = classify_command(test_command, None)
        if classified in (NETWORK, MODIFIES_SYSTEM):
            raise ValueError(
                f"{path.name}: inbox test command classified as {classified} — refused"
            )
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(
            f"{path.name}: inbox test command could not be classified — refused"
        ) from exc

    credibility = 0
    try:
        from xlii.text_hygiene import credibility_of

        credibility = credibility_of(goal)
    except Exception:
        credibility = 0

    return InboxItem(
        path=path,
        goal=goal,
        judges=_as_judge_list(meta.get("judge"), defaults.judge),
        max_cycles=_as_int(meta.get("max_cycles"), defaults.max_cycles),
        test_command=test_command,
        budget_usd=_as_float_or_none(meta.get("budget"), defaults.budget_usd),
        commit_mode=commit,
        credibility=credibility,
        source=source,
        branch=str(meta.get("branch") or "").strip(),
    )


def archive_inbox(path: Path, xli_dir: Path) -> Path:
    """Move a processed inbox file into `inbox/done/`, returning its new path.
    A name already present in `done/` is suffixed (`-2`, `-3`, …) rather than
    clobbered, so re-dropped tasks keep a history."""
    done = inbox_dir(xli_dir) / INBOX_DONE_DIR
    done.mkdir(parents=True, exist_ok=True)
    dest = done / path.name
    if dest.exists():
        n = 2
        while (done / f"{dest.stem}-{n}{dest.suffix}").exists():
            n += 1
        dest = done / f"{dest.stem}-{n}{dest.suffix}"
    try:
        path.rename(dest)
    except FileNotFoundError:
        # Concurrent drain already moved it — tolerate so the loser's archive
        # does not abort the rest of the queue (pr-watch drain mutex).
        return dest
    return dest


def safe_stem(hint: Optional[str], default: str = "webhook") -> str:
    """A filesystem-safe inbox stem from an untrusted hint: lowercase, only
    `[a-z0-9_-]`, no path separators or traversal, capped length. Powers the B2.1
    webhook so a POST header can't write outside `inbox/`."""
    if not hint:
        return default
    s = _STEM_RE.sub("-", str(hint).strip().lower()).strip("-")
    return s[:60] or default


def _inject_source(body: str, source: str) -> str:
    """Stamp ``source:`` into frontmatter so drain can treat webhook files as untrusted."""
    lines = body.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                lines.insert(i, f"source: {source}\n")
                return "".join(lines)
    return f"---\nsource: {source}\n---\n{body}"


def enqueue_inbox(xli_dir: Path, body: str, *, stem: str = "webhook",
                  source: str = "") -> Path:
    """Write `body` as a new `inbox/<stem>.md` task, returning its path. The stem
    is sanitized; a name already present is suffixed (`-2`, `-3`, …), never
    clobbered. Used by `xlii serve-inbox` (B2.1) to queue a webhook POST.

    Scans the body for injection-class Unicode; when dirty, logs a one-line
    take-note to stderr (webhook path has no session console). Does not strip.
    """
    d = inbox_dir(xli_dir)
    d.mkdir(parents=True, exist_ok=True)
    stem = safe_stem(stem)
    if source:
        body = _inject_source(body, source)
    try:
        from xlii.text_hygiene import note_ingress

        note = note_ingress(body or "", source=f"inbox:{stem}")
        if note:
            import sys

            print(f"inbox: {note}", file=sys.stderr)
    except Exception as exc:
        import sys

        print(f"inbox: ingress hygiene note failed ({exc}); enqueue continues", file=sys.stderr)
    n = 1
    while True:
        suffix = "" if n == 1 else f"-{n}"
        dest = d / f"{stem}{suffix}.md"
        try:
            with dest.open("x", encoding="utf-8") as f:
                f.write(body)
            return dest
        except FileExistsError:
            n += 1


def enqueue_inbox_atomic(
    xli_dir: Path,
    body: str,
    *,
    stem: str,
    dest: Optional[Path] = None,
) -> Path:
    """Write ``body`` via ``<name>.md.tmp`` then ``os.replace`` into place.

    The ``*.md`` glob in ``list_inbox`` must never see a half-written file
    (a concurrent drain would parse it empty, archive it as SKIP, and the
    event is gone). ``dest`` rewrites an existing pending file in place
    (producer-side coalescing); otherwise a collision-suffix is chosen.
    """
    d = inbox_dir(xli_dir)
    d.mkdir(parents=True, exist_ok=True)
    if dest is None:
        stem = safe_stem(stem)
        n = 1
        while True:
            suffix = "" if n == 1 else f"-{n}"
            candidate = d / f"{stem}{suffix}.md"
            if not candidate.exists():
                dest = candidate
                break
            n += 1
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, dest)
    return dest


def inbox_hold_reason(item: InboxItem, repo: Path) -> str:
    """Why drain must skip this item without archiving. Empty = proceed."""
    want = (item.branch or "").strip()
    if not want:
        return ""
    from xlii.git_status import branch_name, porcelain_entries

    have = branch_name(repo) or ""
    if have != want:
        return f"branch is {have!r}, item wants {want!r}"
    if porcelain_entries(repo):
        return "working tree is dirty"
    return ""


@contextmanager
def inbox_drain_lock(xli_dir: Path) -> Iterator[bool]:
    """Non-blocking exclusive flock on ``inbox/.lock``. Yields False if busy."""
    d = inbox_dir(xli_dir)
    d.mkdir(parents=True, exist_ok=True)
    lock = d / ".lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

