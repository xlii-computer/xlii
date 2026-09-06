"""/loop — autonomous build→test→fix macro-loop (L0: tests-only oracle)."""

from __future__ import annotations

import shlex
from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command
from xlii.loop import LoopController, loop_start_push_warnings, resolve_commit_mode, resolve_push_mode
from xlii.loop_judge import resolve_judges


def _default_test_command(ctx: dict[str, Any]) -> str:
    state = ctx.get("state")
    cfg = state.cfg if state else None
    project = state.project if state else ctx.get("project")
    if project is not None:
        proj_cfg = getattr(project, "loop_test_command", None)
        if proj_cfg:
            return str(proj_cfg)
    if cfg is not None:
        defaults = getattr(cfg, "loop_defaults", None) or {}
        if isinstance(defaults, dict) and defaults.get("test_command"):
            return str(defaults["test_command"])
    return "pytest -q"


def _default_max_cycles(ctx: dict[str, Any]) -> int:
    state = ctx.get("state")
    cfg = state.cfg if state else None
    if cfg is not None:
        defaults = getattr(cfg, "loop_defaults", None) or {}
        if isinstance(defaults, dict) and defaults.get("max_cycles"):
            return int(defaults["max_cycles"])
    return 5


def _default_oracle(ctx: dict[str, Any]) -> list[str]:
    state = ctx.get("state")
    cfg = state.cfg if state else None
    project = state.project if state else None
    if project is not None:
        proj_loop = getattr(project, "loop_oracle", None)
        if proj_loop:
            return list(proj_loop)
    if cfg is not None:
        defaults = getattr(cfg, "loop_defaults", None) or {}
        if isinstance(defaults, dict) and defaults.get("oracle"):
            return list(defaults["oracle"])
    return ["tests"]


def _parse_loop_start(rest: str, ctx: dict[str, Any]) -> tuple[Optional[dict], Optional[str]]:
    """Parse /loop <goal> [flags]. Returns (options, error)."""
    try:
        tokens = shlex.split(rest)
    except ValueError as e:
        return None, str(e)

    judges: Optional[list[str]] = None
    max_cycles: Optional[int] = None
    test_cmd: Optional[str] = None
    budget: Optional[float] = None
    commit_mode: Optional[str] = None
    push_mode: Optional[str] = None
    read_budget: Optional[int] = None
    from_plan = False
    allow_dirty = False
    criteria: list[str] = []
    goal_parts: list[str] = []

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--judge" and i + 1 < len(tokens):
            judges = [j.strip() for j in tokens[i + 1].split(",") if j.strip()]
            i += 2
            continue
        if tok == "--max" and i + 1 < len(tokens):
            try:
                max_cycles = int(tokens[i + 1])
            except ValueError:
                return None, f"--max requires an integer, got {tokens[i + 1]!r}"
            i += 2
            continue
        if tok == "--test" and i + 1 < len(tokens):
            test_cmd = tokens[i + 1]
            i += 2
            continue
        if tok == "--budget" and i + 1 < len(tokens):
            try:
                budget = float(tokens[i + 1])
            except ValueError:
                return None, f"--budget requires a number, got {tokens[i + 1]!r}"
            i += 2
            continue
        if tok == "--criteria" and i + 1 < len(tokens):
            criteria.append(tokens[i + 1])
            i += 2
            continue
        if tok == "--from-plan":
            from_plan = True
            i += 1
            continue
        if tok == "--allow-dirty":
            allow_dirty = True
            i += 1
            continue
        if tok == "--commit" and i + 1 < len(tokens):
            mode = tokens[i + 1]
            if mode not in ("never", "each", "final"):
                return None, f"--commit must be never, each, or final (got {mode!r})"
            commit_mode = mode
            i += 2
            continue
        if tok == "--push" and i + 1 < len(tokens):
            mode = tokens[i + 1]
            if mode not in ("never", "each", "final"):
                return None, f"--push must be never, each, or final (got {mode!r})"
            push_mode = mode
            i += 2
            continue
        if tok == "--read-budget" and i + 1 < len(tokens):
            try:
                read_budget = int(tokens[i + 1])
            except ValueError:
                return None, f"--read-budget requires an integer, got {tokens[i + 1]!r}"
            i += 2
            continue
        if tok.startswith("--"):
            return None, f"unknown flag: {tok}"
        goal_parts.append(tok)
        i += 1

    goal = " ".join(goal_parts).strip()
    if not goal and not from_plan:
        return None, "missing goal — usage: /loop <goal> [--from-plan] [--judge tests] [--max N]"

    judges_list = judges if judges is not None else _default_oracle(ctx)
    loop_defaults = None
    state = ctx.get("state")
    if state is not None and hasattr(state.cfg, "loop_defaults"):
        loop_defaults = getattr(state.cfg, "loop_defaults", None)

    return {
        "goal": goal,
        "from_plan": from_plan,
        "allow_dirty": allow_dirty,
        "judges": judges_list,
        "max_cycles": max_cycles if max_cycles is not None else _default_max_cycles(ctx),
        "test_command": test_cmd if test_cmd is not None else _default_test_command(ctx),
        "budget_usd": budget,
        "commit_mode": commit_mode,
        "push_mode": push_mode,
        "read_budget": read_budget if read_budget is not None else 3,
        "success_criteria": criteria,
        "loop_defaults": loop_defaults if isinstance(loop_defaults, dict) else None,
    }, None


