"""``xlii account`` — read-only xAI account hub (status, keys, usage, billing).

READ-ONLY by design: no command here mutates the account or spends money. The
data layer is ``xlii.xai_mgmt``; mutation (key CRUD, top-up, limit/payment
changes) deliberately lives elsewhere behind explicit confirmation and is never
reachable from here. See ``proposals/xai-account.md``.

Rendering is console-injected via ``render_account`` so the CLI (``xlii
account``) and the REPL (``/account``) share one implementation — the REPL
passes ``state.console`` (the transcript), the CLI passes a stdout Console.
"""

from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

from xlii import xai_mgmt as mgmt
from xlii.bootstrap import BootstrapError, require_management_key
from xlii.config import GlobalConfig

console = Console()

CHOICES = ("status", "keys", "usage", "billing")


def _fmt_usd(d) -> str:
    """Format an already-parsed dollar amount (from ``mgmt.money_usd``)."""
    return f"${d:,.2f}" if d is not None else "—"


def _date(ts) -> str:
    return str(ts).split("T")[0] if ts else "—"


def _month_to_date() -> tuple[str, str, str]:
    """(start, end, label) for the current calendar month, as API timestamps."""
    from datetime import datetime

    now = datetime.now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    fmt = "%Y-%m-%d %H:%M:%S"
    return start.strftime(fmt), now.strftime(fmt), "month to date"


def render_account(what: str, out, *, days: int = 0) -> int:
    """Resolve creds + team and render ``what`` to the ``out`` console.

    Shared by the CLI (``xlii account``) and the REPL (``/account``)."""
    cfg = GlobalConfig.load()
    try:
        key = require_management_key(cfg)
    except BootstrapError as e:
        out.print(f"[red]{e}[/red]")
        return 1
    try:
        team_id = mgmt.resolve_active_team(key, getattr(cfg, "team_id", None))
        if not team_id:
            out.print("[red]no team found for this management key[/red]")
            return 1
        if what == "keys":
            return _show_keys(out, key, team_id)
        if what == "usage":
            return _show_usage(out, key, team_id, days)
        if what == "billing":
            return _show_billing(out, key, team_id)
        return _show_status(out, key, team_id)
    except mgmt.AccountError as e:
        out.print(f"[red]xAI account error:[/red] {e}")
        return 1


def cmd_account(args: argparse.Namespace) -> int:
    return render_account(args.what, console, days=getattr(args, "days", 0))


def budget_note(key: str, team_id: str) -> str:
    """One-line read-only budget summary, for the agent's [BUDGET] context."""
    limits = mgmt.spending_limits(key, team_id)
    start, end, _ = _month_to_date()
    used = sum(v for _, v in mgmt.usage_by_description(key, team_id, start=start, end=end))
    hard = mgmt.money_usd(limits.get("effectiveHardSl"))
    if hard:
        return (
            f"xAI spend month-to-date: ${used:,.2f} of ${hard:,.2f} cap "
            f"({used / hard * 100:.0f}%)."
        )
    return f"xAI spend month-to-date: ${used:,.2f}."


