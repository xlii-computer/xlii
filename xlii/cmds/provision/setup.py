"""First-time setup (`xlii setup`)."""

from __future__ import annotations

import argparse

from xlii.bootstrap import (
    BootstrapError,
    MultipleTeamsError,
    require_management_key,
    run_setup,
    team_id_of,
)
from xlii.config import GLOBAL_CONFIG_FILE, GlobalConfig
from xlii.ui import console


def _render_setup_event(kind: str, **payload) -> None:
    """Render run_setup's kernel events as the exact lines the ritual has
    always printed — presentation stays in the face."""
    if kind == "team_cached":
        console.print(f"[green]✓[/green] team_id discovered + cached: {payload['team_id']}")
    elif kind == "team_already_cached":
        console.print(f"[dim]·[/dim] team_id already cached: {payload['team_id']}")
    elif kind == "pool_sufficient":
        console.print(
            f"\n[dim]·[/dim] pool already has {payload['existing']} key(s) — skipping bootstrap "
            "(re-run with --force to add more)"
        )
    elif kind == "proceeding":
        console.print()
    elif kind == "creating_primary":
        console.print(f"creating [bold]primary[/bold] key (expires in {payload['expire_days']}d)…")
    elif kind == "creating_workers":
        console.print(
            f"creating [bold]{payload['count']}[/bold] worker key(s) "
            f"(expires in {payload['expire_days']}d)…"
        )
    elif kind == "key_created":
        exp_note = f"  (expires in {payload['expire_days']}d)" if payload["expire_days"] else ""
        console.print(
            f"  [green]✓[/green] {payload['label']}  →  "
            f"xAI: {payload['name_on_server']}{exp_note}"
        )
    elif kind == "key_failed":
        if payload.get("error"):
            console.print(f"  [red]✗[/red] {payload['label']}: {payload['error']}")
        else:
            console.print(
                f"  [red]✗[/red] {payload['label']}: response missing key string. "
                f"raw: {payload.get('raw')}"
            )
    elif kind == "models_pinned":
        console.print(
            f"[dim]·[/dim] models pinned in config "
            f"(orch={payload['orchestrator']}, worker={payload['worker']}) — skipping auto-detect"
        )
    elif kind == "models_discovering":
        console.print("discovering available models on this team…")
    elif kind == "models_unavailable":
        console.print(
            "[yellow]could not discover models (endpoint unavailable) — keeping defaults[/yellow]"
        )
    elif kind == "models_unclassified":
        console.print(
            f"[yellow]found {payload['count']} model(s) but couldn't classify any[/yellow]"
        )
    elif kind == "models_detected":
        console.print(f"[green]✓[/green] auto-detected from {payload['count']} model(s):")
        if payload.get("orchestrator"):
            console.print(f"    orchestrator: [cyan]{payload['orchestrator']}[/cyan]")
        if payload.get("worker"):
            console.print(f"    worker:       [cyan]{payload['worker']}[/cyan]")


