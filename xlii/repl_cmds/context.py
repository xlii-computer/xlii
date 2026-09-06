"""DeepContext management for Grok Build bridging (/context)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from xlii.commands import REPLCommand, register_repl_command


def _cmd_context(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if not state:
        console.print("[red]No active REPLState[/red]")
        return True

    parts = line.split(maxsplit=2)
    sub = parts[1] if len(parts) > 1 else None
    arg = parts[2] if len(parts) > 2 else None

    from xlii.context import (
        delete_deep_context,
        list_deep_contexts,
        load_deep_context,
        save_deep_context,
    )

    if sub == "save":
        loadout_slot = arg or state.get_current_workspace()
        deep_ctx = state.create_deep_context(
            workspace_name=loadout_slot,
            name=None,
            description=None,
            tags=None,
        )
        save_deep_context(deep_ctx)
        console.print(f"[green]✓[/green] Saved DeepContext [cyan]{deep_ctx.name}[/cyan]")
        console.print(
            f"  Source loadout slot: {deep_ctx.source_workspace_name} "
            f"@ {deep_ctx.source_project_path}"
        )
        console.print("  (Use /context show to view, or hand this name to a Grok Build agent)")
        return True

    if sub == "list":
        contexts = list_deep_contexts()
        if not contexts:
            console.print("[dim](no DeepContexts saved globally yet)[/dim]")
        else:
            console.print("[bold]Global DeepContexts:[/bold]")
            for name in contexts:
                console.print(f"  [cyan]{name}[/cyan]")
        return True

    if sub == "show" and arg:
        deep_ctx = load_deep_context(arg)
        if not deep_ctx:
            console.print(f"[red]DeepContext {arg!r} not found[/red]")
            return True
        console.print(f"[bold]DeepContext:[/bold] [cyan]{deep_ctx.name}[/cyan]")
        console.print(f"  Project: {deep_ctx.source_project_path}")
        console.print(f"  Loadout slot: {deep_ctx.source_workspace_name}")
        console.print(f"  Refs: {len(deep_ctx.attached_refs)}")
        console.print(f"  Docs: {len(deep_ctx.attached_docs)}")
        console.print(f"  Tools: {len(deep_ctx.tools)}")
        return True

    if sub == "delete" and arg:
        if delete_deep_context(arg):
            console.print(f"[green]✓[/green] Deleted DeepContext [cyan]{arg}[/cyan]")
        else:
            console.print(f"[red]DeepContext {arg!r} not found[/red]")
        return True

    if sub == "sync" and arg:
        deep_ctx = load_deep_context(arg)
        if not deep_ctx:
            console.print(f"[red]DeepContext {arg!r} not found[/red]")
            return True

        # Sanitize on ingest: old DeepContext snapshots can carry legacy
        # persona-Collection refs (banned, menu-families §5) — they must not
        # re-enter live state. The write-back below persists the cleaned list.
        from xlii.refs import sanitize_refs
        state.attached_refs = sanitize_refs(deep_ctx.attached_refs)
        state.attached_docs = deep_ctx.attached_docs
        state.save()

        deep_ctx.attached_refs = state.attached_refs
        deep_ctx.attached_docs = state.attached_docs
        deep_ctx.last_synced_at = datetime.now(timezone.utc).isoformat()
        save_deep_context(deep_ctx)

        from xlii.context import _sync_attachments_back_to_source_workspace

        _sync_attachments_back_to_source_workspace(deep_ctx)

        console.print(
            f"[green]✓[/green] Synced DeepContext [cyan]{arg}[/cyan] "
            "with current session (bidirectional)"
        )
        return True

    console.print("[dim]Usage:[/dim]")
    console.print("  /context list")
    console.print("  /context save [<loadout-slot>]")
    console.print("  /context show <name>")
    console.print("  /context sync <name>")
    console.print("  /context delete <name>")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="context",
            handler=_cmd_context,
            description="Manage DeepContexts for Grok Build bridging (durable cross-tool memory)",
            usage="/context [list | save | show | delete | sync]",
            category="knowledge",
        )
    )