def _show_status(out, key: str, team_id: str) -> int:
    team = mgmt.team_status(key, team_id)
    limits = mgmt.spending_limits(key, team_id)
    keys = mgmt.list_keys(key, team_id)
    start, end, label = _month_to_date()
    used = sum(v for _, v in mgmt.usage_by_description(key, team_id, start=start, end=end))
    hard = mgmt.money_usd(limits.get("effectiveHardSl"))
    soft = mgmt.money_usd(limits.get("softSl"))
    pct = f"  [dim]({used / hard * 100:.0f}% of cap)[/dim]" if hard else ""

    out.print(f"[bold cyan]xAI account[/bold cyan] · {team.get('name', '—')}")
    out.print(f"  tier            {team.get('tierId', '—')}")
    out.print(f"  MFA required    {'yes' if team.get('mfaRequired') else 'no'}")
    out.print(f"  ZDR eligible    {'yes' if team.get('isSelfServeZdrEligible') else 'no'}")
    out.print(
        f"  models          {'all allowed' if team.get('allowApiKeysAllModels') else 'restricted'}"
    )
    blocked = team.get("blockedReasons") or []
    if blocked:
        out.print(f"  [red]blocked[/red]         {', '.join(map(str, blocked))}")

    prepaid_usd = None
    try:
        prepaid_usd = mgmt.prepaid_total_usd(key, team_id)
    except mgmt.AccountError:
        # prepaid_usd stays None and the status table renders that row as unavailable, rather than failing the
        # whole command.
        pass
    preview_usd = None
    try:
        preview_usd = mgmt.invoice_preview_usd(key, team_id)
    except mgmt.AccountError:
        # preview_usd stays None and the status table renders that row as unavailable.
        pass
    # Cycle spend: invoice preview is the billing period; usage is calendar MTD.
    cycle = preview_usd if preview_usd is not None else used
    remaining = (hard - cycle) if hard is not None else None

    out.print("\n[bold]credits[/bold]")
    if remaining is not None:
        cycle_pct = f"  [dim]({cycle / hard * 100:.0f}% used this cycle)[/dim]" if hard else ""
        out.print(
            f"  remaining       {_fmt_usd(remaining)} of {_fmt_usd(hard)}"
            f"{cycle_pct}"
        )
    if prepaid_usd is not None:
        out.print(f"  prepaid         {_fmt_usd(prepaid_usd)}")
    out.print(f"  used · {label}  ${used:,.2f}{pct}")
    if preview_usd is not None:
        out.print(f"  this cycle      {_fmt_usd(preview_usd)}  [dim](invoice preview)[/dim]")
    out.print(f"  hard limit      {_fmt_usd(hard)}")
    out.print(f"  soft limit      {_fmt_usd(soft)}")

    out.print(
        f"\n[bold]api keys[/bold]: {len(keys)}   "
        "[dim](account keys · account usage · account billing)[/dim]"
    )
    return 0


def account_snapshot() -> dict:
    """Compact read-only account facts for the config panel. Never raises."""
    cfg = GlobalConfig.load()
    key = getattr(cfg, "management_api_key", None) or ""
    if not key:
        return {"error": "no management key"}
    try:
        team_id = mgmt.resolve_active_team(key, getattr(cfg, "team_id", None))
        if not team_id:
            return {"error": "no team"}
        team = mgmt.team_status(key, team_id)
        limits = mgmt.spending_limits(key, team_id)
        keys = mgmt.list_keys(key, team_id)
        start, end, _ = _month_to_date()
        used = sum(v for _, v in mgmt.usage_by_description(key, team_id, start=start, end=end))
        hard = mgmt.money_usd(limits.get("effectiveHardSl"))
        prepaid_usd = None
        preview_usd = None
        try:
            prepaid_usd = mgmt.prepaid_total_usd(key, team_id)
        except mgmt.AccountError:
            # prepaid_usd stays None; the snapshot omits the figure rather than failing.
            pass
        try:
            preview_usd = mgmt.invoice_preview_usd(key, team_id)
        except mgmt.AccountError:
            # preview_usd stays None; the snapshot omits the figure rather than failing.
            pass
    except (mgmt.AccountError, BootstrapError) as e:
        return {"error": str(e)[:80]}
    active = sum(1 for k in keys if not k.get("disabled"))
    cycle = preview_usd if preview_usd is not None else used
    remaining = (hard - cycle) if hard is not None else None
    spend = f"${used:,.2f}"
    if remaining is not None:
        spend = f"{_fmt_usd(remaining)} left of {_fmt_usd(hard)}"
    elif hard:
        spend += f" / ${hard:,.2f} ({used / hard * 100:.0f}%)"
    return {
        "team": team.get("name") or team_id,
        "tier": str(team.get("tierId") or "—"),
        "spend": spend,
        "remaining": _fmt_usd(remaining) if remaining is not None else "",
        "keys": f"{active} active / {len(keys)}",
        "prepaid": _fmt_usd(prepaid_usd) if prepaid_usd is not None else "",
        "models": "all" if team.get("allowApiKeysAllModels") else "restricted",
    }


