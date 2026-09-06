"""Tests for the TranscriptPane — the dialogue surface as a conv:// projection — and the
turn-execution seam (Dock.set_turn_sink / ENQUEUE_TURN)."""

from __future__ import annotations

import pytest

from xlii.panes import ENQUEUE_TURN, RETARGET_SLOT, Outcome, Pane, TurnSink
from xlii.panes.dock import Dock
from xlii.panes.transcript import TranscriptPane
from xlii.panes.view import ViewPane
from xlii.transcript import write_turn


@pytest.fixture
def conv(tmp_path, monkeypatch):
    """A cwd project with three turns under .xlii/turns/, addressable as conv://."""
    turns = tmp_path / ".xlii" / "turns"
    write_turn(turns, "first question", "first answer")
    write_turn(turns, "second question\nwith two lines", "second answer")
    write_turn(turns, "third question", "third answer")
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- projection & contract ---------------------------------------------------


def test_transcript_satisfies_pane_protocol(conv):
    assert isinstance(TranscriptPane("conv://."), Pane)


def test_lists_turns_newest_selected(conv):
    pane = TranscriptPane("conv://.")
    rows = pane.render().rows
    assert len(rows) == 3
    assert all(r.kind == "turn" for r in rows)
    # one-line summaries carry the user message; multi-line user shows only its first line
    assert "first question" in rows[0].text
    assert "second question" in rows[1].text and "two lines" not in rows[1].text
    # newest turn selected by default
    assert rows[-1].selected is True
    assert pane.selection().node.address == rows[-1].address


def test_empty_conversation(tmp_path, monkeypatch):
    (tmp_path / ".xlii" / "turns").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    pane = TranscriptPane("conv://.")
    assert pane.render().empty is True
    assert pane.selection().node is None


def test_nav_moves_selection(conv):
    pane = TranscriptPane("conv://.")  # newest (index 2) selected
    assert pane.handle("up") is True
    assert "second question" in pane.selection().node.address or pane.render().rows[1].selected
    pane.handle("home")
    assert pane.render().rows[0].selected is True
    pane.handle("end")
    assert pane.render().rows[-1].selected is True


def test_enter_is_not_local_nav(conv):
    pane = TranscriptPane("conv://.")
    assert pane.handle("enter") is False


# --- actions: view a turn (other pane) + re-ask (enqueue) --------------------


def test_actions_view_and_reask(conv):
    pane = TranscriptPane("conv://.")  # newest = "third question"
    by_name = {a.name: a for a in pane.actions()}
    assert by_name["view"].outcome.kind == RETARGET_SLOT
    assert by_name["view"].outcome.address == pane.selection().node.address
    assert by_name["re-ask"].outcome.kind == ENQUEUE_TURN
    assert by_name["re-ask"].outcome.text == "third question"


def test_view_action_opens_turn_in_a_view_pane(conv):
    """A turn leaf is a conv:// address, so the existing ViewPane reads it for free."""
    pane = TranscriptPane("conv://.")
    view_outcome = next(a for a in pane.actions() if a.name == "view").outcome
    vp = ViewPane(view_outcome.address)
    text = "\n".join(r.text for r in vp.render().rows)
    assert "third question" in text and "third answer" in text


# --- the turn-execution seam: Dock + a TurnSink ------------------------------


class _RecordingSink:
    """A fake turn sink that appends a turn to the conversation (what the real agent does)."""

    def __init__(self, turns_dir):
        self.turns_dir = turns_dir
        self.calls = []

    def submit(self, prompt: str, *, context: str = "") -> None:
        self.calls.append((prompt, context))
        write_turn(self.turns_dir, prompt, f"(reply to: {prompt})")


def test_sink_satisfies_protocol(tmp_path):
    assert isinstance(_RecordingSink(tmp_path), TurnSink)


def test_enqueue_turn_without_sink_still_raises():
    d = Dock()
    with pytest.raises(NotImplementedError):
        d.dispatch(Outcome(ENQUEUE_TURN, "conv://.", text="hi"))


def test_enqueue_turn_routes_to_sink(conv):
    sink = _RecordingSink(conv / ".xlii" / "turns")
    d = Dock()
    d.set_turn_sink(sink)
    out = d.dispatch(Outcome(ENQUEUE_TURN, "conv://.", text="re-ask me"))
    assert out is None
    assert sink.calls == [("re-ask me", "conv://.")]


def test_full_loop_reask_grows_transcript(conv):
    """The whole dialogue seam: a transcript's re-ask → Dock → sink appends a turn →
    refresh re-projects the longer conversation."""
    turns_dir = conv / ".xlii" / "turns"
    sink = _RecordingSink(turns_dir)
    dock = Dock()
    dock.set_turn_sink(sink)

    transcript = TranscriptPane("conv://.")
    assert len(transcript.render().rows) == 3
    reask = next(a for a in transcript.actions() if a.name == "re-ask").outcome
    dock.dispatch(reask)  # sink appends a 4th turn
    transcript.refresh()
    rows = transcript.render().rows
    assert len(rows) == 4
    assert "third question" in rows[-1].text  # the re-asked prompt is the newest turn


def test_parse_cache_skips_unchanged_mtime(conv, monkeypatch):
    """Phase 6: refresh reuses parsed turns when mtime is unchanged."""
    import xlii.panes.transcript as tp_mod

    calls = []
    real = tp_mod.parse_turn_markdown

    def _spy(text, fallback_ts=""):
        calls.append(fallback_ts)
        return real(text, fallback_ts=fallback_ts)

    monkeypatch.setattr(tp_mod, "parse_turn_markdown", _spy)
    pane = TranscriptPane("conv://.")
    assert len(calls) == 3
    calls.clear()
    pane.refresh()
    assert calls == []  # all three hits from mtime cache


def test_parse_cache_invalidates_on_mtime_change(conv):
    import os

    pane = TranscriptPane("conv://.")
    node = pane._nodes[0]
    path = conv / ".xlii" / "turns" / node.name
    path.write_text(
        "# rewritten\n\n## user\nrewritten question\n\n## assistant\nrewritten answer\n"
    )
    os.utime(path, (path.stat().st_mtime + 5, path.stat().st_mtime + 5))
    pane.refresh()
    assert "rewritten question" in pane.render().rows[0].text


def test_refresh_transcript_dock_refreshes_every_pane():
    """_refresh_transcript_dock must repaint ALL docked transcripts on a stream
    chunk — returning after the first left a second (split) transcript stale."""
    pytest.importorskip("textual")
    import xlii.tui.app  # noqa: F401 — app_menu_mixin reads sys.modules["xlii.tui.app"]
    from xlii.tui.app_menu_mixin import AppMenuMixin

    p1 = TranscriptPane()  # no address → no filesystem mount needed
    p2 = TranscriptPane()
    calls = {"p1": 0, "p2": 0, "repaint": 0}
    p1.refresh = lambda: calls.__setitem__("p1", calls["p1"] + 1)  # type: ignore[method-assign]
    p2.refresh = lambda: calls.__setitem__("p2", calls["p2"] + 1)  # type: ignore[method-assign]

    class _Dock:
        slot_ids = ("a", "b")

        def pane(self, sid):
            return {"a": p1, "b": p2}[sid]

    class _Surface:
        dock = _Dock()

        def repaint(self):
            calls["repaint"] += 1

    class _Self:
        def query_one(self, _cls):
            return _Surface()

    AppMenuMixin._refresh_transcript_dock(_Self())
    assert calls["p1"] == 1
    assert calls["p2"] == 1  # the SECOND transcript must refresh too
    assert calls["repaint"] >= 1