def cmd_setup(args: argparse.Namespace) -> int:
    """One-shot first-time setup: template → env-key check → primary + N workers.

    Idempotent: skips steps that are already done. Safe to re-run.
    """
    # Step 1 — ensure config file exists
    if not GLOBAL_CONFIG_FILE.exists():
        GlobalConfig.write_template()
        console.print(f"[green]✓[/green] wrote config template at {GLOBAL_CONFIG_FILE}")
    else:
        console.print(f"[dim]·[/dim] config exists at {GLOBAL_CONFIG_FILE}")

    cfg = GlobalConfig.load()

    # Step 2 — management key (env-only, never persisted)
    try:
        require_management_key(cfg)
    except BootstrapError as e:
        console.print(f"[red]{e}[/red]")
        return 1
    console.print("[dim]·[/dim] XAI_MANAGEMENT_API_KEY found in environment")

    # Migration nudge: warn if a legacy management_api_key is still in the file
    if GlobalConfig.mgmt_key_in_file():
        console.print(
            "[yellow]⚠ legacy management_api_key found in config.json — "
            "remove it (env var is the only valid source now)[/yellow]"
        )

    # Steps 3+ — team discovery → pool sizing → primary/workers → model
    # auto-detect: the kernel state machine; this file renders its events.
    try:
        rc = run_setup(
            cfg,
            workers=args.workers,
            expire_days=args.expire_days,
            force=args.force,
            on_event=_render_setup_event,
        )
    except MultipleTeamsError as e:
        # Pretty-print the choice for the user
        console.print("[yellow]Multiple teams found on this management key:[/yellow]")
        for t in e.teams:
            name = t.get("name") or "(no name)"
            console.print(f"  • [cyan]{name}[/cyan]  →  team_id: [bold]{team_id_of(t) or '?'}[/bold]")

        console.print("")
        console.print("[dim]Edit your config and add one of the team_ids above:[/dim]")
        console.print(f"  file: {GLOBAL_CONFIG_FILE}")
        console.print('  "team_id": "paste-one-of-the-ids-here"')
        return 1
    except BootstrapError as e:
        console.print(f"[red]team discovery failed: {e}[/red]")
        return 1
    if rc != 0:
        return rc

    return _setup_finish(
        GlobalConfig.load(), console,
        journal=str(getattr(args, "journal", "") or ""),
    )


def _prompt_is_interactive() -> bool:
    """True only when both ends are a real TTY, so the first-run naming ritual can
    read a line. A dedicated seam: tests force it True while monkeypatching
    ``input()``, and CI / piped / non-TTY installs silently default to Mojo
    (or ``--journal`` / ``$XLII_MOJO_NAME`` from the throne)."""
    import sys
    return sys.stdin.isatty() and sys.stdout.isatty()


def _wanted_journal_name(explicit: str = "") -> str:
    """Throne ``/name`` for a limb: ``--journal``, else ``$XLII_MOJO_NAME``."""
    import os

    raw = (explicit or os.environ.get("XLII_MOJO_NAME") or "").strip()
    return raw


