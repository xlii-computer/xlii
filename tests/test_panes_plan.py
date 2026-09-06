"""PlanPane + plan:// provider — the Panel "Plan" doorway.

Lists the project's plans (.xlii/plans/*.md, current first) with progress and
pending-amendment counts; every action PREFILLs a `/plan …` command into the
input (review-before-run) — the pane never writes. No network.
"""

from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _no_session(monkeypatch):
    from xlii import active_session

    monkeypatch.setattr(active_session, "_ACTIVE", None)


def _session_at(monkeypatch, xli_dir):
    from xlii import active_session

    monkeypatch.setattr(
        active_session, "_ACTIVE",
        SimpleNamespace(project=SimpleNamespace(xli_dir=xli_dir), attached_docs=[]),
    )


def _plan(xli_dir, name, text="- [ ] {#a} one\n- [x] {#b} two\n"):
    d = xli_dir / "plans"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(text)
    return d / f"{name}.md"


# --- provider ---------------------------------------------------------------

def test_plan_provider_lists_current_first(tmp_path, monkeypatch):
    import os

    _session_at(monkeypatch, tmp_path)
    old = _plan(tmp_path, "older")
    os.utime(old, (1_000, 1_000))
    _plan(tmp_path, "newer")
    _plan(tmp_path, "current")
    from xlii.addressing import resolve, vfs_list

    assert resolve("plan://").ok
    nodes = vfs_list("plan://")
    assert [n.name for n in nodes] == ["current", "newer", "older"]
    assert all(n.kind == "leaf" and n.extra["type"] == "plan" for n in nodes)