def _show_usage(out, key: str, team_id: str, days: int = 0) -> int:
    if days:
        from datetime import datetime, timedelta

        now = datetime.now()
        start = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        end = now.strftime("%Y-%m-%d %H:%M:%S")
        unit, label = "TIME_UNIT_DAY", f"last {days}d"
    else:
        start, end, label = _month_to_date()
        unit = "TIME_UNIT_MONTH"

    rows = mgmt.usage_by_description(key, team_id, start=start, end=end, time_unit=unit)
    total = sum(v for _, v in rows)
    table = Table(title=f"xAI usage · {label} · total ${total:,.2f}")
    table.add_column("line item", style="cyan")
    table.add_column("spend", justify="right")
    table.add_column("share", justify="right")
    for name, usd in rows:
        share = f"{usd / total * 100:.0f}%" if total else "—"
        table.add_row(name, f"${usd:,.2f}", share)
    out.print(table)
    return 0


def _show_billing(out, key: str, team_id: str) -> int:
    invoices = mgmt.list_invoices(key, team_id)
    txns = mgmt.transactions(key, team_id)
    out.print("[bold cyan]xAI billing[/bold cyan]")

    inv_t = Table(title=f"recent invoices · {len(invoices)}")
    inv_t.add_column("number", style="cyan", no_wrap=True)
    inv_t.add_column("date")
    inv_t.add_column("status")
    inv_t.add_column("total", justify="right")
    for inv in sorted(invoices, key=lambda x: str(x.get("createTime", "")), reverse=True)[:10]:
        st = str(inv.get("invoiceStatus", "—"))
        color = {"PAID": "green", "PENDING": "yellow", "FAILED": "red"}.get(st, "white")
        inv_t.add_row(
            str(inv.get("invoice_number", "—")),
            _date(inv.get("createTime")),
            f"[{color}]{st}[/{color}]",
            _fmt_usd(mgmt.money_usd(inv.get("total"))),
        )
    out.print(inv_t)

    # Money-in events (top-ups / credits / refunds); raw per-cycle SPEND lines are
    # just usage (shown by `account usage`) and carry no date/status here.
    credits = [t for t in txns if str(t.get("changeOrigin")) != "SPEND"]
    tx_t = Table(title=f"top-ups & credits · {len(credits)}")
    tx_t.add_column("date")
    tx_t.add_column("type", style="cyan")
    tx_t.add_column("amount", justify="right")
    tx_t.add_column("status")
    for tx in sorted(credits, key=lambda x: str(x.get("createTime", "")), reverse=True)[:10]:
        # The API reports a top-up as a NEGATIVE balance change; flip it so a $100
        # purchase reads as money in, not "-$100".
        amt = mgmt.money_usd(tx.get("amount"))
        amt = -amt if amt is not None else None
        tx_t.add_row(
            _date(tx.get("createTime")),
            str(tx.get("changeOrigin", "—")),
            _fmt_usd(amt),
            str(tx.get("topupStatus", "—")),
        )
    out.print(tx_t)
    return 0


def _show_keys(out, key: str, team_id: str) -> int:
    keys = mgmt.list_keys(key, team_id)
    table = Table(title=f"xAI API keys · {len(keys)}")
    table.add_column("name", style="cyan", no_wrap=True)
    table.add_column("qps", justify="right")
    table.add_column("qpm", justify="right")
    table.add_column("acls", justify="right")
    table.add_column("status")
    for k in sorted(keys, key=lambda x: str(x.get("name") or "")):
        status = "[red]disabled[/red]" if k.get("disabled") else "[green]active[/green]"
        table.add_row(
            str(k.get("name") or k.get("redactedApiKey") or "—"),
            str(k.get("qps", "—")),
            str(k.get("qpm", "—")),
            str(len(k.get("aclStrings") or [])),
            status,
        )
    out.print(table)
    return 0


def register(sub) -> None:
    p = sub.add_parser(
        "account", help="xAI account: status, keys, usage, billing (read-only)."
    )
    p.add_argument(
        "what",
        nargs="?",
        choices=list(CHOICES),
        default="status",
        help="what to show (default: status)",
    )
    p.add_argument(
        "--days",
        type=int,
        default=0,
        help="for usage: rolling window in days (default: month to date)",
    )
    p.set_defaults(func=cmd_account)
