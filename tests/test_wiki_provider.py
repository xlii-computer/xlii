"""wiki:// — the semantic-memory provider over the ambient project's .xlii/wiki.

Headless: install a fake ambient session pointing at a tmp xli_dir, then browse/read through
the VFS helpers, including the anchor-honoring section read (wiki://page#section)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii import wiki as W
from xlii.addressing import Address, resolve, vfs_list, vfs_read, vfs_stat


@pytest.fixture(autouse=True)
def _no_session(monkeypatch):
    from xlii import active_session

    monkeypatch.setattr(active_session, "_ACTIVE", None)


def _session_at(monkeypatch, xli_dir):
    from xlii import active_session

    monkeypatch.setattr(
        active_session, "_ACTIVE",
        SimpleNamespace(project=SimpleNamespace(xli_dir=xli_dir)),
    )


def test_wiki_root_lists_pages_with_trust_and_brief(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    W.write_page(tmp_path, "arch", "# One kernel\n\nbody", sources=["conv://p/t1"])
    W.write_page(tmp_path, "facts", "# Checked facts\n\nbody")
    W.mark_verified(tmp_path, "facts")

    nodes = vfs_list("wiki://")
    assert [n.name for n in nodes] == ["arch", "facts"]
    by = {n.name: n for n in nodes}
    assert all(n.kind == "leaf" and n.extra["type"] == "wiki" for n in nodes)
    assert by["arch"].extra["verified"] is False and by["arch"].extra["brief"] == "One kernel"
    assert by["facts"].extra["verified"] is True
    assert by["arch"].extra["sources"] == ["conv://p/t1"]


def test_wiki_read_page_and_stat(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    W.write_page(tmp_path, "arch", "# One kernel\n\nEverything is a pane.")
    assert b"Everything is a pane." in vfs_read("wiki://arch")
    node = vfs_stat("wiki://arch")
    assert node.kind == "leaf" and node.extra["verified"] is False
    assert resolve("wiki://arch").ok is True
    assert resolve("wiki://ghost").ok is False


def test_wiki_anchor_reads_just_the_section(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    W.write_page(tmp_path, "notes",
                 "# Notes\n\n## The event seam\n\ninbound only\n\n## Other\n\nrest")
    text = vfs_read("wiki://notes#the-event-seam").decode()
    assert text.startswith("## The event seam")
    assert "inbound only" in text and "rest" not in text
    with pytest.raises(FileNotFoundError):
        vfs_read("wiki://notes#no-such-section")


def test_wiki_outside_a_session_degrades_to_empty(tmp_path):
    assert vfs_list("wiki://") == []
    assert resolve("wiki://anything").ok is False
    with pytest.raises(FileNotFoundError):
        vfs_read("wiki://anything")


def test_wiki_root_resolves_and_stats_as_container(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    assert resolve("wiki://").ok is True
    assert vfs_stat("wiki://").kind == "container"
    assert Address.parse("wiki://arch#sec").anchor == "sec"   # the grammar carries the anchor


# --- write side (WritableVfs) -------------------------------------------------

def test_wiki_is_writable_and_create_read_roundtrips(tmp_path, monkeypatch):
    from xlii.addressing import supports_write, vfs_write

    _session_at(monkeypatch, tmp_path)
    assert supports_write("wiki") is True
    vfs_write("wiki://arch", b"# One kernel\n\nEverything is a pane.")
    assert b"Everything is a pane." in vfs_read("wiki://arch")
    page = W.read_page(tmp_path, "arch")
    assert page.verified is False and page.sources == []   # born unverified, no provenance


def test_wiki_body_edit_keeps_sources_and_resets_verified(tmp_path, monkeypatch):
    from xlii.addressing import vfs_write

    _session_at(monkeypatch, tmp_path)
    W.write_page(tmp_path, "arch", "old body", sources=["conv://p/t1"], verified=True)
    vfs_write("wiki://arch", b"new body text")
    page = W.read_page(tmp_path, "arch")
    assert page.sources == ["conv://p/t1"]   # provenance survives a body edit
    assert page.verified is False            # but trust resets — changed claims need re-verifying


def test_wiki_delete_removes_and_missing_raises(tmp_path, monkeypatch):
    from xlii.addressing import vfs_delete, vfs_exists

    _session_at(monkeypatch, tmp_path)
    W.write_page(tmp_path, "gone", "x")
    vfs_delete("wiki://gone")
    assert vfs_exists("wiki://gone") is False
    with pytest.raises(FileNotFoundError):
        vfs_delete("wiki://gone")


def test_wiki_root_write_and_mkdir_are_refused(tmp_path, monkeypatch):
    from xlii.addressing import vfs_mkdir, vfs_write

    _session_at(monkeypatch, tmp_path)
    with pytest.raises(IsADirectoryError):
        vfs_write("wiki://", b"x")            # the root is not a page
    with pytest.raises(NotImplementedError):
        vfs_mkdir("wiki://anything")          # pages are a flat namespace


def test_wiki_mountable_root_ignores_ambient_session(tmp_path):
    """A WikiProvider mounted at a fixed root reads THAT root — the architected-for-two scope seam,
    independent of the ambient session (which the autouse fixture has cleared to None)."""
    from xlii.addressing import Address
    from xlii.addressing._builtin_providers import WikiProvider

    W.write_page(tmp_path, "glob", "# Global\n\nself-doc body")
    prov = WikiProvider(root=tmp_path)                 # fixed-path scope
    assert prov.exists(Address.parse("wiki://glob")) is True
    assert b"self-doc body" in prov.read(Address.parse("wiki://glob"))
    prov_cb = WikiProvider(root=lambda: tmp_path)      # callable scope
    assert prov_cb.exists(Address.parse("wiki://glob")) is True


def test_stat_preserves_search_query_for_results_pane(tmp_path, monkeypatch):
    """`wiki://?q=…` stats as the container root WITH its query intact, so the Dock
    mounts WikiPane in results mode (the seam /askjo wiki opens)."""
    _session_at(monkeypatch, tmp_path)
    W.write_page(tmp_path, "gitpain", "# gitpain\n\nstash needs a message")
    node = vfs_stat("wiki://?q=stash message")
    assert node.address == "wiki://?q=stash message"   # query survives the stat
    assert node.kind == "container"                     # routes to WikiPane
    # a plain root still stats identically to before
    assert vfs_stat("wiki://").address == "wiki://"
