"""Shared helpers for built-in agent tool handlers."""

from __future__ import annotations

from pathlib import Path

from xlii.tool_context import (
    MAX_OUTPUT_BYTES,
    ToolContext,
)


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_BYTES:
        return text
    keep = MAX_OUTPUT_BYTES // 2
    return (
        text[:keep]
        + f"\n\n... [truncated {len(text) - MAX_OUTPUT_BYTES} bytes] ...\n\n"
        + text[-keep:]
    )


def _spill_threshold(ctx: "ToolContext") -> int:
    cfg = getattr(ctx, "cfg", None)
    try:
        return int(getattr(cfg, "tool_output_spill_threshold", MAX_OUTPUT_BYTES))
    except (TypeError, ValueError):
        return MAX_OUTPUT_BYTES


def _cap_output(ctx: "ToolContext", text: str) -> str:
    """Cap a tool's output for the model. Output over the spill threshold is
    written WHOLE to .xlii/scratch/tool-output/ and replaced by a head+tail
    preview plus the path + a read_file hint (cursor-workflows.md A2). Falls back
    to lossy inline truncation when spill is disabled (threshold 0) or the tree
    isn't writable, so a turn never breaks on this."""
    threshold = _spill_threshold(ctx)
    if threshold <= 0:
        return _truncate(text)  # spill disabled — old lossy behavior
    if len(text) <= threshold:
        return text
    try:
        from xlii.scratch import write_spill

        ctx.spill_seq += 1
        rel = write_spill(ctx.project.project_root, text, seq=ctx.spill_seq)
    except Exception:
        return _truncate(text)  # read-only tree etc. — degrade, never break
    # Head+tail preview, each at most threshold//2 so the halves never overlap
    # (we only reach here when len(text) > threshold) and the preview is always
    # SMALLER than the spilled original — no inflation, no duplication.
    keep = max(threshold // 2, 1)
    return (
        text[:keep]
        + f"\n\n... [{len(text):,} bytes total — full output spilled to "
        + f"`{rel}`; read a slice with "
        + f'read_file(path="{rel}", offset=N, limit=M)] ...\n\n'
        + text[-keep:]
    )


def _resolve_in_project(ctx: ToolContext, relpath: str) -> Path:
    """Resolve a relpath, refusing anything that escapes the project root."""
    root = ctx.project.project_root.resolve()
    target = (root / relpath).resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"path escapes project root: {relpath}")
    return target


def _files_mount(ctx: ToolContext) -> str:
    """Remote Files address for this desk, or ``""`` when tools should stay local."""
    try:
        from xlii.desk_files import files_mount_address

        return files_mount_address(ctx.project) or ""
    except Exception:
        return ""


def _join_mount(base: str, rel: str) -> str:
    text = (rel or "").strip()
    if not text or text in (".", "./"):
        return base
    parts = Path(text).parts
    if ".." in parts or (parts and parts[0] == "/"):
        raise ValueError(f"path escapes Files mount: {rel}")
    return f"{base.rstrip('/')}/{text.lstrip('/')}"


def _iter_remote_files(base: str, *, max_files: int = 2000):
    """Yield ``(address, relpath)`` for leaves under a VFS mount."""
    from xlii.addressing import vfs_list

    n = 0
    stack: list[tuple[str, str]] = [(base, "")]
    while stack:
        addr, rel = stack.pop()
        try:
            nodes = vfs_list(addr)
        except Exception:
            continue
        for node in nodes:
            name = node.name or ""
            if name in (".", "..") or name.startswith("."):
                continue
            child_rel = f"{rel}/{name}" if rel else name
            if node.kind == "container":
                stack.append((node.address, child_rel))
                continue
            n += 1
            if n > max_files:
                return
            yield node.address, child_rel


def _mark_dirty(ctx: ToolContext, path: Path) -> None:
    root = ctx.project.project_root.resolve()
    resolved = path.resolve()
    if root != resolved and root not in resolved.parents:
        # Out-of-root write (allow-domain escape under state_dir_override):
        # nothing to sync — the plans domain is state, not project content.
        return
    ctx.dirty_paths.add(resolved.relative_to(root).as_posix())
