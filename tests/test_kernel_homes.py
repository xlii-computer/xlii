"""Relocation seams (godzilla-mothra V1ab): kernel-shaped modules moved out of
the face package to top-level kernel homes, leaving re-export shims behind.

These tests pin the two properties the relocation must preserve:

1. **Identity** — the shim path and the kernel path resolve to the SAME
   objects, so ``isinstance`` checks, monkeypatches, and re-export call sites
   are unaffected by which path an importer uses.
2. **Face-freedom** — the 8 core files and the flipped repl_cmds no longer
   import ``xlii.tui`` at all (the import-linter burn-down's unit-level lock);
   ``xlii.ui`` and the in-face shims are the only allowed tui importers among
   the flipped set.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The 8 core files + the repl_cmds flipped this vector. repl.py keeps ONE
# baselined face edge (tui.input_chrome, genuinely prompt_toolkit chrome —
# Stage-2 seam work), so it is checked separately below.
FLIPPED_FACE_FREE = [
    "xlii/agent.py",
    "xlii/agent_dispatch.py",
    "xlii/agent_render.py",
    "xlii/commands.py",
    "xlii/commands_help.py",
    "xlii/conversation.py",
    "xlii/help_corpus.py",
    "xlii/repl_cmds/attach.py",
    "xlii/repl_cmds/chat.py",
    "xlii/repl_cmds/delegate.py",
    "xlii/repl_cmds/session.py",
]

FACE_PREFIXES = ("xlii.tui", "xlii.panes", "xlii.tui_textual")


def _face_import_lines(path: Path) -> list[int]:
    """Line numbers where *path* imports a face module (AST, not text)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits = []

    def face(mod: str) -> bool:
        return any(mod == f or mod.startswith(f + ".") for f in FACE_PREFIXES)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(face(a.name) for a in node.names):
                hits.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if face(node.module):
                hits.append(node.lineno)
    return hits


def test_events_shim_identity():
    import xlii.tui.events as shim
    import xlii.turn_events as kernel

    for name in ("ShellRan", "ToolStarted", "ToolFinished", "MetaMessage",
                 "UserTurn", "AssistantAnswer", "ShellSource"):
        assert getattr(shim, name) is getattr(kernel, name), name


def test_shell_shim_identity():
    import xlii.shell_run as kernel
    import xlii.tui.shell as shim

    for name in ("styled_enabled", "Capture", "looks_interactive",
                 "capture", "run_shell_captured"):
        assert getattr(shim, name) is getattr(kernel, name), name


def test_theme_shim_identity():
    import xlii.theme as kernel
    import xlii.tui.theme as shim

    assert shim.THEME is kernel.THEME
    assert shim.Theme is kernel.Theme


def test_status_shim_identity():
    import xlii.status as kernel
    import xlii.tui.status as shim

    for name in ("turn_record", "profile_bar_segments", "frame_tabs", "placeholder_key",
                 "register_frame_tab", "format_primary_axes"):
        assert getattr(shim, name) is getattr(kernel, name), name
    import inspect

    assert inspect.getmodule(shim.profile_bar).__name__ == "xlii.tui.status"
  # Private names still reachable through the shim (tests/face internals use them).
    assert shim._MODE_RICH is kernel._MODE_RICH
    assert shim._FRAME_TAB_PROVIDERS is kernel._FRAME_TAB_PROVIDERS


def test_turn_text_split_identity():
    import xlii.turn_text as kernel
    import xlii.tui.blocks as blocks

    assert blocks.tool_summary is kernel.tool_summary
    assert blocks.turn_footer is kernel.turn_footer


def test_fuzzy_shim_identity():
    import xlii.fuzzy as kernel
    import xlii.tui.discover as discover

    assert discover.fuzzy_score is kernel.fuzzy_score
    assert discover._best_fuzzy is kernel._best_fuzzy


def test_ui_seam_reexports():
    import xlii.tui as tui
    import xlii.ui as ui

    assert ui.console is tui.console
    assert ui.confirm is tui.confirm
    assert ui.renderer is tui.renderer
    assert ui.format_turn_line is tui.format_turn_line
    assert ui.Renderer is tui.Renderer


def test_flipped_files_are_face_free():
    for rel in FLIPPED_FACE_FREE:
        hits = _face_import_lines(REPO_ROOT / rel)
        assert hits == [], f"{rel} imports a face module at lines {hits}"


def test_repl_keeps_only_the_baselined_input_chrome_edge():
    """repl.py's one remaining face import is the baselined, genuinely-face
    tui.input_chrome (prompt_toolkit chrome) — anything more fails here."""
    hits = _face_import_lines(REPO_ROOT / "xlii/repl.py")
    src = (REPO_ROOT / "xlii/repl.py").read_text(encoding="utf-8").splitlines()
    assert hits, "repl.py should still import tui.input_chrome (baselined)"
    for ln in hits:
        assert "input_chrome" in src[ln - 1], f"unexpected face import at line {ln}: {src[ln - 1]}"


def test_new_kernel_modules_are_rich_free():
    """The relocated kernel modules must not import the face's render stack —
    a new body wraps them without a terminal."""
    for rel in ("xlii/turn_events.py", "xlii/shell_run.py", "xlii/turn_text.py",
                "xlii/theme.py", "xlii/fuzzy.py"):
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            else:
                continue
            for mod in mods:
                assert not mod.startswith(("rich", "textual", "xlii.tui")), (rel, mod)
