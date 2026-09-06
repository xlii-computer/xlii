from __future__ import annotations

from pathlib import Path
from typing import Any

_MAX_ROWS = 500  # wire-size bound per pane snapshot
_MAX_INLINE_IMAGE_BYTES = 5 * 1024 * 1024
# The outbox drain's classification (serve_face._IMAGE_SUFFIXES) — only an image
# suffix rides inline as b64; everything else is handed over as a path.
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_TURN_CONTEXT_CAP = 16_000
_CAPTION = "caption"  # the non-selectable row kind (section headers, notes)
_SUBPATH_SCHEMES = frozenset({"project", "conv", "config", "git", "map"})

# Workbench pane name → the root address the slot opens at. Panes not listed
# here (planned placeholders, e.g. the research row's "sources") are skipped
# at mount — a type whose pane set is mostly placeholders is a label, not a
# type (workbench.py's PLANNED_PANES note).
def _root_addresses(state: Any) -> dict[str, str]:
    from xlii.desk_files import files_address

    project = getattr(state, "project", None)
    files = files_address(project, shell_cwd=getattr(state, "shell_cwd", None))
    return {
        "explorer": files,
        "git": "git://",
        "tasks": "tasks://",
        "plan": "plan://",
        "bookmarks": "mark://",
        "wiki": "wiki://",
        "home": "home://",
        "projects": "projects://",
        "results": "results://",
        "sources": "sources://",
        "jobs": "jobs://",
        "farm": "farm://",
        "menu": "menu://",
        "artifacts": "artifacts://",
        "canvas": "canvas://",
        "locker": "locker://",
        "skills": "skills://",
        "plugins": "plugins://",
        "history": "history://",
        "config": "faceconfig://",
        "taskmake": "taskmake://",
        "pluginmake": "pluginmake://",
        "pluginform": "pluginform://",
        "bindmake": "bindmake://",
        "gigmake": "gigmake://",
        "remotemake": "remotemake://",
        "jidmake": "jidmake://",
        "install": "install://",
        # TUI doorway schemes that already have VFS providers (explorer fallback).
        "docs": "docs://",
        "gigwork": "gigwork://",
        "remote": "remote://",
    }


# Human labels for the face Keep menu + in-panel dropdown + /panel targets.
FACE_PANE_LABELS: dict[str, str] = {
    "projects": "Projects / switch",
    "home": "Home Hub",
    "explorer": "Files",
    "git": "Git",
    "tasks": "Tasks",
    "jobs": "Jobs",
    "farm": "Farm",
    "plan": "Plans",
    "bookmarks": "Bookmarks",
    "wiki": "Wiki",
    "docs": "Docs",
    "locker": "Attachments",
    "skills": "Skills",  # plural — matches skills:// and /panel skills
    "plugins": "Plugins",  # catalog + subscribe (was top menubar; TUI panel parity)
    "history": "Input history",  # same panel as TUI /history (not a menubar submenu)
    "config": "Config / session",
    "taskmake": "Task maker",
    "pluginmake": "Plugin maker",
    "bindmake": "Bind chrome",
    "gigmake": "Gigwork",
    "remotemake": "Remotes",
    "jidmake": "XMPP addresses",
    "install": "Install node",
    "sources": "Kept sources",
    "results": "Lookups",
    "artifacts": "Artifacts",
    "canvas": "Canvas",
    "menu": "Commands",
    "gigwork": "Gigwork",
    "remote": "Remotes",
    "pdf": "PDF",
}

# Slot-bodied HTML (makers now; splash later). Occupancy like PDF:
# listing | form, or stream parked in the skinny side.
_HTML_SLOT_PANES = frozenset({
    "taskmake", "pluginmake", "pluginform", "bindmake", "gigmake", "remotemake",
    "jidmake", "install",
})

# Panels + slot-dropdown order. Packs *filter* this list; they do not
# reorder it. A door that exists in more than one pack stays put.
PANEL_ORDER: tuple[str, ...] = tuple(FACE_PANE_LABELS)
_PANEL_RANK = {pid: i for i, pid in enumerate(PANEL_ORDER)}


def _ordered_view_ids(ids: "list[str]") -> list[str]:
    """Stable order: PANEL_ORDER first, then unknown extras by name."""
    seen: list[str] = []
    for pid in ids:
        if pid and pid not in seen:
            seen.append(pid)
    return sorted(seen, key=lambda p: (_PANEL_RANK.get(p, 1000), p))