def test_plan_provider_reads_the_plan_text(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _plan(tmp_path, "current", "# goal\n- [ ] {#a} one\n")
    from xlii.addressing import vfs_read

    assert vfs_read("plan://current").decode() == "# goal\n- [ ] {#a} one\n"


def test_plan_provider_unknown_and_outside_project(tmp_path, monkeypatch):
    from xlii.addressing import resolve, vfs_list

    # no ambient session (autouse fixture) → the root lists empty, never raises
    assert vfs_list("plan://") == []
    _session_at(monkeypatch, tmp_path)
    assert resolve("plan://ghost").ok is False


def test_plan_provider_read_refuses_traversal(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _plan(tmp_path, "current")
    (tmp_path / "SECRETS.md").write_text("secret")
    from xlii.addressing import vfs_read

    with pytest.raises(FileNotFoundError):
        vfs_read("plan://../SECRETS")


def test_vfs_cli_ls_works_on_plan_scheme(tmp_path, monkeypatch):
    """xlii ls plan:// comes free with provider registration."""
    _session_at(monkeypatch, tmp_path)
    _plan(tmp_path, "current")
    from xlii.cmds.vfs import cmd_ls

    assert cmd_ls(Namespace(address="plan://")) == 0


def test_every_listed_key_reads_back(tmp_path, monkeypatch):
    """P3 review Fix 6: the list→read round-trip is a PROPERTY — every key the
    root emits must read() successfully, including a legacy 'spec.md.md' file
    whose stem carries the .md suffix."""
    _session_at(monkeypatch, tmp_path)
    d = tmp_path / "plans"
    d.mkdir(parents=True)
    (d / "spec.md.md").write_text("legacy double-extension plan\n")
    (d / "normal.md").write_text("normal plan\n")
    (d / "current.md").write_text("working plan\n")
    from xlii.addressing import vfs_list, vfs_read

    nodes = vfs_list("plan://")
    assert sorted(n.name for n in nodes) == ["current", "normal", "spec.md"]
    for n in nodes:
        assert vfs_read(n.address)  # every emitted key resolves back


# --- pane -------------------------------------------------------------------

def test_plan_pane_rows_progress_and_amendments(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _plan(tmp_path, "current", "- [x] {#a} one\n- [x?] {#b} two\n- [ ] {#c} three\n")
    done = _plan(tmp_path, "done", "- [x] {#z} all done\n")
    from xlii.plan_ops import amend_plan

    amend_plan(done.parent, "revisit z", plan="done")
    from xlii.panes.plan import PlanPane

    rows = PlanPane("plan://").render().rows
    assert rows[0].address == "plan://current"          # current always first
    assert "2/3" in rows[0].text and rows[0].tone == "modified"
    done_row = next(r for r in rows if r.address == "plan://done")
    assert "1/1" in done_row.text and "✎1" in done_row.text
    assert done_row.tone == "amended" and done_row.accent  # the voice outranks green


def test_plan_pane_tones_all_checked_and_empty(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _plan(tmp_path, "done", "- [x] {#a} one\n")
    _plan(tmp_path, "notes", "just prose, no boxes\n")
    from xlii.panes.plan import PlanPane

    rows = {r.address: r for r in PlanPane("plan://").render().rows}
    assert rows["plan://done"].tone == "added"
    assert rows["plan://notes"].tone == "empty"


def test_plan_pane_actions_prefill_plan_commands(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _plan(tmp_path, "current")
    from xlii.panes import PREFILL, RETARGET_SLOT
    from xlii.panes.plan import PlanPane

    acts = PlanPane("plan://").actions()
    names = [a.name for a in acts]
    assert names == ["show", "check", "amend", "promote", "view"]
    assert acts[0].outcome.kind == PREFILL                 # the Enter default
    assert acts[0].outcome.text == "/plan show current"
    assert acts[1].outcome.text == "/plan check "          # id left for the user
    assert acts[2].outcome.text == "/plan amend "
    assert acts[3].outcome.text == "/plan save "           # christening prefill
    assert acts[-1].outcome.kind == RETARGET_SLOT
    assert all(a.outcome.kind in (PREFILL, RETARGET_SLOT) for a in acts)  # view-only pane


def test_plan_pane_named_plan_actions_carry_scope(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _plan(tmp_path, "feature")
    from xlii.panes.plan import PlanPane

    acts = PlanPane("plan://feature").actions()
    names = [a.name for a in acts]
    assert "promote" not in names                          # only current promotes
    by = {a.name: a for a in acts}
    assert by["check"].outcome.text == "/plan check --plan feature "
    assert by["amend"].outcome.text == "/plan amend --plan feature "


def test_plan_pane_quotes_whitespace_names(tmp_path, monkeypatch):
    """P3 review Fix 7: a plan name with whitespace (the planner's write_file
    has no name grammar) is shlex-quoted in the PREFILLed commands, matching
    /plan's shlex tokenization."""
    _session_at(monkeypatch, tmp_path)
    _plan(tmp_path, "my plan")
    from xlii.panes.plan import PlanPane

    by = {a.name: a for a in PlanPane("plan://").actions()}
    assert by["show"].outcome.text == "/plan show 'my plan'"
    assert by["check"].outcome.text == "/plan check --plan 'my plan' "
    assert by["amend"].outcome.text == "/plan amend --plan 'my plan' "


def test_plan_pane_nav_click_and_mount_select(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    import os

    for i, n in enumerate(("current", "b", "c")):
        f = _plan(tmp_path, n)
        os.utime(f, (2_000 - i, 2_000 - i))
    from xlii.panes.plan import PlanPane

    p = PlanPane("plan://")
    assert p.handle("down") and p.selection().node.name == "b"
    assert p.handle("end") and p.selection().node.name == "c"
    assert p.select_index(0) and p.selection().node.name == "current"
    assert p.select_index(9) is False
    assert p.handle("enter") is False       # falls through to the surface
    assert PlanPane("plan://c").selection().node.name == "c"


def test_plan_pane_empty_outside_a_project(monkeypatch):
    from xlii.panes.plan import PlanPane

    p = PlanPane("plan://")
    r = p.render()
    assert r.empty and not r.rows
    assert p.actions() == [] and p.selection().node is None


# --- dock routing + the PREFILL sink ----------------------------------------

def test_dock_routes_plan_scheme_to_plan_pane(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _plan(tmp_path, "current")
    from xlii.panes.dock import Dock

    dock = Dock()
    assert type(dock.open_address("plan://")).__name__ == "PlanPane"
    # a single plan (a leaf) opens as its ITEM list (plan-surface T1); the raw
    # file stays one action away (the items pane's "View raw file").
    assert type(dock.open_address("plan://current")).__name__ == "PlanItemsPane"


def test_dock_prefill_routes_plan_commands_to_input_sink(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _plan(tmp_path, "current")
    from xlii.panes.dock import Dock
    from xlii.panes.plan import PlanPane

    dock = Dock(slots=("A",))
    seeded: list[str] = []
    dock.set_input_sink(SimpleNamespace(prefill=lambda text: seeded.append(text)))
    pane = PlanPane("plan://")
    dock.dispatch(pane.actions()[0].outcome)
    assert seeded == ["/plan show current"]  # seeded for review, not executed


# --- the app doorway --------------------------------------------------------

def test_doorway_tables_are_consistent():
    """P3 review Fix 8a: the hand-maintained doorway tables must agree — every
    HOTKEY letter needs a scheme (a letter without one is a dead Alt+key), and
    the plan doorway specifically is wired in letters, schemes, AND the Panel
    menu. (Some schemes — tasks, remote — are deliberately menu/scheme-only
    with no hotkey letter; that direction is allowed.) Removing 'p' from any
    table fails here instead of shipping silently."""
    from xlii.tui.app import _DOORWAY_LETTERS, XliiApp

    schemes = XliiApp._DOORWAY_SCHEMES
    # 'f' (Files) is special-cased in action_doorway (opens the vfs explorer
    # directly, no scheme table entry) — every OTHER letter needs a scheme.
    missing = [c for c in _DOORWAY_LETTERS if c != "f" and c not in schemes]
    assert missing == [], f"doorway letters without a scheme: {missing}"
    # Alt-P is NOT a doorway hotkey — it's the Project menu accelerator (menus win a
    # letter shared with a doorway). The plan pane keeps its scheme entry (reached via
    # the Panels menu's Plan row and the palette), just not a colliding Alt key.
    assert "p" not in _DOORWAY_LETTERS
    assert schemes.get("p") == "plan"


def test_panel_menu_lists_the_plan_doorway():
    pytest.importorskip("textual")
    from xlii.tui.app_menu_mixin import AppMenuMixin

    items = AppMenuMixin._menu_items(SimpleNamespace(_panel_open=False), "Panel Workbench")
    assert ("doorway:p", "  Plan", True) in items


def test_alt_p_doorway_opens_the_plan_pane(tmp_path, monkeypatch):
    """Alt-P (and the Panel-menu Plan item) open plan:// in Pane 2."""
    pytest.importorskip("textual")
    import asyncio

    from xlii.agent import SessionState
    from xlii.tui.dock_surface import register_dock_view
    from xlii.tui_textual import XliiApp

    register_dock_view("vfs")            # the panel view the doorway routes through
    _plan(tmp_path, "current")
    agent = SimpleNamespace(console=None, rail=None, debug=None, plan_mode=False,
                            active_mode=None, howto_mode=False, history=[],
                            model_override=None, session=SessionState())
    st = SimpleNamespace(shell_cwd=tmp_path,
                         project=SimpleNamespace(project_root=tmp_path, name="p", xli_dir=tmp_path),
                         agent=agent, attached_docs=[])
    _session_at(monkeypatch, tmp_path)   # the ambient session the provider/pane read
    app = XliiApp(project_name="p", agent=agent, run_turn=lambda q: ("", set(), None), state=st)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_doorway("p")
            await pilot.pause()
            await pilot.pause()
            assert app._panel_open
            assert app._current_dock_scheme() == "plan"
            assert ("doorway:p", "  Plan", True) in app._menu_items("Panel Workbench")

    asyncio.run(scenario())


# --- the items pane (plan-surface T1) -----------------------------------------

def test_plan_items_pane_rows_title_and_tones(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _plan(tmp_path, "current",
          "- [ ] {#a} one\n- [x?] {#b} two\n- [x] {#c} three — receipt: ok\n\n"
          "## Amendments\n- [?] {#am-1} (2026-07-19) new: add X\n")
    from xlii.panes.plan_items import PlanItemsPane

    p = PlanItemsPane("plan://current")
    r = p.render()
    assert "2/3" in r.title and "✎1" in r.title
    texts = [row.text for row in r.rows]
    assert any(t.startswith("☐  one") for t in texts)
    assert any(t.startswith("☑?  two") for t in texts)
    tones = {row.text: row.tone for row in r.rows if row.kind == "leaf"}
    assert tones["☑  three  · receipt: ok"] == "added"      # receipt visible on the row
    assert tones["☑?  two"] == "modified"
    assert any(row.tone == "amended" for row in r.rows if row.kind == "leaf")


def test_plan_items_pane_actions_by_state(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _plan(tmp_path, "current", "- [ ] {#a} one\n- [x?] {#b} two\n- [x] {#c} three\n")
    from xlii.panes import PREFILL
    from xlii.panes.plan_items import PlanItemsPane

    p = PlanItemsPane("plan://current")
    acts = {a.name: a for a in p.actions()}
    assert p.actions()[0].name == "check"                    # the Enter default on ☐
    assert acts["check"].outcome.kind == PREFILL
    assert acts["check"].outcome.text == "/plan check a "
    assert acts["amend"].outcome.text == "/plan amend --re a "
    p.handle("down")                                          # ☑? → upgrade path
    acts = {a.name: a for a in p.actions()}
    assert acts["receipt"].outcome.text == "/plan check b --receipt "
    p.handle("down")                                          # ☑ → nothing to check
    names = [a.name for a in p.actions()]
    assert "check" not in names and "receipt" not in names and "amend" in names
    assert any(a.name == "view" and a.outcome.address.startswith("file://")
               for a in p.actions())                          # raw file one action away


def test_plan_items_pane_named_scope_and_anchor_select(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _plan(tmp_path, "spec", "- [ ] {#a} one\n- [ ] {#b} two\n")
    from xlii.panes.plan_items import PlanItemsPane

    p = PlanItemsPane("plan://spec#b")                        # mount-select by anchor
    assert p.selection().node.name == "b"
    acts = {a.name: a for a in p.actions()}
    assert acts["check"].outcome.text == "/plan check b --plan spec "
    assert acts["amend"].outcome.text == "/plan amend --re b --plan spec "


def test_plan_items_pane_amendment_row_resolves_in_plan_mode(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _plan(tmp_path, "current",
          "- [x] {#a} one\n\n## Amendments\n- [?] {#am-1} (2026-07-19) new: X\n")
    from xlii.panes.plan_items import PlanItemsPane

    p = PlanItemsPane("plan://current#am-1")
    assert p.selection().node.extra["type"] == "plan-amendment"
    assert p.actions()[0].name == "resolve"
    assert p.actions()[0].outcome.text == "/plan"


def test_plan_items_pane_idless_boxes_and_missing_plan(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _plan(tmp_path, "current", "- [ ] no id here\n")
    from xlii.panes.plan_items import PlanItemsPane

    p = PlanItemsPane("plan://current")
    names = [a.name for a in p.actions()]
    assert "check" not in names and "amend" not in names      # unaddressable without an id
    assert "view" in names

    p2 = PlanItemsPane("plan://ghost")
    assert p2.render().empty and p2.actions() == []


def test_plan_items_pane_nav_and_click(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _plan(tmp_path, "current", "- [ ] {#a} one\n- [ ] {#b} two\n- [ ] {#c} three\n")
    from xlii.panes.plan_items import PlanItemsPane

    p = PlanItemsPane("plan://current")
    assert p.selection().node.name == "a"
    assert p.handle("down") and p.selection().node.name == "b"
    assert p.handle("end") and p.selection().node.name == "c"
    assert p.select_index(0) and p.selection().node.name == "a"
    assert p.select_index(99) is False
    assert p.handle("enter") is False                         # falls through to the surface
