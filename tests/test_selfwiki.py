"""The xwiki vendor tier — xlii's shipped self-wiki: bundle, provider scope,
pane presentation, /howto wiki retrieval, and the SOURCE→bundle freshness gate.

Runs against the real bundled corpus (xlii/selfwiki/wiki/) — it ships with the
package, so its presence and shape ARE the product.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from rich.console import Console

from xlii.selfwiki import has_selfwiki, selfwiki_root
from xlii.wiki_retrieval import WikiHit, search_self_wiki, wiki_search_address

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_session(monkeypatch):
    from xlii import active_session

    monkeypatch.setattr(active_session, "_ACTIVE", None)


# --------------------------------------------------------------------------- #
#  Bundle + kernel module
# --------------------------------------------------------------------------- #

def test_bundle_is_present_and_shaped():
    root = selfwiki_root()
    assert root is not None and has_selfwiki()
    pages = list((root / "wiki").glob("*.md"))
    assert len(pages) >= 10                      # the shipped tool corpus
    names = {p.stem for p in pages}
    assert {"xliiwiki", "kernel-architecture", "command-surface"} <= names


def test_bundle_matches_source():
    """SOURCE (docs/selfwiki) → BUNDLE (xlii/selfwiki/wiki) freshness — the
    bundle_selfwiki --check gate, run for real."""
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "bundle_selfwiki.py"),
                        "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_shipped_corpus_has_no_dangling_links():
    import re

    root = selfwiki_root()
    pages = list((root / "wiki").glob("*.md"))
    names = {p.stem for p in pages}
    dangling = [
        (p.stem, m.group(1))
        for p in pages
        for m in re.finditer(r"\[\[([A-Za-z0-9._-]+)\]\]", p.read_text())
        if m.group(1) not in names and m.group(1) not in ("step", "edge")  # TOML syntax
    ]
    assert dangling == []


# --------------------------------------------------------------------------- #
#  Provider — the read-only xwiki:// scope
# --------------------------------------------------------------------------- #

def test_xwiki_lists_reads_and_anchors():
    from xlii.addressing import vfs_list, vfs_read

    nodes = vfs_list("xwiki://")
    assert len(nodes) >= 10
    assert all(n.address.startswith("xwiki://") for n in nodes)
    body = vfs_read("xwiki://xliiwiki").decode()
    assert "semantic" in body.lower()


def test_xwiki_refuses_writes_and_deletes():
    from xlii.addressing import vfs_write
    from xlii.addressing import Address
    from xlii.addressing.builtins.wiki import WikiProvider

    with pytest.raises(PermissionError):
        vfs_write("xwiki://xliiwiki", b"evil")
    prov = WikiProvider(root=selfwiki_root, scheme="xwiki", readonly=True)
    with pytest.raises(PermissionError):
        prov.delete(Address.parse("xwiki://xliiwiki"))


def test_xwiki_stat_preserves_search_query():
    from xlii.addressing import vfs_stat

    node = vfs_stat("xwiki://?q=stash message")
    assert node.address == "xwiki://?q=stash message"
    assert node.kind == "container"


def test_project_wiki_scope_is_untouched(tmp_path, monkeypatch):
    """wiki:// still reads the ambient project — the two scopes never bleed."""
    from types import SimpleNamespace as NS

    from xlii import active_session, wiki as W
    from xlii.addressing import vfs_list

    W.write_page(tmp_path, "mine", "# mine\n\nproject page")
    monkeypatch.setattr(active_session, "_ACTIVE",
                        NS(project=NS(xli_dir=tmp_path), attached_docs=[]))
    names = {n.name for n in vfs_list("wiki://")}
    assert names == {"mine"}                       # project scope: only the project's page
    xnames = {n.name for n in vfs_list("xwiki://")}
    assert "xliiwiki" in xnames and "mine" not in xnames   # vendor scope: only shipped


# --------------------------------------------------------------------------- #
#  Pane — vendor presentation
# --------------------------------------------------------------------------- #

def test_pane_vendor_rows_wear_the_shipped_mark():
    from xlii.panes.wiki import WikiPane

    r = WikiPane("xwiki://").render()
    assert r.title == "xwiki://"
    assert all(row.text.startswith("⌂ ") for row in r.rows)
    assert r.rows[0].address.startswith("xwiki://")


def test_pane_xwiki_results_mode():
    from xlii.panes.wiki import WikiPane

    r = WikiPane("xwiki://?q=stash message").render()
    assert '"stash message"' in r.title
    names = [row.text.split(" ", 1)[1] for row in r.rows]
    assert "gitpain" in names                     # ranked subset, not the whole corpus
    assert len(names) < 15


# --------------------------------------------------------------------------- #
#  Retrieval scheme threading + /howto wiki
# --------------------------------------------------------------------------- #

def test_wikihit_and_search_address_carry_the_scheme():
    assert WikiHit("p", "s", "", 1.0, False, scheme="xwiki").address == "xwiki://p#s"
    assert wiki_search_address("how?", scheme="xwiki") == "xwiki://?q=how"


def test_search_self_wiki_over_the_bundle():
    hits = search_self_wiki(selfwiki_root(), "stash message", limit=3, scheme="xwiki")
    assert hits and hits[0].scheme == "xwiki"
    assert hits[0].address.startswith("xwiki://")


def _ctx():
    sio = io.StringIO()
    state = SimpleNamespace(
        attached_docs=[], howto_mode=False, pending_input="",
        attach_doc=lambda n, c: state.attached_docs.append((n, c)),
        detach_doc=lambda n: False,
    )
    ctx = {
        "console": Console(file=sio, force_terminal=False, width=200),
        "state": state, "project": None, "command_scope": "code",
    }
    return ctx, sio, state


