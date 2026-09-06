"""``repl_history_util`` — kind tags and row formatting for the history panel."""

from __future__ import annotations

from xlii.repl_history_util import (
    abbreviate_history_line,
    clear_repl_history,
    format_history_row,
    history_line_kind,
    load_repl_history_strings,
)


def test_history_line_kind_tags():
    assert history_line_kind("/tasks list") == "slash"
    assert history_line_kind("  ?what is x") == "ask"
    assert history_line_kind("!ls -la") == "shell"
    assert history_line_kind("plain prompt") == "text"


def test_abbreviate_long_line():
    long = "x" * 100
    out = abbreviate_history_line(long, max_chars=20)
    assert len(out) <= 20
    assert out.endswith("…")


def test_format_history_row_includes_kind():
    assert format_history_row("/help").startswith("[slash]")


def test_load_repl_history_strings_newest_first(tmp_path):
    from prompt_toolkit.history import FileHistory

    xli = tmp_path / ".xlii"
    xli.mkdir()
    hist = FileHistory(str(xli / "repl_history"))
    hist.append_string("/old line")
    hist.append_string("/recent line")
    lines = load_repl_history_strings(xli)
    assert lines[0] == "/recent line"
    assert "/old line" in lines


def test_clear_repl_history_wipes_file(tmp_path):
    from prompt_toolkit.history import FileHistory

    xli = tmp_path / ".xlii"
    xli.mkdir()
    hist = FileHistory(str(xli / "repl_history"))
    hist.append_string("/keep? no")
    assert (xli / "repl_history").exists()
    assert clear_repl_history(xli) is True
    assert not (xli / "repl_history").exists()
    assert load_repl_history_strings(xli) == []
    # missing file is still a successful clear
    assert clear_repl_history(xli) is True
