"""Code-REPL slash commands: /status, /model, /temp, /project.

/model is registered in both code and chat REPLs (with `models` as a hidden
alias — the old /models status view folded into bare /model). /project absorbs
the former /projects list/find/switch surface (`projects` is a hidden alias).
/status and the rest are code-only — the chat REPL has its own /status in chat.py.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command
from xlii.project_resolver import ProjectResolution, project_is_alive, resolve_registered_project
from xlii.registry import Registry


def _temp_handler(line: str, ctx: dict[str, Any]) -> bool:
    agent = ctx["agent"]
    cfg = ctx["cfg"]
    parts = line.split()
    if len(parts) < 2:
        ctx["console"].print(
            f"[dim]usage: /temp <value 0.0..2.0> [--chat]  "
            f"(current orch={cfg.orchestrator_temp()}, "
            f"chat={cfg.chat_temp()}, "
            f"override={agent.next_turn_temp_override})[/dim]"
        )
        return True
    chat_only = "--chat" in parts
    nums = [p for p in parts[1:] if p != "--chat"]
    if len(nums) != 1:
        ctx["console"].print(
            "[dim]usage: /temp <value 0.0..2.0> [--chat][/dim]"
        )
        return True
    try:
        t = float(nums[0])
    except ValueError:
        ctx["console"].print(f"[red]invalid temperature: {nums[0]!r}[/red]")
        return True
    if not 0.0 <= t <= 2.0:
        ctx["console"].print("[red]temperature must be 0.0..2.0[/red]")
        return True
    agent.next_turn_temp_override = t
    agent.session.next_turn_temp_chat_only = chat_only
    scope = "chat/help turns only" if chat_only else "next turn"
    ctx["console"].print(
        f"[yellow]temperature override = {t}[/yellow] "
        f"[dim](applies to {scope}, then reverts to config)[/dim]"
    )
    return True


def _swarm_handler(line: str, ctx: dict[str, Any]) -> bool:
    """Set or show the live ceiling on concurrent worker agents — the swarm.

    `cfg.max_parallel_workers` is read fresh at dispatch time (agent.py), so a
    change here lands on the very next turn: no restart, no config edit. It is a
    *ceiling*, not a target — the orchestrator still decides how many subagents
    to actually fan out. Session-sticky by default (reverts to config.json on
    next launch); pass --save to persist. Note: this same ceiling also bounds
    the end-of-turn sync upload fan-out (sync.py).
    """
    agent = ctx["agent"]
    cfg = agent.cfg
    console = ctx["console"]
    pool = ctx.get("pool")
    n_keys = len(pool) if pool is not None else 1
    # pool[0] is reserved for the orchestrator when there is more than one key;
    # with a single key everything shares it. That's the worker-key headroom.
    worker_keys = n_keys - 1 if n_keys > 1 else 1

    args = line.split()[1:]  # drop "/swarm"
    save = "--save" in args
    nums = [a for a in args if a != "--save"]

    if not nums:
        console.print(
            f"swarm ceiling: [cyan]{cfg.max_parallel_workers}[/cyan] concurrent worker(s)  "
            f"[dim]({worker_keys} worker key(s) in pool)[/dim]"
        )
        console.print(
            "[dim]/swarm <n> to change (live, this session; --save to persist). "
            "A ceiling, not a target; also bounds sync upload threads.[/dim]"
        )
        return True

    try:
        n = int(nums[0])
    except ValueError:
        console.print(f"[red]invalid swarm size: {nums[0]!r}[/red]")
        return True
    if n < 1:
        console.print("[red]swarm size must be >= 1[/red]")
        return True

    cfg.max_parallel_workers = n
    suffix = " [dim](saved to config.json)[/dim]" if save else " [dim](this session; --save to persist)[/dim]"
    if save:
        cfg.save()
    console.print(f"[yellow]swarm ceiling = {n}[/yellow]{suffix}")
    if n > worker_keys:
        console.print(
            f"[dim]note: only {worker_keys} worker key(s) in the pool — beyond that, "
            f"workers reuse keys and share rate budget (provision more with "
            f"`xlii bootstrap`).[/dim]"
        )
    return True


def _configured_model_ids(cfg) -> set[str]:
    """Ids already in this config (roles + pricing table)."""
    priced = cfg.pricing if isinstance(getattr(cfg, "pricing", None), dict) else {}
    return {m for m in (cfg.orchestrator(), cfg.worker(), cfg.chat(), cfg.help()) if m} | set(priced)


def _discovered_model_ids(cfg) -> list[str]:
    """Live OpenAI-compatible ``/v1/models`` catalog, or [] if no key works."""
    from xlii.bootstrap import discover_models

    try:
        keys = [kp.api_key for kp in cfg.key_pairs() if kp.api_key]
    except Exception:
        keys = []
    mgmt = getattr(cfg, "management_api_key", None) or ""
    if not keys and not mgmt:
        return []
    return discover_models(mgmt, getattr(cfg, "team_id", None) or "", chat_keys=keys)


def _model_handler(line: str, ctx: dict[str, Any]) -> bool:
    """Switch the orchestrator (or --worker) model live — no relaunch.

    Both models are resolved fresh at dispatch (agent.py reads the orchestrator
    model each turn, worker_agent.py reads the worker model each dispatch), so a
    change here lands on the **very next turn**. Persisted to config.json by
    default (survives restart); pass --session for a this-session-only change
    that reverts to config.json on next launch — the same session/save split as
    /swarm.

    The orchestrator model can be shadowed by a sticky persona/loadout
    `model_override`; when one is active we move it to the new id too so the
    switch is actually visible this session (otherwise we leave it None, which
    keeps the status line from falsely reading "persona-pinned").
    """
    agent = ctx["agent"]
    cfg = agent.cfg
    console = ctx["console"]

    args = line.split()[1:]  # drop "/model"
    session_only = "--session" in args
    worker = "--worker" in args
    chat = "--chat" in args
    profile_name: str | None = None
    if "--profile" in args:
        idx = args.index("--profile")
        if idx + 1 >= len(args) or args[idx + 1].startswith("-"):
            console.print("[red]usage: /model --profile <name> [--session][/red]")
            return True
        profile_name = args[idx + 1]
        args = args[:idx] + args[idx + 2:]
    names = [a for a in args if not a.startswith("-")]

    if profile_name:
        from xlii.model_profiles import apply_model_profile

        try:
            prof = apply_model_profile(cfg, profile_name, persist=not session_only)
        except KeyError:
            console.print(
                f"[red]unknown profile {profile_name!r}[/red] "
                "[dim](`/model` shows roles; `xlii models profile list` for profiles)[/dim]"
            )
            return True
        agent.model_override = None
        where = (
            "[dim](saved to config.json)[/dim]" if not session_only
            else "[dim](this session; reverts to config.json on next launch)[/dim]"
        )
        console.print(
            f"[yellow]profile {profile_name}[/yellow] applied — "
            f"orch={prof.get('orchestrator')} worker={prof.get('worker')} "
            f"chat={prof.get('chat')} help={prof.get('help')} {where} "
            "[dim]— takes effect next turn[/dim]"
        )
        return True

    # `--list` — live /v1/models catalog (same fetch as `xlii models list`),
    # merged with whatever is already configured or priced so a dead key
    # still shows the local set.
    if "--list" in args:
        from xlii.model_profiles import BUILTIN_MODEL_PROFILES

        local = _configured_model_ids(cfg)
        live = _discovered_model_ids(cfg)
        ids = sorted(set(live) | local)
        if live:
            console.print(
                f"[bold]{len(ids)} model(s)[/bold] "
                f"[dim]({len(live)} from the live catalog; /model <id> to switch)[/dim]"
            )
        else:
            console.print(
                "[bold]known models[/bold] "
                "[dim](configured + priced — live catalog unavailable; "
                "check keys / `xlii models list`)[/dim]"
            )
        orch = cfg.orchestrator()
        worker = cfg.worker()
        chat_m = cfg.chat()
        help_m = cfg.help()
        for mid in ids:
            marks = []
            if mid == orch:
                marks.append("orch")
            if mid == worker:
                marks.append("worker")
            if mid == chat_m:
                marks.append("chat")
            if mid == help_m:
                marks.append("help")
            tag = f"  [cyan]← {' + '.join(marks)}[/cyan]" if marks else ""
            console.print(f"  · [cyan]{mid}[/cyan]{tag}")
        console.print(
            "[dim]profiles:[/dim] "
            + ", ".join(sorted(BUILTIN_MODEL_PROFILES))
            + " [dim](`/model --profile <name>`)[/dim]"
        )
        return True

    if not names:
        # Bare `/model` (and its `/models` alias) — the live status view: every
        # role + its temperature + the active slot. Folds in what the old
        # standalone `/models` showed, so collapsing the pair loses nothing.
        ov = agent.model_override
        ov_note = f"  [yellow](session override: {ov})[/yellow]" if ov else ""
        next_temp = agent.next_turn_temp_override
        temp_note = (
            f"  [yellow](next turn override: {next_temp})[/yellow]"
            if next_temp is not None else ""
        )
        chat_temp = getattr(cfg, "chat_temp", lambda: cfg.orchestrator_temp())()
        effective, role = agent.orchestrator_model_and_role()
        console.print(
            f"orchestrator: [cyan]{cfg.orchestrator()}[/cyan]  "
            f"temp=[cyan]{cfg.orchestrator_temp()}[/cyan]{ov_note}{temp_note}"
        )
        console.print(
            f"worker:       [cyan]{cfg.worker()}[/cyan]  temp=[cyan]{cfg.worker_temp()}[/cyan]"
        )
        console.print(
            f"chat:         [cyan]{cfg.chat()}[/cyan]  "
            f"temp=[cyan]{chat_temp}[/cyan]  [dim](persona / conversational)[/dim]"
        )
        console.print(
            f"help:         [cyan]{cfg.help()}[/cyan]  "
            f"[dim](/howto surface)[/dim]"
        )
        console.print(
            f"active slot:  [cyan]{effective}[/cyan]  [dim]({role} role)[/dim]"
        )
        console.print(
            "[dim]/model <id> switches the orchestrator (live, persisted); "
            "--worker / --chat / --help-model target other roles; "
            "--profile <name> applies a preset; "
            "--list fetches every id this key can see; --session = this session only.[/dim]"
        )
        from xlii.model_profiles import BUILTIN_MODEL_PROFILES

        console.print(
            "[dim]profiles:[/dim] "
            + ", ".join(sorted(BUILTIN_MODEL_PROFILES))
            + " [dim](`xlii models profile list`)[/dim]"
        )
        return True

    name = names[0]
    known = {cfg.orchestrator(), cfg.worker(), cfg.chat(), cfg.help()} | set(cfg.pricing)
    help_slot = "--help-model" in args or "--help-role" in args

    if worker:
        cfg.worker_model = name
        role_label = "worker"
    elif chat:
        cfg.chat_model = name
        role_label = "chat"
    elif help_slot:
        cfg.help_model = name
        role_label = "help"
    else:
        cfg.orchestrator_model = name
        role_label = "orchestrator"
        # A persona/loadout pin shadows cfg for the orchestrator (agent.py:344
        # resolves `model_override or cfg...`). Move the pin too so the switch is
        # visible this session; leave it None when there is none, so status
        # doesn't read as persona-pinned.
        if agent.model_override:
            agent.model_override = name

    if not session_only:
        cfg.save()

    where = (
        "[dim](saved to config.json)[/dim]" if not session_only
        else "[dim](this session; reverts to config.json on next launch)[/dim]"
    )
    console.print(
        f"[yellow]{role_label} model = {name}[/yellow] {where} "
        "[dim]— takes effect next turn[/dim]"
    )
    if name not in known:
        console.print(
            f"[dim]note: {name!r} isn't in your configured/known set — applying "
            "anyway (an invalid id will error at call time).[/dim]"
        )
    return True


def _cwd_handler(line: str, ctx: dict[str, Any]) -> bool:
    """Return the live shell to the project root, or /cwd <path> to navigate.

    The no-arg form is the quick way back after wandering with `cd`. Unlike a
    bare `cd`, this works in plan mode too (where bare input goes to the model).
    """
    state = ctx.get("state")
    console = ctx["console"]
    if state is None or getattr(state, "shell_cwd", None) is None:
        console.print("[dim]live shell cwd is only tracked in `xlii code`[/dim]")
        return True
    from xlii.repl import _change_dir

    parts = line.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    # `_change_dir` handles a bare `cd` (→ project root) and `cd <path>`,
    # including the outside-project warning and prompt-prefix update.
    _change_dir(state, f"cd {arg}".rstrip())
    return True


def _project_entry_line(entry, *, current_root=None) -> str:
    marker = "[green]●[/green]" if project_is_alive(entry) else "[red]✗[/red]"
    current = ""
    if current_root is not None:
        try:
            if entry.path and Path(entry.path).resolve() == current_root:
                current = " [cyan](current)[/cyan]"
        except OSError:
            # Path resolution can fail (e.g., broken symlink/permission issues);
            # omit the "(current)" marker and continue rendering the entry.
            pass
    cid = entry.collection_id or "local-only"
    node = (getattr(entry, "node", None) or "").strip()
    via = f"  [dim]via {node}[/dim]" if node else ""
    return f"  {marker} [dim]{entry.name}[/dim]  {entry.path}  [dim]{cid}[/dim]{via}{current}"


def _print_project_resolution(console, res: ProjectResolution) -> bool:
    if res.ok and res.entry is not None:
        console.print("[bold]project match:[/bold]")
        console.print(_project_entry_line(res.entry))
        console.print(
            f"[dim]teleport: /project switch {res.entry.name}  "
            f"· shell: xlii code {res.entry.name}[/dim]"
        )
        return True
    if res.ambiguous:
        console.print(f"[yellow]ambiguous project query {res.query!r}; matches:[/yellow]")
        for entry in res.matches:
            console.print(_project_entry_line(entry))
        return False
    if res.entry is not None:
        console.print(f"[yellow]matched project is unavailable: {res.entry.name}[/yellow]")
        console.print(_project_entry_line(res.entry))
        if res.reason:
            console.print(f"[dim]{res.reason}[/dim]")
        return False
    console.print(f"[yellow]no projects matching {res.query!r}[/yellow]")
    return False


def _split_project_query(parts: list[str], known_flags: set[str]) -> tuple[str, set[str], list[str]]:
    flags: set[str] = set()
    query_parts: list[str] = []
    unknown: list[str] = []
    for part in parts:
        if part.startswith("--"):
            if part in known_flags:
                flags.add(part)
            else:
                unknown.append(part)
        else:
            query_parts.append(part)
    return " ".join(query_parts).strip(), flags, unknown


def _project_list(
    reg: Registry,
    console,
    state,
    parts: list[str],
) -> bool:
    flt = " ".join(parts[1:]).lower()
    if not reg.entries:
        console.print("[dim](no registered projects)[/dim]")
    else:
        current_root = None
        if state is not None and getattr(state, "project", None) is not None:
            current_root = state.project.project_root.resolve()
        entries = reg.entries
        if flt:
            entries = [
                e for e in entries
                if flt in e.name.lower() or flt in e.path.lower()
            ]
        if not entries:
            console.print(f"[yellow]no projects matching {flt!r}[/yellow]")
        else:
            for e in sorted(entries, key=lambda entry: entry.name.lower()):
                console.print(_project_entry_line(e, current_root=current_root))
            console.print(
                "[dim]use /project find <name> to inspect or "
                "/project switch <name> to teleport[/dim]"
            )
    return True


def _switch_to_resolved_project(ctx: dict[str, Any], res: ProjectResolution, *, flags: set[str]) -> None:
    console = ctx["console"]
    if res.project is None:
        return
    from xlii.repl_cmds.switch import switch_to_code_project

    switch_to_code_project(
        ctx,
        res.project,
        reset=("--reset" in flags or "--fresh" in flags),
        reload_surface="--no-reload" not in flags,
    )
    state = ctx.get("state")
    if "--no-sync" in flags and state is not None:
        state.no_sync = True
        console.print("[dim]sync disabled for this session[/dim]")


_PROJECT_STARTUP_FLAGS = {"--show", "--clear", "--off", "--auto"}
_PROJECT_STARTUP_USAGE = (
    "[dim]usage: /project startup <task> [--auto] | "
    "/project startup --show | --clear | --off[/dim]"
)


def _project_startup_handler(parts: list[str], ctx: dict[str, Any]) -> bool:
    """`/project startup` — per-machine bind / show / clear / session mute."""
    from xlii.commands import session_is_elevated
    from xlii.session_boot import (
        bind_startup_task,
        clear_startup_binding,
        mute_startup,
        startup_show_lines,
    )

    console = ctx["console"]
    state = ctx.get("state")
    project = None
    if state is not None:
        project = getattr(state, "project", None)
    if project is None:
        project = ctx.get("project")
    query, flags, unknown = _split_project_query(parts[2:], _PROJECT_STARTUP_FLAGS)
    if unknown:
        console.print(f"[yellow]unknown flag(s): {' '.join(unknown)}[/yellow]")
        return True
    exclusive = flags & {"--show", "--clear", "--off"}
    if len(exclusive) > 1:
        console.print("[red]use one of --show, --clear, --off[/red]")
        return True
    if exclusive and query:
        console.print(_PROJECT_STARTUP_USAGE)
        return True
    if "--auto" in flags and ("--show" in flags or "--clear" in flags or "--off" in flags):
        console.print(_PROJECT_STARTUP_USAGE)
        return True
    if "--off" in flags:
        if state is None:
            console.print("[red]/project startup --off needs an interactive session[/red]")
            return True
        mute_startup(state)
        console.print("[dim]startup task muted for this session[/dim]")
        return True
    if project is None:
        console.print("[red]/project startup needs an active project[/red]")
        return True
    if "--show" in flags:
        for ln in startup_show_lines(project):
            console.print(ln, markup=False)
        if state is not None and getattr(state, "startup_off", False):
            console.print("[dim]muted this session (--off)[/dim]")
        return True
    if "--clear" in flags:
        from pathlib import Path as _P

        root = _P(str(project.project_root)).resolve()
        from xlii.session_boot import load_startup_binding

        if load_startup_binding(root) is None:
            console.print("[dim]no startup task bound for this project[/dim]")
            return True
        clear_startup_binding(root, state=state)
        console.print("[dim]startup task unbound[/dim]")
        return True
    if not query:
        console.print(_PROJECT_STARTUP_USAGE)
        return True
    auto = "--auto" in flags
    tier = "safe"
    if auto and state is not None:
        tier = (
            getattr(
                getattr(getattr(state, "agent", None), "session", None),
                "trust_tier",
                "safe",
            )
            or "safe"
        )
    bind_startup_task(
        project, query, console=console, auto=auto,
        elevated=session_is_elevated(ctx), tier=str(tier),
        state=state,
    )
    return True


_PROJECT_RM_FLAGS = {"--yes", "-y", "--dry-run", "--keep-local", "--local-only"}


def _project_rm_handler(parts: list[str], ctx: dict[str, Any]) -> bool:
    """`/project rm` — in-session twin of `xlii project rm`."""
    console = ctx["console"]
    state = ctx.get("state")

    rest = parts[2:]
    flags = {a for a in rest if a.startswith("-")}
    names = [a for a in rest if not a.startswith("-")]
    unknown = flags - _PROJECT_RM_FLAGS
    if unknown:
        console.print(f"[yellow]unknown flag(s): {' '.join(sorted(unknown))}[/yellow]")
        return True
    keep_local = "--keep-local" in flags
    local_only = "--local-only" in flags
    if keep_local and local_only:
        console.print("[red]--keep-local and --local-only are mutually exclusive[/red]")
        return True

    from xlii.cmds.project import _resolve_rm_target, _run_project_rm

    # Bare / "." → the current session's project; a name → registry resolution.
    if not names or names[0] == ".":
        project = state.project if state is not None else None
        if project is None:
            console.print("[red]/project rm needs an active project or a name[/red]")
            return True
    else:
        project = _resolve_rm_target(names[0])
        if project is None:
            return True  # _resolve_rm_target already explained

    # Removing the live current project pulls its .xlii state out from under this
    # session — warn (the confirm still gates the actual delete).
    is_current = False
    if state is not None and getattr(state, "project", None) is not None:
        try:
            is_current = (
                Path(project.project_root).resolve()
                == Path(state.project.project_root).resolve()
            )
            if is_current:
                console.print(
                    "[yellow]⚠ this is the CURRENT project — removing it tears down "
                    "this session's .xlii state[/yellow]"
                )
        except OSError as e:
            console.print(
                f"[dim]could not compare project paths ({e}); skipping current-project warning[/dim]"
            )

    clients = None
    if not local_only:
        from xlii.client import Clients, MissingCredentials
        cfg = ctx.get("cfg")
        try:
            clients = Clients.from_config(cfg)
        except MissingCredentials as e:
            console.print(f"[red]{e}[/red]")
            console.print(
                "[dim](use [/dim][cyan]--local-only[/cyan][dim] to skip the cloud "
                "Collection)[/dim]"
            )
            return True

    rc = _run_project_rm(
        clients, project,
        keep_local=keep_local, local_only=local_only,
        dry_run="--dry-run" in flags, assume_yes=("--yes" in flags or "-y" in flags),
        console=console,
    )
    # Tear-down removed this session's .xlii tree — end the REPL cleanly instead
    # of continuing with a dangling project/journal reference.
    if (
        rc == 0
        and is_current
        and "--dry-run" not in flags
        and not keep_local
        and state is not None
    ):
        state._project_removed_locally = True
        state.quit_requested = True
    return True


def _project_forget_handler(parts: list[str], ctx: dict[str, Any]) -> bool:
    """Drop a registry row by path (``forget``) or every dead row (``prune``).

    Ghosts from pytest /tmp have no project.json — ``rm`` cannot load them.
    This only edits ``projects.json``. Disk and cloud are untouched.
    """
    console = ctx["console"]
    from pathlib import Path

    from xlii.project_paths import is_home_desk_project
    from xlii.registry import Registry

    reg = Registry.load()
    if parts[1] == "prune":
        dead = reg.prune_dead()
        if not dead:
            console.print("[dim]no ghost registry rows[/dim]")
            return True
        reg.save()
        for e in dead:
            console.print(f"[dim]forgot[/dim] {e.name}  [dim]{e.path}[/dim]")
        console.print(f"[green]pruned {len(dead)} ghost row(s)[/green]")
        return True

    path = " ".join(parts[2:]).strip()
    if not path:
        console.print("[dim]usage: /project forget <path>  |  /project prune[/dim]")
        return True
    try:
        from xlii.config import ProjectConfig

        pc = ProjectConfig.load(Path(path))
        if pc is not None and is_home_desk_project(pc):
            console.print("[yellow]won't forget the home desk[/yellow]")
            return True
    except Exception:
        # An unreadable config can't be matched against the home desk; the forget proceeds rather than wedging
        # on a bad file.
        pass
    if not reg.remove_by_path(path):
        console.print(f"[yellow]no registry row for {path}[/yellow]")
        return True
    reg.save()
    console.print(f"[green]forgot[/green] {path}")
    return True


def _project_sync_fabric_handler(ctx: dict[str, Any]) -> bool:
    """Pull every node's registry onto this throne; push the catalog back."""
    console = ctx["console"]
    from xlii.config import GlobalConfig
    from xlii.fabric import _default_connect, _default_require_remote
    from xlii.fabric_projects import sync_fabric_projects

    nodes = getattr(GlobalConfig.load(), "fabric_nodes", None) or {}
    if not nodes:
        console.print("[dim]no fabric nodes — xlii fabric add-node[/dim]")
        return True
    result = sync_fabric_projects(
        nodes,
        connect=_default_connect,
        require_remote=_default_require_remote,
        this_node="throne",
    )
    for res in result.nodes:
        console.print(f"[dim]{res.summary()}[/dim]")
    for line in result.pushed:
        console.print(f"[dim]pushed {line}[/dim]")
    for err in result.errors:
        console.print(f"[yellow]{err}[/yellow]")
    if not result.nodes and not result.errors:
        console.print("[dim]fabric projects: nothing to merge[/dim]")
    return True


