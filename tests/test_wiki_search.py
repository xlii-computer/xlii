"""The wiki retrieval floor — search_pages ranking/trust + its merge into t_search_project.

The point of the floor is that the xlii can *find* its own distilled pages, in every backend mode,
the instant they're written. Wiki pages are always local, so they must surface even when there is
no file index and no Collection."""

from __future__ import annotations

from xlii import wiki as W
from xlii.config import GlobalConfig
from xlii.tools import ToolContext, t_search_project

from tests.helpers import make_project


# --- search_pages (unit) ------------------------------------------------------

def test_search_pages_ranks_and_tags_trust(tmp_path):
    W.write_page(tmp_path, "kernel", "# Kernel\n\nThe kernel owns the turn lifecycle.",
                 sources=["conv://p/t1"])
    W.mark_verified(tmp_path, "kernel")
    W.write_page(tmp_path, "panes", "# Panes\n\nPanes are views; they never own the turn.")

    hits = W.search_pages(tmp_path, "turn lifecycle", 10)
    names = {name for name, _s, _sc, _v in hits}
    assert "kernel" in names                       # matched on 'turn' + 'lifecycle'
    trust = {name: verified for name, _s, _sc, verified in hits}
    assert trust["kernel"] is True                 # verified flag carried through
    if "panes" in trust:
        assert trust["panes"] is False


def test_search_pages_matches_on_title(tmp_path):
    # title is indexed, so a page whose body never repeats its subject still matches.
    W.write_page(tmp_path, "authentication", "# Authentication\n\nWe use bcrypt with a pepper.")
    assert any(n == "authentication" for n, *_ in W.search_pages(tmp_path, "authentication", 5))


def test_search_pages_empty_and_no_match(tmp_path):
    W.write_page(tmp_path, "p", "# P\n\nbody")
    assert W.search_pages(tmp_path, "   ", 5) == []          # operator/empty query
    assert W.search_pages(tmp_path, "zzznotpresent", 5) == []


def test_search_pages_no_pages_dir(tmp_path):
    assert W.search_pages(tmp_path / "empty", "anything", 5) == []


# --- merge into t_search_project ---------------------------------------------

def _ctx(root, *, collection_id=None):
    proj = make_project(root)
    proj.collection_id = collection_id
    return ToolContext(project=proj, clients=None, cfg=GlobalConfig())


def test_wiki_surfaces_even_without_a_file_index(tmp_path):
    # local-only project, no index built yet — the wiki is the ONLY ground, and it must show
    # (not a "(no index)" error).
    xli = tmp_path / ".xlii"
    W.write_page(xli, "kernel", "# Kernel\n\nThe kernel owns the turn.", sources=["conv://p/t1"])
    res = t_search_project(_ctx(tmp_path), {"query": "kernel turn"})
    assert res.is_error is False
    assert "wiki://kernel" in res.content
    assert "UNVERIFIED" in res.content                # born unverified → flagged loudly


def test_wiki_appends_below_local_file_results(tmp_path):
    (tmp_path / "readme.md").write_text("This project parses invoices.\n")
    from xlii.storage import LocalIndex

    proj = make_project(tmp_path)
    LocalIndex(proj).rebuild(GlobalConfig())          # a real file index with a hit
    xli = tmp_path / ".xlii"
    W.write_page(xli, "invoices", "# Invoices\n\nInvoice totals go to a sheet.")

    res = t_search_project(_ctx(tmp_path), {"query": "invoices"})
    assert "readme.md" in res.content                 # file-index hit
    assert "wiki://invoices" in res.content           # wiki hit, merged in
    assert res.content.index("readme.md") < res.content.index("wiki://invoices")  # wiki below


def test_no_wiki_pages_leaves_search_output_unchanged(tmp_path):
    # nothing in the wiki → no wiki header, and the underlying no-index error is preserved.
    res = t_search_project(_ctx(tmp_path), {"query": "anything"})
    assert "wiki" not in res.content.lower()
    assert res.is_error is True                        # unchanged: (no local index yet …)


def test_verified_page_shows_trusted_marker(tmp_path):
    xli = tmp_path / ".xlii"
    W.write_page(xli, "facts", "# Facts\n\nChecked kernel facts.", sources=["conv://p/t1"])
    W.mark_verified(xli, "facts")
    res = t_search_project(_ctx(tmp_path), {"query": "kernel facts"})
    assert "wiki://facts" in res.content and "✓ verified" in res.content
