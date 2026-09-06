"""Structural tests for the TUI render kernel (T1).

These render blocks to a StringIO-backed Console (no live terminal, no_color so
substring asserts are clean) and pin the shape each event produces. They also
lock the compatibility seam: xlii.ui must re-export the SAME console object and
the SAME confirm/format_turn_line so the ~10 existing call sites don't change.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console, RenderableType

from xlii.tui import blocks, renderer as _singleton
from xlii.tui.events import (
    AssistantAnswer,
    MetaMessage,
    ShellRan,
    ToolFinished,
    UserTurn,
)
from xlii.tui.renderer import Renderer
from xlii.tui.theme import THEME


def _render(renderable: RenderableType, width: int = 80) -> str:
    buf = StringIO()
    Console(file=buf, width=width, no_color=True, highlight=False).print(renderable)
    return buf.getvalue()


def _emit(event, *, plain: bool = False, **kw) -> str:
    buf = StringIO()
    con = Console(file=buf, width=80, no_color=True, highlight=False)
    Renderer(con, plain=plain, **kw).emit(event)
    return buf.getvalue()


# -- ShellBlock -----------------------------------------------------------

def test_shell_block_shows_command_context_and_output():
    out = _render(
        blocks.shell_block(
            ShellRan(
                command="git status --short",
                cwd=Path("/tmp/proj"),
                stdout=" M xlii/repl.py\n M xlii/agent.py\n",
                stderr="",
                returncode=0,
                duration_s=1.2,
                source="user_shell",
            )
        )
    )
    assert "shell" in out
    assert "git status --short" in out
    assert "exit 0" in out
    assert "1.2s" in out
    assert "M xlii/repl.py" in out
    assert "M xlii/agent.py" in out


def test_shell_block_nonzero_exit_is_visible():
    out = _render(
        blocks.shell_block(
            ShellRan(command="false", cwd=Path("/tmp"), stdout="", stderr="boom",
                     returncode=1, duration_s=0.1)
        )
    )
    assert "exit 1" in out
    assert "boom" in out


def test_shell_block_agent_source_labels_agent_and_intent():
    out = _render(
        blocks.shell_block(
            ShellRan(command="pytest -q", cwd=Path("/tmp"), stdout="ok", stderr="",
                     returncode=0, source="agent_bash", intent="read-only")
        )
    )
    assert "agent" in out
    assert "read-only" in out
    assert "you" not in out


def test_shell_block_head_and_tail_truncation_keeps_verdict():
    body = "\n".join(f"line {i}" for i in range(1, 51)) + "\nVERDICT"  # 51 lines
    out = _render(
        blocks.shell_block(
            ShellRan(command="x", cwd=Path("/tmp"), stdout=body, stderr="", returncode=0),
            max_lines=20,
        )
    )
    assert "line 1" in out          # head kept
    assert "VERDICT" in out         # tail kept — verdict survives truncation
    assert "+31 lines hidden" in out  # 51 - head(12) - tail(8)
    assert "line 25" not in out     # middle dropped


def test_shell_block_small_budget_is_head_only():
    body = "\n".join(f"l{i}" for i in range(1, 11))  # 10 lines
    out = _render(
        blocks.shell_block(
            ShellRan(command="x", cwd=Path("/tmp"), stdout=body, stderr="", returncode=0),
            max_lines=3,
        )
    )
    assert "l1" in out and "l3" in out
    assert "+7 lines hidden" in out
    assert "l10" not in out


def test_default_max_body_lines_policy(monkeypatch):
    from xlii.tui.renderer import _default_max_body_lines

    monkeypatch.delenv("XLII_SHELL_MAXLINES", raising=False)
    assert _default_max_body_lines() == 40
    assert _default_max_body_lines(compact=True) == 8   # the tight TUI preview
    monkeypatch.setenv("XLII_SHELL_MAXLINES", "0")
    assert _default_max_body_lines() is None
    assert _default_max_body_lines(compact=True) is None  # override wins for both
    monkeypatch.setenv("XLII_SHELL_MAXLINES", "5")
    assert _default_max_body_lines() == 5
    monkeypatch.setenv("XLII_SHELL_MAXLINES", "garbage")
    assert _default_max_body_lines() == 40
    assert _default_max_body_lines(compact=True) == 8


def test_compact_renderer_previews_long_output_head_and_tail(monkeypatch):
    """A git-show / cat dump in the compact TUI surface renders as a tight ~8-line
    preview — head AND verdict — not 40 lines."""
    monkeypatch.delenv("XLII_SHELL_MAXLINES", raising=False)
    body = "\n".join(f"diff line {i}" for i in range(1, 49)) + "\nVERDICT"  # 49 lines
    ev = ShellRan(command="git show", cwd=Path("/tmp"), stdout=body, stderr="", returncode=0)

    out = _emit(ev, compact=True)
    assert "diff line 1" in out          # head kept (start of the content)
    assert "VERDICT" in out              # tail kept (the exit/verdict line survives)
    assert "diff line 25" not in out     # middle dropped
    assert "lines hidden" in out
    # the whole block stays tight — a handful of body lines, not dozens
    assert sum(1 for ln in out.splitlines() if "diff line" in ln) <= 8


def test_transcript_console_flags_compact():
    from xlii.tui.transcript_console import _TranscriptConsole

    assert _TranscriptConsole.compact_tool_output is True


def test_agent_renderer_is_compact_over_the_transcript_console(tmp_path, monkeypatch):
    """The agent's cached Renderer reads the console's compact flag: tight over
    the transcript, roomy over a plain console (the inline REPL)."""
    from tests.helpers import make_agent

    monkeypatch.delenv("XLII_SHELL_MAXLINES", raising=False)  # assert the unset defaults

    class _CompactCon(SimpleNamespace):
        compact_tool_output = True

    agent = make_agent(tmp_path, console=_CompactCon())
    assert agent._renderer().max_body_lines == 8      # TUI transcript → tight

    agent2 = make_agent(tmp_path, console=SimpleNamespace())  # no flag → inline default
    assert agent2._renderer().max_body_lines == 40


def test_renderer_uses_default_policy_and_explicit_none_disables(monkeypatch):
    long_out = "\n".join(str(i) for i in range(20))
    monkeypatch.setenv("XLII_SHELL_MAXLINES", "5")
    ev = ShellRan(command="x", cwd=Path("/tmp"), stdout=long_out, stderr="", returncode=0)

    assert "lines hidden" in _emit(ev)  # default Renderer picks up the env budget

    buf = StringIO()
    con = Console(file=buf, width=80, no_color=True, highlight=False)
    Renderer(con, max_body_lines=None).emit(ev)  # explicit None = faithful
    assert "lines hidden" not in buf.getvalue()


def test_shell_block_truncates_with_hidden_footer():
    out = _render(
        blocks.shell_block(
            ShellRan(command="seq 5", cwd=Path("/tmp"),
                     stdout="a\nb\nc\nd\ne\n", stderr="", returncode=0),
            max_lines=2,
        )
    )
    assert "a" in out and "b" in out
    assert "+3 lines hidden" in out
    assert "\n   e" not in out  # tail line was hidden


# -- ToolBlock ------------------------------------------------------------

def test_tool_block_ok_and_error_icons():
    ok = _render(blocks.tool_block(
        ToolFinished(name="grep", args_preview="/TODO/", content="3 matches",
                     is_error=False, duration_s=0.1)))
    assert THEME.ok in ok and "grep" in ok and "/TODO/" in ok

    err = _render(blocks.tool_block(
        ToolFinished(name="read_file", args_preview="x.py", content="not found",
                     is_error=True)))
    assert THEME.err in err and "not found" in err


def test_tool_block_empty_content_is_header_only():
    out = _render(blocks.tool_block(
        ToolFinished(name="edit_file", args_preview="x.py", content="")))
    assert "edit_file" in out


# -- Meta / User / Answer -------------------------------------------------

def test_meta_block_carries_icon_and_text():
    out = _render(blocks.meta_block(MetaMessage("129 files synced", "success")))
    assert THEME.meta in out
    assert "129 files synced" in out


def test_user_block_shows_prompt():
    out = _render(blocks.user_block(UserTurn("fix the failing auth test")))
    assert "you" in out
    assert "fix the failing auth test" in out


def test_theme_user_differs_from_assistant():
    """Track H0: speakers must not share one cyan — collapse was the dogfood bug."""
    assert THEME.user != THEME.assistant
    assert THEME.user == "yellow"
    assert THEME.assistant == "cyan"


def test_user_block_header_uses_speaker_color():
    """Track H0: the you rule is speaker-colored (not the dim generic rule)."""
    block = blocks.user_block(UserTurn("hello"))
    # Group(header Rule, body Text) — Rule.style carries theme.user.
    header = block.renderables[0]
    assert str(header.style) == THEME.user
    assert "bold" in str(header.title.style) or THEME.user in str(header.title)


def test_answer_block_is_a_framed_panel():
    out = _render(blocks.answer_block(
        AssistantAnswer(markdown="Fixed **it**.", model="grok-build-0.1")))
    assert "xlii" in out
    assert "grok-build-0.1" in out
    assert "Fixed" in out
    assert "╭" in out  # the reply is framed so it lifts off the tool/shell homework


def test_answer_block_tense_chrome_chip():
    out = _render(blocks.answer_block(
        AssistantAnswer(markdown="Fixed **it**.", mode="code", mode_color="green")))
    assert "┤ code ├" in out
    assert "grok" not in out
    assert "xlii" not in out
    assert "Fixed" in out


def test_renderer_styled_answer_is_framed():
    out = _emit(AssistantAnswer(markdown="hello world", model="m"))
    assert "xlii" in out
    assert "hello world" in out


def test_error_box_is_prominent():
    out = _render(blocks.error_box("turn failed: 401 Unauthorized"))
    assert "error" in out
    assert "401 Unauthorized" in out


# -- Renderer dispatch + plain mode --------------------------------------

def test_renderer_styled_shell_emits_block():
    out = _emit(ShellRan(command="echo hi", cwd=Path("/tmp"), stdout="hi",
                         stderr="", returncode=0))
    assert "shell" in out and "echo hi" in out and "hi" in out


def test_renderer_plain_shell_is_flat_text():
    out = _emit(
        ShellRan(command="echo hi", cwd=Path("/tmp"), stdout="hi\n",
                 stderr="", returncode=2),
        plain=True,
    )
    assert "$ echo hi" in out
    assert "hi" in out
    assert "exit 2" in out
    assert "shell ·" not in out  # no styled header chrome in plain mode


def test_renderer_plain_answer_is_raw_markdown():
    out = _emit(AssistantAnswer(markdown="just text", model="m"), plain=True)
    assert "just text" in out
    assert "xlii" not in out  # no box title


def test_renderer_skips_streamed_answer():
    out = _emit(AssistantAnswer(markdown="already shown", streamed=True))
    assert out.strip() == ""


def test_renderer_meta_helper():
    buf = StringIO()
    con = Console(file=buf, width=80, no_color=True, highlight=False)
    Renderer(con).meta("hello", "info")
    assert "hello" in buf.getvalue()


# -- turn_footer parity + compat shim ------------------------------------

def _fake_turn_stats():
    orch = SimpleNamespace(model="grok-build-0.1", iterations=3,
                           total_tokens=12400, cost_usd=0.01)
    workers = SimpleNamespace(total_tokens=0, cost_usd=None)
    return SimpleNamespace(orch=orch, tool_calls=4, workers=workers,
                           workers_dispatched=0, total_cost=None)


def test_turn_footer_format():
    line = blocks.turn_footer(_fake_turn_stats())
    assert "grok-build-0.1" in line
    assert "3 iter" in line
    assert "4 tools" in line
    assert "orch 12.4k" in line
    assert line.startswith("[dim]") and line.endswith("[/dim]")


def test_ui_shim_reexports_same_objects():
    import xlii.ui as ui
    import xlii.tui as tui

    assert ui.console is tui.console
    assert ui.confirm is tui.confirm
    assert ui.format_turn_line is tui.format_turn_line
    assert ui.renderer is tui.renderer


def test_module_singleton_renderer_uses_shared_console():
    import xlii.tui as tui

    assert _singleton.console is tui.console


def test_confirm_routes_through_tools_indirection(monkeypatch):
    # Under the Textual TUI, launch() swaps xlii.tools._confirm for a modal-backed
    # confirm; xlii.tui.confirm must go through that indirection rather than a raw
    # input() — a raw input() on the TUI worker thread can't read the keyboard and
    # blocks forever (the /imagine hang).
    import xlii.tools as tools
    from xlii.tui import confirm

    seen = []
    monkeypatch.setattr(tools, "_confirm", lambda prompt: seen.append(prompt) or "y")
    assert confirm("Spend $0.05? ") is True
    assert seen == ["Spend $0.05? "]

    monkeypatch.setattr(tools, "_confirm", lambda prompt: "n")
    assert confirm("again? ") is False

    def _boom(prompt):
        raise AssertionError("assume_yes must not reach the prompt")

    monkeypatch.setattr(tools, "_confirm", _boom)
    assert confirm("x", assume_yes=True) is True
