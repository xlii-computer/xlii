"""The cwd file-explorer panel + its root-resolution policy (textual-only).
Split out of the one-file ``panels.py`` (V1c decomposition); behavior unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual.widgets import DirectoryTree, Static

from xlii.tui.panels.base import _PanelBase

if TYPE_CHECKING:
    from xlii.tui.panels.host import PanelActions


def _panel_root(state: Any) -> Path:
    """Where the explorer roots — the live shell cwd, else the project root,
    else the process cwd. Free traversal from there (Vector S's scratch mode
    wanders the shell; the explorer follows)."""
    import os

    cwd = getattr(state, "shell_cwd", None)
    if cwd:
        p = Path(str(cwd)).expanduser()
        if p.is_dir():
            return p
    project = getattr(state, "project", None)
    root = getattr(project, "project_root", None) if project is not None else None
    if root:
        p = Path(str(root)).expanduser()
        if p.is_dir():
            return p
    return Path(os.getcwd())


class ExplorerPanel(_PanelBase):
    """A cwd file-explorer (Textual ``DirectoryTree``). **Selecting a file
    attaches it** (the primary verb) — it rides the next turn as context and
    shows up as an A1 frame tab. A directory just expands; only a file is an
    attach. The panel does not change on file-click."""

    DEFAULT_CSS = """
    ExplorerPanel > #panel-explorer-tree {
        height: 1fr;
        width: 1fr;
    }
    """

    def __init__(self, state: Any, actions: "PanelActions") -> None:
        super().__init__()
        self._state = state
        self._actions = actions
        self._root = _panel_root(state)

    def compose(self):  # type: ignore[override]
        from rich.text import Text

        yield Static(Text(f"explorer · {self._root.name or self._root}"), classes="panel-title")
        yield DirectoryTree(str(self._root), id="panel-explorer-tree")

    def on_directory_tree_file_selected(self, event: "DirectoryTree.FileSelected") -> None:
        # select = attach. The status repaint (so the new A1 tab shows) rides
        # PanelActions._after_attach.
        event.stop()
        path = str(getattr(event, "path", "") or "")
        if not path:
            return
        self._actions.attach_file(path)
