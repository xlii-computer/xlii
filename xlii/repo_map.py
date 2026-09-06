"""The repo-map engine — the project's shape as deterministic markdown.

One engine, many thin clients (client-#1 doctrine): the agent tool ``map``, the
REPL ``/map``, the CLI ``xlii map``, and the ``map://`` provider all call
:func:`build_map` and render the same bytes for the same scope — no surface
grows its own walker. The ``map://`` listing rides :func:`list_children`, which
derives from the SAME file source the engine maps, so the pane view can never
disagree with the engine about what exists.

Locked decisions (proposals/map.md):

1. **Regenerate on read, never cache.** Generation is subsecond; a stale shape
   map is worse than none. No snapshot store.
2. **Deterministic, diffable output.** Sorted entries, relative paths only, no
   timestamps — two runs on the same tree are byte-identical (the deferred
   journal-diff hook depends on this).
3. **Not an index.** Orientation, not lookup — the tool description points to
   grep / search_project for finding things.
4. **Budget policy only, no ranking.** ``max_bytes`` is a HARD cap — it wins
   over everything, including the truncation marker itself. Over budget,
   symbol detail drops largest-files-first (costed once, arithmetically — no
   quadratic re-render), then the deepest tree levels; the output ALWAYS ends
   with an explicit truncation marker carrying honest counts. In the
   pathological case of a cap smaller than the marker, the output is the
   truncated marker alone — loud even then. Silent truncation is the one
   unforgivable failure mode.

A nonexistent root or scope raises ``ValueError`` — an empty map for a path
that isn't there would read as "the directory is empty", which is a lie.
"""

from __future__ import annotations

import ast
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Directories the os.walk fallback prunes (git ls-files gets this free via .gitignore).
PRUNE_DIRS = {".git", "__pycache__", "node_modules", ".venv"}

DEFAULT_MAX_BYTES = 16_384

# The loud-truncation invariant: this marker ends every over-budget map, with
# both counts honest — files elided from the tree vs. files stripped to bare
# tree entries. Tests and surfaces key on the stem before the first brace.
TRUNCATION_MARKER = (
    "… truncated ({elided} files elided, {stripped} stripped to tree entries "
    "— scope with map(path=…))"
)


# --------------------------------------------------------------------------- #
#  Per-file records
# --------------------------------------------------------------------------- #


@dataclass
class _FileEntry:
    rel: str                       # posix path relative to the rendered scope
    abspath: Path                  # where the file actually lives
    size: int = 0
    is_python: bool = False
    note: str = ""                 # e.g. "(unparseable)"
    doc: str = ""                  # module docstring first line
    symbols: list[str] = field(default_factory=list)   # rendered symbol lines

    @property
    def depth(self) -> int:
        return self.rel.count("/")


def _first_docline(node: "ast.AST") -> str:
    doc = ast.get_docstring(node, clean=True)
    if not doc:
        return ""
    return doc.strip().splitlines()[0].strip()