def _project_remote_new_handler(parts: list[str], ctx: dict[str, Any]) -> bool:
    """``/project remote <node> <name>`` — folder on that node, Files here."""
    console = ctx["console"]
    if len(parts) < 4:
        console.print("[dim]usage: /project remote <node> <name>[/dim]")
        return True
    node, name = parts[2], parts[3]
    from xlii.config import GlobalConfig
    from xlii.fabric import _default_connect, _default_require_remote
    from xlii.fabric_projects import create_fabric_project

    cfg = ctx.get("cfg") or GlobalConfig.load()
    roster = getattr(cfg, "fabric_nodes", None) or {}
    try:
        created = create_fabric_project(
            name, node, roster=roster,
            connect=_default_connect, require_remote=_default_require_remote,
        )
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return True
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]{type(e).__name__}: {e}[/red]")
        return True
    console.print(
        f"[green]✓[/green] {created.name}  [dim]node {created.node}[/dim]"
    )
    from xlii.project_resolver import resolve_registered_project

    res = resolve_registered_project(created.name)
    if res.ok:
        _switch_to_resolved_project(ctx, res, flags=set())
    return True


def _project_handler(line: str, ctx: dict[str, Any]) -> bool:
    """`/project` — list, find, switch, or rm registered projects.

    Bare `/project` (or `/project <filter>`) lists the registry. Subcommands:
    `find`, `switch`, `startup`, `rm`, `sync-fabric` (merge node registries),
    `remote <node> <name>` (Collection-first create on a fabric node).
    `startup` is reserved (subcommand-wins); a project named "startup" stays
    reachable via `/project find startup`. `/projects` is a hidden alias."""
    console = ctx["console"]
    state = ctx.get("state")
    try:
        parts = shlex.split(line)
    except ValueError as e:
        console.print(f"[red]could not parse /project: {e}[/red]")
        return True

    if len(parts) > 1 and parts[1] == "rm":
        return _project_rm_handler(parts, ctx)
    if len(parts) > 1 and parts[1] == "startup":
        return _project_startup_handler(parts, ctx)
    if len(parts) > 1 and parts[1] in {"forget", "prune"}:
        return _project_forget_handler(parts, ctx)
    if len(parts) > 1 and parts[1] == "sync-fabric":
        return _project_sync_fabric_handler(ctx)
    if len(parts) > 1 and parts[1] in {"remote", "on"}:
        return _project_remote_new_handler(parts, ctx)

    reg = Registry.load()

    if len(parts) > 1 and parts[1] in {"find", "search"}:
        query, flags, unknown = _split_project_query(
            parts[2:],
            {
                "--go", "--switch", "--open",  # --open = CLI twin of --go
                "--reset", "--fresh", "--reload", "--no-reload", "--no-sync",
            },
        )
        if unknown:
            console.print(f"[yellow]unknown flag(s): {' '.join(unknown)}[/yellow]")
            return True
        if not query:
            console.print(
                "[dim]usage: /project find <name> [--go|--open] [--reset|--fresh][/dim]"
            )
            return True
        res = resolve_registered_project(query, registry=reg)
        if not _print_project_resolution(console, res):
            return True
        if "--go" in flags or "--switch" in flags or "--open" in flags:
            _switch_to_resolved_project(ctx, res, flags=flags)
        return True

    if len(parts) > 1 and parts[1] in {"switch", "go"}:
        query, flags, unknown = _split_project_query(
            parts[2:],
            {"--reset", "--fresh", "--reload", "--no-reload", "--no-sync"},
        )
        if unknown:
            console.print(f"[yellow]unknown flag(s): {' '.join(unknown)}[/yellow]")
            return True
        if not query:
            console.print(
                "[dim]usage: /project switch <name> "
                "[--reset|--fresh] [--no-reload] [--no-sync][/dim]"
            )
            return True
        res = resolve_registered_project(query, registry=reg)
        if not _print_project_resolution(console, res):
            return True
        _switch_to_resolved_project(ctx, res, flags=flags)
        return True

    return _project_list(reg, console, state, parts)


