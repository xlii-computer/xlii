"""Turn-level checkpoints: object-snapshot, scoped restore, ledger.

Snapshots capture tracked *and* untracked files via a throwaway index (see
proposals/terminal-native-toolkit.md Phase 1). Restore is scoped to
``dirty_paths`` for single-step rewind; full-tree when paths are unknown
(``__rescan__``) or when rewinding multiple write-turns.

``dirty_paths`` in the ledger are **git-repo-root-relative** (not project-root-
relative) so rewind is correct when ``project_root`` is a subdirectory of the
repo.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from xlii.atomicio import write_bytes_atomic
from xlii.git_status import find_repo_root
from xlii.loop_bundle import git_cmd

RESCAN_SENTINEL = "__rescan__"
LEDGER_NAME = "checkpoints.jsonl"


@dataclass
class CheckpointEntry:
    turn_id: int
    ts: str
    tree_sha: str
    label: str
    dirty_paths: list[str]
    rescan: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "CheckpointEntry":
        return cls(
            turn_id=int(data["turn_id"]),
            ts=str(data["ts"]),
            tree_sha=str(data["tree_sha"]),
            label=str(data.get("label") or ""),
            dirty_paths=[str(p) for p in (data.get("dirty_paths") or [])],
            rescan=bool(data.get("rescan")),
        )


def ledger_path(xli_dir: Path) -> Path:
    return Path(xli_dir) / LEDGER_NAME


def repo_root_for(project_root: Path) -> Optional[Path]:
    return find_repo_root(project_root)


def project_dirty_to_repo_paths(
    repo: Path, project_root: Path, paths: set[str]
) -> list[str]:
    """Map project-root-relative dirty paths to git-repo-root-relative paths."""
    repo = repo.resolve()
    proj = project_root.resolve()
    out: list[str] = []
    for rel in paths:
        rel = rel.replace("\\", "/")
        try:
            out.append((proj / rel).resolve().relative_to(repo).as_posix())
        except ValueError:
            continue
    return sorted(out)


def load_ledger(xli_dir: Path) -> list[CheckpointEntry]:
    path = ledger_path(xli_dir)
    if not path.exists():
        return []
    entries: list[CheckpointEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(CheckpointEntry.from_dict(json.loads(line)))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return entries


def _append_ledger(xli_dir: Path, entry: CheckpointEntry) -> None:
    path = ledger_path(xli_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")


def _rewrite_ledger(xli_dir: Path, entries: list[CheckpointEntry]) -> None:
    path = ledger_path(xli_dir)
    if not entries:
        if path.exists():
            path.unlink()
        return
    body = "".join(json.dumps(asdict(e), ensure_ascii=False) + "\n" for e in entries)
    from xlii.atomicio import write_text_atomic

    write_text_atomic(path, body, mode=0o644)


def write_paths(dirty: set[str]) -> set[str]:
    """Real file paths touched by a write-turn (excludes the bash rescan sentinel)."""
    return {p for p in dirty if p != RESCAN_SENTINEL}


def object_snapshot(repo: Path) -> tuple[Optional[str], Optional[str]]:
    """Capture tracked + untracked working-tree state without touching the real index.

    Returns (tree_sha, error). error is None on success.
    """
    head, err = git_cmd(repo, ["rev-parse", "HEAD"])
    if err:
        return None, err

    tmp_index = tempfile.NamedTemporaryFile(prefix="xlii-cp-", delete=False)
    tmp_index.close()
    index_path = tmp_index.name
    env = {**os.environ, "GIT_INDEX_FILE": index_path}
    try:
        _, err = _git_env(repo, ["read-tree", head.strip()], env)
        if err:
            return None, err
        _, err = _git_env(repo, ["add", "-A", "-f"], env)
        if err:
            return None, err
        tree_out, err = _git_env(repo, ["write-tree"], env)
        if err:
            return None, err
        sha = (tree_out or "").strip()
        if not sha:
            return None, "write-tree returned empty sha"
        return sha, None
    finally:
        try:
            os.unlink(index_path)
        except OSError:
            # Temp index cleanup in a finally: the snapshot result above is already computed.
            pass


def _git_env(cwd: Path, args: list[str], env: dict[str, str]) -> tuple[str, Optional[str]]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return "", str(e)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"git exit {proc.returncode}"
        return "", err
    return proc.stdout, None


def _git_cat_blob(repo: Path, tree_sha: str, rel: str) -> tuple[Optional[bytes], Optional[str]]:
    try:
        proc = subprocess.run(
            ["git", "cat-file", "blob", f"{tree_sha}:{rel}"],
            cwd=str(repo),
            capture_output=True,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return None, str(e)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace").strip()
        return None, err or f"git cat-file exit {proc.returncode}"
    return proc.stdout, None


def _tree_file_mode(repo: Path, tree_sha: str, rel: str) -> Optional[int]:
    out, err = git_cmd(repo, ["ls-tree", tree_sha, "--", rel])
    if err or not (out or "").strip():
        return None
    mode_token = (out or "").strip().split()[0]
    try:
        return int(mode_token, 8) & 0o777
    except (ValueError, IndexError):
        return None


def tree_paths(repo: Path, tree_sha: str) -> set[str]:
    out, err = git_cmd(repo, ["ls-tree", "-r", "--name-only", tree_sha])
    if err:
        return set()
    return {ln.strip() for ln in (out or "").splitlines() if ln.strip()}


def diff_against(
    repo: Path, tree_sha: str, paths: Optional[list[str]] = None
) -> tuple[str, Optional[str]]:
    """Diff working tree against ``tree_sha``. Returns (diff_text, error)."""
    args = ["diff", tree_sha]
    if paths:
        args += ["--", *paths]
    out, err = git_cmd(repo, args)
    if err:
        return "", err
    diff = out or ""

    untracked, _ = git_cmd(repo, ["ls-files", "--others", "--exclude-standard"])
    scope = set(paths) if paths else None
    for rel in [ln.strip() for ln in (untracked or "").splitlines() if ln.strip()]:
        if scope is not None and rel not in scope:
            continue
        in_tree, _ = git_cmd(repo, ["ls-tree", tree_sha, "--", rel])
        if (in_tree or "").strip():
            continue
        fp = repo / rel
        if not fp.is_file():
            continue
        try:
            raw = fp.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:8192]:
            diff += f"\ndiff --git a/{rel} b/{rel}\nnew file mode 100644\n(binary file)\n"
            continue
        body = raw.decode("utf-8", errors="replace")
        diff += (
            f"\ndiff --git a/{rel} b/{rel}\nnew file mode 100644\n"
            f"--- /dev/null\n+++ b/{rel}\n"
        )
        diff += "".join(f"+{ln}\n" for ln in body.splitlines())
        if body and not body.endswith("\n"):
            diff += "\n"

    return diff, None


def begin_turn(project_root: Path) -> tuple[Optional[str], Optional[str]]:
    """Snapshot at turn start. Returns (tree_sha, error_message_for_user)."""
    root = repo_root_for(project_root)
    if root is None:
        return None, None
    sha, err = object_snapshot(root)
    if err:
        return None, f"checkpoint snapshot failed: {err}"
    return sha, None


def end_turn(
    xli_dir: Path,
    project_root: Path,
    tree_sha: Optional[str],
    dirty: set[str],
    *,
    label: str = "",
) -> Optional[str]:
    """Record a checkpoint when a write-turn finishes. Returns warning text or None."""
    paths = write_paths(dirty)
    if not paths:
        return None
    root = repo_root_for(project_root)
    if root is None or not tree_sha:
        return None
    repo_paths = project_dirty_to_repo_paths(root, project_root, paths)
    if not repo_paths:
        return None
    ledger = load_ledger(xli_dir)
    entry = CheckpointEntry(
        turn_id=len(ledger) + 1,
        ts=datetime.now(timezone.utc).isoformat(),
        tree_sha=tree_sha,
        label=label.strip(),
        dirty_paths=repo_paths,
        rescan=RESCAN_SENTINEL in dirty,
    )
    _append_ledger(xli_dir, entry)
    return None


def manual_checkpoint(
    xli_dir: Path, project_root: Path, *, label: str = ""
) -> tuple[Optional[CheckpointEntry], Optional[str]]:
    """Force a snapshot now. Returns (entry, error)."""
    root = repo_root_for(project_root)
    if root is None:
        return None, "not a git repository — checkpoints require git"
    sha, err = object_snapshot(root)
    if err or not sha:
        return None, err or "snapshot failed"
    ledger = load_ledger(xli_dir)
    entry = CheckpointEntry(
        turn_id=len(ledger) + 1,
        ts=datetime.now(timezone.utc).isoformat(),
        tree_sha=sha,
        label=label.strip(),
        dirty_paths=[],
    )
    _append_ledger(xli_dir, entry)
    return entry, None


def _restore_file(repo: Path, tree_sha: str, rel: str) -> Optional[str]:
    data, err = _git_cat_blob(repo, tree_sha, rel)
    if err:
        return err
    assert data is not None
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = _tree_file_mode(repo, tree_sha, rel) or 0o644
    write_bytes_atomic(target, data, mode=mode)
    return None


def _delete_path(repo: Path, rel: str) -> None:
    target = repo / rel
    if target.is_file() or target.is_symlink():
        target.unlink(missing_ok=True)
    elif target.is_dir():
        shutil.rmtree(target)


def restore_scoped(repo: Path, tree_sha: str, dirty_paths: set[str]) -> Optional[str]:
    """Restore only repo-root-relative paths touched by the undone turn."""
    paths = write_paths(dirty_paths)
    if not paths:
        return restore_full(repo, tree_sha)
    snap = tree_paths(repo, tree_sha)
    for rel in paths:
        rel = rel.replace("\\", "/")
        if rel in snap:
            err = _restore_file(repo, tree_sha, rel)
            if err:
                return err
        else:
            _delete_path(repo, rel)
    return None


def restore_full(repo: Path, tree_sha: str) -> Optional[str]:
    """Restore every path in the snapshot tree; remove files absent from it."""
    snap = tree_paths(repo, tree_sha)
    for rel in sorted(snap):
        err = _restore_file(repo, tree_sha, rel)
        if err:
            return err

    tracked, _ = git_cmd(repo, ["ls-files"])
    untracked, _ = git_cmd(repo, ["ls-files", "--others", "--exclude-standard"])
    current = {ln.strip() for ln in (tracked or "").splitlines() if ln.strip()}
    current |= {ln.strip() for ln in (untracked or "").splitlines() if ln.strip()}
    for rel in sorted(current - snap):
        _delete_path(repo, rel)
    return None


def _entries_for_rewind(ledger: list[CheckpointEntry], n: int) -> tuple[list[CheckpointEntry], CheckpointEntry]:
    if n < 1 or n > len(ledger):
        raise ValueError(f"no checkpoint {n} back (have {len(ledger)})")
    target = ledger[-n]
    undone = ledger[-n:]
    return undone, target


def rewind_plan(
    ledger: list[CheckpointEntry], n: int = 1
) -> tuple[CheckpointEntry, list[CheckpointEntry], bool]:
    """Return (target_entry, undone_entries, use_full_restore)."""
    undone, target = _entries_for_rewind(ledger, n)
    use_full = n > 1
    if not use_full:
        last = undone[-1]
        if last.rescan or not last.dirty_paths:
            use_full = True
    return target, undone, use_full


def rewind(
    xli_dir: Path,
    project_root: Path,
    n: int = 1,
    *,
    confirm: Callable[[str], bool],
) -> tuple[bool, str]:
    """Rewind ``n`` write-turn checkpoints. Returns (ok, message)."""
    root = repo_root_for(project_root)
    if root is None:
        return False, "not a git repository — cannot rewind"

    ledger = load_ledger(xli_dir)
    if not ledger:
        return False, "no checkpoints recorded"

    try:
        target, undone, use_full = rewind_plan(ledger, n)
    except ValueError as e:
        return False, str(e)

    if use_full:
        diff, err = diff_against(root, target.tree_sha)
    else:
        paths = write_paths(set(undone[-1].dirty_paths))
        diff, err = diff_against(root, target.tree_sha, sorted(paths))
    if err:
        return False, f"cannot preview rewind: {err}"

    preview = (diff or "").strip() or "(no file changes — tree already matches checkpoint)"
    label = target.label or f"turn {target.turn_id}"
    if not confirm(f"Rewind {n} checkpoint(s) → {label}\n\n{preview}"):
        return False, "(cancelled)"

    if use_full:
        err = restore_full(root, target.tree_sha)
    else:
        err = restore_scoped(root, target.tree_sha, set(undone[-1].dirty_paths))
    if err:
        return False, f"rewind failed: {err}"

    _rewrite_ledger(xli_dir, ledger[:-n])
    return True, f"rewound {n} checkpoint(s) — restored to {label}"


def diff_since(
    xli_dir: Path, project_root: Path, n: int = 1
) -> tuple[str, Optional[str]]:
    """Diff working tree against the Nth-from-last checkpoint."""
    root = repo_root_for(project_root)
    if root is None:
        return "", "not a git repository — no diff"
    ledger = load_ledger(xli_dir)
    if not ledger:
        return "", "no checkpoints recorded"
    if n < 1 or n > len(ledger):
        return "", f"no checkpoint {n} back (have {len(ledger)})"
    entry = ledger[-n]
    diff, err = diff_against(root, entry.tree_sha)
    if err:
        return "", err
    return diff or "", None
