"""Tests for the pane layer — client #1, the ExplorerPane over the VFS.

Headless: a pane is ``(address, selection) → rendered Nodes``, no terminal required."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.addressing import Address
from xlii.panes import NAVIGATE, PREFILL, RETARGET_SLOT, Action, Pane, Rendered
from xlii.panes.explorer import ExplorerPane, reconstruct


# --- a small fixture tree ----------------------------------------------------


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "b.txt").write_text("hello")
    (tmp_path / "a.txt").write_text("hi")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.txt").write_text("deep")
    return tmp_path


def _addr(p) -> str:
    return f"file://{p}"


def _files(r):
    return [row.text for row in r.rows if row.kind != "caption"]


# --- contract conformance ----------------------------------------------------


def test_explorer_satisfies_pane_protocol(tree):
    pane = ExplorerPane(_addr(tree))
    assert isinstance(pane, Pane)


def test_mount_lists_containers_first(tree):
    pane = ExplorerPane(_addr(tree))
    r = pane.render()
    assert isinstance(r, Rendered)
    names = _files(r)
    # provider sorts containers first, then case-insensitive by name
    assert names == ["sub/", "a.txt", "b.txt"]
    assert r.rows[0].kind == "caption" and r.rows[0].address == "key:hidden"


def test_mount_selects_first_node(tree):
    pane = ExplorerPane(_addr(tree))
    assert pane.selection().node.name == "sub"
    assert [row.selected for row in pane.render().rows if row.kind != "caption"] == [True, False, False]


def test_mount_unbrowseable_scheme_raises(tree):
    with pytest.raises(NotImplementedError):
        ExplorerPane("nope://x")  # no provider registered for this scheme


def test_empty_dir_renders_empty(tmp_path):
    pane = ExplorerPane(_addr(tmp_path))
    r = pane.render()
    assert r.empty is True
    assert _files(r) == []
    assert pane.selection().node is None


# --- local navigation (handle) ----------------------------------------------


def test_handle_down_up_moves_selection(tree):
    pane = ExplorerPane(_addr(tree))
    assert pane.handle("down") is True
    assert pane.selection().node.name == "a.txt"
    pane.handle("down")
    assert pane.selection().node.name == "b.txt"
    pane.handle("up")
    assert pane.selection().node.name == "a.txt"


def test_handle_down_clamps_at_end(tree):
    pane = ExplorerPane(_addr(tree))
    for _ in range(10):
        pane.handle("down")
    assert pane.selection().node.name == "b.txt"  # last row, no wrap
    pane.handle("home")
    assert pane.selection().node.name == "sub"
    pane.handle("end")
    assert pane.selection().node.name == "b.txt"


def test_enter_container_navigates_by_child_address(tree):
    pane = ExplorerPane(_addr(tree))  # "sub" is focused
    assert pane.handle("enter") is True
    assert pane.address == Address.parse(_addr(tree / "sub"))
    assert _files(pane.render()) == ["deep.txt"]


def test_enter_leaf_is_not_local_nav(tree):
    pane = ExplorerPane(_addr(tree))
    pane.handle("down")  # focus a.txt (a leaf)
    assert pane.handle("enter") is False  # surface should fall through to actions()
    assert pane.address == Address.parse(_addr(tree))  # unchanged


def test_back_walks_to_parent(tree):
    pane = ExplorerPane(_addr(tree / "sub"))
    assert pane.handle("back") is True
    assert pane.address == Address.parse(_addr(tree))


def test_back_at_filesystem_root_is_noop(tree):
    pane = ExplorerPane("file:///")
    assert pane.handle("back") is False


def test_unknown_key_is_unhandled(tree):
    pane = ExplorerPane(_addr(tree))
    assert pane.handle("f5") is False


# --- actions: bounded outcomes over the selection ----------------------------


def test_container_actions(tree):
    pane = ExplorerPane(_addr(tree))  # "sub" focused
    acts = pane.actions()
    assert all(isinstance(a, Action) for a in acts)
    by_name = {a.name: a for a in acts}
    assert by_name["open"].outcome.kind == NAVIGATE
    assert by_name["open"].outcome.address == _addr(tree / "sub")
    assert by_name["open-other"].outcome.kind == RETARGET_SLOT


def test_leaf_actions(tree):
    pane = ExplorerPane(_addr(tree))
    pane.handle("down")  # a.txt
    acts = pane.actions()
    assert [a.name for a in acts] == ["view"]
    assert acts[0].outcome.kind == RETARGET_SLOT
    assert acts[0].outcome.address == _addr(tree / "a.txt")


def test_empty_listing_offers_no_actions(tmp_path):
    pane = ExplorerPane(_addr(tmp_path))
    assert pane.actions() == []


def test_via_parent_unwraps_inner():
    inner = Address.parse("sftp://appbox/srv/apps/fuel.xlii-code.com/assets")
    via = Address.parse(f"via://xliiec2/{inner}")
    parent = ExplorerPane._parent(via)
    assert parent is not None
    assert str(parent) == "via://xliiec2/sftp://appbox/srv/apps/fuel.xlii-code.com"


def test_parent_stops_at_remote_files_root(monkeypatch):
    """Files over SFTP cannot walk out of the project's files_root (xlii-venv)."""
    from xlii import active_session

    fence = "sftp://xliiec2//home/admin/serve-sandbox"
    proj = SimpleNamespace(
        project_root="/tmp/stub",
        files_root=fence,
        node="xliiec2",
        remote_path="/home/admin/serve-sandbox",
    )
    monkeypatch.setattr(active_session, "_ACTIVE", SimpleNamespace(project=proj))

    child = Address.parse(f"{fence}/src/lib")
    parent = ExplorerPane._parent(child)
    assert parent is not None
    assert str(parent) == f"{fence}/src"

    assert ExplorerPane._parent(Address.parse(fence)) is None

    # Remotes host browse (not under this desk's Files root) still walks up.
    sibling = Address.parse("sftp://xliiec2//home/admin")
    up = ExplorerPane._parent(sibling)
    assert up is not None
    assert str(up) == "sftp://xliiec2//home"


