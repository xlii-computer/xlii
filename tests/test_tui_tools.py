"""Tests for the unified tool grammar (tui-layer T4a).

tool_summary is the single source for both the agent's classic dim `⎿` preview
and the styled ToolBlock body. These pin (1) the summary shapes, (2) that the
agent's _format_tool_preview still emits the exact classic markup on top of it,
(3) the styled ToolBlock rendering, and (4) the agent's emit/announce gating.

Opt-in first: with no XLII_SHELL_STYLE set the classic path is unchanged, so the
rest of the suite is unaffected — these flip the gate explicitly.
"""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

from rich.console import Console, RenderableType

from xlii.tui import blocks
from xlii.tui.blocks import tool_summary
from xlii.tui.events import ToolFinished
from xlii.tui.theme import THEME


def _render(renderable: RenderableType, width: int = 80) -> str:
    buf = StringIO()
    Console(file=buf, width=width, no_color=True, highlight=False).print(renderable)
    return buf.getvalue()


# -- tool_summary: the shared gist (plain lines, no markup/gutter) --------

def test_tool_summary_shapes():
    s = tool_summary
    assert s("read_file", "     1\tdef foo():\n     2\t    pass", False) == ["2 lines · def foo():"]
    assert s("read_file", "     1\tonly", False) == ["1 line · only"]
    assert s("list_dir", "(empty)", False) == ["(empty)"]
    assert s("list_dir", "a\nb\nc", False) == ["3 entries · a  b  c"]
    assert s("glob", "(no matches)", False) == ["no matches"]
    assert s("glob", "x.py\ny.py", False) == ["2 matches · x.py, y.py"]
    assert s("grep", "(no matches)", False) == ["no matches"]
    assert s("grep", "f.py:1:a\nf.py:2:b\nf.py:3:c", False) == ["3 matches", "f.py:1:a", "f.py:2:b"]
    assert s("search_project", "[1] hit\n[2] other", False) == ["[1] hit"]
    assert s("write_file", "wrote foo.py (12 bytes)", False) == []  # self-narrating
    assert s("read_file", "boom: missing\nrest", True) == ["boom: missing"]


def test_tool_summary_empty_is_empty():
    assert tool_summary("grep", "", False) == []
    assert tool_summary("read_file", "   \n  ", False) == []


# -- the agent's classic markup still rides on top of the shared summary --

def test_format_tool_preview_keeps_classic_markup():
    from xlii.agent import _format_tool_preview as f

    assert f("grep", "f.py:1:a\nf.py:2:b\nf.py:3:c", False) == [
        "  [dim]⎿ 3 matches[/dim]",
        "  [dim]   f.py:1:a[/dim]",
        "  [dim]   f.py:2:b[/dim]",
    ]
    assert f("read_file", "     1\tdef foo():", False) == ["  [dim]⎿ 1 line · def foo():[/dim]"]
    assert f("read_file", "nope", True) == ["  [red]⎿[/red] [red]nope[/red]"]
    assert f("write_file", "wrote foo.py", False) == []


# -- styled ToolBlock -----------------------------------------------------

def test_tool_block_header_and_summary():
    out = _render(blocks.tool_block(
        ToolFinished(name="grep", args_preview="/TODO/", content="a\nb\nc", is_error=False)))
    assert "grep" in out and "/TODO/" in out
    assert THEME.ok in out
    assert "3 matches" in out


def test_tool_block_error_is_red_with_x():
    out = _render(blocks.tool_block(
        ToolFinished(name="read_file", args_preview="m.py", content="not found", is_error=True)))
    assert THEME.err in out
    assert "not found" in out


def test_tool_block_self_narrating_is_header_only():
    out = _render(blocks.tool_block(
        ToolFinished(name="edit_file", args_preview="a.py", content="edited a.py", is_error=False)))
    assert "edit_file" in out and "a.py" in out
    assert "edited a.py" not in out  # write/edit narrate themselves; no body


# -- agent emit / announce gating ----------------------------------------

class _AgentStub:
    """Minimal self for the unbound Agent display methods under test."""

    def __init__(self):
        self.console = Console(file=StringIO(), no_color=True, width=80)
        self.emitted: list = []
        self.classic: list = []

    def _renderer(self):
        return SimpleNamespace(emit=self.emitted.append)

    def _emit_tool_result(self, name, content, is_error, suffix=""):
        self.classic.append((name, content, is_error, suffix))


def test_emit_tool_styled_routes_to_toolblock(monkeypatch):
    from xlii.agent import Agent

    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    stub = _AgentStub()
    Agent._emit_tool(stub, "grep", {"pattern": "x"}, "1 match", False)
    assert stub.classic == []
    assert len(stub.emitted) == 1
    ev = stub.emitted[0]
    assert isinstance(ev, ToolFinished)
    assert ev.name == "grep"
    assert ev.args_preview == "/x/"
    assert ev.is_error is False


def test_emit_tool_raw_routes_to_classic(monkeypatch):
    from xlii.agent import Agent

    monkeypatch.delenv("XLII_SHELL_STYLE", raising=False)
    stub = _AgentStub()
    Agent._emit_tool(stub, "grep", {"pattern": "x"}, "1 match", True, suffix=" (parallel)")
    assert stub.emitted == []
    assert stub.classic == [("grep", "1 match", True, " (parallel)")]


def test_announce_skipped_for_any_tool_when_styled(monkeypatch):
    from xlii.agent import Agent

    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    buf = StringIO()
    stub = SimpleNamespace(console=Console(file=buf, no_color=True, width=80))
    Agent._announce_tool(stub, "read_file", {"path": "x.py"})
    assert buf.getvalue().strip() == ""


def test_announce_shown_for_any_tool_when_raw(monkeypatch):
    from xlii.agent import Agent

    monkeypatch.delenv("XLII_SHELL_STYLE", raising=False)
    buf = StringIO()
    stub = SimpleNamespace(console=Console(file=buf, no_color=True, width=80))
    Agent._announce_tool(stub, "grep", {"pattern": "TODO"})
    out = buf.getvalue()
    assert "grep" in out and "/TODO/" in out
