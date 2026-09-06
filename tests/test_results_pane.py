"""results:// + the results panes (typed-workbenches F2)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from xlii import active_session
from xlii.addressing import vfs_exists, vfs_list, vfs_read
from xlii.panes.results import ResultsRootPane, ResultsTablePane


@pytest.fixture(autouse=True)
def _ambient():
    prev = active_session.set_active_session(None)
    yield
    active_session.set_active_session(prev)


@pytest.fixture
def results(tmp_path):
    """A project with two stored provider runs."""
    xli = tmp_path / ".xlii"
    run = xli / "provider-results" / "gdelt-doc"
    run.mkdir(parents=True)
    records = [
        {"title": "Beta story", "score": 5, "url": "https://b.example"},
        {"title": "Alpha story", "score": 10, "url": "https://a.example"},
        {"title": None, "score": 1, "url": "https://c.example"},
    ]
    payload = {"provider": "gdelt-doc", "fetched_at": "2026-08-02T01:23:45+00:00",
               "params": {"query": "x"}, "pages": 1, "count": 3, "records": records}
    (run / "latest.json").write_text(json.dumps(payload))
    ids = xli / "provider-results" / "hackernews-top"
    ids.mkdir(parents=True)
    (ids / "latest.json").write_text(json.dumps(
        {"provider": "hackernews-top", "fetched_at": "2026-08-02T01:00:00+00:00",
         "params": {}, "pages": 1, "count": 2, "records": [101, 102]}))
    active_session.set_active_session(
        SimpleNamespace(project=SimpleNamespace(xli_dir=xli, project_root=tmp_path)))
    return xli


# --- the provider -----------------------------------------------------------------


def test_root_lists_providers_with_runs(results):
    names = [n.name for n in vfs_list("results://")]
    assert names == ["gdelt-doc", "hackernews-top"]


def test_read_run_and_record(results):
    data = json.loads(vfs_read("results://gdelt-doc"))
    assert data["count"] == 3
    rec = json.loads(vfs_read("results://gdelt-doc/1"))
    assert rec["title"] == "Alpha story"


def test_empty_without_project():
    assert vfs_list("results://") == []
    assert vfs_exists("results://")


def test_exists_and_resolve_record_leaves(results):
    assert vfs_exists("results://gdelt-doc/1")
    assert not vfs_exists("results://gdelt-doc/99")


def test_invalid_provider_name_is_rejected(results):
    assert not vfs_exists("results://..")


# --- the root pane ------------------------------------------------------------------


def test_root_pane_rows_and_open(results):
    pane = ResultsRootPane("results://")
    rendered = pane.render()
    assert rendered.rows[0].text.startswith("gdelt-doc  · 3 records ·")
    acts = pane.actions()
    assert acts[0].name == "open" and acts[0].outcome.address == "results://gdelt-doc"


# --- the table pane ------------------------------------------------------------------


def test_table_renders_columns_and_header(results):
    pane = ResultsTablePane("results://gdelt-doc")
    rendered = pane.render()
    header = rendered.rows[0]
    assert header.address == "key:sort:next"
    assert "title" in header.text and "score" in header.text
    assert rendered.rows[1].text.strip().startswith("Beta story")


def test_sort_cycles_columns_and_direction(results):
    pane = ResultsTablePane("results://gdelt-doc")
    pane.handle("sort:next")  # first column asc (title)
    titles = [r.text.strip()[:5] for r in pane.render().rows[1:]]
    assert titles[0] == "Alpha"
    pane.handle("sort:next")  # title desc — None sorts last
    titles = [r.text.strip()[:5].strip() for r in pane.render().rows[1:]]
    assert titles[0] == "Beta"
    pane.handle("sort:next")  # next column (score) asc
    rendered = pane.render().rows
    assert rendered[1].address == "results://gdelt-doc/2"
    assert rendered[2].address == "results://gdelt-doc/0"
    assert rendered[3].address == "results://gdelt-doc/1"


def test_sort_survives_reselect(results):
    pane = ResultsTablePane("results://gdelt-doc")
    pane.handle("sort:next")  # title asc
    pane.mount(pane.address, select="results://gdelt-doc/1")
    assert pane.render().rows[1].text.strip().startswith("Alpha")  # still sorted
    assert pane.selection().node.address == "results://gdelt-doc/1"


def test_sorted_row_addresses_track_original_record_indexes(results):
    pane = ResultsTablePane("results://gdelt-doc")
    pane.handle("sort:next")  # title asc: Alpha, Beta, None
    rows = pane.render().rows
    assert rows[1].address == "results://gdelt-doc/1"
    assert rows[2].address == "results://gdelt-doc/0"
    pane.mount(pane.address, select=rows[1].address)
    assert pane.selection().node.address == "results://gdelt-doc/1"
    assert pane.actions()[0].outcome.address == "results://gdelt-doc/1"


def test_sort_preserves_selected_record(results):
    pane = ResultsTablePane("results://gdelt-doc")
    pane.mount(pane.address, select="results://gdelt-doc/0")  # Beta story
    pane.handle("sort:next")  # title asc — Beta moves to row index 1
    assert pane.selection().node.address == "results://gdelt-doc/0"


def test_ask_action_enqueues_record(results):
    pane = ResultsTablePane("results://gdelt-doc")
    pane.mount(pane.address, select="results://gdelt-doc/0")
    acts = pane.actions()
    assert acts[0].name == "ask" and acts[0].outcome.kind == "enqueue_turn"
    assert acts[0].outcome.address == "results://gdelt-doc/0"
    assert "Beta story" not in acts[0].outcome.text
    from xlii.face_panes import _with_context
    submitted = _with_context(acts[0].outcome.text, acts[0].outcome.address)
    assert "Beta story" in submitted


def test_blank_strings_sort_last(results):
    records = [
        {"title": "", "score": 2},
        {"title": "has title", "score": 1},
    ]
    run = results / "provider-results" / "blank-sort"
    run.mkdir()
    (run / "latest.json").write_text(json.dumps(
        {"provider": "blank-sort", "fetched_at": "2026-08-02T01:00:00+00:00",
         "params": {}, "pages": 1, "count": 2, "records": records}))
    pane = ResultsTablePane("results://blank-sort")
    pane.handle("sort:title")
    rows = [r.text for r in pane.render().rows[1:]]
    assert "has title" in rows[0]
    assert "has title" not in rows[1]


def test_scalar_records_render(results):
    pane = ResultsTablePane("results://hackernews-top")
    rendered = pane.render()
    assert any("101" in r.text for r in rendered.rows)
    assert rendered.rows[0].address != "key:sort:next"  # no columns → no header


def test_dock_picks_the_right_pane(results):
    from xlii.panes.dock import Dock

    dock = Dock(slots=("A",))
    assert isinstance(dock.open_address("results://", slot="A"), ResultsRootPane)
    assert isinstance(dock.open_address("results://gdelt-doc", slot="A"), ResultsTablePane)


def test_dock_re_picks_pane_type_on_same_scheme_navigation(results):
    from xlii.panes.dock import Dock

    dock = Dock(slots=("A",))
    root = dock.open_address("results://", slot="A")
    table = dock.dispatch(root.actions()[0].outcome, from_slot="A")
    assert isinstance(table, ResultsTablePane)


# --- over the face deck -------------------------------------------------------------


def test_chat_workbench_mounts_results_tab(results):
    """Results tab lives on the chat pack (research doors folded into chat)."""
    from xlii.face_panes import FaceDeck
    from xlii.workbench import BUILTIN_WORKBENCHES

    state = SimpleNamespace(
        workbench=BUILTIN_WORKBENCHES["chat"], shell_cwd=None,
        project=SimpleNamespace(project_root=results.parent, xli_dir=results))
    active_session.set_active_session(state)
    server = SimpleNamespace(state=state, sent=[], send=lambda o: server.sent.append(o),
                             submit=lambda t: True)
    deck = FaceDeck(server)
    from xlii.ws_protocol import serialize_event

    snap = serialize_event(deck.snapshot())
    assert "results" in [p["id"] for p in snap["panes"]]
    # the key: convention round-trips: header click → sort key op
    deck.handle({"pane": "results", "op": "action", "name": "open"})
    pane = deck._dock.pane("results")
    assert isinstance(pane, ResultsTablePane)
    deck.handle({"pane": "results", "op": "key", "key": "sort:next"})
    deck.handle({"pane": "results", "op": "key", "key": "back"})
    assert isinstance(deck._dock.pane("results"), ResultsRootPane)
    errors = [o for o in server.sent if o.get("type") == "error"]
    assert errors == []


def test_columns_include_fields_from_visible_window(tmp_path):
    xli = tmp_path / ".xlii"
    run = xli / "provider-results" / "wide"
    run.mkdir(parents=True)
    records = [{"title": f"row-{i}"} for i in range(60)]
    records[55]["late_key"] = "present"
    payload = {"provider": "wide", "fetched_at": "2026-08-02T01:23:45+00:00",
               "params": {}, "pages": 1, "count": len(records), "records": records}
    (run / "latest.json").write_text(json.dumps(payload))
    active_session.set_active_session(
        SimpleNamespace(project=SimpleNamespace(xli_dir=xli, project_root=tmp_path)))
    pane = ResultsTablePane("results://wide")
    assert "late_key" in pane.render().rows[0].text


def test_root_pane_sees_a_run_stored_after_mount(results):
    """Re-projection IS the refresh: a run stored after the deck mounted shows
    up, and the selection is kept by name."""
    pane = ResultsRootPane("results://")
    pane.mount(pane.address, select="results://hackernews-top")
    late = results / "provider-results" / "zzz-late"
    late.mkdir(parents=True)
    (late / "latest.json").write_text(json.dumps(
        {"provider": "zzz-late", "fetched_at": "2026-08-03T01:00:00+00:00", "params": {},
         "pages": 1, "count": 0, "records": []}))
    rows = pane.render().rows
    assert [r.address for r in rows][-1] == "results://zzz-late"
    assert pane.selection().node.address == "results://hackernews-top"