def test_howto_wiki_cites_queues_and_scopes_vendor_context():
    from xlii.repl_cmds import howto, register_all

    register_all()
    ctx, sio, state = _ctx()
    assert howto._howto_handler("/howto wiki how do stash messages work", ctx) is True
    out = sio.getvalue()
    assert "⌂ xwiki://" in out                    # vendor citations, shipped mark
    assert "wiki://gitpain" in out                # gitpain is the top tool answer
    assert state.pending_input == "how do stash messages work"
    assert state.howto_mode is True
    attached = "\n".join(c for _n, c in state.attached_docs)
    assert "xwiki://" in attached                 # scoped vendor context, not the guide


def test_howto_wiki_opens_vendor_results_panel(monkeypatch):
    from xlii.repl_cmds import howto, register_all

    register_all()
    ctx, sio, _state = _ctx()
    opened = []
    import xlii.tui.panels as panels
    monkeypatch.setattr(
        panels, "current_panel_host",
        lambda: SimpleNamespace(open_address=lambda a: opened.append(a) or True,
                                open_doorway=lambda s: False),
    )
    howto._howto_handler("/howto wiki stash message", ctx)
    assert opened == ["xwiki://?q=stash message"]  # the VENDOR results address


def test_howto_wiki_no_match_falls_back_to_the_guide():
    from xlii.repl_cmds import howto, register_all

    register_all()
    ctx, sio, state = _ctx()
    howto._howto_handler("/howto wiki zzz qqq nonexistent nonsense", ctx)
    assert "no self-doc matches" in sio.getvalue()
    assert state.pending_input.startswith("how")


def test_howto_wiki_bare_docks_the_xwiki_pane(monkeypatch):
    from xlii.repl_cmds import howto, register_all

    register_all()
    ctx, sio, _state = _ctx()
    opened = []
    import xlii.tui.panels as panels
    monkeypatch.setattr(
        panels, "current_panel_host",
        lambda: SimpleNamespace(open_doorway=lambda s: opened.append(s) or True,
                                open_address=lambda a: False),
    )
    howto._howto_handler("/howto wiki", ctx)
    assert opened == ["xwiki"]
    assert "xwiki panel docked" in sio.getvalue()


# --------------------------------------------------------------------------- #
#  Attach — the vendor channel
# --------------------------------------------------------------------------- #

def test_xwiki_attach_carries_shipped_header_not_trust_warning():
    from xlii.attach import XWIKI_ATTACH_PREFIX, attach_address, detach_address, is_attached

    state = SimpleNamespace(attached_docs=[])
    state.attach_doc = lambda n, c: state.attached_docs.append((n, c))
    state.detach_doc = lambda n: bool([state.attached_docs.remove(t)
                                       for t in list(state.attached_docs) if t[0] == n])
    assert attach_address(state, "xwiki://xliiwiki") is True
    name, body = state.attached_docs[0]
    assert name == XWIKI_ATTACH_PREFIX + "xliiwiki"
    assert "shipped self-doc" in body
    assert "UNVERIFIED" not in body               # the trust ladder is the project tier's
    assert is_attached(state, "xwiki://xliiwiki") is True
    assert detach_address(state, "xwiki://xliiwiki") is True
    assert is_attached(state, "xwiki://xliiwiki") is False


# --------------------------------------------------------------------------- #
#  Review-pass fixes: export guard, dock routing, no-bundle degradation
# --------------------------------------------------------------------------- #

def test_xwiki_shell_export_never_hands_out_the_bundle_path():
    """The read-only scope exports content snapshots, never a writable path into
    the installed bundle (a real path + `!$EDITOR` would mutate the shipped store)."""
    from xlii.addressing import Address
    from xlii.addressing.builtins.wiki import WikiProvider

    prov = WikiProvider(root=selfwiki_root, scheme="xwiki", readonly=True)
    exp = prov.shell_export(Address.parse("xwiki://xliiwiki"))
    assert exp.kind == "content"                  # a snapshot, not the bundle file
    assert b"semantic" in exp.content.lower() if isinstance(exp.content, bytes) else True
    with pytest.raises(NotImplementedError):
        prov.shell_export(Address.parse("xwiki://"))   # never the bundle dir


def test_dock_routes_xwiki_scheme_to_wiki_pane():
    from xlii.panes.dock import Dock
    from xlii.panes.view import ViewPane

    dock = Dock()
    root_pane = dock.open_address("xwiki://")
    assert type(root_pane).__name__ == "WikiPane"
    assert dock.open_address("xwiki://?q=stash message").render().rows  # results mode mounts
    # a single shipped page (a leaf) falls through to the text viewer
    assert isinstance(dock.open_address("xwiki://xliiwiki"), ViewPane)


def test_no_bundle_degrades_quietly(monkeypatch):
    """A bundleless checkout: /howto wiki falls back to the guide, the pane lists
    empty, and a provider over a None root lists []."""
    import xlii.selfwiki as SW
    from xlii.addressing import Address
    from xlii.addressing.builtins.wiki import WikiProvider
    from xlii.panes.wiki import WikiPane
    from xlii.repl_cmds import howto, register_all

    register_all()
    monkeypatch.setattr(SW, "selfwiki_root", lambda: None)

    ctx, sio, state = _ctx()
    howto._howto_handler("/howto wiki anything at all", ctx)
    assert "no shipped self-wiki" in sio.getvalue()
    assert state.pending_input                      # question still queued for the guide

    assert WikiPane("xwiki://").render().rows == ()  # empty listing, no crash
    prov = WikiProvider(root=lambda: None, scheme="xwiki", readonly=True)
    assert prov.list(Address.parse("xwiki://")) == []
