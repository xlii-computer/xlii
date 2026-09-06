"""autonomous-loop L3.3 — the between-cycle loop status block in the TUI.

The factory (`blocks.loop_status_block`) is pure rich; the wiring point
(`XliiApp._loop_post_turn`) is exercised via the unbound method against a light
fake so we don't stand up the whole Textual app (which the other
test_tui_textual cases need).
"""

from __future__ import annotations

import io
from types import SimpleNamespace

from rich.console import Console

from xlii.tui import blocks


def _render(renderable) -> str:
    buf = Console(file=io.StringIO(), width=60)
    buf.print(renderable)
    return buf.file.getvalue()


def test_loop_status_block_renders_and_strips_tag():
    out = _render(blocks.loop_status_block([
        "[loop] cycle 2/5 · phase: verify",
        "builder $0.04 · judges $0.11",
    ]))
    assert "cycle 2/5" in out and "judges" in out
    assert "loop" in out            # the panel title
    assert "[loop]" not in out      # the inline-REPL tag is stripped


def test_loop_continuation_prints_status_between_cycles(monkeypatch):
    # Phase 5b: the between-cycle loop panel is printed by the shared
    # _drive_loop_continuation via the live console (every surface), replacing
    # the TUI-only _loop_post_turn block.

    from xlii.repl import _drive_loop_continuation

    lines: list = []
    console = SimpleNamespace(print=lambda *a, **k: lines.append(" ".join(map(str, a))))

    class Ctrl:
        def __init__(self):
            self.advances = 0
            self.is_active = True

        def status_lines(self):
            return ["[loop] cycle 3/5 · phase: build"]

        def advance_after_build(self, root, judge_ctx=None):
            self.advances += 1
            if self.advances == 1:
                return SimpleNamespace(status="continue", next_prompt="go", message="")
            self.is_active = False
            return SimpleNamespace(status="done", next_prompt=None, message="loop done")

        def clear(self):
            pass

    ctrl = Ctrl()
    state = SimpleNamespace(
        loop=ctrl,
        console=console,
        agent=SimpleNamespace(history=[]),
        project=SimpleNamespace(project_root="."),
    )
    from xlii.conversation import TurnResult
    monkeypatch.setattr(
        "xlii.conversation.drive_turn",
        lambda *a, **k: TurnResult(reply="ok", dirty=set(), stats=None),
    )
    _drive_loop_continuation(state, lambda q: ("ok", set(), None),
                             render=lambda r, p: None, on_error=lambda e: None)
    joined = "\n".join(lines)
    assert "cycle 3/5" in joined          # status printed while still active
    assert "loop done" in joined


def test_loop_continuation_noop_when_loop_ended():

    from xlii.repl import _drive_loop_continuation

    lines: list = []
    console = SimpleNamespace(print=lambda *a, **k: lines.append(" ".join(map(str, a))))
    state = SimpleNamespace(
        loop=SimpleNamespace(is_active=False),
        console=console,
        agent=SimpleNamespace(history=[]),
        project=SimpleNamespace(project_root="."),
    )
    _drive_loop_continuation(state, lambda q: ("ok", set(), None),
                             render=lambda r, p: None, on_error=lambda e: None)
    assert lines == []
