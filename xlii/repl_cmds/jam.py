"""/gaggle (alias /jam) — named multi-brain preset, then merge (gigwork Phase B).

``/gaggle ls`` lists stock + configured presets; ``/gaggle <name> <question>``
fans the members out (home xAI + gig brains, read-only worker passes, capped
parallelism) and prints the merged result — a conflict-surfacing synthesis or a
plain digest, per the preset's merge policy. ``/jam`` is the shipped synonym.
``/gaggle add`` composes a custom crew from ``backend[:kit][@model]`` tokens
(``/gaggle rm`` drops it); presets live in code (stock) and ``gigwork.jams``
in config (see /describe gaggle and /howto gigwork).
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command


def _verb(line: str) -> str:
    """The slash the operator typed (gaggle vs jam vs gigwork-nested)."""
    token = (line.split(maxsplit=1) or [""])[0].lstrip("/").split(":")[-1]
    return "gaggle" if token == "gaggle" else "jam"


def _usage(verb: str) -> str:
    return (
        f"[dim]usage: /{verb} <name> <question>[/dim]\n"
        f"[dim]       /{verb} ls   — stock + configured presets[/dim]\n"
        f"[dim]       /{verb} add <name> <backend[:kit][@model]>… "
        "[--merge synth_conflicts|concat_digest] [--cap N][/dim]\n"
        f"[dim]       /{verb} rm <name>[/dim]\n"
        "[dim]  Runs every member (home xAI + gig brains) on the question in "
        "parallel, then merges: synth_conflicts = agreements/conflicts/verdict; "
        "concat_digest = answers under member headers. write: false.[/dim]\n"
        "[dim]  Stock 'gig' slots bind to your default provider — configure one "
        "with /gigwork add first.[/dim]"
    )


def _add_usage(verb: str) -> str:
    return (
        f"[dim]usage: /{verb} add <name> <backend[:kit][@model]>… "
        "[--merge synth_conflicts|concat_digest] [--cap N][/dim]\n"
        "[dim]  members: xai · gig (default provider) · any configured provider; "
        "kit explore|bash|general (default explore); @model overrides the "
        "provider's model for that member.[/dim]\n"
        f"[dim]  e.g. /{verb} add trio xai kimi:bash anthropic@claude-haiku-4-5 "
        "--cap 3[/dim]"
    )


def _jam_origin(cfg: Any, name: str) -> str:
    """``config`` when the name is user-defined (including a stock name the
    config shadows), else ``stock``."""
    from xlii.jam import _user_jams
    return "config" if name in _user_jams(cfg) else "stock"


def _ls(console: Any, cfg: Any, verb: str) -> None:
    from xlii.chat_backend import GigError
    from xlii.jam import jam_specs

    try:
        specs = jam_specs(cfg)
    except GigError as e:
        console.print(f"[red]/{verb}: {e}[/red]")
        return
    console.print(f"[bold]{verb}s[/bold]")
    for name in sorted(specs):
        s = specs[name]
        members = " + ".join(m.label for m in s.members)
        console.print(
            f"  {name:<16} {members}  ·  merge {s.merge}  ·  "
            f"≤{s.max_parallel} parallel  [dim]({_jam_origin(cfg, name)})[/dim]"
        )


def _add(console: Any, agent: Any, rest: str, verb: str) -> None:
    from xlii.chat_backend import GigError
    from xlii.jam import STOCK_JAMS, add_jam_to_config
    from xlii.repl_cmds.gigwork import _persist_gigwork

    toks = rest.split()
    name = toks[0] if toks else ""
    members: "list[str]" = []
    merge = "synth_conflicts"
    cap = 0
    err = ""
    i = 1
    while i < len(toks):
        t = toks[i]
        if t == "--merge" and i + 1 < len(toks):
            merge = toks[i + 1]; i += 2
        elif t == "--cap" and i + 1 < len(toks):
            try:
                cap = int(toks[i + 1])
            except ValueError:
                err = f"--cap needs an integer; got {toks[i + 1]!r}"
                break
            i += 2
        elif t.startswith("--"):
            err = f"unknown flag {t!r}"
            break
        else:
            members.append(t); i += 1
    if err:
        console.print(f"[red]/{verb} add: {err}[/red]")
        return
    if not name or not members:
        console.print(_add_usage(verb))
        return
    try:
        spec = _persist_gigwork(
            agent,
            lambda cfg: add_jam_to_config(
                cfg, name, members, merge=merge, max_parallel=cap
            ),
        )
    except GigError as e:
        console.print(f"[red]/{verb} add: {e}[/red]")
        return
    shadow = " · shadows the stock preset" if name in STOCK_JAMS else ""
    console.print(
        f"[green]added[/green] {verb}[{spec.name}] · "
        f"{' + '.join(m.label for m in spec.members)} · merge {spec.merge} · "
        f"≤{spec.max_parallel} parallel{shadow}\n"
        f"[dim]  run it: /{verb} {spec.name} <question>[/dim]"
    )


def _rm(console: Any, agent: Any, name: str, verb: str) -> None:
    from xlii.chat_backend import GigError
    from xlii.jam import remove_jam_from_config
    from xlii.repl_cmds.gigwork import _persist_gigwork

    if not name:
        console.print(f"[red]/{verb} rm: pass a {verb} name[/red]")
        return
    try:
        existed, uncovers = _persist_gigwork(
            agent, lambda cfg: remove_jam_from_config(cfg, name)
        )
    except GigError as e:
        console.print(f"[red]/{verb} rm: {e}[/red]")
        return
    if not existed:
        console.print(f"[yellow]/{verb} rm: no configured {verb} {name!r}[/yellow]")
    elif uncovers:
        console.print(
            f"[green]removed[/green] {verb}[{name}] — the stock preset resurfaces"
        )
    else:
        console.print(f"[green]removed[/green] {verb}[{name}]")


def _jam_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    agent = state.agent if state else ctx.get("agent")
    verb = _verb(line)
    if agent is None:
        console.print(f"[red]/{verb} needs an active agent session[/red]")
        return True
    cfg = agent.cfg

    parts = line.split(maxsplit=2)
    name = parts[1].strip() if len(parts) > 1 else ""
    question = parts[2].strip() if len(parts) > 2 else ""

    if not name:
        console.print(_usage(verb))
        return True
    if name == "ls":
        _ls(console, cfg, verb)
        return True
    if name == "add":
        _add(console, agent, question, verb)
        return True
    if name == "rm":
        _rm(console, agent, question.split()[0] if question.split() else "", verb)
        return True
    if not question:
        console.print(f"[red]/{verb} {name}: pass a question[/red]")
        return True

    from xlii.chat_backend import GigError
    from xlii.jam import resolve_jam, run_jam
    from xlii.plugin import load_subscriptions

    try:
        spec = resolve_jam(cfg, name)
    except GigError as e:
        console.print(f"[red]/{verb}: {e}[/red]")
        return True

    members = " + ".join(m.label for m in spec.members)
    console.print(f"[dim]{verb}[{spec.name}] · {members} · running…[/dim]")

    try:
        result = run_jam(
            name,
            question,
            cfg=cfg,
            project=agent.project,
            clients=agent.clients,
            pool=getattr(agent, "pool", None),
            subscribed_plugins=load_subscriptions(agent.project.xli_dir),
            on_progress=lambda msg: console.print(f"[dim]  {msg}[/dim]"),
        )
    except GigError as e:
        console.print(f"[red]/{verb}: {e}[/red]")
        return True

    from xlii.cost import format_tokens

    for r in result.results:
        if r.ok:
            console.print(
                f"[dim]--- {r.member.label} · {r.model} · {r.iterations} iter · "
                f"{format_tokens(r.total_tokens)} ---[/dim]"
            )
        else:
            console.print(f"[dim]--- {r.member.label} · failed: {r.error} ---[/dim]")
    merge_note = (
        f"synth via {result.synth_model}" if result.synth_model else result.spec.merge
    )
    console.print(f"[dim]--- {verb}[{result.spec.name}] · {merge_note} ---[/dim]")
    console.print(result.merged or "[dim](empty)[/dim]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="jam",
            handler=_jam_handler,
            description="Gaggle: named multi-brain preset (home + gigs) and merge the answers",
            usage="/jam <name> <question> | ls | add <name> <backend[:kit][@model]>… [--merge …] [--cap N] | rm <name>",
            category="knowledge",
            aliases=["gaggle"],
            repls=["code", "chat"],
        )
    )