# Code REPL /status (project focused)
def _code_status_handler(line: str, ctx: dict[str, Any]) -> bool:
    state = ctx.get("state")
    project = state.project if state else ctx.get("project")
    agent = state.agent if state else ctx.get("agent")
    console = ctx["console"]

    if not project:
        return True

    # The stack frame (status-stack-fleet-view): what's above me · me · what's
    # below me. Vendor is a reserved slot (vendor-status-idea, deferred); self
    # leads with the primary axes; the fleet section renders at the end.
    console.print("[dim]▲ vendor   xAI · (deferred — vendor-status-idea)[/dim]")

    # Primary axes first (grades Phase 1) — one exclusive mode · trust · surface.
    try:
        from xlii import status as _st

        axes_src = state if state is not None else agent
        if axes_src is not None:
            console.print(f"[bold]● self     {_st.format_primary_axes(axes_src)}[/bold]")
    except Exception as exc:
        # Best-effort status enrichment; do not fail /status if this optional
        # formatter/import path errors.
        console.print(f"[dim]status detail unavailable: {exc}[/dim]")

    conv = (project.conversation_id or "")[:12]
    sync_mode = "[magenta]local-only[/magenta]" if project.local_only else "full (synced)"
    console.print(f"project: [bold]{project.name}[/bold]")
    console.print(f"  root:          {project.project_root}")
    console.print(f"  sync:          {sync_mode}")

    # project-browser.md P5: surface the cached fingerprint + git pulse (if any).
    try:
        from xlii.project_fingerprint import load_project_profile
        profile = load_project_profile(project.project_root)
        if profile and profile.fingerprints:
            console.print(f"  stack:         {'/'.join(profile.fingerprints)} [dim](/browse to explore)[/dim]")
    except Exception:
        # No cached fingerprint, or an unreadable profile -- the stack row is simply omitted.
        pass
    try:
        from xlii.git_status import git_snapshot
        snap = git_snapshot(project.project_root)
        if snap.is_repo:
            n = snap.changed_count
            dirty = f"[yellow]{n} changed[/yellow]" if n else "[green]clean[/green]"
            console.print(f"  git:           {snap.branch or '?'} [dim]({dirty}[dim])[/dim]")
    except Exception:
        # Not a repo, or git is unavailable -- the git row is simply omitted.
        pass
    try:
        from xlii.skills import active_skill_names, load_skills
        skills = load_skills(project.project_root)
        if skills:
            active = active_skill_names(state.attached_docs if state else [])
            extra = f", {len(active)} attached" if active else ""
            console.print(f"  skills:        {len(skills)} available{extra} [dim](/skill)[/dim]")
    except Exception:
        # An unreadable skills dir -- the skills row is simply omitted.
        pass
    if project.local_only:
        idx = project.xli_dir / "index.txt"
        if idx.exists():
            n = sum(1 for _ in idx.open())
            console.print(f"  index:         .xlii/index.txt — {n} files cached")
    else:
        console.print(f"  collection_id: {project.collection_id}")
    console.print(f"  conv_id:       {conv}…")
    console.print(f"  pool:          {len(state.pool) if state else '?'} key(s)")
    try:
        _cfg = agent.cfg if agent is not None else None
        if _cfg is not None:
            _region = _cfg.api_region()
            _tag = f"region {_region}" if _region else "global edge"
            console.print(f"  api:           {_cfg.api_host()} [dim]({_tag})[/dim]")
    except Exception:
        # A cfg without region accessors -- the api row is simply omitted.
        pass
    from xlii.repl_cmds.consult import consult_status_line
    console.print(consult_status_line(project.xli_dir))
    plan = state.plan_mode if state else (agent.plan_mode if agent else False)
    console.print(f"  plan mode:     {'[yellow]ON[/yellow]' if plan else 'off'}")
    disc = state.discovery_mode if state else (agent.discovery_mode if agent else False)
    if disc:
        console.print("  discovery:     [cyan]ON[/cyan] [dim](read-only discussion)[/dim]")
    ops = state.ops_mode if state else (agent.ops_mode if agent else False)
    if ops:
        console.print("  ops:           [green]ON[/green] [dim](OS diagnostics)[/dim]")
    rail = agent.rail if agent else None
    if rail is not None:
        gate = "read-only" if rail.is_read_only_stage else "writes unlocked"
        console.print(f"  rail:          [magenta]{rail.get_status_header()}[/magenta] [dim]({gate})[/dim]")
    dbg = getattr(agent, "debug", None) if agent else None
    if dbg is not None:
        console.print(f"  debug:         [cyan]{dbg.get_status_header()}[/cyan] [dim]({dbg.tool_mode})[/dim]")

    # Use the nice first-class status formatter (includes workspace + durable attachments)
    extra = state.format_status(include_attachments=True) if state else ""
    if extra.strip():
        console.print(extra)

    # ▼ fleet — the downstream stack (the /remote roster + fabric roles). The
    # DEFAULT path opens no socket (specs + probe cache only); --probe [name]
    # pays a real connect per host, concurrent, hard-capped. Exception-guarded:
    # a fleet hiccup must never sink the rest of /status.
    try:
        from xlii import fleet_status
        from xlii.remotefs import manager as _rmgr

        tokens = line.split()[1:]
        if tokens and tokens[0] == "--probe":
            target = tokens[1] if len(tokens) > 1 else None
            roster = _rmgr.names()
            if target is not None and target not in roster:
                console.print(f"[yellow]no remote named {target!r}[/yellow] "
                              f"[dim](configured: {', '.join(roster) or 'none'})[/dim]")
            else:
                names = [target] if target else roster
                if names:
                    console.print(f"[dim]probing {len(names)} host(s), "
                                  f"{fleet_status.PROBE_TIMEOUT_S:.0f}s cap each…[/dim]")
                    results = fleet_status.probe_fleet(names)
                    fleet_status.save_probe_cache(results, roster)

        from xlii.config import GlobalConfig as _GC
        rows = fleet_status.fleet_rows(
            fabric_nodes=getattr(_GC.load(), "fabric_nodes", {}) or {},
            cache=fleet_status.load_probe_cache())
        if rows:
            for ln in fleet_status.format_fleet_lines(rows):
                console.print(ln)
        else:
            console.print("[dim]▼ fleet    (no remotes — /remote add <name>)[/dim]")
    except Exception as exc:
        console.print(f"[dim]fleet unavailable: {exc}[/dim]")

    return True


