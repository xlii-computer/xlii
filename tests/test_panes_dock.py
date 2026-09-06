"""Tests for the Dock — the kernel-side layout: slots, registry, and outcome execution."""

from __future__ import annotations

import pytest

from xlii.panes import ENQUEUE_TURN, NAVIGATE, RETARGET_SLOT, Outcome
from xlii.panes.dock import Dock
from xlii.panes.explorer import ExplorerPane
from xlii.panes.view import ViewPane


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "a.txt").write_text("hello\nworld")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.txt").write_text("deep")
    return tmp_path


def _addr(p) -> str:
    return f"file://{p}"


# --- slots & focus -----------------------------------------------------------


def test_default_two_slots_focus_a():
    d = Dock()
    assert d.slot_ids == ("A", "B")
    assert d.focused == "A"
    assert d.pane("A") is None and d.pane("B") is None


def test_focus_unknown_slot_raises():
    d = Dock()
    with pytest.raises(KeyError):
        d.focus("Z")


# --- the registry picks pane type by node kind -------------------------------


def test_open_container_gets_explorer(tree):
    d = Dock()
    pane = d.open_address(_addr(tree), slot="A")
    assert isinstance(pane, ExplorerPane)
    assert d.pane("A") is pane


def test_open_leaf_gets_view(tree):
    d = Dock()
    pane = d.open_address(_addr(tree / "a.txt"), slot="A")
    assert isinstance(pane, ViewPane)
    assert [row.text for row in pane.render().rows] == ["hello", "world"]


def test_open_conv_gets_transcript(tmp_path, monkeypatch):
    from xlii.panes.transcript import TranscriptPane
    from xlii.transcript import write_turn

    write_turn(tmp_path / ".xlii" / "turns", "q", "a")
    monkeypatch.chdir(tmp_path)
    d = Dock()
    pane = d.open_address("conv://.", slot="A")
    # the conv:// container rule precedes the generic container->explorer rule
    assert isinstance(pane, TranscriptPane)
    assert len(pane.render().rows) == 1


def test_open_conv_turn_leaf_gets_view(tmp_path, monkeypatch):
    from xlii.transcript import write_turn

    write_turn(tmp_path / ".xlii" / "turns", "q", "a")
    monkeypatch.chdir(tmp_path)
    d = Dock()
    # a single turn file (leaf) still opens in a viewer, not a transcript
    turn = d.open_address("conv://.", slot="A").render().rows[0].address
    pane = d.open_address(turn, slot="B")
    assert isinstance(pane, ViewPane)


def test_place_puts_prebuilt_pane(tmp_path, monkeypatch):
    from xlii.panes.transcript import TranscriptPane

    (tmp_path / ".xlii" / "turns").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    d = Dock()
    pane = TranscriptPane("conv://.")  # empty conversation — no stat-based pick needed
    assert d.place("B", pane, focus=True) is pane
    assert d.pane("B") is pane and d.focused == "B"


def test_register_pane_type_overrides(tree):
    d = Dock()
    # re-register "view" with a different factory; replacement-by-name must win
    d.register_pane_type("view", lambda node: node.kind == "leaf", ExplorerPane)
    pane = d.open_address(_addr(tree / "a.txt"), slot="A")
    assert type(pane) is ExplorerPane  # the replaced factory was used, not the default ViewPane


# --- NAVIGATE: re-mount the originating slot in place ------------------------


def test_navigate_remounts_same_pane(tree):
    d = Dock()
    pane = d.open_address(_addr(tree), slot="A", focus=True)
    out = d.dispatch(Outcome(NAVIGATE, _addr(tree / "sub")), from_slot="A")
    assert out is pane  # same instance, kept its type
    assert pane.address.target == str(tree / "sub")


def test_navigate_into_empty_slot_opens_fresh(tree):
    d = Dock()
    pane = d.dispatch(Outcome(NAVIGATE, _addr(tree)), from_slot="A")
    assert isinstance(pane, ExplorerPane)
    assert d.pane("A") is pane


# --- RETARGET_SLOT: open in the other pane -----------------------------------


def test_retarget_opens_other_slot_and_focuses(tree):
    d = Dock()
    d.open_address(_addr(tree), slot="A", focus=True)
    view = d.dispatch(Outcome(RETARGET_SLOT, _addr(tree / "a.txt")), from_slot="A")
    assert isinstance(view, ViewPane)
    assert d.pane("B") is view  # landed in the other slot
    assert d.focused == "B"  # and took focus
    assert d.pane("A") is not None  # source slot untouched


def test_non_layout_outcome_is_the_kernels_job(tree):
    d = Dock()
    d.open_address(_addr(tree / "a.txt"), slot="A")
    with pytest.raises(NotImplementedError):
        d.dispatch(Outcome(ENQUEUE_TURN, _addr(tree / "a.txt")), from_slot="A")


# --- end-to-end: explorer selection-action drives the Dock -------------------


def test_explorer_action_opens_leaf_in_other_pane(tree):
    """The whole seam: browse in A, pick a leaf's action, dispatch → B shows its content."""
    d = Dock()
    explorer = d.open_address(_addr(tree), slot="A", focus=True)
    # move selection onto the leaf a.txt (containers sort first, so: sub/, a.txt)
    explorer.handle("down")
    assert explorer.selection().node.name == "a.txt"
    # Enter on a leaf is not local nav → fall through to the leaf's action
    assert explorer.handle("enter") is False
    (view_action,) = explorer.actions()
    assert view_action.outcome.kind == RETARGET_SLOT
    view = d.dispatch(view_action.outcome, from_slot="A")
    assert isinstance(view, ViewPane)
    assert [row.text for row in view.render().rows] == ["hello", "world"]
    assert d.focused == "B"


def test_single_slot_dock_retarget_targets_itself(tree):
    d = Dock(slots=("only",))
    d.open_address(_addr(tree), slot="only", focus=True)
    view = d.dispatch(Outcome(RETARGET_SLOT, _addr(tree / "a.txt")), from_slot="only")
    assert isinstance(view, ViewPane)
    assert d.pane("only") is view