def _install_companion_persona(console_, *, journal: str = "") -> str:
    """First-run naming ritual → the mobile journal (mojo).

    Invites the user to name the journal; a non-empty valid answer creates it
    under that name from the mojo template, enter keeps Mojo, and a non-TTY
    install defaults silently (Mojo, or the throne nickname via ``--journal`` /
    ``$XLII_MOJO_NAME``). NEVER clobbers an existing *journal*: if the user
    already has one, it is kept and the ritual is skipped. The shipped chat
    costume (iXaac) is a voice, not the journal — never pick it as the seat.
    Returns the journal's id (never the chat costume).
    """
    from xlii.persona import (
        CHAT_DEFAULT_PERSONA_ID,
        DEFAULT_PERSONA_DISPLAY,
        DEFAULT_PERSONA_ID,
        FACTORY_PERSONA_ALIASES,
        _adopt_legacy_unnamed_ixaac,
        ensure_default_persona,
        ensure_stock_persona,
        is_legacy_unnamed_journal,
        is_valid_name,
        list_personas,
    )

    _adopt_legacy_unnamed_ixaac()
    wanted = _wanted_journal_name(journal)
    existing = list_personas()
    if existing:
        # Already onboarded (or personas made by hand): keep the journal.
        # iXaac is seeded as a costume on every box — it must not win the seat
        # just because its filename sorts first.
        ensure_stock_persona(CHAT_DEFAULT_PERSONA_ID)
        names = [p.name for p in existing]
        if DEFAULT_PERSONA_ID in names:
            return DEFAULT_PERSONA_ID
        if wanted:
            for p in existing:
                if p.name.lower() == wanted.lower():
                    return p.name
        for p in existing:
            n = p.name
            if n == CHAT_DEFAULT_PERSONA_ID or is_legacy_unnamed_journal(n):
                continue
            if n.lower() in FACTORY_PERSONA_ALIASES:
                return DEFAULT_PERSONA_ID
            return n
        # Only the chat costume on disk — still mint the journal below.

    name = DEFAULT_PERSONA_ID
    if wanted:
        if wanted.lower() in FACTORY_PERSONA_ALIASES:
            name = DEFAULT_PERSONA_ID
        elif is_valid_name(wanted):
            name = wanted
        else:
            console_.print(
                f"[yellow]invalid journal name {wanted!r} — keeping "
                f"{DEFAULT_PERSONA_DISPLAY}[/yellow]"
            )
    elif _prompt_is_interactive():
        console_.print(
            f"\n[bold]Name the mojo[/bold] — the mobile journal "
            f"([cyan][M][/cyan] / [cyan]/mojo[/cyan]).\n[dim]Press enter to keep "
            f"[magenta]{DEFAULT_PERSONA_DISPLAY}[/magenta]; it is fully editable "
            f"later with [cyan]/edit --id {DEFAULT_PERSONA_ID}[/cyan]. "
            f"Chat costumes are [cyan]/chat[/cyan]; jobs are [cyan]/role[/cyan].[/dim]"
        )
        try:
            raw = input(f"Name the mojo [{DEFAULT_PERSONA_DISPLAY}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            raw = ""
        if raw:
            if is_valid_name(raw):
                name = raw
            else:
                console_.print(
                    f"[yellow]invalid name {raw!r} — keeping "
                    f"{DEFAULT_PERSONA_DISPLAY}[/yellow]"
                )

    p = ensure_default_persona(name)
    ensure_stock_persona(CHAT_DEFAULT_PERSONA_ID)
    console_.print(
        f"[green]✓[/green] journal [magenta]{p.name}[/magenta] ready "
        f"[dim](edit with [cyan]/edit --id {p.name}[/cyan])[/dim]"
    )
    return p.name


def _setup_finish(cfg: GlobalConfig, console_, *, journal: str = "") -> int:
    pool = len(cfg.key_pairs())
    console_.print(
        f"\n[green]setup complete[/green] — pool size: {pool} key(s)"
    )
    # Install-time base identity so a fresh machine always has a companion persona
    # for naked `xlii chat` / binding (RP4 / Vector C). The lazy bootstrap in
    # _resolve_persona_to_load still covers no-key installs that never reach setup.
    companion = _install_companion_persona(console_, journal=journal)
    # Persist a custom setup name so factory_persona_id / talk resolve to it.
    # Empty stays mojo. Mid-use rename is still not a knob — this is first-run only.
    try:
        from xlii.persona import DEFAULT_PERSONA_ID, is_legacy_unnamed_journal

        if companion and companion != DEFAULT_PERSONA_ID:
            cfg.fallback_persona = companion
            cfg.save()
        elif is_legacy_unnamed_journal(cfg.fallback_persona or ""):
            # Split leftover: ixaac is the chat costume, not the journal name.
            cfg.fallback_persona = ""
            cfg.save()
    except Exception:
        # Naming is cosmetic here -- first-run setup must still finish if the persona name can't be persisted.
        pass

    # Onboarding: report the full-screen programs we found installed. They run in
    # a real terminal automatically inside xlii (mc/vim/htop/… need a PTY that
    # captured-block mode can't give them); anything else can be forced with `!!`.
    from xlii.interactive import installed_defaults
    found = installed_defaults()
    if found:
        shown = ", ".join(found[:12]) + ("…" if len(found) > 12 else "")
        console_.print(
            f"[dim]·[/dim] full-screen programs detected ([cyan]{len(found)}[/cyan]): {shown}\n"
            "[dim]  these get a real terminal automatically; force any other with [/dim]"
            "[cyan]!!<cmd>[/cyan][dim] or add it via [/dim][cyan]/interactive add[/cyan]"
        )

    console_.print(
        "[dim]next:[/dim] run [cyan]xlii doctor[/cyan] (recovery mantra), then "
        "[cyan]xlii init --local --snapshot[/cyan] for a first-hour project "
        "(or [cyan]xlii init[/cyan] / [cyan]xlii new <name>[/cyan] for full sync). "
        f"Companion [magenta]{companion}[/magenta] is ready for [cyan]xlii chat[/cyan]. "
        "[dim]Spine: docs/GOLDEN-PATH.md · in-session [/dim][cyan]/howto first-session[/cyan]"
    )
    return 0