def _tui_handler(line: str, ctx: dict[str, Any]) -> bool:
    """Open the full-screen Textual UI *in this session* — same process, same
    agent and state. This is the supported alternative to launching a nested
    `xlii code --tui` (which would collide on on-disk state). On exit, drop back
    to this inline prompt."""
    console = ctx["console"]
    try:
        import textual  # noqa: F401
    except ImportError:
        console.print("[yellow]/tui needs the Textual front-end[/yellow] — "
                      "install with [cyan]pip install 'xlii[tui]'[/cyan].")
        return True

    state = ctx.get("state")
    agent = ctx["agent"]
    if state is None:
        console.print("[red]/tui is only available in the code REPL[/red]")
        return True

    from xlii.tui_textual import run_tui_over_session

    # The TUI's on_mount repoints BOTH the agent's and the state's console at the
    # transcript, installs TUI-only shell hooks, and forces the styled view; the
    # shared helper snapshots and restores all of it on exit — otherwise the next
    # state.console.print() (e.g. /exit's "bye") calls into the torn-down Textual
    # app and raises "App is not running".
    run_tui_over_session(state, agent, project_name=state.project.name)
    # A2: /exit·/quit inside the TUI set state.quit_requested. The session is
    # over — don't announce a return to the inline prompt; the inline loop's
    # quit check fires next and raises _QuitSession (the fire-alarm exit).
    if getattr(state, "quit_requested", False):
        return True
    console.print("[dim]left the TUI — back at the inline prompt[/dim]")
    return True