# /panel target word + VFS scheme → face pane slot id
_SCHEME_TO_PANE: dict[str, str] = {
    "skills": "skills",
    "skill": "skills",  # alias — VFS is skills://
    "plugins": "plugins",
    "plugin": "plugins",
    "history": "history",
    "config": "config",
    "faceconfig": "config",
    "taskmake": "taskmake",
    "pluginmake": "pluginmake",
    "pluginform": "pluginform",
    "bindmake": "bindmake",
    "gigmake": "gigmake",
    "remotemake": "remotemake",
    "jidmake": "jidmake",
    "install": "install",
    "docs": "docs",
    "mark": "bookmarks",
    "locker": "locker",
    "wiki": "wiki",
    "tasks": "tasks",
    "jobs": "jobs",
    "farm": "farm",
    "gigwork": "gigwork",
    "plan": "plan",
    "git": "git",
    "artifacts": "artifacts",
    "canvas": "canvas",
    "remote": "remote",
    "home": "home",
    "projects": "projects",
    "sources": "sources",
    "results": "results",
    "menu": "menu",
    "file": "explorer",
}
_VIEW_TO_PANE: dict[str, str] = {
    "vfs": "explorer",
    "explorer": "explorer",
    "tree": "explorer",
    "files": "explorer",
    "locker": "locker",
    "gallery": "locker",
    "home": "home",
    "projects": "projects",
    "skills": "skills",
    "skill": "skills",  # singular alias → skills pane
    "plugins": "plugins",
    "plugin": "plugins",
    "history": "history",
    "config": "config",
    "taskmake": "taskmake",
    "pluginmake": "pluginmake",
    "pluginform": "pluginform",
    "bindmake": "bindmake",
    "gigmake": "gigmake",
    "remotemake": "remotemake",
    "jidmake": "jidmake",
    "install": "install",
    "docs": "docs",
    "bookmarks": "bookmarks",
    "wiki": "wiki",
    "tasks": "tasks",
    "jobs": "jobs",
    "farm": "farm",
    "plan": "plan",
    "git": "git",
    "gigwork": "gigwork",
    "sources": "sources",
    "results": "results",
    "artifacts": "artifacts",
    "canvas": "canvas",
    "menu": "menu",
    "remote": "remote",
}


def _parent_address(addr: Any) -> Any:
    """Parent address for back navigation — mirrors ExplorerPane._parent.

    Duplicated here because faces may import only xlii.panes.dock, not
    xlii.panes.explorer (the import contract). ``scheme://a/b`` walks up
    even when the scheme is not a file tree (plugins://id/source).

    File / sftp walks that would leave the session Files root return
    ``None`` so Face Back cannot leak out of a remote project the way
    ExplorerPane.handle("back") already refuses.
    """
    from xlii.addressing import Address
    from xlii.desk_files import parent_inside_files_fence

    parent = None
    if addr.scheme == "file":
        p = Path(addr.target)
        if p != p.parent:
            parent = Address.parse(f"file://{p.parent}")
    elif addr.scheme in _SUBPATH_SCHEMES and addr.subpath:
        parent_sub = addr.subpath.rsplit("/", 1)[0] if "/" in addr.subpath else ""
        target = f"{addr.key}/{parent_sub}".rstrip("/")
        parent = Address.parse(f"{addr.scheme}://{target}")
    elif addr.target and "/" in addr.target:
        parent = Address.parse(f"{addr.scheme}://{addr.target.rsplit('/', 1)[0]}")
    elif addr.target:
        parent = Address.parse(f"{addr.scheme}://")
    return parent_inside_files_fence(addr, parent)


def _with_context(prompt: str, context: str) -> str:
    """Fold the pane's context address into the prompt (bounded, decoded).
    Same rule as the TUI dock's _with_context — duplicated here because
    xlii.tui is off-limits to the kernel-tier face server."""
    if not context:
        return prompt
    try:
        from xlii.addressing import classify, resolve, vfs_read, vfs_stat

        kind = classify(vfs_stat(context))
        if kind == "image":
            return f"{prompt}\n\n(the file in question: {context})"
        if kind == "pdf":
            from xlii.pdf_text import extract_pdf_text

            r = resolve(context)
            text = extract_pdf_text(r.path) if r.path else None
            if not text:
                return f"{prompt}\n\n(the PDF in question: {context})"
            if len(text) > _TURN_CONTEXT_CAP:
                text = text[:_TURN_CONTEXT_CAP] + "\n… (truncated)"
            return f"{prompt}\n\n--- {context} ---\n{text}\n--- end ---"
        text = vfs_read(context).decode("utf-8", errors="replace")
        if len(text) > _TURN_CONTEXT_CAP:
            text = text[:_TURN_CONTEXT_CAP] + "\n… (truncated)"
        return f"{prompt}\n\n--- {context} ---\n{text}\n--- end ---"
    except Exception:
        return f"{prompt}\n\n(context: {context})"


def _refresh(pane: Any) -> None:
    """Re-mount a pane from its own ``(address, selection)`` before projecting it.

    The state-ownership rule: a pane is a pure projection of those two values, so
    rebuilding from them is the refresh — otherwise a slot keeps showing the listing
    it captured at mount (files a turn wrote, tasks it saved, git status it moved).
    A pane that can't re-mount (its address went away) keeps its last good state; the
    render that follows reports the real trouble."""
    try:
        select = pane.selection().address or None
    except Exception:  # noqa: BLE001 — a pane without a live selection re-mounts bare
        select = None
    try:
        pane.mount(pane.address, select=select)
    except Exception:  # noqa: BLE001 — degrade to the cached projection
        pass


STREAM_VIEW = "stream"


def is_stream_view(view: str) -> bool:
    """Live tape (``stream``) or a parked project tape (``stream:<id>``)."""
    v = (view or "").strip()
    return v == STREAM_VIEW or v.startswith("stream:")