def test_parent_stops_at_local_project_root(tree, monkeypatch):
    from xlii import active_session

    proj = SimpleNamespace(project_root=tree, files_root="")
    monkeypatch.setattr(active_session, "_ACTIVE", SimpleNamespace(project=proj))

    pane = ExplorerPane(_addr(tree / "sub"))
    assert pane.handle("back") is True
    assert pane.address == Address.parse(_addr(tree))
    assert pane.handle("back") is False


def test_via_parent_stops_at_inner_files_root(monkeypatch):
    from xlii import active_session

    fence = "sftp://appbox/srv/apps/fuel.xlii-code.com"
    proj = SimpleNamespace(project_root="/tmp/stub", files_root=fence)
    monkeypatch.setattr(active_session, "_ACTIVE", SimpleNamespace(project=proj))

    via = Address.parse(f"via://xliiec2/{fence}/assets")
    parent = ExplorerPane._parent(via)
    assert parent is not None
    assert str(parent) == f"via://xliiec2/{fence}"
    assert ExplorerPane._parent(Address.parse(f"via://xliiec2/{fence}")) is None


def test_remote_listing_offers_use_as_project_files():
    pane = ExplorerPane.__new__(ExplorerPane)
    pane._address = Address.parse("sftp://appbox/srv/apps/foo")
    pane._all = ()
    pane._nodes = ()
    pane._selected = ""
    pane._show_hidden = False
    acts = {a.name: a for a in pane.actions()}
    assert "use-files" in acts
    assert acts["use-files"].outcome.kind == PREFILL
    assert acts["use-files"].outcome.text == "/project files sftp://appbox/srv/apps/foo"


# --- reading a leaf for a paired view pane -----------------------------------


def test_read_selected_leaf(tree):
    pane = ExplorerPane(_addr(tree))
    pane.handle("down")  # a.txt
    assert pane.read_selected() == b"hi"


def test_read_selected_container_raises(tree):
    pane = ExplorerPane(_addr(tree))  # sub is a container
    with pytest.raises(IsADirectoryError):
        pane.read_selected()


# --- refresh: re-projection picks up VFS changes -----------------------------


def test_refresh_picks_up_new_node_and_keeps_selection(tree):
    pane = ExplorerPane(_addr(tree))
    pane.handle("down")  # a.txt
    (tree / "c.txt").write_text("new")
    pane.refresh()
    assert pane.selection().node.name == "a.txt"  # selection survived
    assert "c.txt" in [row.text for row in pane.render().rows]


