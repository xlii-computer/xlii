"""/replay — re-print the last captured output, verbatim and token-free.

The human-facing half of the harness-output-capture seam
(proposals/FINDING-harness-output-ephemeral.md, Vector B). The last output —
a shell command, an external harness exchange, or an agent answer — is cached
on SessionState (``last_output``), which outlives a screen ``clear``. ``/replay``
re-spits that cached string: no model call, no tokens, can't fail.

Deliberately NOT ``/recall`` (taken by the cross-persona mark picker) and NOT
``/recap`` ("recap" implies an AI summary — the opposite of a verbatim re-print).
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command
from xlii.shell_toolkit import last_output_capture


def _replay_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if state is None:
        console.print("[red]no session state[/red]")
        return True

    cap = last_output_capture(state)
    if cap is None or not (getattr(cap, "text", "") or "").strip():
        console.print(
            "[yellow]nothing to replay yet[/yellow] "
            "[dim](run a shell command, /cursor, or /consult first)[/dim]"
        )
        return True

    header = cap.label or cap.source or "output"
    console.print(f"[dim]↻ replay · {header}[/dim]")
    # Verbatim re-print: never interpret the captured text as Rich markup, and
    # don't re-highlight it — it's a faithful copy of what was already shown.
    console.print(cap.text, markup=False, highlight=False)
    return True


def _harness_output_preview(payload: Any):
    """Preview provider for a captured harness exchange (seam #2, A2's registry).

    Built as the first client of A2's preview seam: given an OutputCapture (or
    anything with ``.text``/``.label``), render a framed panel A2's surface can
    show. Registered defensively below so this vector stands alone — it activates
    once A2's ``tui/preview.py`` lands at merge.
    """
    text = getattr(payload, "text", None)
    if text is None:
        text = str(payload)
    label = getattr(payload, "label", "") or getattr(payload, "source", "output")
    from rich.panel import Panel
    from rich.text import Text

    return Panel(Text(text or ""), title=f"↻ {label}", border_style="magenta")


def _register_preview_provider() -> None:
    """Register the harness-output preview provider through A2's published seam.

    Fully guarded: in an isolated Vector-B branch ``tui/preview.py`` does not yet
    exist, so this is a silent no-op; the integrator's merge brings A2's seam in
    and the provider activates. Never raises — registration must not break /replay.
    """
    try:
        from xlii.tui.preview import register_preview  # type: ignore
    except Exception:
        return
    try:
        register_preview("harness", _harness_output_preview)
    except Exception:
        # The preview provider is optional; replay works without the harness preview.
        pass


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="replay",
            handler=_replay_handler,
            aliases=["last"],
            description="Re-print the last captured output verbatim (token-free; survives /clear).",
            usage="/replay",
            category="session",
            repls=["code", "chat"],
        )
    )
    _register_preview_provider()
