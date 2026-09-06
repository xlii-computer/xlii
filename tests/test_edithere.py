"""Vector A2 — the /edithere command (TUI surface + inline $EDITOR fallback).

These pin: argument parsing, the project-root jail, the TUI host path (builds the
editor surface and hands it to the host without writing until save), and the
inline fallback (cat + $EDITOR, and --draft seeding the file from a stubbed AI).
"""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

from xlii.repl_cmds import edithere
from xlii.tui import preview


@pytest.fixture(autouse=True)
def _no_host():
    """Each test starts with no surface host (inline path) unless it installs one."""
    prev = preview.set_surface_host(None)
    yield
    preview.set_surface_host(prev)


def _ctx(tmp_path):
    buf = StringIO()
    console = Console(file=buf, width=100, no_color=True, highlight=False)
    state = SimpleNamespace(
        project=SimpleNamespace(project_root=tmp_path, name="proj"),
        shell_cwd=tmp_path,
    )
    return {"console": console, "state": state}, buf


class _RecordingHost(preview.SurfaceHost):
    def __init__(self):
        self.screen = None

    def open_surface(self, screen, on_result=None):
        self.screen = screen


# -- parsing --------------------------------------------------------------

def test_parse_variants():
    assert edithere._parse(["notes.md"]) == (None, "notes.md")
    assert edithere._parse(["--draft", "make a readme"]) == ("make a readme", None)
    assert edithere._parse(["--draft", "make a readme", "README.md"]) == ("make a readme", "README.md")
    assert edithere._parse(["--draft=inline desc", "f.md"]) == ("inline desc", "f.md")
    assert edithere._parse([]) == (None, None)


def test_no_args_prints_usage(tmp_path):
    ctx, buf = _ctx(tmp_path)
    assert edithere._edithere_handler("/edithere", ctx) is True
    assert "usage:" in buf.getvalue()


# -- jail -----------------------------------------------------------------

def test_path_outside_project_is_refused(tmp_path):
    ctx, buf = _ctx(tmp_path)
    edithere._edithere_handler("/edithere ../../etc/passwd", ctx)
    assert "refused" in buf.getvalue()


# -- TUI host path --------------------------------------------------------