def _attach_loop(ctx: dict[str, Any], controller: LoopController) -> None:
    state = ctx.get("state")
    if state is not None:
        state.loop = controller


def _clear_conflicts(ctx: dict[str, Any]) -> None:
    agent = ctx["agent"]
    had_rail = agent.rail is not None
    had_debug = getattr(agent, "debug", None) is not None
    had_plan = agent.plan_mode
    agent.set_mode(None)
    if had_rail:
        ctx["console"].print("[dim]rail OFF (loop takes over)[/dim]")
    if had_debug:
        ctx["console"].print("[dim]debug OFF (loop takes over)[/dim]")
    if had_plan:
        ctx["console"].print("[dim]plan mode OFF (loop takes over)[/dim]")


def _dirty_tree_summary(project_root):
    """First porcelain lines of an unclean tree, '' when clean, None when unknown."""
    import subprocess
    from pathlib import Path

    from xlii.git_status import is_git_repo

    if project_root is None or not is_git_repo(Path(project_root)):
        return ""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(project_root), capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _refuse_if_dirty(console, project_root, *, allow_dirty: bool) -> bool:
    """The dirty-tree rail. A loop's builder judges — and may 'clean' — the WHOLE
    working tree: uncommitted changes ride into every judge diff as apparent
    scope violations, and on 2026-07-10 a builder cycle hard-reset staged work
    it decided was out-of-scope noise. Rails, not discipline: refuse to start
    over an unclean tree unless the operator explicitly says --allow-dirty."""
    dirty = _dirty_tree_summary(project_root)
    if dirty is None:
        console.print(
            "[red]/loop: could not verify working tree is clean[/red] — "
            "git status failed or timed out. Fix git access or use --allow-dirty "
            "only if you accept the risk."
        )
        return True
    if not dirty or allow_dirty:
        if dirty and allow_dirty:
            console.print(
                "[yellow]⚠ starting over a dirty tree (--allow-dirty)[/yellow] — "
                "uncommitted changes will appear in every judge diff and a builder "
                "cycle may revert them."
            )
        return False
    lines = dirty.splitlines()
    preview = "\n".join(f"  {ln}" for ln in lines[:5])
    more = f"\n  [dim]… {len(lines) - 5} more[/dim]" if len(lines) > 5 else ""
    console.print(
        f"[red]/loop: working tree is not clean[/red] ({len(lines)} path(s)):\n"
        f"{preview}{more}\n"
        "[dim]Commit or stash first — the loop's judges diff the whole tree, and a "
        "builder cycle may discard changes it can't explain. "
        "Override with [/dim][cyan]--allow-dirty[/cyan][dim].[/dim]"
    )
    return True


def _warn_if_not_git(console, project_root) -> None:
    """A `/loop` reviews each cycle's work via `git diff` (LLM judges, the stall
    signature). With no repo the diff is always empty, so the loop stalls on
    "zero change" no matter how much the builder writes — exactly the trap that's
    easy to fall into in a fresh, un-init'd folder. Nudge loudly, don't block:
    a tests-only loop can still run, and the user may be mid-setup."""
    from pathlib import Path

    from xlii.git_status import is_git_repo

    if project_root is not None and not is_git_repo(Path(project_root)):
        console.print(
            "[yellow]⚠ not a git repo[/yellow] — the loop's judges review changes by "
            "[cyan]git diff[/cyan], so without a repo they'll see no changes and it will "
            "stall on \"zero change\". Run [cyan]git init && git add -A && "
            "git commit -m baseline[/cyan] first."
        )