def _signature(fn: "ast.FunctionDef | ast.AsyncFunctionDef") -> str:
    """``name(args) -> ret`` with defaults/annotations as written (ast.unparse)."""
    try:
        args = ast.unparse(fn.args)
    except Exception:
        args = "…"
    ret = ""
    if fn.returns is not None:
        try:
            ret = f" -> {ast.unparse(fn.returns)}"
        except Exception:
            ret = ""
    prefix = "async def" if isinstance(fn, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {fn.name}({args}){ret}"


def _class_header(cls: "ast.ClassDef") -> str:
    bases = []
    for b in cls.bases:
        try:
            bases.append(ast.unparse(b))
        except Exception:
            bases.append("…")
    return f"class {cls.name}({', '.join(bases)})" if bases else f"class {cls.name}"


def _symbol_lines(tree: "ast.Module") -> list[str]:
    """Rendered top-level classes/functions with full signatures + docstring
    first lines, nested one level (methods under their class)."""
    lines: list[str] = []

    def _emit(node: "ast.AST", indent: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.append(f"{indent}- `{_signature(node)}`")
            doc = _first_docline(node)
            if doc:
                lines.append(f"{indent}  — {doc}")
        elif isinstance(node, ast.ClassDef):
            lines.append(f"{indent}- `{_class_header(node)}`")
            doc = _first_docline(node)
            if doc:
                lines.append(f"{indent}  — {doc}")
            if not indent:  # one level of nesting only
                for child in node.body:
                    _emit(child, "    ")

    for node in tree.body:
        _emit(node, "")
    return lines


def _parse_python(path: Path, entry: _FileEntry) -> None:
    """Fill the symbol layer for one .py file. Parse failure → a ``(unparseable)``
    note on the tree entry — never raises."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (SyntaxError, ValueError, OSError, RecursionError):
        entry.note = "(unparseable)"
        return
    entry.doc = _first_docline(tree)
    entry.symbols = _symbol_lines(tree)


# --------------------------------------------------------------------------- #
#  Tree layer — ONE file source for every view
# --------------------------------------------------------------------------- #


def _git_files(root: Path) -> "Optional[list[str]]":
    """Relative posix paths from ``git ls-files`` (gitignore respected for free),
    or None when ``root`` is not inside a git work tree.

    ``-z`` + ``core.quotepath=off`` so non-ASCII/quote/backslash filenames come
    back verbatim (NUL-separated) instead of C-quoted — a C-quoted name would
    fail its stat and silently vanish from the map, violating the
    loud-truncation invariant. Output is decoded with ``surrogateescape``,
    matching how Python paths carry undecodable filesystem bytes."""
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=off", "ls-files", "-z",
             "--cached", "--others", "--exclude-standard"],
            cwd=root, capture_output=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.decode("utf-8", errors="surrogateescape")
    return [f for f in out.split("\0") if f.strip()]


def _walk_files(root: Path) -> "list[str]":
    """os.walk fallback with the usual prunes."""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in PRUNE_DIRS)
        rel_dir = Path(dirpath).relative_to(root)
        for name in filenames:
            rel = (rel_dir / name).as_posix()
            out.append(rel[2:] if rel.startswith("./") else rel)
    return out


def _source_files(root: Path) -> "list[str]":
    """THE file source every map view shares (engine + ``list_children``):
    ``git ls-files`` when in a repo, pruned ``os.walk`` otherwise — including
    when git succeeds but lists *nothing* (a root that is itself gitignored
    inside an enclosing repo must still map, not come back empty)."""
    files = _git_files(root)
    if not files:
        files = _walk_files(root)
    return sorted(set(files))


def _resolve_scope(root: Path, scope: "Optional[str]") -> "tuple[Path, str]":
    """Resolve ``scope`` inside ``root``; reject traversal outside and a scope
    that doesn't exist (an empty map for a missing path would read as "empty
    directory"). Returns (absolute scope dir-or-file, relative posix prefix —
    '' for the root)."""
    root = root.resolve()
    if not scope or scope in (".", "./"):
        return root, ""
    target = (root / scope).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"scope escapes the project root: {scope!r}")
    if not target.exists():
        raise ValueError(f"no such path in the project: {scope!r}")
    rel = target.relative_to(root).as_posix()
    return target, "" if rel == "." else rel


def list_children(root: Path, rel: str = "") -> "list[tuple[str, bool, Optional[int]]]":
    """Immediate children of ``rel`` under ``root`` as ``(name, is_dir, size)``
    (size ``None`` for dirs), derived from the SAME file source the engine maps
    (client-#1: the ``map://`` pane must never disagree with :func:`build_map`
    about what exists — gitignored paths neither browse nor map). Containers
    first, then case-insensitive by name. Raises ``ValueError`` for a scope
    outside the root or one that doesn't exist, like the engine."""
    root = Path(root).resolve()
    _scope_abs, prefix = _resolve_scope(root, rel or None)
    dirs: set[str] = set()
    leaves: dict[str, Optional[int]] = {}
    for f in _source_files(root):
        sub = f
        if prefix:
            if not f.startswith(prefix + "/"):
                continue
            sub = f[len(prefix) + 1:]
        head, sep, _rest = sub.partition("/")
        if sep:
            dirs.add(head)
        else:
            try:
                leaves[head] = (root / f).stat().st_size
            except OSError:
                leaves[head] = None
    out = [(d, True, None) for d in dirs] + [(n, False, s) for n, s in leaves.items()]
    return sorted(out, key=lambda t: (not t[1], t[0].lower()))


# --------------------------------------------------------------------------- #
#  The engine
# --------------------------------------------------------------------------- #


def build_map(
    root: Path,
    *,
    scope: "Optional[str]" = None,     # subtree filter ("xlii/tui")
    depth: "Optional[int]" = None,     # tree depth cap; None = full
    detail: str = "symbols",           # "files" (tree only) | "symbols" (+ classes/defs)
    max_bytes: int = DEFAULT_MAX_BYTES,  # hard output budget (~4K tokens)
) -> str:
    """The project's shape as deterministic markdown — see the module docstring.

    Raises ``ValueError`` for a root that isn't a directory, a scope outside
    ``root`` or one that doesn't exist, or an unknown ``detail``.
    """
    if detail not in ("files", "symbols"):
        raise ValueError(f"detail must be 'files' or 'symbols', not {detail!r}")
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"map root is not a directory: {root}")
    _scope_abs, prefix = _resolve_scope(root, scope)

    # Scope filter (relative to root; a scope of "pkg" keeps "pkg/…" only) and
    # depth cap, both applied to the SCOPED relative path so depth means "levels
    # below what you asked for".
    entries: list[_FileEntry] = []
    for rel in _source_files(root):
        if prefix:
            if rel != prefix and not rel.startswith(prefix + "/"):
                continue
            scoped = rel[len(prefix) + 1:] if rel != prefix else Path(rel).name
        else:
            scoped = rel
        if depth is not None and scoped.count("/") >= max(depth, 1):
            continue
        path = root / rel
        try:
            size = path.stat().st_size
        except OSError:
            continue  # racy delete / dangling symlink — not part of the shape
        entries.append(_FileEntry(rel=scoped, abspath=path, size=size,
                                  is_python=rel.endswith(".py")))

    if detail == "symbols":
        for e in entries:
            if e.is_python:
                _parse_python(e.abspath, e)

    title = f"# map: {prefix or '.'}"
    return _render(title, entries, max_bytes=max_bytes)


