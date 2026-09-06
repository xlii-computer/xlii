"""`/account` — the read-only xAI account hub, in-session.

Surfaces the same status / keys / usage / billing views as `xlii account`,
rendered into the live transcript (state.console). Read-only — see
``xlii/cmds/account.py``.
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command


def _handle_budget(parts: list[str], ctx: dict[str, Any]) -> bool:
    """`/account budget [on|off]` — toggle the agent's read-only [BUDGET] line.

    Fetched once and cached on the session (no per-turn API cost); re-run to
    refresh. Read-only: the agent only sees the line, it can never mutate."""
    console = ctx["console"]
    state = ctx.get("state")
    agent = getattr(state, "agent", None) or ctx.get("agent")
    session = getattr(agent, "session", None)
    if session is None:
        console.print("[yellow]no live agent session for budget-awareness[/yellow]")
        return True
    if len(parts) > 2 and parts[2] in ("off", "clear"):
        session.budget_note = None
        console.print("[dim]budget-awareness off[/dim]")
        return True

    from xlii import xai_mgmt as mgmt
    from xlii.cmds.account import budget_note
    from xlii.config import GlobalConfig

    cfg = GlobalConfig.load()
    if not cfg.management_api_key:
        console.print("[red]XAI_MANAGEMENT_API_KEY not set[/red] — needed for budget-awareness.")
        return True
    try:
        team_id = mgmt.resolve_active_team(cfg.management_api_key, getattr(cfg, "team_id", None))
        note = budget_note(cfg.management_api_key, team_id)
    except mgmt.AccountError as e:
        console.print(f"[red]xAI account error:[/red] {e}")
        return True
    session.budget_note = note
    console.print(f"[green]budget-awareness on[/green] — the agent now sees: [dim]{note}[/dim]")
    return True


def _account_handler(line: str, ctx: dict[str, Any]) -> bool:
    from xlii.cmds.account import CHOICES, render_account

    parts = line.split()
    sub = parts[1] if len(parts) > 1 else "status"
    if sub == "budget":
        return _handle_budget(parts, ctx)
    what = sub if sub in CHOICES else "status"
    days = 0
    if "--days" in parts:
        i = parts.index("--days")
        if i + 1 < len(parts):
            try:
                days = int(parts[i + 1])
            except ValueError:
                days = 0
    render_account(what, ctx["console"], days=days)
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="account",
            handler=_account_handler,
            aliases=["accounts"],
            description="xAI account hub: status · keys · usage · billing · budget (read-only).",
            usage="/account [status|keys|usage|billing|budget [off]] [--days N]",
            category="session",
            repls=["code", "chat"],
        )
    )
