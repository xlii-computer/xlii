"""Self-documentation slash commands: /help and /describe.

/help is generated from the command registry (the single source of truth — no
hand-maintained help strings to drift); /describe introspects the live
command / tool / plugin registries for one name.
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command, render_repl_help


def _help_handler(line: str, ctx: dict[str, Any]) -> bool:
    """Progressive /help — default Daily; compose · power · all for more surface.

    Grades plan Phase 1: bare `/help` must fit ~one screen; power users opt into
    `/help all`. Tier filter lives in :mod:`xlii.commands_help` so plain-text and
    Rich presentations stay aligned.

    Also the one help door (less doors, more flags): ``--search <keyword>`` is
    the old ``/apropos`` (keyword search across commands + topics) and
    ``--topics`` is the old ``/manuals`` (the attachable-topic index). The old
    names keep working forever as hidden aliases of this command (the
    ``/marks`` → ``/bookmarks`` play) — dispatch is on the invoked token.
    """
    from xlii.commands_help import normalize_help_tier
    from xlii.repl_cmds.apropos import _apropos_handler, _manuals_handler

    console = ctx["console"]
    parts = line.split(maxsplit=1)
    invoked = parts[0].lstrip("/").lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    # Hidden-alias dispatch — the absorbed doors behave exactly as before.
    if invoked in ("apropos", "search-help"):
        return _apropos_handler(f"/apropos {rest}".rstrip(), ctx)
    if invoked == "manuals":
        return _manuals_handler("/manuals", ctx)

    # Flag forms on /help itself.
    if rest.startswith("--search"):
        query = rest[len("--search"):].strip()
        return _apropos_handler(f"/apropos {query}".rstrip(), ctx)
    if rest == "--topics":
        return _manuals_handler("/manuals", ctx)

    repl = "chat" if ctx.get("persona") else "code"
    title = "Chat" if repl == "chat" else "Code"
    raw = rest
    tier = normalize_help_tier(raw) if raw else "daily"
    if raw and not tier:
        console.print(
            "[dim]usage:[/dim] [cyan]/help[/cyan] [dim]|[/dim] "
            "[cyan]/help compose[/cyan] [dim]|[/dim] "
            "[cyan]/help power[/cyan] [dim]|[/dim] "
            "[cyan]/help all[/cyan] [dim]|[/dim] "
            "[cyan]/help --search <keyword>[/cyan] [dim]|[/dim] "
            "[cyan]/help --topics[/cyan]"
        )
        return True

    console.print(
        f"[bold]{title} REPL slash commands[/bold] "
        f"[dim]· {tier}[/dim]"
    )
    # render_repl_help() returns a Rich renderable: the usage column is colored
    # and each description wraps within its own table column (much easier to read
    # in a narrow terminal than the flat padded text). Literal brackets in usages
    # ("/rail [next|back]") are pre-wrapped in Text, so no markup-parsing hazard.
    console.print(render_repl_help(repl, tier=tier))
    if tier == "daily":
        more = (
            "\n[dim]More:[/dim] [cyan]/help compose[/cyan][dim] · [/dim]"
            "[cyan]/help power[/cyan][dim] · [/dim]"
            "[cyan]/help all[/cyan][dim] · [/dim]"
            "[cyan]/describe <name>[/cyan][dim] · [/dim]"
            "[cyan]/help --search <word>[/cyan][dim] · [/dim]"
            "[cyan]/help --topics[/cyan]"
        )
    elif tier != "all":
        more = (
            "\n[dim]More:[/dim] [cyan]/help all[/cyan][dim] · [/dim]"
            "[cyan]/describe <name>[/cyan]"
        )
    else:
        more = ""
    console.print(
        "\n[dim]/exit, /quit leave the REPL. "
        "[/dim][cyan]/attach[/cyan][dim] knowledge is durable "
        "(docs/refs survive restarts; aliases: /doc, /recall). "
        "Type [/dim][bold]/howto[/bold][dim] to ask xlii about itself, or "
        "[/dim][bold]/describe modes[/bold][dim] for plan/rail/loop guidance. "
        "Run [/dim][bold]xlii help[/bold][dim] from your shell for the full "
        "CLI reference.[/dim]"
        + more
    )
    return True


def _describe_modes(console) -> None:
    console.print("[bold cyan]modes[/bold cyan]  [dim](decision tree — which gate or review to use)[/dim]")
    console.print(
        "  [bold]Risky code change, want structure?[/bold]  → /rail (6-stage pipeline) "
        "or /plan then /execute (lighter two-phase gate)"
    )
    console.print("  [bold]Walk away until green?[/bold]  → /loop or `xlii loop` (automated build→test→judges)")
    console.print("  [bold]One-shot review of current work?[/bold]  → /verify (task + diff) or /peer (blind diff)")
    console.print("  [bold]Outside-vendor second opinion?[/bold]  → /consult (never enters history)")
    console.print("  [bold]Parallel writers in worktrees?[/bold]  → /loop --swarm (see /describe loop)")
    console.print("  [bold]Skip bash prompts?[/bold]  → /yolo  ·  restore gate: /safe")
    console.print("[dim]See also: /describe plan · /describe rail · /describe loop · /describe verify[/dim]")


def _describe_consult_providers(console) -> None:
    console.print("\n[bold]Cross-vendor providers[/bold] [dim](pick one — not locked to anthropic)[/dim]")
    console.print("  [cyan]anthropic[/cyan]  model e.g. [dim]claude-sonnet-4-6[/dim]  ·  api_key_env [dim]ANTHROPIC_API_KEY[/dim]")
    console.print("  [cyan]openai[/cyan]     model e.g. [dim]gpt-4o-mini[/dim]        ·  api_key_env [dim]OPENAI_API_KEY[/dim]")
    console.print("  [cyan]xai[/cyan]        model e.g. [dim]grok-4[/dim]             ·  api_key_env [dim]XAI_CONSULT_KEY[/dim] "
                  "[dim](same vendor as primary — not an independent check)[/dim]")
    console.print("[dim]In ~/.config/xlii/config.json, then export the api_key_env you name:[/dim]")
    console.print('  [dim]"judges": {"anthropic": {"kind": "cross_vendor", "provider": "anthropic",[/dim]')
    console.print('  [dim]            "model": "claude-sonnet-4-6", "api_key_env": "ANTHROPIC_API_KEY"}},[/dim]')
    console.print('  [dim]"consult": {"default_judge": "anthropic"}[/dim]')


def _describe_corpus_fusion(console, name: str) -> bool:
    """Append the expansive, always-current GitHub description for `name` plus
    its see-also graph edges. Live registry facts above are code-truth; this adds
    prose that can evolve without a release. Never raises; returns True if it
    printed an expansive doc."""
    try:
        from rich.markdown import Markdown

        from xlii.help_corpus import load_command_doc, load_manifest, see_also_for

        manifest = load_manifest()
        printed = False
        doc = load_command_doc(manifest, name)  # None unless a doc is configured
        if doc is not None:
            body, source = doc
            console.print(f"\n[dim]── more · {source} · always current ──[/dim]")
            console.print(Markdown(body))
            printed = True
        spec = manifest.commands.get(name)
        if spec and spec.topic:
            console.print(f"[dim]deep-dive:[/dim] [cyan]/howto {spec.topic}[/cyan]")
        edges = see_also_for(manifest, name)
        if edges:
            rendered = ", ".join(
                f"[cyan]/howto {e}[/cyan]" if e in manifest.topics
                else f"[cyan]/describe {e}[/cyan]"
                for e in edges
            )
            console.print(f"[dim]see also:[/dim] {rendered}")
        return printed
    except Exception:
        return False


def _describe_handler(line: str, ctx: dict[str, Any]) -> bool:
    """Self-documentation from the live registries — the C-h f of xlii."""
    console = ctx["console"]
    parts = line.split(maxsplit=1)
    if len(parts) != 2:
        console.print("[dim]usage: /describe <command|tool|plugin|modes>[/dim]")
        return True
    name = parts[1].strip().lstrip("/")
    if name == "modes":
        _describe_modes(console)
        return True
    found = False

    from rich.markup import escape

    from xlii.commands import _INDEX
    for cmd in _INDEX.get(name, []):
        found = True
        console.print(f"[bold cyan]/{cmd.name}[/bold cyan]  [dim](slash command · {cmd.source} · "
                      f"repls: {', '.join(cmd.repls)} · category: {cmd.category})[/dim]")
        if cmd.aliases:
            console.print(f"  aliases: {', '.join('/' + a for a in cmd.aliases)}")
        console.print(f"  {escape(cmd.description) if cmd.description else '(no description)'}")
        if cmd.usage:
            # Same discipline as /help (which pre-wraps usages in Text): usage strings
            # legitimately contain bracket tokens — never feed them to the markup parser.
            console.print(f"  usage: [cyan]{escape(cmd.usage)}[/cyan]")

    if name == "consult":
        found = True
        _describe_consult_providers(console)

    from xlii.tools import tool_schemas, dispatch_subagent_schema, PARALLEL_SAFE, PLAN_MODE_TOOLS
    for schema in tool_schemas() + [dispatch_subagent_schema()]:
        fn = schema["function"]
        if fn["name"] != name:
            continue
        found = True
        traits = []
        if name in PARALLEL_SAFE:
            traits.append("parallel-safe")
        if name in PLAN_MODE_TOOLS:
            traits.append("plan-mode")
        console.print(f"[bold cyan]{name}[/bold cyan]  [dim](agent tool"
                      + (f" · {', '.join(traits)}" if traits else "") + ")[/dim]")
        console.print(f"  {escape(fn.get('description', '(no description)'))}")
        props = fn.get("parameters", {}).get("properties", {})
        required = set(fn.get("parameters", {}).get("required", []))
        for pname, spec in props.items():
            req = " [red]*[/red]" if pname in required else ""
            console.print(f"    {pname}{req}: [dim]{escape(spec.get('type', '?'))}"
                          + (f" — {escape(spec['description'])}" if spec.get("description") else "") + "[/dim]")

    from xlii.plugin import Plugin
    p = Plugin(id=name)
    if p.exists():
        found = True
        meta = p.metadata()
        console.print(f"[bold cyan]{name}[/bold cyan]  [dim](plugin · risk: {p.risk()})[/dim]")
        console.print(f"  {escape(meta.get('description', '(no description)'))}")
        actions = meta.get("actions") or []
        if actions:
            console.print("  actions: " + ", ".join(
                a.get("id", "?") for a in actions if isinstance(a, dict)))
        envs = p.auth_env_vars()
        if envs:
            console.print(f"  auth env vars: {', '.join(envs)} [dim](store via xlii auth set)[/dim]")

    # Fuse in the expansive, always-current GitHub description + see-also graph.
    corpus_printed = _describe_corpus_fusion(console, name)
    found = found or corpus_printed

    if not found:
        console.print(f"[dim]nothing named {name!r} in commands, tools, or plugins[/dim]")
    elif name == "loop":
        console.print(
            "  [dim]state file: .xlii/loop-active.json · "
            "swarm progress: state.swarm in that file · "
            "pre-land judges reuse /verify-style profiles[/dim]"
        )
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="help",
            handler=_help_handler,
            # Hidden aliases: the absorbed search/index doors (old /apropos,
            # /search-help, /manuals) — they keep working, drop out of listings.
            aliases=["apropos", "search-help", "manuals"],
            description="Show slash commands (daily by default; compose · power · all); --search finds commands + topics, --topics lists the manual index",
            usage="/help [compose|power|all] [--search <keyword>] [--topics]",
            category="session",
        )
    )
    register_repl_command(
        REPLCommand(
            name="describe",
            handler=_describe_handler,
            aliases=["man"],
            description="Describe a slash command, agent tool, or plugin from the live registries",
            usage="/describe <name>  (alias /man; try /describe modes)",
            category="session",
        )
    )
