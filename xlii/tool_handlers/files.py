"""File, directory, and project-search tool handlers."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any, Optional

from xlii.tool_context import (
    ToolContext,
    ToolResult,
)
from xlii.tool_gating import _debug_marker_violation, _test_path_locked, write_path_refusal

from ._common import (
    _cap_output,
    _files_mount,
    _iter_remote_files,
    _join_mount,
    _mark_dirty,
    _resolve_in_project,
)


def _should_write_mount(ctx: ToolContext, relpath: str) -> bool:
    """App writes follow the Files mount; ``.xlii/`` and plan domains stay local."""
    if ctx.write_allow:
        return False
    from xlii.desk_files import is_xlii_relpath, writes_follow_files_mount

    if is_xlii_relpath(relpath or ""):
        return False
    return writes_follow_files_mount(ctx.project)


def _vfs_write_text(mount: str, rel: str, text: str) -> None:
    from xlii.addressing import vfs_mkdir, vfs_write

    addr = _join_mount(mount, rel)
    try:
        vfs_write(addr, text.encode("utf-8"))
        return
    except Exception:
        parts = Path(rel).parts
        acc: list[str] = []
        for part in parts[:-1]:
            acc.append(part)
            try:
                vfs_mkdir(_join_mount(mount, "/".join(acc)))
            except Exception:
                # Parent may already exist or the hop may create it on write.
                # vfs_write below is authoritative and raises if the write fails.
                pass
        vfs_write(addr, text.encode("utf-8"))


def _resolve_write_target(ctx: ToolContext, relpath: str) -> Path:
    """Resolve a WRITE target: inside project_root as usual, or — only when the
    profile carries an allow domain (plan-write-domain P0) — strictly inside one
    of the ``write_allow`` roots.

    The escape exists because the plans domain follows ``xli_dir``, which
    ``state_dir_override`` (preview mode) or a symlinked ``.xlii`` can place
    OUTSIDE project_root; without it every plan write would be refused as a
    project escape. It is safe: ``write_allow`` comes from the mode controller,
    never the model, and the read tools / ``_resolve_in_project`` itself stay
    project-confined."""
    try:
        return _resolve_in_project(ctx, relpath)
    except ValueError:
        if not ctx.write_allow:
            raise
        p = Path(relpath)
        if not p.is_absolute():
            p = ctx.project.project_root / relpath
        resolved = p.resolve()
        for root in ctx.write_allow:
            if resolved != Path(root) and resolved.is_relative_to(root):
                return resolved
        raise


def _notify_if_plan(ctx: ToolContext, path: Path) -> None:
    """Fire the plan listener when a file write landed in the plans domain —
    the planner rewriting current.md must repaint the strip/pane exactly like
    a plan_check does (plan-surface T1). Never fails the write."""
    try:
        plans_dir = (Path(ctx.project.xli_dir) / "plans").resolve()
        if path.resolve().parent == plans_dir:
            from xlii.plan_ops import notify_plan_changed

            notify_plan_changed()
    except Exception:
        # Plan-change notification is best-effort and must not block file writes.
        pass


def t_read_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    mount = _files_mount(ctx)
    if mount:
        from xlii.addressing import vfs_read

        rel = args.get("path") or ""
        try:
            raw = vfs_read(_join_mount(mount, rel))
        except Exception as e:
            return ToolResult(f"file not found: {rel} ({e})", is_error=True)
        text = raw.decode("utf-8", errors="replace")
        offset = int(args.get("offset", 0))
        limit = int(args.get("limit", 0)) or None
        lines = text.splitlines()
        if offset or limit:
            end = offset + limit if limit else len(lines)
            lines = lines[offset:end]
        numbered = "\n".join(f"{i+offset+1:6}\t{ln}" for i, ln in enumerate(lines))
        return ToolResult(_cap_output(ctx, numbered))
    path = _resolve_in_project(ctx, args["path"])
    if not path.exists():
        return ToolResult(f"file not found: {args['path']}", is_error=True)
    if not path.is_file():
        return ToolResult(f"not a file: {args['path']}", is_error=True)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return ToolResult(f"read error: {e}", is_error=True)
    offset = int(args.get("offset", 0))
    limit = int(args.get("limit", 0)) or None
    lines = text.splitlines()
    if offset or limit:
        end = offset + limit if limit else len(lines)
        lines = lines[offset:end]
    numbered = "\n".join(f"{i+offset+1:6}\t{ln}" for i, ln in enumerate(lines))
    return ToolResult(_cap_output(ctx, numbered))


def t_write_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if _test_path_locked(ctx, args["path"]):
        return ToolResult(
            "refused: test files are locked during autonomous loop (cross-vendor judges)",
            is_error=True,
        )
    if ctx.debug_instrument:
        return ToolResult(
            "refused: debug Instrument phase adds marked log lines to existing files "
            "via edit_file — no new files. /debug next to reach the Fix phase.",
            is_error=True,
        )
    if _should_write_mount(ctx, args["path"]):
        mount = _files_mount(ctx)
        try:
            _vfs_write_text(mount, args["path"], args["content"])
        except Exception as e:
            return ToolResult(f"write error: {args['path']} ({e})", is_error=True)
        return ToolResult(f"wrote {args['path']} ({len(args['content'])} bytes)")
    path = _resolve_write_target(ctx, args["path"])
    refusal = write_path_refusal(ctx, path)
    if refusal is not None:
        return ToolResult(refusal, is_error=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args["content"], encoding="utf-8")
    _mark_dirty(ctx, path)
    _notify_if_plan(ctx, path)
    return ToolResult(f"wrote {args['path']} ({len(args['content'])} bytes)")


def t_edit_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if _test_path_locked(ctx, args["path"]):
        return ToolResult(
            "refused: test files are locked during autonomous loop (cross-vendor judges)",
            is_error=True,
        )
    old = args["old_string"]
    new = args["new_string"]
    replace_all = bool(args.get("replace_all", False))
    if _should_write_mount(ctx, args["path"]):
        from xlii.addressing import vfs_read

        mount = _files_mount(ctx)
        rel = args["path"]
        try:
            text = vfs_read(_join_mount(mount, rel)).decode("utf-8")
        except Exception as e:
            return ToolResult(f"file not found: {rel} ({e})", is_error=True)
        if old not in text:
            return ToolResult("old_string not found in file", is_error=True)
        if not replace_all and text.count(old) > 1:
            return ToolResult(
                f"old_string is not unique ({text.count(old)} occurrences); "
                "use replace_all=true or include more context",
                is_error=True,
            )
        viol = _debug_marker_violation(ctx, old, new)
        if viol:
            return ToolResult(viol, is_error=True)
        new_text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        try:
            _vfs_write_text(mount, rel, new_text)
        except Exception as e:
            return ToolResult(f"write error: {rel} ({e})", is_error=True)
        return ToolResult(f"edited {rel}")
    path = _resolve_write_target(ctx, args["path"])
    refusal = write_path_refusal(ctx, path)
    if refusal is not None:
        return ToolResult(refusal, is_error=True)
    if not path.exists():
        return ToolResult(f"file not found: {args['path']}", is_error=True)
    text = path.read_text(encoding="utf-8")
    if old not in text:
        return ToolResult("old_string not found in file", is_error=True)
    if not replace_all and text.count(old) > 1:
        return ToolResult(
            f"old_string is not unique ({text.count(old)} occurrences); "
            "use replace_all=true or include more context",
            is_error=True,
        )
    viol = _debug_marker_violation(ctx, old, new)
    if viol:
        return ToolResult(viol, is_error=True)
    new_text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    path.write_text(new_text, encoding="utf-8")
    _mark_dirty(ctx, path)
    _notify_if_plan(ctx, path)
    return ToolResult(f"edited {args['path']}")

def t_list_dir(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    mount = _files_mount(ctx)
    if mount:
        from xlii.addressing import vfs_list

        rel = args.get("path", ".")
        try:
            nodes = vfs_list(_join_mount(mount, rel))
        except Exception as e:
            return ToolResult(f"not a directory: {rel} ({e})", is_error=True)
        entries = []
        for node in sorted(nodes, key=lambda n: n.name or ""):
            marker = "/" if node.kind == "container" else ""
            entries.append(f"{node.name}{marker}")
        return ToolResult("\n".join(entries) or "(empty)")
    path = _resolve_in_project(ctx, args.get("path", "."))
    if not path.is_dir():
        return ToolResult(f"not a directory: {args.get('path', '.')}", is_error=True)
    entries = []
    for child in sorted(path.iterdir()):
        marker = "/" if child.is_dir() else ""
        entries.append(f"{child.name}{marker}")
    return ToolResult("\n".join(entries) or "(empty)")


def _iter_searchable_files(ctx: ToolContext):
    """Walk project files, skipping everything the sync ignore rules skip
    (.git, venvs, node_modules, secrets, …). Stops glob/grep from burning the
    30k output budget on noise the model should never see."""
    from xlii.ignore import load_ignore_spec
    root = ctx.project.project_root
    spec = load_ignore_spec(root, ctx.project.extra_ignores)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if spec.match_file(rel) or spec.match_file(rel + "/"):
            continue
        yield path, rel


def t_glob(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    pattern = args["pattern"]
    mount = _files_mount(ctx)
    if mount:
        matches = sorted(
            rel for _addr, rel in _iter_remote_files(mount)
            if fnmatch.fnmatch(rel, pattern)
        )
    else:
        matches = sorted(
            rel for _p, rel in _iter_searchable_files(ctx)
            if fnmatch.fnmatch(rel, pattern)
        )
    if not matches:
        return ToolResult("(no matches)")
    return ToolResult(_cap_output(ctx, "\n".join(matches)))


def t_grep(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    pattern = args["pattern"]
    glob_pat = args.get("glob")
    case_insensitive = bool(args.get("case_insensitive", False))
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        return ToolResult(f"invalid regex: {e}", is_error=True)
    out_lines: list[str] = []
    mount = _files_mount(ctx)
    if mount:
        from xlii.addressing import vfs_read

        for addr, rel in _iter_remote_files(mount):
            if glob_pat and not fnmatch.fnmatch(rel, glob_pat):
                continue
            try:
                text = vfs_read(addr).decode("utf-8", errors="replace")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    out_lines.append(f"{rel}:{i}: {line.rstrip()}")
    else:
        for path, rel in _iter_searchable_files(ctx):
            if glob_pat and not fnmatch.fnmatch(rel, glob_pat):
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            out_lines.append(f"{rel}:{i}: {line.rstrip()}")
            except OSError:
                continue
    if not out_lines:
        return ToolResult("(no matches)")
    return ToolResult(_cap_output(ctx, "\n".join(out_lines)))


def _wiki_search_block(ctx: ToolContext, query: str, limit: int) -> Optional[str]:
    """A trust-tagged result block for the project's wiki pages, or None when there are no hits.

    The wiki is *always local* (never in Collections yet), so this runs regardless of the backend
    and is merged into every path below — the project's distilled semantic memory is retrievable in
    every mode. ✓ pages are trusted; **? UNVERIFIED** pages are recorded-but-unchecked and flagged
    loudly (same posture as attaching one)."""
    xli_dir = getattr(ctx.project, "xli_dir", None)
    if xli_dir is None:
        return None
    from xlii.wiki import search_pages

    hits = search_pages(xli_dir, query, limit)
    if not hits:
        return None
    lines = []
    for i, (name, snippet, score, verified) in enumerate(hits, 1):
        mark = "✓ verified" if verified else "? UNVERIFIED"
        clean = " ".join(snippet.split())
        lines.append(f"[W{i}] wiki://{name}  ({mark}, bm25={score:.2f})\n{clean}")
    return ("=== wiki — project semantic memory (✓ trusted · ? UNVERIFIED = recorded but "
            "unchecked) ===\n\n" + "\n\n".join(lines))


def t_search_project(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    query = args["query"]
    limit = int(args.get("limit", 10))
    mode = args.get("retrieval_mode", ctx.cfg.retrieval_mode)

    # Wiki pages are always local — search them once and merge into whatever the file/Collections
    # backend returns, so distilled semantic memory surfaces in every mode.
    wiki_block = _wiki_search_block(ctx, query, limit)

    def _finish(text: str, *, is_error: bool = False) -> ToolResult:
        if not wiki_block:
            return ToolResult(_cap_output(ctx, text), is_error=is_error)
        # The wiki has hits: on a primary error, serve the wiki alone (not an error); otherwise
        # append it below the primary results.
        body = wiki_block if is_error else (text + "\n\n---\n\n" + wiki_block)
        return ToolResult(_cap_output(ctx, body))

    # search_project reaches the project's OWN Collection only (menu-families
    # §5: personas are sealed islands; the old /attach-ref extra-collections
    # leg is gone). Keep the falsy filter — local-only projects have an empty
    # collection_id and must still hit the LocalBackend branch below.
    collection_ids = [ctx.project.collection_id] if ctx.project.collection_id else []
    if not collection_ids:
        # Local-only project: serve from LocalBackend (protocol) first; fall
        # back to the LocalIndex floor text when the backend store is empty.
        from xlii.storage import local_search_text
        from xlii.storage_backend import LocalBackend

        backend = LocalBackend(ctx.project, cfg=getattr(ctx, "cfg", None))
        hits = backend.search(query, limit=limit)
        if hits:
            from xlii.fabric import recall_label

            out = []
            for i, h in enumerate(hits, 1):
                header = f"[{i}] {recall_label(h.source)}" + (
                    f"  (score={h.score:.3f})" if isinstance(h.score, float) else ""
                )
                # LocalBackend returns full content; keep snippets lean.
                body = " ".join(h.text.split())
                if len(body) > 240:
                    body = body[:239] + "…"
                out.append(header + "\n" + body)
            return _finish(
                "\n\n---\n\n".join(out)
                + "\n\n[searched the LOCAL backend — offline/local mode]"
            )
        text = local_search_text(ctx.project, query, limit)
        if text is None:
            return _finish("(no local index yet — run /sync once to build it)", is_error=True)
        return _finish(text)
    # Primary retrieval through ComposedBackend: CollectionsBackend + LocalIndex
    # floor. Writes stay on the primary; search degrades to the floor on error
    # (OQ 5 — replaces the call-site try/except). Banner text is byte-preserved.
    from xlii.storage import local_search_text
    from xlii.storage_backend import CollectionsBackend, compose_with_local_floor

    composed = compose_with_local_floor(
        CollectionsBackend(ctx.clients, collection_ids), ctx.project
    )
    hits = composed.search(query, limit=limit, retrieval_mode=mode)
    if composed.degraded_from is not None:
        e = composed.degraded_from
        text = local_search_text(ctx.project, query, limit)
        if text is not None:
            return _finish(
                f"[remote search failed: {type(e).__name__} — degraded to local index]\n\n" + text
            )
        return _finish(f"search failed: {e}", is_error=True)
    if not hits:
        return _finish("(no results)")
    from xlii.fabric import recall_label

    out = []
    for i, h in enumerate(hits, 1):
        header = f"[{i}] {recall_label(h.source)}" + (
            f"  (score={h.score:.3f})" if isinstance(h.score, float) else ""
        )
        out.append(header + "\n" + h.text.strip())
    return _finish("\n\n---\n\n".join(out))