def test_tui_path_opens_surface_without_writing(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("original body", encoding="utf-8")
    host = _RecordingHost()
    preview.set_surface_host(host)

    ctx, buf = _ctx(tmp_path)
    assert edithere._edithere_handler("/edithere notes.md", ctx) is True

    # The surface was handed to the host, seeded with the file, and nothing was
    # written yet — the save is explicit (ctrl+s → on_save).
    assert host.screen is not None
    assert host.screen._seed == "original body"
    assert f.read_text(encoding="utf-8") == "original body"

    # Driving the surface's commit callback writes the file (atomic).
    host.screen._on_save("rewritten body")
    assert f.read_text(encoding="utf-8") == "rewritten body"


def test_tui_draft_path_passes_draft_prompt(tmp_path):
    host = _RecordingHost()
    preview.set_surface_host(host)
    ctx, _ = _ctx(tmp_path)
    edithere._edithere_handler('/edithere --draft "a release note" CHANGELOG.md', ctx)
    assert host.screen is not None
    assert host.screen._draft_prompt == "a release note"
    assert host.screen._on_draft is not None
    assert host.screen._on_discuss is not None


def test_tui_host_never_falls_through_to_editor(tmp_path, monkeypatch):
    # Regression: under the TUI, /edithere must NEVER launch the inline $EDITOR — a
    # full-screen editor inside Textual floods the input box and steals the keyboard
    # (ctrl+c won't escape). Even when the surface fails to open, it errors
    # gracefully instead of shelling out.
    preview.set_surface_host(_RecordingHost())
    monkeypatch.setattr(preview, "open_surface", lambda screen, on_result=None: False)
    monkeypatch.setattr("xlii.editor.open_for_edit",
                        lambda p: pytest.fail("$EDITOR launched while a TUI host was set"))
    monkeypatch.setattr("xlii.tools._confirm", lambda *a, **k: "y")  # notes.md is new here
    ctx, buf = _ctx(tmp_path)
    assert edithere._edithere_handler("/edithere notes.md", ctx) is True
    assert "unavailable" in buf.getvalue()  # graceful error — not a crash, not $EDITOR


# -- no-arg → the file-tab panel's current target (Vector A seam) ---------

def test_no_arg_edits_panel_target_inline(tmp_path, monkeypatch):
    # /editthis with no path and no --draft edits whatever the two-pane panel is
    # currently file-viewing (panels.current_panel_target), through the normal
    # resolve/cat/$EDITOR path. No surface host here → the inline fallback.
    from xlii.tui import panels

    viewed = tmp_path / "viewed.md"
    viewed.write_text("panel body", encoding="utf-8")
    monkeypatch.setattr(panels, "current_panel_target", lambda: viewed, raising=False)
    opened = {}
    monkeypatch.setattr("xlii.editor.open_for_edit", lambda p: opened.setdefault("path", str(p)))

    ctx, buf = _ctx(tmp_path)
    assert edithere._edithere_handler("/editthis", ctx) is True

    out = buf.getvalue()
    assert opened.get("path") == str(viewed)   # the panel's file was the target
    assert "viewed.md" in out
    assert "panel body" in out                  # cat preview before $EDITOR


def test_no_arg_edits_panel_target_tui_surface(tmp_path, monkeypatch):
    # Under the TUI the panel target runs through the same live surface as an
    # explicit /editthis <file>: seeded with the file, nothing written until save.
    from xlii.tui import panels

    viewed = tmp_path / "api.md"
    viewed.write_text("# API", encoding="utf-8")
    monkeypatch.setattr(panels, "current_panel_target", lambda: viewed, raising=False)
    host = _RecordingHost()
    preview.set_surface_host(host)

    ctx, _ = _ctx(tmp_path)
    assert edithere._edithere_handler("/editthis", ctx) is True
    assert host.screen is not None
    assert host.screen._seed == "# API"
    assert viewed.read_text(encoding="utf-8") == "# API"   # explicit save only


def test_no_arg_none_target_prints_usage(tmp_path, monkeypatch):
    # Panel on the tree/gallery/closed (or off the TUI) → None → keep usage.
    from xlii.tui import panels

    monkeypatch.setattr(panels, "current_panel_target", lambda: None, raising=False)
    ctx, buf = _ctx(tmp_path)
    assert edithere._edithere_handler("/editthis", ctx) is True
    assert "usage:" in buf.getvalue()


def test_no_arg_seam_failure_falls_back_to_usage(tmp_path, monkeypatch):
    # Defensive: a raising seam (no [tui], no host, or the seam not yet shipped)
    # must degrade to usage, never crash — the branch builds before Vector A lands.
    from xlii.tui import panels

    def _boom():
        raise RuntimeError("seam not shipped yet")

    monkeypatch.setattr(panels, "current_panel_target", _boom, raising=False)
    ctx, buf = _ctx(tmp_path)
    assert edithere._edithere_handler("/editthis", ctx) is True
    assert "usage:" in buf.getvalue()


# -- inline fallback ------------------------------------------------------

def test_inline_edit_existing_file_cats_and_opens_editor(tmp_path, monkeypatch):
    f = tmp_path / "hello.txt"
    f.write_text("line A\nline B", encoding="utf-8")
    opened = {}
    monkeypatch.setattr("xlii.editor.open_for_edit", lambda p: opened.setdefault("path", str(p)))

    ctx, buf = _ctx(tmp_path)
    edithere._edithere_handler("/edithere hello.txt", ctx)

    out = buf.getvalue()
    assert "hello.txt" in out
    assert "line A" in out  # cat preview before $EDITOR
    assert opened.get("path") == str(f)
    assert "edited" in out


def test_inline_new_file_is_created_then_edited(tmp_path, monkeypatch):
    target = tmp_path / "sub" / "new.txt"
    opened = {}
    monkeypatch.setattr("xlii.editor.open_for_edit", lambda p: opened.setdefault("path", str(p)))
    monkeypatch.setattr("xlii.tools._confirm", lambda *a, **k: "y")  # approve creating it

    ctx, buf = _ctx(tmp_path)
    edithere._edithere_handler("/edithere sub/new.txt", ctx)

    assert target.exists()
    assert opened.get("path") == str(target)
    assert "new file" in buf.getvalue()


def test_new_file_confirm_declined_creates_nothing(tmp_path, monkeypatch):
    # `/editthis tell me about this file?` parses 'tell' as the path → a NEW target;
    # declining the confirm must create nothing and never open an editor.
    monkeypatch.setattr("xlii.tools._confirm", lambda *a, **k: "n")
    monkeypatch.setattr("xlii.editor.open_for_edit",
                        lambda p: pytest.fail("opened an editor for a declined new file"))
    ctx, buf = _ctx(tmp_path)
    assert edithere._edithere_handler("/editthis tell me about this file?", ctx) is True
    assert not (tmp_path / "tell").exists()
    assert "cancelled" in buf.getvalue()


def test_target_badge_tracked_untracked_new(tmp_path):
    import subprocess

    state = SimpleNamespace()
    # new (doesn't exist) — no git probe needed
    assert edithere._target_badge(tmp_path / "nope.txt", state, False) == "new file"
    # scratch session tags the no-sync contract onto a new target
    assert "scratch" in edithere._target_badge(
        tmp_path / "nope.txt", SimpleNamespace(no_sync=True), False
    )
    # a real repo: staged file is tracked, an unadded sibling is untracked
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("hi", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    untracked = tmp_path / "untracked.txt"
    untracked.write_text("yo", encoding="utf-8")
    assert edithere._target_badge(tracked, state, True) == "tracked"
    assert edithere._target_badge(untracked, state, True) == "untracked"


def test_inline_draft_seeds_file_from_ai(tmp_path, monkeypatch):
    monkeypatch.setattr(edithere, "_ai_text", lambda state, system, user: "# Drafted\n\nbody")
    opened = {}
    monkeypatch.setattr("xlii.editor.open_for_edit", lambda p: opened.setdefault("path", str(p)))

    ctx, buf = _ctx(tmp_path)
    edithere._edithere_handler('/edithere --draft "a readme" README.md', ctx)

    target = tmp_path / "README.md"
    assert target.read_text(encoding="utf-8") == "# Drafted\n\nbody"
    assert opened.get("path") == str(target)
    assert "drafted" in buf.getvalue().lower()


def test_inline_draft_without_file_uses_scratch(tmp_path, monkeypatch):
    monkeypatch.setattr(edithere, "_ai_text", lambda state, system, user: "scratch draft")
    opened = {}
    monkeypatch.setattr("xlii.editor.open_for_edit", lambda p: opened.setdefault("path", str(p)))

    ctx, buf = _ctx(tmp_path)
    edithere._edithere_handler('/edithere --draft "throwaway idea"', ctx)

    scratch = tmp_path / ".xlii" / "drafts"
    files = list(scratch.glob("draft-*.md"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == "scratch draft"


def test_inline_draft_ai_unavailable_opens_empty(tmp_path, monkeypatch):
    def _boom(state, system, user):
        raise RuntimeError("no secondary AI configured")

    monkeypatch.setattr(edithere, "_ai_text", _boom)
    monkeypatch.setattr("xlii.editor.open_for_edit", lambda p: None)

    ctx, buf = _ctx(tmp_path)
    edithere._edithere_handler('/edithere --draft "x" notes.md', ctx)
    assert "draft unavailable" in buf.getvalue()


# -- registration ---------------------------------------------------------

def test_command_registered():
    from xlii.commands import find_repl_command
    import xlii.repl_cmds as rc

    rc.register_all()
    # primary name
    assert find_repl_command("/editthis f.py", repl="code") is not None
    assert find_repl_command("/editthis f.py", repl="chat") is not None
    # `edithere` stays a hidden alias (muscle memory + shipped docs keep working)
    assert find_repl_command("/edithere f.py", repl="code") is not None
    assert find_repl_command("/edithere f.py", repl="chat") is not None
