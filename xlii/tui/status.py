"""Session status for the Textual TUI — Rich profile bar + kernel reader re-exports."""

from __future__ import annotations

from rich.text import Text

from xlii.status import (
    attachments,
    auto_approve,
    chat_tier,
    cwd,
    exclusive_mode,
    exit_hint,
    format_primary_axes,
    frame_mode,
    frame_tabs,
    identity,
    journal,
    loadout,
    location,
    mode,
    model,
    placeholder_key,
    plugin_count,
    primary_axes,
    profile_bar_segments,
    profile_mode_tag,
    register_frame_tab,
    role,
    session_cost,
    surface_axis,
    trust_axis,
    turn_record,
    unregister_frame_tab,
)

_STYLE_MAP = {
    "journal_on": "bold green",
    "journal_off": "dim",
    "sess": "cyan",
    "plugin": "bold magenta",
    "cost_over": "bold red",
}


def _rich_style(style_key: str) -> str:
    if style_key.startswith("mode:"):
        return f"bold {style_key[5:]}"
    return _STYLE_MAP.get(style_key, style_key)


def profile_bar(state, *, meter: str = "") -> Text:
    """The RP3 profile bar: Rich assembly over :func:`profile_bar_segments`."""
    line = Text(no_wrap=True, overflow="ellipsis")
    for seg in profile_bar_segments(state, meter=meter):
        gap = seg.get("gap", "tight")
        if gap == "inline":
            line.append(seg["text"], style=_rich_style(seg["style"]))
            continue
        if line.plain:
            line.append("  ·  " if gap == "wide" else " · ", style="dim")
        line.append(seg["text"], style=_rich_style(seg["style"]))

    try:
        from xlii import jobs as _jobs

        seg = _jobs.summary_segment(state)
    except Exception:
        seg = None
    if seg is not None:
        line.append("  ·  ", style="dim")
        try:
            line.append_text(seg)
        except (AttributeError, TypeError):
            line.append(str(seg))
    return line


__all__ = [
    "attachments",
    "auto_approve",
    "chat_tier",
    "cwd",
    "exclusive_mode",
    "exit_hint",
    "format_primary_axes",
    "frame_mode",
    "frame_tabs",
    "identity",
    "journal",
    "loadout",
    "location",
    "mode",
    "model",
    "placeholder_key",
    "plugin_count",
    "primary_axes",
    "profile_bar",
    "profile_bar_segments",
    "profile_mode_tag",
    "register_frame_tab",
    "role",
    "session_cost",
    "surface_axis",
    "trust_axis",
    "turn_record",
    "unregister_frame_tab",
]


def __getattr__(name: str):
    from xlii import status as _impl

    return getattr(_impl, name)
