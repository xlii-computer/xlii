"""/gigwork — hire a named non-xAI brain for one bounded worker pass (G1).

The worker loop, tools, gating, and read-only contract are xlii's; the chat
brain is the configured provider's (Kimi, DeepSeek, OpenRouter, Ollama, …).
Home plane stays xAI — a gig is hired for a pass, never promoted to
orchestrator. Providers live under ``gigwork.providers`` in config.json with
keys referenced from the environment (``api_key_env``); see
``proposals/gigwork.md``.

``/gig`` is the power alias; menus, help, and completion lead with the
canonical ``/gigwork`` (bare "gig" reads too much like "git").
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command

_USAGE = (
    "[dim]usage: /gigwork <provider> [--kit explore|bash|general] <task>[/dim]\n"
    "[dim]       /gigwork ls                — configured providers + key status[/dim]\n"
    "[dim]       /gigwork presets           — the built-in endpoint catalog[/dim]\n"
    "[dim]       /gigwork add <preset> [--as name] [--model m][/dim]\n"
    "[dim]       /gigwork add --custom <name> <base_url> <api_key_env> <model>[/dim]\n"
    "[dim]       /gigwork rm <name>[/dim]\n"
    "[dim]       /gigwork allow <name> | deny <name>  — the dispatch allow "
    "list (what the AGENT may hire)[/dim]\n"
    "[dim]       /gigwork gaggle <name> <question> | ls | add … | rm <name>[/dim]\n"
    "[dim]       /gigwork panel             — providers + jams panel (TUI)[/dim]\n"
    "[dim]  Hires the named provider's API brain for ONE read-only worker pass "
    "on xlii's tools (default kit explore: search/read, no shell).[/dim]\n"
    "[dim]  Keys live in the environment (api_key_env), never in the file.[/dim]"
)


def _presets(console: Any) -> None:
    from xlii.chat_backend import GIG_PRESETS

    console.print("[bold]gigwork presets[/bold]  [dim](/gigwork add <preset> — "
                  "endpoint + conventional env; model is editable)[/dim]")
    for name in sorted(GIG_PRESETS):
        p = GIG_PRESETS[name]
        console.print(
            f"  {name:<12} {p['model']}  ·  ${p['api_key_env']}  ·  [dim]{p['note']}[/dim]"
        )


def _persist_gigwork(agent: Any, mutate) -> Any:
    """Apply ``mutate(cfg)`` to the GLOBAL config, save it, and mirror the new
    gigwork block onto the live session cfg so the change is usable immediately."""
    from xlii.config import GlobalConfig

    cfg = GlobalConfig.load()
    result = mutate(cfg)
    cfg.save()
    agent.cfg.gigwork = cfg.gigwork
    return result


def _add(console: Any, agent: Any, rest: str) -> None:
    from xlii.chat_backend import GigError, add_provider_to_config

    parts = rest.split()
    try:
        if parts and parts[0] == "--custom":
            if len(parts) != 5:
                console.print(
                    "[red]/gigwork add --custom needs: <name> <base_url> "
                    "<api_key_env> <model>[/red]"
                )
                return
            name, base_url, env, model = parts[1:5]
            provider = _persist_gigwork(
                agent,
                lambda cfg: add_provider_to_config(
                    cfg, name, base_url=base_url, api_key_env=env, model=model
                ),
            )
        else:
            if not parts:
                console.print("[red]/gigwork add: pass a preset name "
                              "(see /gigwork presets) or --custom[/red]")
                return
            preset = parts[0]
            name, model = preset, ""
            i = 1
            while i < len(parts):
                if parts[i] == "--as" and i + 1 < len(parts):
                    name = parts[i + 1]; i += 2
                elif parts[i] == "--model" and i + 1 < len(parts):
                    model = parts[i + 1]; i += 2
                else:
                    console.print(f"[red]/gigwork add: unknown arg {parts[i]!r}[/red]")
                    return
            provider = _persist_gigwork(
                agent,
                lambda cfg: add_provider_to_config(cfg, name, preset=preset, model=model),
            )
    except GigError as e:
        console.print(f"[red]/gigwork add: {e}[/red]")
        return

    console.print(
        f"[green]added[/green] gigwork[{provider.name}] → {provider.model} · {provider.base_url}"
    )
    if provider.key_set:
        console.print(f"[dim]  ${provider.api_key_env} already set — ready: "
                      f"/gigwork {provider.name} <task>[/dim]")
    else:
        console.print(
            f"  [yellow]export {provider.api_key_env}=…[/yellow] to activate "
            "(the key stays in your environment, never the file)"
        )


def _rm(console: Any, agent: Any, rest: str) -> None:
    from xlii.chat_backend import remove_provider_from_config

    name = rest.strip()
    if not name:
        console.print("[red]/gigwork rm: pass a provider name[/red]")
        return
    existed = _persist_gigwork(
        agent, lambda cfg: remove_provider_from_config(cfg, name)
    )
    if existed:
        console.print(f"[green]removed[/green] gigwork[{name}] (key env untouched)")
    else:
        console.print(f"[yellow]/gigwork rm: no provider {name!r} configured[/yellow]")


def _allow(console: Any, agent: Any, verb: str, name: str) -> None:
    from xlii.chat_backend import GigError, set_provider_allowed

    if not name:
        console.print(f"[red]/gigwork {verb}: pass a provider name[/red]")
        return
    try:
        allow = _persist_gigwork(
            agent, lambda cfg: set_provider_allowed(cfg, name, verb == "allow")
        )
    except GigError as e:
        console.print(f"[red]/gigwork {verb}: {e}[/red]")
        return
    listed = ", ".join(allow) if allow else "(none — only /gigwork can hire)"
    past = "allowed" if verb == "allow" else "denied"
    console.print(f"[green]{past}[/green] gigwork[{name}] · agent-hireable: {listed}")


def _panel(console: Any) -> None:
    # Routed through file_tab's shared opener (the one command home for the
    # panel-host seam) so this module never imports a face.
    from xlii.repl_cmds.file_tab import open_door

    open_door(console, "gigwork",
              hint="Inline, /gigwork ls and /jam ls carry the same accounting.")


def _ls(console: Any, cfg: Any) -> None:
    from xlii.chat_backend import GigError, gig_allowlist, gig_providers

    try:
        providers = gig_providers(cfg)
    except GigError as e:
        console.print(f"[red]/gigwork: {e}[/red]")
        return
    if not providers:
        console.print(
            "[dim]gigwork: no providers configured — add one under "
            "gigwork.providers in ~/.config/xlii/config.json "
            "(see /describe gigwork)[/dim]"
        )
        return
    allow = set(gig_allowlist(cfg))
    console.print("[bold]gigwork providers[/bold]")
    for p in sorted(providers.values(), key=lambda p: p.name):
        if p.key_optional:
            key = "[green]no key needed[/green] [dim](local)[/dim]" if p.is_local else "[green]no key needed[/green]"
        elif p.key_set:
            key = "[green]key set[/green]"
        else:
            key = f"[red]{p.api_key_env} unset[/red]"
        agent_ok = " · agent-hireable" if p.name in allow else ""
        cache = " · cache marks" if p.cache_effective else ""
        console.print(
            f"  {p.name:<12} {p.model}  ·  {p.base_url}  ·  {key}{agent_ok}{cache}"
        )
    if not allow:
        console.print(
            "[dim]  (defaults.allow is empty — only /gigwork can hire; the "
            "agent's dispatch gig= is refused)[/dim]"
        )


def _parse(rest: str) -> tuple[str, str, str, str]:
    """``(provider, kit, task, err)`` from the argument tail."""
    parts = rest.split()
    if not parts:
        return ("", "", "", "usage")
    provider = parts[0]
    kit = "explore"
    i = 1
    if i < len(parts) and parts[i] == "--kit":
        if i + 1 >= len(parts):
            return ("", "", "", "--kit needs a value (explore|bash|general)")
        kit = parts[i + 1].strip().lower()
        if kit not in {"explore", "bash", "general"}:
            return ("", "", "", f"unknown kit {kit!r} (explore|bash|general)")
        i += 2
    task = rest.split(maxsplit=i)[i] if len(parts) > i else ""
    if not task.strip():
        return ("", "", "", "usage")
    return (provider, kit, task.strip(), "")


def _gigwork_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    agent = state.agent if state else ctx.get("agent")

    parts = line.split(maxsplit=1)
    rest = parts[1].strip() if len(parts) > 1 else ""

    if agent is None:
        console.print("[red]/gigwork: no active agent session[/red]")
        return True
    cfg = agent.cfg

    if not rest or rest == "ls":
        if rest == "ls":
            _ls(console, cfg)
        else:
            console.print(_USAGE)
        return True
    if rest == "presets":
        _presets(console)
        return True
    if rest == "panel":
        _panel(console)
        return True
    verb, _, tail = rest.partition(" ")
    if verb == "add":
        _add(console, agent, tail.strip())
        return True
    if verb == "rm":
        _rm(console, agent, tail.strip())
        return True
    if verb in ("allow", "deny"):
        _allow(console, agent, verb, tail.strip())
        return True
    if verb in ("gaggle", "jam"):
        from xlii.repl_cmds.jam import _jam_handler

        nested = f"/{verb}" + (f" {tail.strip()}" if tail.strip() else "")
        return _jam_handler(nested, ctx)

    provider, kit, task, err = _parse(rest)
    if err:
        if err == "usage":
            console.print(_USAGE)
        else:
            console.print(f"[red]/gigwork: {err}[/red]")
        return True

    from xlii.chat_backend import GigError, resolve_gig_backend

    try:
        backend = resolve_gig_backend(cfg, provider)
    except GigError as e:
        console.print(f"[red]/gigwork: {e}[/red]")
        return True

    from xlii.plugin import load_subscriptions
    from xlii.worker_agent import WorkerAgent

    console.print(
        f"[dim]gigwork[{backend.label}] · {backend.model} · kit {kit} — hired[/dim]"
    )
    worker = WorkerAgent(
        clients=agent.clients,
        project=agent.project,
        cfg=cfg,
        subscribed_plugins=load_subscriptions(agent.project.xli_dir),
        role=kit,  # the agent-tool API calls the kit `role` (A3)
        chat_backend=backend,
    )
    try:
        text, wcall = worker.run(task)
    except Exception as e:  # foreign endpoint errors are the user's to read
        console.print(f"[red]gigwork[{backend.label}]: {type(e).__name__}: {e}[/red]")
        return True

    from xlii.cost import format_cost, format_tokens

    cost_part = (
        f" · {format_cost(wcall.cost_usd)}" if wcall.cost_usd is not None else ""
    )
    console.print(
        f"[dim]--- gigwork[{backend.label}] · {wcall.model} · "
        f"{wcall.iterations} iter · {format_tokens(wcall.total_tokens)}"
        f"{cost_part} ---[/dim]"
    )
    console.print(text or "[dim](empty reply)[/dim]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="gigwork",
            handler=_gigwork_handler,
            aliases=["gig"],
            description="Hire a configured non-xAI provider for one read-only worker pass",
            usage="/gigwork <provider> [--kit explore|bash|general] <task> | ls | presets | add <preset>|--custom … | rm <name> | allow <name> | deny <name> | gaggle <name> <question> | panel",
            category="knowledge",
            repls=["code", "chat"],
        )
    )
