"""_TranscriptConsole streaming: end="" chunks must join into ONE block.

Regression for /cursor · /delegate in the TUI: their ACP streams print each
token chunk with `console.print(chunk, end="")`. A real terminal joins those on
one flowing line, but the transcript shim emitted one block widget PER CALL —
shredding the answer into one word per line hugging the left margin.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from rich.text import Text  # noqa: E402

from xlii.tui_textual import _TranscriptConsole  # noqa: E402


def _shim():
    writes: list = []
    app = SimpleNamespace(write_block=writes.append)
    return _TranscriptConsole(app), writes


def test_streamed_chunks_join_into_one_block():
    con, writes = _shim()
    for chunk in ("The `", "improvements.txt", "` backlog", " is mostly", " stale."):
        con.print(chunk, end="", markup=False, highlight=False, soft_wrap=True)
    assert writes == []                       # buffered, not one-block-per-chunk
    con.print("\n[dim]· tool (read)…[/dim]")  # a normal print flushes first
    assert len(writes) == 2
    assert isinstance(writes[0], Text)
    assert writes[0].plain == "The `improvements.txt` backlog is mostly stale."
    assert writes[1] == "\n[dim]· tool (read)…[/dim]"


def test_flush_stream_emits_pending_tail():
    con, writes = _shim()
    con.print("trailing answer text", end="")
    assert writes == []
    con.flush_stream()                        # the post-dispatch safety flush
    assert len(writes) == 1 and writes[0].plain == "trailing answer text"
    con.flush_stream()                        # idempotent — nothing left
    assert len(writes) == 1


def test_whitespace_only_stream_is_dropped():
    con, writes = _shim()
    con.print("\n", end="")
    con.flush_stream()
    assert writes == []                       # no empty ghost blocks


def test_non_streaming_prints_unchanged():
    con, writes = _shim()
    con.print("[green]ok[/green]")
    con.print("literal [brackets]", markup=False)
    con.print()
    assert writes[0] == "[green]ok[/green]"
    assert isinstance(writes[1], Text) and writes[1].plain == "literal [brackets]"
    assert writes[2] == ""


def test_print_json_and_rule_flush_first():
    con, writes = _shim()
    con.print("streamed", end="")
    con.rule("done")
    assert writes[0].plain == "streamed"      # ordering preserved: stream, then rule
    assert len(writes) == 2
