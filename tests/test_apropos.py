"""The help-search engines (/help --search · --topics) and the search_corpus seam.

/apropos, /search-help, and /manuals were absorbed as hidden aliases of /help
(2026-07-10, vector 2 of the selfbuild plan) — the old spellings must keep
working byte-identically while dropping out of the rendered listings.

These use the bundled (offline) help manifest and the live command registry, so
they run without network or an xAI account.
"""

import io

from rich.console import Console

from xlii.commands import find_repl_command, get_repl_help
from xlii.help_corpus import SearchHit, load_manifest, search_corpus
from xlii.repl_cmds import apropos, register_all

register_all()


def _ctx():
    sio = io.StringIO()
    ctx = {
        "console": Console(file=sio, force_terminal=False),
        "command_scope": "code",
    }
    return ctx, sio


# --- registration -----------------------------------------------------------


def test_absorbed_doors_resolve_to_help():
    # The old doors keep resolving forever — as hidden aliases of /help.
    for tok in ("/apropos", "/search-help", "/manuals"):
        for repl in ("code", "chat"):
            cmd = find_repl_command(tok, repl)
            assert cmd is not None, f"{tok} missing in {repl}"
            assert cmd.name == "help", f"{tok} should alias /help, got /{cmd.name}"
    # /man is the classic Unix nod — an alias of /describe (registered in meta.py)
    assert find_repl_command("/man", "code").name == "describe"


def test_absorbed_doors_hidden_from_listing():
    # Aliases never render: the listing shows one help door, not three.
    for repl in ("code", "chat"):
        text = get_repl_help(repl)
        assert "/help" in text
        assert "/apropos" not in text
        assert "/manuals" not in text


def test_help_search_flag_matches_apropos():
    from xlii.repl_cmds.meta import _help_handler

    ctx_a, sio_a = _ctx()
    assert _help_handler("/help --search rewind", ctx_a) is True
    ctx_b, sio_b = _ctx()
    assert apropos._apropos_handler("/apropos rewind", ctx_b) is True
    assert sio_a.getvalue() == sio_b.getvalue()


def test_help_topics_flag_matches_manuals():
    from xlii.repl_cmds.meta import _help_handler

    ctx_a, sio_a = _ctx()
    assert _help_handler("/help --topics", ctx_a) is True
    ctx_b, sio_b = _ctx()
    assert apropos._manuals_handler("/manuals", ctx_b) is True
    assert sio_a.getvalue() == sio_b.getvalue()


def test_alias_dispatch_through_help_handler():
    # The registry hands /apropos lines to the help handler — it must route
    # by invoked token, byte-identical to the old command.
    from xlii.repl_cmds.meta import _help_handler

    ctx_a, sio_a = _ctx()
    assert _help_handler("/apropos rewind", ctx_a) is True
    ctx_b, sio_b = _ctx()
    assert apropos._apropos_handler("/apropos rewind", ctx_b) is True
    assert sio_a.getvalue() == sio_b.getvalue()

    ctx_c, sio_c = _ctx()
    assert _help_handler("/manuals", ctx_c) is True
    assert "topic" in sio_c.getvalue().lower()


# --- search_corpus seam (#2) ------------------------------------------------


def test_search_hit_is_a_plain_tuple():
    h = SearchHit("command", "rewind", 100, "undo recent turns")
    assert h == ("command", "rewind", 100, "undo recent turns")
    assert (h.kind, h.name, h.score, h.description) == h


def test_search_corpus_blank_query_is_empty():
    manifest = load_manifest()
    assert search_corpus(manifest, "") == []
    assert search_corpus(manifest, "   ") == []


def test_search_corpus_finds_command_by_name_top_ranked():
    hits = search_corpus(load_manifest(), "rewind")
    assert any(h.kind == "command" and h.name == "rewind" for h in hits)
    # an exact command-name match ranks first
    assert hits[0].kind == "command" and hits[0].name == "rewind"


def test_search_corpus_finds_topic():
    hits = search_corpus(load_manifest(), "troubleshoot")
    assert any(h.kind == "topic" and h.name == "troubleshoot" for h in hits)


def test_search_corpus_ranked_descending_and_positive():
    hits = search_corpus(load_manifest(), "plan")
    assert hits, "expected matches for 'plan'"
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(h.score > 0 for h in hits)


def test_search_corpus_tag_match_finds_topic():
    # 'keys' is a tag/alias in the corpus (config-models) — keyword search hits it.
    hits = search_corpus(load_manifest(), "keys")
    assert any(h.kind == "topic" for h in hits)


# --- /apropos handler -------------------------------------------------------


def test_apropos_lists_hits():
    ctx, sio = _ctx()
    assert apropos._apropos_handler("/apropos rewind", ctx) is True
    out = sio.getvalue()
    assert "apropos" in out
    assert "/rewind" in out


def test_apropos_requires_keyword():
    ctx, sio = _ctx()
    assert apropos._apropos_handler("/apropos", ctx) is True
    assert "usage" in sio.getvalue()


def test_apropos_reports_no_matches():
    ctx, sio = _ctx()
    assert apropos._apropos_handler("/apropos zxqwvplugh", ctx) is True
    assert "no matches" in sio.getvalue()


# --- /manuals handler -------------------------------------------------------


def test_manuals_lists_topic_index():
    ctx, sio = _ctx()
    assert apropos._manuals_handler("/manuals", ctx) is True
    assert "topic" in sio.getvalue().lower()