def test_refresh_clamps_selection_when_node_vanishes(tree):
    pane = ExplorerPane(_addr(tree))
    pane.handle("end")  # b.txt
    (tree / "b.txt").unlink()
    pane.refresh()
    # selected node gone → falls back to the first node
    assert pane.selection().node.name == "sub"


# --- the state-ownership rule: pure projection of (address, selection) -------


def test_reconstruct_is_byte_identical(tree):
    pane = ExplorerPane(_addr(tree))
    pane.handle("down")
    pane.handle("down")  # b.txt focused
    rebuilt = reconstruct(pane)
    assert rebuilt.address == pane.address
    assert rebuilt.selection() == pane.selection()
    assert rebuilt.render() == pane.render()  # byte-identical projection


def test_reconstruct_after_navigation(tree):
    pane = ExplorerPane(_addr(tree))
    pane.handle("enter")  # into sub/
    rebuilt = reconstruct(pane)
    assert rebuilt.render() == pane.render()


# --- works across schemes (project://) ---------------------------------------


def test_explorer_over_project_scheme(tree, monkeypatch):
    # make `tree` an xlii project rooted at cwd so project://. resolves to it
    (tree / ".xlii").mkdir()
    monkeypatch.chdir(tree)
    pane = ExplorerPane("project://.")
    names = _files(pane.render())
    assert "a.txt" in names and "sub/" in names
    assert ".xlii/" not in names  # hidden by default
    # navigate into sub via its child address, then back to the project root
    sub = next(row for row in pane.render().rows if row.kind == "container" and row.text == "sub/")
    pane.mount(sub.address)
    assert pane.handle("back") is True
    assert pane.address.scheme == "project"


def test_parent_backs_a_keyed_root_scheme_out_to_its_root():
    from xlii.addressing import Address
    from xlii.panes.explorer import ExplorerPane

    assert str(ExplorerPane._parent(Address.parse("docs://conventions"))) == "docs://"
    assert ExplorerPane._parent(Address.parse("docs://")) is None  # already at the root


# --- attach/view/detach + green dots for attachable schemes (docs/marks) -----


def test_docs_leaf_offers_view_attach_detach(tmp_path, monkeypatch):
    import xlii.doc as docmod

    monkeypatch.setattr(docmod, "DOCS_DIR", tmp_path)
    (tmp_path / "conventions.md").write_text("# Conventions")
    from xlii.panes import ATTACH, DETACH, RETARGET_SLOT
    from xlii.panes.explorer import ExplorerPane

    pane = ExplorerPane("docs://")               # lists the doc store; first doc selected (a leaf)
    acts = {a.name: a for a in pane.actions()}
    assert set(acts) == {"view", "attach", "detach"}
    assert acts["view"].outcome.kind == RETARGET_SLOT
    assert acts["attach"].outcome.kind == ATTACH and acts["attach"].outcome.address == "docs://conventions"
    assert acts["detach"].outcome.kind == DETACH


def test_file_leaf_stays_view_only(tree):
    # a plain file browser is not attachable — no attach/detach buttons appear
    pane = ExplorerPane(_addr(tree))
    pane.handle("down")  # a.txt (a leaf)
    assert [a.name for a in pane.actions()] == ["view"]


def test_explorer_green_dots_attached_leaves(tmp_path, monkeypatch):
    import xlii.doc as docmod

    monkeypatch.setattr(docmod, "DOCS_DIR", tmp_path)
    (tmp_path / "alpha.md").write_text("a")
    (tmp_path / "beta.md").write_text("b")
    from xlii import active_session
    from xlii.panes.explorer import ExplorerPane

    monkeypatch.setattr(active_session, "_ACTIVE", SimpleNamespace(attached_docs=[("alpha", "body")]))
    pane = ExplorerPane("docs://")
    by = {r.text: r for r in pane.render().rows}
    assert by["alpha"].accent is True    # attached → green dot
    assert by["beta"].accent is False


def test_file_scheme_pays_nothing_for_riding(tree, monkeypatch):
    # a non-attachable scheme never consults the session — no accents, no import cost per row
    from xlii import active_session
    from xlii.panes.explorer import ExplorerPane

    monkeypatch.setattr(active_session, "_ACTIVE", SimpleNamespace(attached_docs=[("x", "y")]))
    pane = ExplorerPane(_addr(tree))
    assert all(not r.accent for r in pane.render().rows)


