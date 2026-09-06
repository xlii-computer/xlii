"""``GitPane`` — the source-control "Changed" view (the Panel "Git" doorway).

Sectioned Staged / Changes / Untracked list over ``git://`` with green rider-dots on staged rows;
view-diff morphs the working slot to a ``git://diff`` leaf, and stage/unstage/discard/commit seed
``/git …`` into the command line (review-before-run — the pane mutates nothing). Headless, mirroring
test_panes_tasks: build a temp repo, assert the projection + the outcome each action emits.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from xlii.loop_bundle import git_cmd


@pytest.fixture(autouse=True)
def _no_session(monkeypatch):
    from xlii import active_session

    monkeypatch.setattr(active_session, "_ACTIVE", None)


def _session_at(monkeypatch, cwd):
    from xlii import active_session

    monkeypatch.setattr(
        active_session, "_ACTIVE",
        SimpleNamespace(shell_cwd=Path(cwd),
                        project=SimpleNamespace(project_root=Path(cwd), xli_dir=Path(cwd) / ".xlii")),
    )


def _init_repo(root: Path) -> Path:
    git_cmd(root, ["init"])
    git_cmd(root, ["config", "user.email", "t@e"])
    git_cmd(root, ["config", "user.name", "T"])
    (root / "tracked.txt").write_text("a\n")
    git_cmd(root, ["add", "tracked.txt"])
    git_cmd(root, ["commit", "-m", "init"])
    return root


def test_git_pane_sections_and_accent(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")     # Changes
    (tmp_path / "new.txt").write_text("x\n")            # Untracked
    (tmp_path / "staged.txt").write_text("s\n")
    git_cmd(tmp_path, ["add", "staged.txt"])            # Staged
    _session_at(monkeypatch, tmp_path)
    from xlii.panes.git import GitPane

    r = GitPane("git://").render()
    texts = [row.text for row in r.rows]
    assert any("Staged (1)" in t for t in texts)
    assert any("Changes (1)" in t for t in texts)
    assert any("Untracked (1)" in t for t in texts)
    staged_rows = [row for row in r.rows if row.kind == "leaf" and "staged.txt" in row.text]
    assert staged_rows and staged_rows[0].accent is True     # the green rider dot
    assert r.title.startswith("Gitpanel")

    # each file row carries a semantic status tone the surface colours (git status palette)
    def _row(name):
        return next(row for row in r.rows if row.kind == "leaf" and name in row.text)
    assert _row("tracked.txt").tone == "modified"   # unstaged M → yellow
    assert _row("new.txt").tone == "untracked"      # ? → green
    assert _row("staged.txt").tone == "added"       # staged new file A → green


def test_git_pane_actions_for_unstaged_file(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    _session_at(monkeypatch, tmp_path)
    from xlii.panes import ENQUEUE_TURN, PREFILL, RETARGET_SLOT
    from xlii.panes.git import GitPane

    acts = GitPane("git://").actions()
    by = {a.name: a for a in acts}
    assert acts[0].name == "view"                            # the Enter default
    assert acts[0].outcome.kind == RETARGET_SLOT and acts[0].outcome.address == "git://diff/tracked.txt"
    assert by["stage"].outcome.kind == PREFILL and by["stage"].outcome.text == "/gitpain stage tracked.txt"
    assert by["commit"].outcome.kind == PREFILL and by["commit"].outcome.text == "/gitpain commit "
    assert by["generate"].outcome.kind == PREFILL and by["generate"].outcome.text == "/gitpain commit summary"
    assert by["discard"].outcome.text == "/gitpain discard tracked.txt"
    assert by["review"].outcome.kind == ENQUEUE_TURN and by["review"].outcome.address == "git://diff/tracked.txt"
    assert "unstage" not in by


def test_git_pane_staged_file_offers_unstage(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    git_cmd(tmp_path, ["add", "tracked.txt"])
    _session_at(monkeypatch, tmp_path)
    from xlii.panes import PREFILL
    from xlii.panes.git import GitPane

    by = {a.name: a for a in GitPane("git://").actions()}
    assert "unstage" in by and "stage" not in by
    assert by["unstage"].outcome.kind == PREFILL and by["unstage"].outcome.text == "/gitpain unstage tracked.txt"


def test_git_pane_nav_and_click(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    for n in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / n).write_text("x\n")
    _session_at(monkeypatch, tmp_path)
    from xlii.panes.git import GitPane

    p = GitPane("git://")
    first = p.selection().node.name
    assert p.handle("down") and p.selection().node.name != first
    assert p.handle("end")
    assert p.select_index(0) and p.selection().node.name == first
    assert p.select_index(99) is False
    assert p.handle("enter") is False   # falls through to the surface (actions)


def test_git_pane_mount_restores_selection(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    for n in ("a.txt", "b.txt"):
        (tmp_path / n).write_text("x\n")
    _session_at(monkeypatch, tmp_path)
    from xlii.panes.git import GitPane

    assert GitPane("git://diff/b.txt").selection().node.name == "b.txt"


def test_git_pane_repo_actions_and_no_sync_without_upstream(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    _session_at(monkeypatch, tmp_path)
    from xlii.panes.git import GitPane

    names = [a.name for a in GitPane("git://").actions()]
    assert "branch" in names and "stash" in names and "stash-u" in names
    assert "sync" not in names                       # a fresh local repo has no upstream


def test_git_pane_sync_action_appears_with_upstream(tmp_path, monkeypatch):
    import subprocess

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True)
    work = tmp_path / "work"
    work.mkdir()
    _init_repo(work)
    git_cmd(work, ["remote", "add", "origin", str(remote)])
    git_cmd(work, ["push", "-u", "origin", "HEAD"])
    _session_at(monkeypatch, work)
    from xlii.panes.git import GitPane

    assert "sync" in [a.name for a in GitPane("git://").actions()]   # an upstream → sync is offered


def test_git_pane_stash_indicator_in_title(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    git_cmd(tmp_path, ["stash", "push"])
    _session_at(monkeypatch, tmp_path)
    from xlii.panes.git import GitPane

    assert "⚑1" in GitPane("git://").render().title   # one stash → the ⚑ badge


def test_git_pane_stash_section_rows(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    git_cmd(tmp_path, ["stash", "push", "-m", "pause: auth race"])
    _session_at(monkeypatch, tmp_path)
    from xlii.panes.git import GitPane

    r = GitPane("git://").render()
    texts = [row.text for row in r.rows]
    assert any("Stashes (1)" in t for t in texts)
    assert any("stash@{0}" in t and "pause: auth race" in t for t in texts)


def test_git_pane_stash_action_uses_claim_input(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    _session_at(monkeypatch, tmp_path)
    from xlii.panes import CLAIM_INPUT
    from xlii.panes.git import GitPane

    by = {a.name: a for a in GitPane("git://").actions()}
    assert by["stash"].outcome.kind == CLAIM_INPUT
    assert by["stash-u"].outcome.kind == CLAIM_INPUT
    assert by["stash"].outcome.claim.prompt == "stash message:"


def test_git_pane_empty_outside_a_repo(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    from xlii.panes.git import GitPane

    p = GitPane("git://")
    r = p.render()
    assert r.empty and not r.rows
    assert p.actions() == [] and p.selection().node is None


def test_alt_g_doorway_opens_the_git_pane(tmp_path, monkeypatch):
    """Alt-G (and the Panel-menu Git item) open git:// in Pane 2 as the sectioned GitPane."""
    pytest.importorskip("textual")
    import asyncio

    from xlii.agent import SessionState
    from xlii.tui.dock_surface import register_dock_view
    from xlii.tui_textual import XliiApp

    register_dock_view("vfs")
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    agent = SimpleNamespace(console=None, rail=None, debug=None, plan_mode=False,
                            active_mode=None, howto_mode=False, history=[],
                            model_override=None, session=SessionState())
    st = SimpleNamespace(shell_cwd=tmp_path,
                         project=SimpleNamespace(project_root=tmp_path, name="p", xli_dir=tmp_path),
                         agent=agent, attached_docs=[], cfg=None)
    _session_at(monkeypatch, tmp_path)   # the ambient session the provider/pane read
    app = XliiApp(project_name="p", agent=agent, run_turn=lambda q: ("", set(), None), state=st)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_doorway("g")
            await pilot.pause()
            await pilot.pause()
            assert app._panel_open
            assert app._current_dock_scheme() == "git"
            assert ("doorway:g", "  Git", True) in app._menu_items("Panel Workbench")

    asyncio.run(scenario())


