"""Walk a project tree, honoring .gitignore + .xliiignore + sane defaults."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from pathspec import PathSpec

# Hard budget on the directory walk that discovers nested .gitignore files.
# Spec construction must never hang: the `xlii init` bulk-upload guard (and
# every tool that builds a spec) may be pointed at ~ or even /. 20k visited
# directories covers any sane project many times over.
_NESTED_GITIGNORE_VISIT_BUDGET = 20_000

DEFAULT_IGNORES = [
    # VCS + xlii internals
    ".git/",
    ".xlii/",
    ".xliiignore",
    # Per-body bearings sidecar (persona turns/.last_turn.json) — never sync.
    ".last_turn.json",
    # Python virtualenvs + caches
    "venv/",
    ".venv/",
    "env/",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".tox/",
    "*.egg-info/",
    # JS / TS build outputs
    "node_modules/",
    ".next/",
    ".nuxt/",
    ".svelte-kit/",
    ".turbo/",
    ".vercel/",
    ".astro/",
    "out/",
    # Generic build / dist / cache dirs
    "dist/",
    "build/",
    "target/",
    ".cache/",
    "coverage/",
    # OS / editor cruft + logs
    ".DS_Store",
    "*.log",
    # Secrets — never upload, ever
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".aws/",
    ".ssh/",
    "*.p12",
    "*.pfx",
    "credentials.json",
    "secrets.*",
    # Tool-local config (other assistants/editors' private state)
    ".claude/",
    ".vscode/",
    ".idea/",
    ".direnv/",
]


# Force-include carve-outs — applied LAST so user .gitignore patterns can't
# accidentally re-ignore files xlii depends on shipping to the Collection.
# Specifically: archived plan investigations under .xlii/plans/ need to flow
# through sync to be RAG-searchable, even though .xlii/ is otherwise ignored. A
# user's `*.md` line in .gitignore would otherwise silently disable plans-sync.
FORCE_INCLUDE_PATTERNS = [
    "!.xlii/plans/",
    "!.xlii/plans/**",
]


def _read_patterns(f: Path) -> list[str]:
    try:
        return [
            line for line in f.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    except OSError:
        return []


def _prefix_gitignore_patterns(patterns: list[str], rel_dir: str) -> list[str]:
    """Rewrite a nested .gitignore's patterns so they apply relative to its
    own directory (git semantics) when matched from the project root."""
    out = []
    for p in patterns:
        neg = p.startswith("!")
        body = p[1:] if neg else p
        if body.startswith("/"):
            scoped = f"{rel_dir}{body}"
        elif "/" in body.rstrip("/"):
            # contains a slash → anchored to the gitignore's directory
            scoped = f"{rel_dir}/{body}"
        else:
            # bare name → matches at any depth below the gitignore's directory
            scoped = f"{rel_dir}/**/{body}"
        out.append(("!" if neg else "") + scoped)
    return out


def load_ignore_spec(project_root: Path, extra_patterns: list[str] | None = None) -> PathSpec:
    patterns = list(DEFAULT_IGNORES)

    # Root-level ignore files. `.xliiignore` is the only name read; a legacy
    # misspelling `.xliignore` is auto-renamed to it (best-effort) on access.
    patterns.extend(_read_patterns(project_root / ".gitignore"))
    try:
        from xlii.legacy_migrate import migrate_ignore_file
        migrate_ignore_file(project_root)
    except Exception:
        # The .gitignore patterns above are already loaded; a failed rename just leaves the misspelled file.
        pass
    patterns.extend(_read_patterns(project_root / ".xliiignore"))

    # Nested .gitignore files (git honors these; so do we). The walk MUST be
    # bounded: callers like the `xlii init` bulk-upload guard rely on spec
    # construction never hanging, even when pointed at ~ or /. So we use
    # os.walk with in-place pruning of already-ignored directories (rglob
    # would enumerate all of node_modules before we could filter it) plus a
    # hard directory-visit budget as a backstop for pathological trees.
    root_spec = PathSpec.from_lines("gitwildmatch", patterns)
    nested: list[tuple[str, Path]] = []
    visits = 0
    for dirpath, dirnames, filenames in os.walk(project_root):
        d = Path(dirpath)
        rel_dir = d.relative_to(project_root).as_posix()
        kept = []
        for name in dirnames:
            sub = f"{name}" if rel_dir == "." else f"{rel_dir}/{name}"
            if not (root_spec.match_file(sub) or root_spec.match_file(sub + "/")):
                kept.append(name)
        dirnames[:] = kept
        visits += 1 + len(dirnames)
        if rel_dir != "." and ".gitignore" in filenames:
            nested.append((rel_dir, d / ".gitignore"))
        if visits >= _NESTED_GITIGNORE_VISIT_BUDGET:
            import sys
            print(
                f"xlii: tree too large — stopped scanning for nested .gitignore "
                f"files after {visits:,} directories (root ignore rules still apply)",
                file=sys.stderr,
            )
            break
    for rel_dir, f in sorted(nested):
        patterns.extend(_prefix_gitignore_patterns(_read_patterns(f), rel_dir))

    if extra_patterns:
        patterns.extend(extra_patterns)
    # Carve-outs go last — user gitignore can't override these.
    patterns.extend(FORCE_INCLUDE_PATTERNS)
    return PathSpec.from_lines("gitwildmatch", patterns)


def is_probably_text(path: Path, sniff_bytes: int = 8192) -> bool:
    """Heuristic: file is text if no NUL byte in the first chunk."""
    try:
        with path.open("rb") as f:
            chunk = f.read(sniff_bytes)
    except OSError:
        return False
    return b"\x00" not in chunk


def walk_project(
    project_root: Path,
    spec: PathSpec,
    *,
    max_bytes: int = 1_000_000,
) -> Iterator[Path]:
    """Yield every file in the project that should be tracked."""
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(project_root)
        rel_posix = rel.as_posix()
        if spec.match_file(rel_posix) or spec.match_file(rel_posix + "/"):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > max_bytes:
            continue
        # xAI Collections rejects empty uploads with "Empty stream received".
        # Skip 0-byte files entirely — they carry no RAG signal anyway.
        if size == 0:
            continue
        if not is_probably_text(path):
            continue
        yield path


def walk_pruned(
    project_root: Path, spec: PathSpec, *, max_files: int = 200_000
) -> Iterator[Path]:
    """Yield non-ignored file Paths, pruning ignored directories *in place*.

    Unlike :func:`walk_paths_only` (rglob then post-filter), this prunes ignored
    directories during ``os.walk`` so it never descends into ``node_modules/`` or
    ``.venv/`` — cheap enough for interactive, human-facing callers like the
    project browser. Symlinked directories are not followed (``os.walk`` default),
    keeping the walk inside the tree. Bounded by ``max_files`` as a backstop.
    """
    count = 0
    for dirpath, dirnames, filenames in os.walk(project_root):
        rel_dir = Path(dirpath).relative_to(project_root).as_posix()
        kept = []
        for name in dirnames:
            sub = name if rel_dir == "." else f"{rel_dir}/{name}"
            if not (spec.match_file(sub) or spec.match_file(sub + "/")):
                kept.append(name)
        dirnames[:] = kept
        for fn in filenames:
            rel = fn if rel_dir == "." else f"{rel_dir}/{fn}"
            if spec.match_file(rel):
                continue
            yield project_root / rel
            count += 1
            if count >= max_files:
                return


def walk_paths_only(project_root: Path, spec: PathSpec) -> Iterator[Path]:
    """Walk every file in the tree, respecting ignores — NO content sniffing,
    NO size cap, NO binary skip.

    For `--snapshot` mode where we just want paths and sizes for grep-based
    structural search. Critical: walk_project's `is_probably_text` opens every
    file to read 8KB, which is O(N) network round-trips on a NAS. This walker
    only stat()s files, so it's roughly as fast as `find`.
    """
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        rel_posix = path.relative_to(project_root).as_posix()
        if spec.match_file(rel_posix) or spec.match_file(rel_posix + "/"):
            continue
        yield path