def _terminal_handler(line: str, ctx: dict[str, Any]) -> bool:
    """The named inverse of /tui (A1). From the *inline* REPL this is a friendly
    no-op — you're already at the inline terminal. The full-screen TUI intercepts
    /terminal (and /inline) directly (tui_textual._submit_prompt) and drops back
    to this prompt, so this handler is only ever reached from the inline side."""
    ctx["console"].print(
        "[dim]already in the inline terminal — use [/dim][cyan]/tui[/cyan]"
        "[dim] for the full-screen view[/dim]"
    )
    return True


def _persist_chat_tier(cfg: Any, key: Optional[str]) -> None:
    """Write the sticky ``/tier`` pick so the next session starts the same."""
    if cfg is None:
        return
    try:
        cfg.chat_tier = key if key else "off"
        save = getattr(cfg, "save", None)
        if callable(save):
            save()
    except Exception:
        # The tier already applied to the live session; only persisting the choice is lost.
        pass


def _tier_handler(line: str, ctx: dict[str, Any]) -> bool:
    """Set or show the chat tier — the per-conversation reasoning-depth dial.

    `fast` (cheap fast model) / `expert` (reasoning model) / `heavy` (coordinated
    deep search) / `auto` (routed per message). The tier steers the chat-slot
    model, read live by resolve_orchestrator_model, so a change lands on the next
    turn. It applies to chat-mode (conversational) turns; in plain code turns the
    orchestrator role is used and the tier is inert. A sticky persona/loadout
    model pin still wins over a tier. `/tier off` clears it.
    """
    from xlii.chat_tiers import (
        AUTO,
        CONCRETE_TIERS,
        VALID_TIERS,
        normalize_tier,
        resolve_tier_model,
    )

    agent = ctx["agent"]
    cfg = agent.cfg
    console = ctx["console"]
    session = agent.session

    args = line.split()[1:]  # drop "/tier"
    if not args:
        cur = getattr(session, "chat_tier", None)
        cur_label = cur if cur else "off [dim](plain chat model)[/dim]"
        console.print(f"[bold]chat tier:[/bold] {cur_label}")
        for name in VALID_TIERS:
            active = " [cyan]← active[/cyan]" if name == cur else ""
            if name == AUTO:
                console.print(
                    "  · [bold]auto[/bold]  routes each message to "
                    f"fast/expert/heavy{active}"
                )
                continue
            t = CONCRETE_TIERS[name]
            model = resolve_tier_model(cfg, name) or "?"
            console.print(
                f"  · [bold]{name}[/bold]  {t.blurb} [dim]({model})[/dim]{active}"
            )
        console.print(
            "[dim]usage: /tier <fast|expert|heavy|auto|off>  "
            "· applies to chat-mode turns "
            "· one message: >>tier <message>  (>>f >>e >>h >>a)[/dim]"
        )
        return True

    choice = args[0].strip().lower()
    if choice in ("off", "none", "clear"):
        session.chat_tier = None
        _persist_chat_tier(cfg, None)
        console.print(
            "[yellow]chat tier cleared[/yellow] "
            "[dim](plain chat model — takes effect next turn)[/dim]"
        )
        return True
    key = normalize_tier(choice)
    if key is None:
        console.print(
            f"[red]unknown tier {choice!r}[/red] "
            f"[dim](pick: {', '.join(VALID_TIERS)}, or off)[/dim]"
        )
        return True
    session.chat_tier = key
    _persist_chat_tier(cfg, key)
    if key == AUTO:
        console.print(
            "[yellow]chat tier = auto[/yellow] "
            "[dim](routed per message — takes effect next turn)[/dim]"
        )
    elif key == "heavy":
        model = resolve_tier_model(cfg, key) or "?"
        console.print(
            f"[yellow]chat tier = heavy[/yellow] "
            f"[dim]({CONCRETE_TIERS[key].blurb} · {model}) — takes effect next turn[/dim]"
        )
    else:
        model = resolve_tier_model(cfg, key) or "?"
        console.print(
            f"[yellow]chat tier = {key}[/yellow] "
            f"[dim]({CONCRETE_TIERS[key].blurb} · {model}) — takes effect next turn[/dim]"
        )
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="tier",
            handler=_tier_handler,
            description="Set/show the chat reasoning-depth tier (fast|expert|heavy|auto)",
            usage="/tier [fast|expert|heavy|auto|off]",
            category="mode",
            repls=["code", "chat"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="tui",
            handler=_tui_handler,
            description="Open the full-screen Textual UI in this session (no nested process)",
            category="session",
            repls=["code"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="terminal",
            handler=_terminal_handler,
            aliases=["inline"],
            description="Leave the full-screen TUI and return to the inline REPL (inverse of /tui)",
            category="session",
            repls=["code"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="temp",
            handler=_temp_handler,
            description="One-shot temperature override for next turn only",
            usage="/temp <0.0..2.0> [--chat]",
            category="mode",
            repls=["code"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="swarm",
            handler=_swarm_handler,
            description="Show or set the live ceiling on concurrent worker agents",
            usage="/swarm [n] [--save]",
            category="mode",
            repls=["code"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="model",
            handler=_model_handler,
            # `models` (the old status view) is now a hidden alias — bare /model
            # shows the same roles+temps, /model --list catalogs ids.
            aliases=["models"],
            description="Show/switch orchestrator/worker/chat/help model live (bare shows; <id> sets; --list fetches the live catalog)",
            usage="/model [id] [--worker | --chat | --help-model | --profile <name>] [--list] [--session]",
            category="mode",
            repls=["code", "chat"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="project",
            handler=_project_handler,
            aliases=["projects"],
            description="List, find, switch, bind a startup task, or remove registered projects",
            usage=(
                "/project [filter] | /project find <name> | /project switch <name> | "
                "/project startup <task> [--auto] | /project startup --show|--clear|--off | "
                "/project rm [name|.] [--dry-run] [--yes] [--keep-local|--local-only]"
            ),
            category="admin",
            repls=["code"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="cwd",
            handler=_cwd_handler,
            description="Return the live shell to the project root (or /cwd <path> to go elsewhere)",
            usage="/cwd [path]",
            category="session",
            repls=["code"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="status",
            handler=_code_status_handler,
            description="Stack status: vendor · session/project state · downstream fleet (remotes + fabric roles; --probe rechecks reachability)",
            usage="/status [--probe [name]]",
            category="session",
            repls=["code"],
        )
    )
