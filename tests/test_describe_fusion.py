"""/describe fuses live registry facts with the GitHub corpus + see-also graph."""

import io

from rich.console import Console

import xlii.help_corpus as hc
from xlii.repl_cmds.meta import _describe_corpus_fusion


def _con():
    return Console(file=io.StringIO(), force_terminal=False, width=100)


def test_fusion_prints_prose_topic_and_graph(monkeypatch):
    monkeypatch.setattr(hc, "load_command_doc", lambda m, name, **k: ("EXPANSIVE PROSE", "github"))
    con = _con()
    printed = _describe_corpus_fusion(con, "plan")
    out = con.file.getvalue()
    assert printed is True
    assert "EXPANSIVE PROSE" in out
    assert "always current" in out
    assert "/howto modes" in out  # plan's deep-dive topic
    assert "/describe execute" in out or "/describe rail" in out  # graph edges


def test_fusion_no_doc_still_shows_graph(monkeypatch):
    monkeypatch.setattr(hc, "load_command_doc", lambda m, name, **k: None)
    con = _con()
    printed = _describe_corpus_fusion(con, "loop")
    out = con.file.getvalue()
    assert printed is False
    assert "/howto loop-swarm" in out  # loop's deep-dive topic
    assert "see also" in out.lower()


def test_fusion_unknown_name_is_silent(monkeypatch):
    monkeypatch.setattr(hc, "load_command_doc", lambda m, name, **k: None)
    con = _con()
    printed = _describe_corpus_fusion(con, "totally-unknown-xyz")
    assert printed is False
    assert con.file.getvalue().strip() == ""  # no graph, no prose, no crash


def test_describe_survives_markup_shaped_usage(monkeypatch):
    """Regression: `/describe ftp` crashed the REPL — the /ftp usage string contains
    bracket tokens, and pre-fix a literal closing-tag-shaped "[/path]" hit rich's
    markup parser raw. /describe must escape registry-sourced text."""
    from xlii.repl_cmds import register_all
    from xlii.repl_cmds.meta import _describe_handler

    register_all()
    monkeypatch.setattr(hc, "load_command_doc", lambda m, name, **k: None)
    con = _con()
    assert _describe_handler("/describe ftp", {"console": con}) is True
    out = con.file.getvalue()
    assert "/ftp" in out and "usage:" in out


def test_describe_every_registered_command_renders(monkeypatch):
    """The whole class of bug: every registered command's description + usage must
    render through /describe without a MarkupError, whatever brackets they carry."""
    from xlii.commands import _REPL_COMMANDS
    from xlii.repl_cmds import register_all
    from xlii.repl_cmds.meta import _describe_handler

    register_all()
    monkeypatch.setattr(hc, "load_command_doc", lambda m, name, **k: None)
    for cmd in list(_REPL_COMMANDS):
        con = _con()
        assert _describe_handler(f"/describe {cmd.name}", {"console": con}) is True