def test_file_listing_marks_registered_project_folders(tmp_path, monkeypatch):
    """Scratch Files (~) greens folders that are xlii desks."""
    from xlii import registry as R
    from xlii.registry import Registry, RegistryEntry

    home = tmp_path / "home"
    home.mkdir()
    proj = home / "myapp"
    proj.mkdir()
    (proj / ".xlii").mkdir()
    (proj / ".xlii" / "project.json").write_text("{}")
    (home / "Downloads").mkdir()
    (home / "notes.txt").write_text("hi")

    monkeypatch.setattr(R, "REGISTRY_FILE", tmp_path / "projects.json")
    reg = Registry()
    reg.upsert(RegistryEntry(
        path=str(proj.resolve()), collection_id="", name="myapp", created_at="now",
    ))
    reg.save()

    pane = ExplorerPane(_addr(home))
    by = {r.text: r for r in pane.render().rows if r.kind != "caption"}
    assert by["myapp/"].accent is True
    assert by["Downloads/"].accent is False
    assert by["notes.txt"].accent is False


def test_file_listing_marks_unregistered_xlii_folder(tmp_path, monkeypatch):
    from xlii import registry as R
    from xlii.registry import Registry

    home = tmp_path / "home"
    home.mkdir()
    orphan = home / "old-lab"
    orphan.mkdir()
    (orphan / ".xlii").mkdir()
    (orphan / ".xlii" / "project.json").write_text("{}")
    (home / "random").mkdir()

    monkeypatch.setattr(R, "REGISTRY_FILE", tmp_path / "projects.json")
    Registry().save()

    pane = ExplorerPane(_addr(home))
    by = {r.text: r for r in pane.render().rows if r.kind != "caption"}
    assert by["old-lab/"].accent is True
    assert by["random/"].accent is False


def test_dotfiles_hidden_by_default(tmp_path):
    (tmp_path / "visible.txt").write_text("v")
    (tmp_path / ".env").write_text("s")
    (tmp_path / ".cache").mkdir()
    pane = ExplorerPane(_addr(tmp_path))
    r = pane.render()
    assert _files(r) == ["visible.txt"]
    assert r.rows[0].text == "hidden · 2 hidden"


def test_hidden_toggle_shows_dotfiles(tmp_path):
    (tmp_path / "visible.txt").write_text("v")
    (tmp_path / ".env").write_text("s")
    (tmp_path / ".cache").mkdir()
    pane = ExplorerPane(_addr(tmp_path))
    assert pane.handle("hidden") is True
    names = _files(pane.render())
    assert names == [".cache/", ".env", "visible.txt"]
    assert pane.render().rows[0].text == "hidden · on"
    pane.handle("hidden")
    assert _files(pane.render()) == ["visible.txt"]


def test_hidden_toggle_keeps_visible_selection(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / ".env").write_text("s")
    pane = ExplorerPane(_addr(tmp_path))
    assert pane.selection().node.name == "a.txt"
    pane.handle("hidden")
    assert pane.selection().node.name == "a.txt"


def test_hiding_drops_selection_of_a_dotfile(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / ".env").write_text("s")
    pane = ExplorerPane(_addr(tmp_path))
    pane.handle("hidden")
    pane.mount(pane.address, select=_addr(tmp_path / ".env"))
    assert pane.selection().node.name == ".env"
    pane.handle("hidden")
    assert pane.selection().node.name == "a.txt"


def test_docs_listing_has_no_hidden_toggle(tmp_path, monkeypatch):
    import xlii.doc as docmod

    monkeypatch.setattr(docmod, "DOCS_DIR", tmp_path)
    (tmp_path / "conventions.md").write_text("#")
    pane = ExplorerPane("docs://")
    assert all(row.kind != "caption" for row in pane.render().rows)
    assert pane.handle("hidden") is False


def test_session_pref_survives_a_new_pane(tmp_path):
    from xlii import active_session

    (tmp_path / "a.txt").write_text("a")
    (tmp_path / ".env").write_text("s")
    sess = SimpleNamespace()
    prev = active_session.set_active_session(sess)
    try:
        pane = ExplorerPane(_addr(tmp_path))
        pane.handle("hidden")
        assert getattr(sess, "explorer_show_hidden") is True
        other = ExplorerPane(_addr(tmp_path))
        assert ".env" in _files(other.render())
    finally:
        active_session.set_active_session(prev)
