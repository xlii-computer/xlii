"""Checkpoint / rewind / diff slash commands (Phase 1 safety rail)."""

from __future__ import annotations

from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command
from xlii.checkpoints import (
    diff_since,
    load_ledger,
    manual_checkpoint,
    rewind,
)


def _confirm(console, preview: str) -> bool:
    console.print("[bold]checkpoint rewind[/bold]")
    for line in preview.splitlines():
        console.print(f"  {line}")
    from xlii.tools import _confirm as gate_confirm

    try:
        answer = gate_confirm("  restore? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


def refresh_after_tree_mutation(state: Any) -> None:
    """Keep REPLState / prompt consistent after a tree restore."""
    agent = state.agent
    if agent.history and agent.history[0].get("role") == "system":
        agent.history[0] = {"role": "system", "content": agent._effective_system_prompt()}
    try:
        from xlii.project_fingerprint import refresh_project_profile

        refresh_project_profile(state.project.project_root)
    except Exception as exc:
        # Best-effort: rewind already succeeded; a stale fingerprint must not fail the command.
        try:
            state.console.print(f"[dim]profile refresh skipped: {exc}[/dim]")
        except Exception:
            # Nested inside the notice above -- if even printing the skip fails, stay silent.
            pass


def _parse_n(raws: list[str], default: int = 1) -> tuple[int, Optional[str]]:
    if not raws:
        return default, None
    try:
        n = int(raws[0])
    except ValueError:
        return default, f"expected a number, got {raws[0]!r}"
    if n < 1:
        return default, "N must be >= 1"
    return n, None


def _checkpoint_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if state is None:
        console.print("[red]no session state[/red]")
        return True
    parts = line.split()
    label = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
    entry, err = manual_checkpoint(state.project.xli_dir, state.project.project_root, label=label)
    if err:
        console.print(f"[red]{err}[/red]")
        return True
    assert entry is not None
    shown = entry.label or f"turn {entry.turn_id}"
    console.print(f"[green]checkpoint[/green] {shown} [dim]({entry.tree_sha[:12]})[/dim]")
    return True


def _diff_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if state is None:
        console.print("[red]no session state[/red]")
        return True
    parts = line.split()
    n, err = _parse_n(parts[1:])
    if err:
        console.print(f"[red]{err}[/red]")
        return True
    diff, err = diff_since(state.project.xli_dir, state.project.project_root, n=n)
    if err:
        console.print(f"[dim]{err}[/dim]")
        return True
    if not diff.strip():
        console.print("[dim]no changes since that checkpoint[/dim]")
        return True
    ledger = load_ledger(state.project.xli_dir)
    entry = ledger[-n]
    label = entry.label or f"turn {entry.turn_id}"
    console.print(f"[bold]diff since {label}[/bold]")
    console.print(diff)
    return True


def _rewind_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if state is None:
        console.print("[red]no session state[/red]")
        return True
    parts = line.split()
    n, err = _parse_n(parts[1:])
    if err:
        console.print(f"[red]{err}[/red]")
        return True

    def confirm_fn(preview: str) -> bool:
        return _confirm(console, preview)

    ok, msg = rewind(
        state.project.xli_dir,
        state.project.project_root,
        n,
        confirm=confirm_fn,
    )
    if ok:
        refresh_after_tree_mutation(state)
        console.print(f"[green]{msg}[/green]")
    elif msg != "(cancelled)":
        console.print(f"[red]{msg}[/red]" if "failed" in msg or "cannot" in msg else f"[dim]{msg}[/dim]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="checkpoint",
            handler=_checkpoint_handler,
            description="Snapshot the working tree now (manual checkpoint).",
            usage="/checkpoint [label]",
            category="session",
            repls=["code"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="diff",
            handler=_diff_handler,
            description="Diff working tree against a prior checkpoint.",
            usage="/diff [N]",
            category="session",
            repls=["code"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="rewind",
            handler=_rewind_handler,
            aliases=["undo"],
            description="Restore the working tree to before recent write-turn(s).",
            usage="/rewind [N]",
            category="session",
            repls=["code"],
        )
    )
