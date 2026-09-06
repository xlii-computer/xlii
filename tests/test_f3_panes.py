"""sources:// + jobs pane + menu:// (typed-workbenches F3)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii import active_session
from xlii.addressing import vfs_list, vfs_read
from xlii.panes.jobs import JobsPane
from xlii.panes.menu import MenuPane
from xlii.panes.sources import SourcesPane
from xlii.repl_cmds import register_all

register_all()


@pytest.fixture(autouse=True)
def _ambient():
    prev = active_session.set_active_session(None)
    yield
    active_session.set_active_session(prev)


@pytest.fixture
def sources(tmp_path):
    xli = tmp_path / ".xlii"
    (xli / "sources").mkdir(parents=True)
    (xli / "sources" / "jane-doe.toml").write_text(
        'name = "Jane Doe"\ntype = "person"\nnotes = "covers the port authority beat"\n'
    )
    (xli / "sources" / "city-budget.toml").write_text(
        'name = "City Budget PDF"\ntype = "filing"\nurl = "https://city.example/budget.pdf"\n'
    )
    active_session.set_active_session(
        SimpleNamespace(project=SimpleNamespace(xli_dir=xli, project_root=tmp_path)))
    return xli


# --- sources ---------------------------------------------------------------------


def test_sources_list_badges_types(sources):
    nodes = vfs_list("sources://")
    by_name = {n.name: n.extra["source_type"] for n in nodes}
    assert by_name == {"city-budget": "filing", "jane-doe": "person"}


def test_sources_read_returns_the_card(sources):
    card = vfs_read("sources://jane-doe").decode()
    assert "Jane Doe" in card and "port authority" in card


def test_sources_pane_actions_ask_and_view(sources):
    pane = SourcesPane("sources://")
    rendered = pane.render()
    assert rendered.rows[0].text.startswith("city-budget  · filing")
    acts = pane.actions()
    assert [a.name for a in acts] == ["ask", "view"]
    assert acts[0].outcome.kind == "enqueue_turn"
    assert "Jane Doe" not in acts[0].outcome.text  # row 0 is city-budget
    assert "City Budget PDF" in acts[0].outcome.text


def test_sources_empty_without_project():
    assert vfs_list("sources://") == []
    pane = SourcesPane("sources://")
    assert pane.render().empty and pane.actions() == []


# --- jobs (the provider exists; the pane is F3) -------------------------------------


def test_jobs_pane_empty_without_session():
    pane = JobsPane("jobs://")
    rendered = pane.render()
    assert not rendered.empty
    assert rendered.rows[0].address.endswith("#clear")
    assert pane.actions()[0].name == "clear"


def test_jobs_pane_actions_reference_the_job(monkeypatch):
    from xlii.addressing import Node

    pane = JobsPane("jobs://")
    monkeypatch.setattr(
        "xlii.panes.jobs.vfs_list",
        lambda addr: [Node(address="jobs://j1", name="▶ j1 · running · nightly", kind="leaf")],
    )
    pane.mount("jobs://")
    rows = pane.render().rows
    assert rows[0].address.endswith("#clear")
    assert rows[1].text.startswith("▶ j1")
    acts = pane.actions()
    assert acts[0].name == "clear"
    assert acts[1].outcome.kind == "retarget_slot"
    assert acts[2].outcome.kind == "prefill"
    assert acts[2].outcome.text == "/jobs cancel j1"


# --- menu ----------------------------------------------------------------------------


def test_menu_lists_registered_commands():
    nodes = vfs_list("menu://")
    names = {n.name for n in nodes}
    assert {"workbench", "providers", "tasks"} <= names
    wb = next(n for n in nodes if n.name == "workbench")
    assert wb.extra["category"] == "session" and "workbench" in wb.extra["usage"]


def test_menu_read_is_a_command_card():
    card = vfs_read("menu://workbench").decode()
    assert "/workbench" in card and "category: session" in card


def test_menu_pane_groups_and_seeds():
    pane = MenuPane("menu://")
    rendered = pane.render()
    cats = [r.text for r in rendered.rows if r.address == "key:noop"]
    assert any("mode" in c for c in cats)  # category header rows exist
    # select the /tasks command by address (never by index — headers interleave)
    pane.mount(pane.address, select="menu://tasks")
    acts = pane.actions()
    assert acts[0].name == "seed" and acts[0].outcome.kind == "prefill"
    assert acts[0].outcome.text == "/tasks "  # takes args → trailing space


def test_menu_pane_seed_no_args_has_no_space():
    pane = MenuPane("menu://")
    pane.mount(pane.address, select="menu://workbench")
    usage = next(n for n in pane._nodes if n.name == "workbench").extra["usage"]
    expected = "/workbench " if usage != "/workbench" else "/workbench"
    assert pane.actions()[0].outcome.text == expected


def test_dock_picks_f3_panes(sources):
    from xlii.panes.dock import Dock

    dock = Dock(slots=("A",))
    assert isinstance(dock.open_address("sources://", slot="A"), SourcesPane)
    assert isinstance(dock.open_address("menu://", slot="A"), MenuPane)
    assert isinstance(dock.open_address("jobs://", slot="A"), JobsPane)


def test_chat_row_has_research_power_tabs(sources):
    """Chat pack mounts fetch · keep · show panes (ex-research peer pack)."""
    from xlii.face_panes import FaceDeck
    from xlii.workbench import BUILTIN_WORKBENCHES
    from xlii.ws_protocol import serialize_event

    state = SimpleNamespace(
        workbench=BUILTIN_WORKBENCHES["chat"], shell_cwd=None,
        project=SimpleNamespace(project_root=sources.parent, xli_dir=sources))
    active_session.set_active_session(state)
    server = SimpleNamespace(state=state, sent=[], send=lambda o: server.sent.append(o),
                             submit=lambda t: True)
    deck = FaceDeck(server)
    snap = serialize_event(deck.snapshot())
    assert {p["id"] for p in snap["panes"]} == {
        "menu", "locker", "bookmarks", "sources", "wiki", "artifacts", "canvas",
        "results", "plugins",
    }


# --- live boards + scoped menu (review follow-ups) -----------------------------------


def _job_nodes(*ids):
    from xlii.addressing import Node

    return [Node(address=f"jobs://{j}", name=f"▶ {j} · running · nightly", kind="leaf")
            for j in ids]


def test_jobs_pane_cancel_seeds_the_job_id(monkeypatch):
    monkeypatch.setattr("xlii.panes.jobs.vfs_list", lambda addr: _job_nodes("j1", "j2"))
    pane = JobsPane("jobs://")
    pane.mount("jobs://", select="jobs://j2")  # the face selects by ADDRESS
    assert pane.selection().node.address == "jobs://j2"
    assert pane.actions()[2].outcome.text == "/jobs cancel j2"


def test_jobs_pane_is_live_and_keeps_its_selection(monkeypatch):
    monkeypatch.setattr("xlii.panes.jobs.vfs_list", lambda addr: [])
    pane = JobsPane("jobs://")
    assert pane.render().rows[0].address.endswith("#clear")
    monkeypatch.setattr("xlii.panes.jobs.vfs_list", lambda addr: _job_nodes("j1", "j2"))
    assert [r.address for r in pane.render().rows] == ["jobs://#clear", "jobs://j1", "jobs://j2"]
    pane.mount(pane.address, select="jobs://j2")
    monkeypatch.setattr("xlii.panes.jobs.vfs_list", lambda addr: _job_nodes("j0", "j1", "j2"))
    rendered = pane.render()  # a job spawned ahead of it — selection follows the id
    assert [r.selected for r in rendered.rows] == [False, False, False, True]


def test_sources_pane_sees_a_card_authored_after_mount(sources):
    pane = SourcesPane("sources://")
    pane.mount(pane.address, select="sources://jane-doe")
    (sources / "sources" / "port-authority.toml").write_text(
        'name = "Port Authority"\ntype = "api"\n')
    rows = pane.render().rows
    assert [r.address for r in rows] == [
        "sources://city-budget", "sources://jane-doe", "sources://port-authority"]
    assert pane.selection().node.address == "sources://jane-doe"  # kept by address


def test_menu_is_scoped_to_the_active_surface():
    """/status is registered twice — one per surface. The listing is scoped the
    way dispatch is, so each address is unique and reads the live card."""
    from xlii.commands import iter_repl_commands

    def names(scope):
        active_session.set_active_session(SimpleNamespace(command_scope=scope))
        return [n.name for n in vfs_list("menu://")]

    code, chat = names("code"), names("chat")
    assert len(set(code)) == len(code) and len(set(chat)) == len(chat)  # unique addresses
    both = {c.name for c in iter_repl_commands() if "code" in c.repls and "chat" in c.repls}
    code_only = {c.name for c in iter_repl_commands() if c.repls == ["code"]} - both
    chat_only = {c.name for c in iter_repl_commands() if c.repls == ["chat"]} - both
    assert code_only <= set(code) and not (code_only & set(chat) - {"status"})
    assert chat_only <= set(chat) and not (chat_only & set(code) - {"status"})


def test_menu_card_follows_the_active_surface():
    """The doubly-registered /status reads as the ACTIVE surface's command."""
    active_session.set_active_session(SimpleNamespace(command_scope="code"))
    code_card = vfs_read("menu://status").decode()
    active_session.set_active_session(SimpleNamespace(command_scope="chat"))
    chat_card = vfs_read("menu://status").decode()
    assert "repls: code" in code_card and "repls: chat" in chat_card
    assert code_card != chat_card