def h_loop(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    agent = state.agent if state else ctx.get("agent")
    project = state.project if state else ctx.get("project")

    parts = line.split(maxsplit=1)
    sub = parts[1].strip() if len(parts) > 1 else ""

    if not sub:
        console.print(
            "[dim]usage:[/dim] /loop <goal> [flags]  |  /loop status  |  "
            "/loop resume  |  /loop pause  |  /loop cancel"
        )
        return True

    sub_lower = sub.lower()
    if sub_lower in ("status", "?"):
        ctrl = getattr(state, "loop", None) if state else None
        if ctrl is None and project is not None:
            cfg = state.cfg if state else None
            judges_cfg = getattr(cfg, "judges", None) if cfg else None
            ctrl = LoopController.load(project.xli_dir, judges_cfg)
        if ctrl is None:
            console.print("[dim](no active loop)[/dim]")
            return True
        for ln in ctrl.status_lines():
            console.print(ln)
        return True

    if sub_lower == "resume":
        if state is None or project is None:
            console.print("[dim](no project context)[/dim]")
            return True
        cfg = state.cfg
        ctrl = LoopController.load(project.xli_dir, getattr(cfg, "judges", None))
        if ctrl is None:
            console.print("[dim](no loop to resume)[/dim]")
            return True
        ctrl.resume()
        _attach_loop(ctx, ctrl)
        console.print("[cyan][loop] resumed[/cyan]")
        ctx["_loop_rewritten"] = ctrl.resume_build_prompt()
        return False

    if sub_lower == "pause":
        ctrl = getattr(state, "loop", None) if state else None
        if ctrl is None:
            console.print("[dim](no active loop)[/dim]")
            return True
        ctrl.pause()
        console.print("[dim][loop] paused — /loop resume to continue[/dim]")
        return True

    if sub_lower in ("cancel", "off"):
        ctrl = getattr(state, "loop", None) if state else None
        if ctrl is None and state and project:
            ctrl = LoopController.load(project.xli_dir, getattr(state.cfg, "judges", None))
        if ctrl is not None:
            ctrl.cancel()
        if state is not None:
            state.loop = None
        console.print("[dim][loop] cancelled[/dim]")
        return True

    if agent.rail is not None or agent.plan_mode or getattr(agent, "debug", None) is not None:
        console.print("[yellow]/loop: rail, plan, or debug mode is active — "
                      "/rail off, /cancel, or /debug exit first[/yellow]")
        return True

    if getattr(state, "loop", None) is not None and state.loop.is_active:
        console.print("[yellow]/loop: a loop is already active — /loop status or /loop cancel[/yellow]")
        return True

    opts, err = _parse_loop_start(sub, ctx)
    if err:
        console.print(f"[red]/loop: {err}[/red]")
        return True

    goal = opts["goal"]
    if opts.get("from_plan"):
        try:
            plan_text = LoopController.load_plan_goal(project.xli_dir)
        except ValueError as e:
            console.print(f"[red]/loop: {e}[/red]")
            return True
        goal = plan_text if not goal else f"{goal}\n\n---\n\n{plan_text}"

    config_judges = getattr(state.cfg, "judges", None) if state else {}
    if state is not None and hasattr(state.cfg, "effective_judges"):
        effective = state.cfg.effective_judges()
    else:
        effective = config_judges if isinstance(config_judges, dict) else {}
    _warn_if_not_git(console, project.project_root)
    if _refuse_if_dirty(console, project.project_root,
                        allow_dirty=bool(opts.get("allow_dirty"))):
        return True
    resolved_push = resolve_push_mode(
        opts.get("push_mode"),
        opts["judges"],
        effective if isinstance(effective, dict) else config_judges,
        loop_defaults=opts.get("loop_defaults"),
    )
    resolved_commit = resolve_commit_mode(
        opts.get("commit_mode"),
        resolved_push,
        loop_defaults=opts.get("loop_defaults"),
    )
    try:
        ctrl = LoopController.start(
            xli_dir=project.xli_dir,
            goal=goal,
            judges=opts["judges"],
            max_cycles=opts["max_cycles"],
            test_command=opts["test_command"],
            success_criteria=opts.get("success_criteria"),
            budget_usd=opts.get("budget_usd"),
            commit_mode=resolved_commit,
            push_mode=resolved_push,
            read_budget=opts.get("read_budget", 3),
            config_judges=effective if isinstance(effective, dict) else config_judges,
            project_root=project.project_root,
        )
    except ValueError as e:
        console.print(f"[red]/loop: {e}[/red]")
        return True

    _clear_conflicts(ctx)
    _attach_loop(ctx, ctrl)
    profiles = resolve_judges(
        opts["judges"],
        effective if state and isinstance(effective, dict) else config_judges,
    )
    if not any(p.kind == "shell" for p in profiles):
        console.print(
            "[yellow]heads-up:[/yellow] no shell judge in stack — LLM judges run without a test gate"
        )
    loop_start_push_warnings(
        console=console,
        profiles=profiles,
        push_mode=ctrl.state.push_mode,
        project_root=project.project_root,
        max_cycles=ctrl.state.max_cycles,
    )
    console.print(
        f"[cyan][loop] started[/cyan] · judges: {', '.join(ctrl.state.judges)} "
        f"· max {ctrl.state.max_cycles} cycles"
    )
    console.print(
        f"[dim][loop] cycle {ctrl.state.cycle}/{ctrl.state.max_cycles} · phase: build[/dim]"
    )
    ctx["_loop_rewritten"] = ctrl.initial_build_prompt()
    return False


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="loop",
            handler=h_loop,
            description="Autonomous build→test→fix loop (walk away to green)",
            usage="/loop <goal> [--from-plan] [--judge tests,xai-verify] [--max N] "
            "[--test CMD] [--budget USD] [--commit never|each|final] [--read-budget N] "
            "[--allow-dirty]",
            category="general",
            repls=["code"],
        )
    )
