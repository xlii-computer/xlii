"""The plan strip — one line above the input: ``plan 3/7 ▸ <next item>`` (plan-surface T1).

The working plan IS the session's todo list (checkboxes + ids + receipts), so the
TUI carries its state as a single always-current line riding directly above the
input — not a scrolling checkmark ticker. Two halves, the ``status_strip`` split:

* :func:`plan_strip_state` — pure (testable without a terminal): reads the working
  plan through :mod:`xlii.plan_ops` and decides visibility + content. Visible when
  the plan has items and is either unfinished or plan mode is on; the finished
  line (``7/7 ✓``) shows only while plan mode holds the session.
* :class:`_PlanStrip` — the one-line Static. Hidden (``display: none``) when there
  is nothing to say; a click opens the plan's ITEM view in Pane 2 (the panel is a
  keypress away — the strip never claims more than one line).

Refresh paths: the kernel plan listener (:func:`xlii.plan_ops.set_plan_listener`,
marshalled to the main thread), the end of every turn, and app mount.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from rich.text import Text
from textual.widgets import Static

# The strip is one line; the next-item text is bounded so the count and the
# amendment flag never get pushed out (the Static also ellipsizes on width).
_ITEM_CAP = 72


def plan_strip_state(state: Any) -> "Optional[tuple[str, Text]]":
    """``(plan_name, line)`` for the strip, or None when it should hide.

    Hide when: no project, no plan file, a plan with zero checkbox items, or a
    fully-checked plan outside plan mode (done work should leave the chrome).
    """
    from xlii.plan_ops import (
        PlanOpError,
        pending_amendments,
        plan_items,
        resolve_plan_file,
    )

    project = getattr(state, "project", None)
    xli_dir = getattr(project, "xli_dir", None) if project is not None else None
    if not xli_dir:
        return None
    from pathlib import Path

    try:
        f = resolve_plan_file((Path(xli_dir) / "plans").resolve())
        text = f.read_text(errors="replace")
    except (PlanOpError, OSError):
        return None
    items = plan_items(text)
    total = len(items)
    if total == 0:
        return None
    checked = sum(1 for it in items if it.state in ("x", "x?"))
    plan_mode = bool(getattr(getattr(state, "agent", None), "plan_mode", False))
    if checked == total and not plan_mode:
        return None

    name = f.name[:-3] if f.name.endswith(".md") else f.name
    line = Text(no_wrap=True, overflow="ellipsis")
    line.append("plan", style="dim")
    if name != "current":
        line.append(f"[{name}]", style="dim")
    line.append(f" {checked}/{total}", style="bold")
    if checked == total:
        line.append(" ✓ all checked", style="green")
    else:
        nxt = next((it for it in items if it.state == " "), None)
        label = (nxt.text or f"{{#{nxt.item_id}}}") if nxt is not None else ""
        if len(label) > _ITEM_CAP:
            label = label[: _ITEM_CAP - 1] + "…"
        line.append(" ▸ ", style="dim")
        line.append(label)
    n_am = len(pending_amendments(text))
    if n_am:
        line.append(f"  ✎{n_am}", style="magenta")
    return (name, line)


class _PlanStrip(Static):
    """The one-line plan readout above the input; click → the plan's item pane."""

    DEFAULT_CSS = """
    _PlanStrip {
        height: 1;
        background: $panel-darken-1;
        color: $text;
        padding: 0 1;
        display: none;
    }
    """

    def __init__(self, on_open: "Callable[[str], None]", **kwargs: Any) -> None:
        super().__init__("", id="plan-strip", **kwargs)
        self._on_open = on_open
        self._plan_name = ""

    def update_from(self, state: Any) -> None:
        """Recompute content + visibility from the session state (main thread)."""
        try:
            got = plan_strip_state(state)
        except Exception:
            got = None
        if got is None:
            self._plan_name = ""
            self.display = False
            return
        self._plan_name, line = got
        self.update(line)
        self.display = True

    def on_click(self, event: Any) -> None:
        if self._plan_name:
            event.stop()
            self._on_open(self._plan_name)