def _entry_lines(e: _FileEntry, *, with_symbols: bool) -> "list[str]":
    note = f" {e.note}" if e.note else ""
    head = f"- **{e.rel}**{note}  ({e.size:,} B)"
    lines = [head]
    if with_symbols:
        if e.doc:
            lines.append(f"  — {e.doc}")
        for s in e.symbols:
            lines.append("  " + s)
    return lines


def _render(title: str, entries: "list[_FileEntry]", *, max_bytes: int) -> str:
    """Assemble under the HARD byte cap — the cap wins over everything.

    Over budget: drop symbol detail largest-files-first (per-file byte costs
    are computed once and subtracted arithmetically — one re-render, not one
    per dropped file), then trim the deepest tree levels; the output always
    ends with the truncation marker carrying honest elided/stripped counts.
    Degenerate caps: the body is hard-sliced to make room for the marker, and
    a cap smaller than the marker itself returns the truncated marker alone —
    loud truncation survives even there."""

    def _marker(elided: int, stripped: int) -> str:
        return TRUNCATION_MARKER.format(elided=elided, stripped=stripped)

    # Per-entry rendered lines and symbol byte-costs, computed ONCE. For a
    # non-empty document, body bytes == sum(len(line)+1) over all its lines
    # ("\n".join + trailing "\n"), so the budget arithmetic below mirrors the
    # renderer exactly.
    full_lines: dict[str, list[str]] = {}
    costs: dict[str, int] = {}
    total = len(title.encode()) + 1 + 1  # title line + the blank spacer line
    for e in entries:
        lines = _entry_lines(e, with_symbols=True)
        full_lines[e.rel] = lines
        total += sum(len(ln.encode()) + 1 for ln in lines)
        extra = sum(len(ln.encode()) + 1 for ln in lines[1:])
        if extra:
            costs[e.rel] = extra

    def _assemble(dropped: "set[str]", max_depth: "Optional[int]") -> str:
        lines = [title, ""]
        elided = stripped = 0
        for e in entries:
            if max_depth is not None and e.depth > max_depth:
                elided += 1
                continue
            strip = e.rel in dropped
            if strip and e.rel in costs:
                stripped += 1
            lines.extend(full_lines[e.rel][:1] if strip else full_lines[e.rel])
        body = "\n".join(lines).rstrip() + "\n"
        if dropped or max_depth is not None:
            body += "\n" + _marker(elided, stripped) + "\n"
        return body

    if total <= max_bytes:
        return _assemble(set(), None)

    # Pass 1 — drop symbol detail, largest files first (arithmetic, then ONE render).
    dropped: set[str] = set()
    running = total
    for e in sorted(entries, key=lambda x: (-x.size, x.rel)):
        cost = costs.get(e.rel)
        if cost is None:
            continue
        dropped.add(e.rel)
        running -= cost
        if running + len(_marker(0, len(dropped)).encode()) + 2 <= max_bytes:
            break
    out = _assemble(dropped, None)
    if len(out.encode()) <= max_bytes:
        return out

    # Pass 2 — tree-only, trimming the deepest levels (≤ depth+2 renders).
    dropped = set(costs)
    deepest = max((e.depth for e in entries), default=0)
    for cap in (None, *range(deepest - 1, -2, -1)):
        out = _assemble(dropped, cap)
        if len(out.encode()) <= max_bytes:
            return out

    # Degenerate cap: even title + marker overflows. The cap STILL wins — slice
    # the body to make room for the marker; below marker size, return the
    # truncated marker alone.
    marker = _marker(len(entries), 0)
    tail = "\n" + marker + "\n"
    if len(tail.encode()) >= max_bytes:
        return (marker + "\n").encode()[:max_bytes].decode("utf-8", errors="ignore")
    keep = max_bytes - len(tail.encode())
    return out.encode()[:keep].decode("utf-8", errors="ignore") + tail