def test_dock_routes_git_scheme(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    _session_at(monkeypatch, tmp_path)
    from xlii.panes.dock import Dock
    from xlii.panes.explorer import ExplorerPane
    from xlii.panes.view import ViewPane

    dock = Dock(slots=("A",))
    assert type(dock.open_address("git://")).__name__ == "GitPane"          # root → the changed view
    assert isinstance(dock.open_address("git://diff"), ExplorerPane)         # sub-listing → generic explorer
    assert isinstance(dock.open_address("git://diff/tracked.txt"), ViewPane)  # a single diff → the text viewer

def test_git_pane_stash_rows_mint_no_vfs_address(tmp_path, monkeypatch):
    """git:// resolves no stash path, so stash rows must not mint git://stash/<n>
    addresses (selection/copy flows would raise 'unknown git path'); the patch
    view is a PREFILLed shell command instead."""
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    git_cmd(tmp_path, ["stash", "push", "-m", "pause: auth race"])
    _session_at(monkeypatch, tmp_path)
    from xlii.panes import PREFILL
    from xlii.panes.git import GitPane

    pane = GitPane("git://")  # tree is clean after the stash → only the stash row
    node = pane.selection().node
    assert node is not None and node.extra["type"] == "stash"
    assert node.address == ""
    by = {a.name: a for a in pane.actions()}
    assert by["view"].outcome.kind == PREFILL
    assert by["view"].outcome.text == "!git stash show -p stash@{0}"
    assert by["pop"].outcome.text == "/gitpain stash pop 0"


def test_git_pane_file_row_offers_a_catalog_tool(tmp_path, monkeypatch):
    """D19: a file row carries one Tools action — the fingerprint-first available
    check tool from the xtool catalog, seeded as a `!` line (review-before-run);
    destructive fix variants never seed from here."""
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    _session_at(monkeypatch, tmp_path)
    from xlii.panes import PREFILL
    from xlii.panes.git import GitPane

    monkeypatch.setattr("xlii.xtool_catalog.entry_available", lambda e: e.id == "ruff-check")
    by = {a.name: a for a in GitPane("git://").actions()}
    assert by["tools"].label == "Ruff check"
    assert by["tools"].outcome.kind == PREFILL
    assert by["tools"].outcome.text.startswith("!ruff check ")
    assert by["tools"].outcome.text.endswith("tracked.txt")

    monkeypatch.setattr("xlii.xtool_catalog.entry_available", lambda e: False)
    assert "tools" not in {a.name for a in GitPane("git://").actions()}
