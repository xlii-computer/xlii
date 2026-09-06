"""Local git snapshot for the project browser (project-browser.md P1).

Forge-agnostic by design: every fact comes from the local ``git`` binary, never
a GitHub/GitLab/Gitea REST call. Remote URLs are surfaced verbatim, so a
self-hosted ``git@forge:acme/api.git`` is as first-class as a github.com URL.

This module is the *single* place new git-snapshot logic lives. Rather than add a
third copy of the subprocess-git boilerplate (alongside ``loop_bundle.git_cmd``
and ``repl_cmds.review._git``), it thin-wraps :func:`xlii.loop_bundle.git_cmd` —
which already treats exit codes 0 and 1 as success (correct for ``status`` /
``diff``) and returns ``(stdout, error)`` with ``error`` set only on real
failure. Non-git trees degrade gracefully: every function returns an empty or
``None`` result instead of raising, so a browse over a plain directory still works.

Gitpanel additions (gitpain GP1 + sweep): stash listing/patches
(:func:`stash_entries` / :func:`stash_patch`) and the merged-branch sweep
(:func:`sweep_report` / :func:`sweep_cleanup_commands`) — still read-only; the
sweep only *describes* cleanup, the ``/gitpain`` command PREFILLs it for review.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from xlii.loop_bundle import git_cmd

_STASH_LINE = re.compile(r"^stash@\{(\d+)\}:\s*(.+)$")


@dataclass(frozen=True)
class StashEntry:
    """One ``git stash list`` row: ``stash@{index}`` plus its human message."""

    index: int
    ref: str
    message: str


@dataclass(frozen=True)
class WorktreeEntry:
    """One ``git worktree list --porcelain`` record (path + checked-out branch)."""

    path: str
    branch: Optional[str]
    head: str
    bare: bool = False


@dataclass(frozen=True)
class SweepReport:
    """What ``/gitpain sweep`` found: branches/worktrees merged into the default branch."""

    merged_local: tuple[str, ...] = ()
    stale_worktrees: tuple[WorktreeEntry, ...] = ()
    merged_remote: tuple[str, ...] = ()


def find_repo_root(start: Path) -> Optional[Path]:
    """Absolute repo root for ``start``, or None if it is not inside a git repo."""
    out, err = git_cmd(start, ["rev-parse", "--show-toplevel"])
    if err or not out.strip():
        return None
    return Path(out.strip())


def is_git_repo(start: Path) -> bool:
    return find_repo_root(start) is not None


def branch_name(repo: Path) -> Optional[str]:
    """Current branch, or ``detached@<sha>`` when HEAD is detached."""
    out, err = git_cmd(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    if err:
        return None
    name = out.strip()
    if not name:
        return None
    if name == "HEAD":  # detached — surface the short sha instead of the literal "HEAD"
        sha, sha_err = git_cmd(repo, ["rev-parse", "--short", "HEAD"])
        return f"detached@{sha.strip()}" if not sha_err and sha.strip() else "detached"
    return name


def _unquote_git_path(s: str) -> str:
    """Decode a path as git emits it with ``core.quotePath`` (the default).

    Git wraps paths containing spaces or non-ASCII bytes in double quotes and
    C-escapes the bytes (``"uni-caf\\303\\251.txt"``). Decode the octal byte
    escapes back to the real UTF-8 name; plain ASCII paths pass through. Failures
    fall back to the literal inner text rather than raising.
    """
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        inner = s[1:-1]
        try:
            return inner.encode("latin-1").decode("unicode_escape").encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return inner
    return s


def porcelain_entries(repo: Path) -> list[tuple[str, str, str]]:
    """Parse ``git status --porcelain`` into ``(index, worktree, path)`` triples.

    ``index`` / ``worktree`` are the two single-char status columns (X and Y);
    for renames/copies (R/C) the *destination* path is reported. Quoted paths
    (git's ``core.quotePath``) are decoded back to their real names.
    """
    out, err = git_cmd(repo, ["status", "--porcelain"])
    if err:
        return []
    entries: list[tuple[str, str, str]] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        x, y, rest = line[0], line[1], line[3:]
        # The " -> " separator only appears for rename/copy status codes; a
        # plain modified file whose name contains " -> " must NOT be split.
        if (x in ("R", "C") or y in ("R", "C")) and " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        entries.append((x, y, _unquote_git_path(rest)))
    return entries


def changed_paths(repo: Path) -> list[str]:
    """Every changed path (staged, unstaged, untracked) once, in porcelain order."""
    paths: list[str] = []
    seen: set[str] = set()
    for _x, _y, path in porcelain_entries(repo):
        if path not in seen:
            paths.append(path)
            seen.add(path)
    return paths


def short_status_map(repo: Path) -> dict[str, str]:
    """``path -> single-letter status``, worktree-biased (for badges + --changed).

    Prefers the worktree column (Y) so an unstaged edit shows ``M``; falls back
    to the index column (X) for staged-only changes; untracked is ``?``.
    """
    result: dict[str, str] = {}
    for x, y, path in porcelain_entries(repo):
        code = y if y != " " else x
        result[path] = "?" if code in (" ", "?") else code.upper()
    return result


def staged_status_map(repo: Path) -> dict[str, str]:
    """``path -> index status letter`` for files with staged changes only."""
    result: dict[str, str] = {}
    for x, _y, path in porcelain_entries(repo):
        if x not in (" ", "?"):
            result[path] = x.upper()
    return result


def stash_entries(repo: Path) -> list[StashEntry]:
    """Parsed ``git stash list`` rows, newest first (index 0 = most recent)."""
    out, err = git_cmd(repo, ["stash", "list"])
    if err or not out.strip():
        return []
    entries: list[StashEntry] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _STASH_LINE.match(line)
        if not m:
            continue
        idx = int(m.group(1))
        tail = m.group(2).strip()
        # ``On main: pause: auth race`` → keep the message after the branch prefix.
        message = tail.split(": ", 1)[-1] if tail.startswith("On ") and ": " in tail else tail
        entries.append(StashEntry(index=idx, ref=f"stash@{{{idx}}}", message=message))
    return entries


def stash_patch(repo: Path, index: int) -> Optional[str]:
    """The ``git stash show -p`` patch text for ``stash@{index}``, or None."""
    out, err = git_cmd(repo, ["stash", "show", "-p", f"stash@{{{index}}}"])
    if err:
        return None
    return out.strip() or None


def remotes(repo: Path) -> dict[str, str]:
    """``name -> url`` from ``git remote -v`` (fetch URL; displayed verbatim)."""
    out, err = git_cmd(repo, ["remote", "-v"])
    if err:
        return {}
    result: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            result.setdefault(parts[0], parts[1])
    return result


def ahead_behind(repo: Path) -> Optional[tuple[int, int]]:
    """``(ahead, behind)`` vs the upstream, or None when there is no upstream."""
    out, err = git_cmd(repo, ["rev-list", "--left-right", "--count", "@{u}...HEAD"])
    if err or not out.strip():
        return None
    parts = out.split()
    if len(parts) != 2:
        return None
    try:
        behind, ahead = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return (ahead, behind)


def last_commit_oneline(repo: Path) -> Optional[str]:
    out, err = git_cmd(repo, ["log", "-1", "--oneline"])
    if err:
        return None
    return out.strip() or None


def diff_stat(repo: Path, path: Optional[str] = None) -> Optional[str]:
    """``git diff --stat`` of unstaged working-tree changes (optionally scoped to
    ``path``). None on error or when there is nothing to show. Read-only."""
    args = ["diff", "--stat"]
    if path:
        args += ["--", path]
    out, err = git_cmd(repo, args)
    if err:
        return None
    return out.strip() or None


def default_branch_ref(repo: Path) -> str:
    """The ref the sweep measures "merged" against — the repo's *default* branch.

    Resolution order (local git only, no forge call): ``origin/HEAD`` when the
    remote declares one (``git symbolic-ref refs/remotes/origin/HEAD``), then
    the first of ``origin/main`` / ``origin/master`` / local ``main`` /
    ``master`` that exists, then ``HEAD`` as a last resort (no default
    discernible). Never HEAD when a default exists: computing "merged" against
    HEAD on a topic branch would mark every ancestor of the current work —
    the fleet base, sibling branches, the branch's own remote — as sweepable.
    """
    out, err = git_cmd(repo, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if not err and out.strip():
        return out.strip()
    for cand in ("origin/main", "origin/master", "main", "master"):
        out, err = git_cmd(repo, ["rev-parse", "--verify", "--quiet", cand])
        if not err and out.strip():
            return cand
    return "HEAD"


def _merged_local_branches(repo: Path, target: str) -> list[str]:
    """Local branches fully merged into ``target`` (the default branch), minus the
    current branch, the long-lived mainlines, and ``target`` itself."""
    current = branch_name(repo) or ""
    out, err = git_cmd(repo, ["branch", "--format=%(refname:short)", "--merged", target])
    if err:
        return []
    keep = {current, "main", "master", "develop", target, target.rpartition("/")[2]}
    return sorted(name for line in out.splitlines()
                  if (name := line.strip().lstrip("* ").strip()) and name not in keep)


def _worktree_entries(repo: Path) -> list[WorktreeEntry]:
    """Parsed ``git worktree list --porcelain`` records (including the main worktree)."""
    out, err = git_cmd(repo, ["worktree", "list", "--porcelain"])
    if err or not out.strip():
        return []
    entries: list[WorktreeEntry] = []
    path = ""
    head = ""
    branch: Optional[str] = None
    bare = False
    for line in out.splitlines():
        if line.startswith("worktree "):
            if path:
                entries.append(WorktreeEntry(path=path, branch=branch, head=head, bare=bare))
            path = line[len("worktree "):].strip()
            head = ""
            branch = None
            bare = False
        elif line.startswith("HEAD "):
            head = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            branch = ref.removeprefix("refs/heads/") if ref.startswith("refs/heads/") else ref
        elif line == "bare":
            bare = True
    if path:
        entries.append(WorktreeEntry(path=path, branch=branch, head=head, bare=bare))
    return entries


def _merged_remote_branches(repo: Path, target: str) -> list[str]:
    """Remote branches fully merged into ``target`` (the default branch), minus the
    mainlines/HEAD pointer and ``target`` itself."""
    out, err = git_cmd(repo, ["branch", "-r", "--format=%(refname:short)", "--merged", target])
    if err:
        return []
    skip = {"origin/HEAD", "origin/main", "origin/master", "origin/develop", target}
    # "/" required: refs/remotes/origin/HEAD shortens to a bare "origin", which is
    # a pointer, not a deletable branch.
    return sorted(name for line in out.splitlines()
                  if (name := line.strip()) and name not in skip and "->" not in name and "/" in name)


def sweep_report(repo: Path) -> SweepReport:
    """Everything already merged into the *default* branch (never HEAD): local
    branches, the worktrees those branches are checked out in, remote branches.
    Read-only — the report is rendered and its cleanup PREFILLed, never run."""
    target = default_branch_ref(repo)
    merged_local = tuple(_merged_local_branches(repo, target))
    merged_set = set(merged_local)
    root = find_repo_root(repo)
    root_s = str(root.resolve()) if root else ""
    stale: list[WorktreeEntry] = []
    for wt in _worktree_entries(repo):
        if wt.bare:
            continue
        if root_s and str(Path(wt.path).resolve()) == root_s:
            continue
        if wt.branch and wt.branch in merged_set:
            stale.append(wt)
    return SweepReport(
        merged_local=merged_local,
        stale_worktrees=tuple(stale),
        merged_remote=tuple(_merged_remote_branches(repo, target)),
    )


def sweep_cleanup_commands(report: SweepReport) -> list[str]:
    """The exact cleanup commands for a report, in an order that survives an
    ``&&`` chain: worktree removals FIRST (git refuses ``branch -d`` while the
    branch is checked out in a worktree), then local branch deletes, then remote
    deletes. Paths and names are shell-quoted. Nothing here executes anything."""
    cmds: list[str] = []
    for wt in report.stale_worktrees:
        cmds.append(f"git worktree remove {shlex.quote(wt.path)}")
    for name in report.merged_local:
        cmds.append(f"git branch -d {shlex.quote(name)}")
    for name in report.merged_remote:
        remote, _, branch = name.partition("/")
        if branch:
            cmds.append(f"git push {shlex.quote(remote)} --delete {shlex.quote(branch)}")
    return cmds


@dataclass(frozen=True)
class GitSnapshot:
    """Forge-agnostic point-in-time view of a repo. ``is_repo`` is False for a
    plain (non-git) directory, in which case every other field is empty/None."""

    is_repo: bool
    root: Optional[str] = None
    branch: Optional[str] = None
    status: dict[str, str] = field(default_factory=dict)
    remotes: dict[str, str] = field(default_factory=dict)
    ahead_behind: Optional[tuple[int, int]] = None
    last_commit: Optional[str] = None

    @property
    def changed_count(self) -> int:
        return len(self.status)


def git_snapshot(start: Path) -> GitSnapshot:
    """One-shot snapshot for a path. Resolves the repo root first; if ``start``
    is not in a git repo, returns ``GitSnapshot(is_repo=False)``."""
    root = find_repo_root(start)
    if root is None:
        return GitSnapshot(is_repo=False)
    return GitSnapshot(
        is_repo=True,
        root=str(root),
        branch=branch_name(root),
        status=short_status_map(root),
        remotes=remotes(root),
        ahead_behind=ahead_behind(root),
        last_commit=last_commit_oneline(root),
    )
