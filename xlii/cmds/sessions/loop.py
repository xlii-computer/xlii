"""`xlii loop` headless autonomous loop command."""

from __future__ import annotations

import argparse
import sys

from xlii.agent import Agent, SessionState
from xlii.client import MissingCredentials
from xlii.config import GlobalConfig, ProjectConfig
from xlii.pool import ClientPool

from .resolve import _resolve_project_target


def _drain_inbox(args, project, cfg, judges_cfg, ui) -> int:
    """B2 automation-lite: run every `.xlii/inbox/*.md` task in sorted order,
    each as its own headless loop, archiving the file to `inbox/done/` after.

    Exit code is 0 only if every drained task reached LOOP_PASS, so a cron job
    can branch on success. A malformed file is reported, archived (so it doesn't
    jam the queue), and counted as a failure."""
    from xlii.inbox import inbox_drain_lock

    with inbox_drain_lock(project.xli_dir) as got:
        if not got:
            ui.print("[dim]inbox: drain already running[/dim]")
            return 0
        return _drain_inbox_locked(args, project, cfg, judges_cfg, ui)


def _drain_inbox_locked(args, project, cfg, judges_cfg, ui) -> int:
    from xlii.inbox import InboxDefaults, archive_inbox, inbox_hold_reason, list_inbox, parse_inbox_item
    from xlii.loop import LoopController, run_loop_cli

    items = list_inbox(project.xli_dir)
    if not items:
        ui.print("[dim]inbox empty — nothing to drain[/dim]")
        return 0

    defaults = InboxDefaults(
        judge=getattr(args, "judge", "tests"),
        max_cycles=getattr(args, "max_cycles", 5),
        test_command=getattr(args, "test", "pytest -q"),
        budget_usd=getattr(args, "budget", None),
        commit_mode=getattr(args, "commit", "never") or "never",
    )
    try:
        pool = ClientPool.from_config(cfg)
    except MissingCredentials as e:
        print(f"loop: {e}", file=sys.stderr)
        return 1

    results: list[tuple[str, str]] = []
    for path in items:
        try:
            item = parse_inbox_item(path, defaults)
        except (ValueError, OSError) as e:
            ui.print(f"[yellow]inbox: skipping — {e}[/yellow]")
            archive_inbox(path, project.xli_dir)
            results.append((path.name, "SKIP"))
            continue

        hold = inbox_hold_reason(item, project.project_root)
        if hold:
            ui.print(f"[yellow]inbox: holding {path.name} — {hold}[/yellow]")
            results.append((path.name, "HOLD"))
            continue

        ui.print(f"[cyan]inbox → {path.name}[/cyan] [dim]{item.goal[:70]}[/dim]")
        if getattr(item, "credibility", 0) > 0:
            try:
                from xlii.text_hygiene import note_ingress

                note = note_ingress(item.goal, source=f"inbox:{path.name}")
                if note:
                    ui.print(f"[yellow]{note}[/yellow]")
            except Exception:
                ui.print(
                    f"[yellow]take note · credibility {item.credibility} "
                    f"· inbox:{path.name}[/yellow]"
                )
        # Fresh agent per task so unrelated goals don't share conversation history.
        agent = Agent(pool=pool, project=project, cfg=cfg, console=ui,
                      session=SessionState.from_flat(yolo=getattr(args, "yolo", False)))
        try:
            ctrl = LoopController.start(
                xli_dir=project.xli_dir,
                goal=item.goal,
                judges=item.judges,
                max_cycles=item.max_cycles,
                test_command=item.test_command,
                budget_usd=item.budget_usd,
                commit_mode=item.commit_mode,
                # Drain never pushes. pr-watch P1 flushes the outbox from the
                # watcher after a green cycle — a `push:` inbox key would let
                # serve-inbox grant itself push authority.
                push_mode="never",
                read_budget=getattr(args, "read_budget", 3),
                config_judges=judges_cfg,
                project_root=project.project_root,
            )
        except ValueError as e:
            ui.print(f"[yellow]inbox: {path.name}: {e}[/yellow]")
            archive_inbox(path, project.xli_dir)
            results.append((path.name, "ERROR"))
            continue

        outcome = run_loop_cli(
            controller=ctrl,
            project_root=project.project_root,
            run_turn=lambda p, _a=agent: _a.run_turn(p),
            console=ui,
            agent=agent,
            config_judges=judges_cfg,
        )
        archive_inbox(path, project.xli_dir)
        results.append((path.name, outcome))
        ui.print(f"[dim]inbox: {path.name} → {outcome} (archived)[/dim]")

    ui.print("inbox drained:")
    for name, outcome in results:
        ui.print(f"  {name}: {outcome}")
    processed = [(n, o) for n, o in results if o != "HOLD"]
    if not processed:
        return 0
    return 0 if all(o == "LOOP_PASS" for _, o in processed) else 1


def cmd_loop(args: argparse.Namespace) -> int:
    """Headless autonomous loop — runs to completion."""
    from rich.console import Console as _Console

    from xlii.loop import LoopController, loop_start_push_warnings, resolve_commit_mode, resolve_push_mode, run_loop_cli
    from xlii.loop_judge import resolve_judges

    target = _resolve_project_target(getattr(args, "workspace", None))
    if target is None:
        print("loop: could not resolve workspace", file=sys.stderr)
        return 1
    project = ProjectConfig.load(target.resolve())
    if not project:
        print(f"loop: not an xlii project: {target}", file=sys.stderr)
        return 1

    cfg = GlobalConfig.load()
    judges_cfg = cfg.effective_judges()
    ui = _Console(stderr=True)

    if getattr(args, "status", False):
        ctrl = LoopController.load(project.xli_dir, judges_cfg)
        if ctrl is None:
            print("(no active loop)")
            return 0
        for ln in ctrl.status_lines():
            ui.print(ln)
        return 0

    if getattr(args, "drain_inbox", False):
        return _drain_inbox(args, project, cfg, judges_cfg, ui)

    try:
        pool = ClientPool.from_config(cfg)
    except MissingCredentials as e:
        print(f"loop: {e}", file=sys.stderr)
        return 1

    agent = Agent(pool=pool, project=project, cfg=cfg, console=ui,
                  session=SessionState.from_flat(yolo=getattr(args, "yolo", False)))

    if getattr(args, "resume", False):
        ctrl = LoopController.load(project.xli_dir, judges_cfg)
        if ctrl is None:
            print("loop: no loop to resume", file=sys.stderr)
            return 1
        if ctrl.state.swarm:
            from xlii.swarm import WorktreeManager, _project_slug

            mgr = WorktreeManager(
                repo_root=project.project_root,
                loop_id=ctrl.state.loop_id,
                project_slug=_project_slug(project.project_root),
            )
            mgr.prune_dangling()
        ctrl.resume()
        outcome = run_loop_cli(
            controller=ctrl,
            project_root=project.project_root,
            run_turn=lambda p: agent.run_turn(p),
            console=ui,
            agent=agent,
            config_judges=judges_cfg,
        )
        print(outcome)
        return 0 if outcome == "LOOP_PASS" else 1

    goal = getattr(args, "goal", None)
    from_plan = getattr(args, "from_plan", False)
    if not goal and not from_plan:
        print("loop: missing goal (or use --from-plan, --status, or --resume)", file=sys.stderr)
        return 1

    if from_plan:
        try:
            plan_text = LoopController.load_plan_goal(project.xli_dir)
        except ValueError as e:
            print(f"loop: {e}", file=sys.stderr)
            return 1
        goal = plan_text if not goal else f"{goal}\n\n---\n\n{plan_text}"

    swarm_size = max(1, getattr(args, "swarm", 1) or 1)
    swarm_size = min(swarm_size, cfg.max_parallel_workers)
    merge_mode = getattr(args, "merge", "auto") or "auto"
    merge_judge = getattr(args, "merge_judge", None)
    if not merge_judge:
        merge_judge = (cfg.loop_defaults or {}).get("merge_judge", "merge")

    judges = [j.strip() for j in args.judge.split(",") if j.strip()]
    resolved_push = resolve_push_mode(
        getattr(args, "push", None) or None,
        judges,
        judges_cfg,
        loop_defaults=cfg.loop_defaults if isinstance(cfg.loop_defaults, dict) else None,
    )
    resolved_commit = resolve_commit_mode(
        getattr(args, "commit", None) or None,
        resolved_push,
        loop_defaults=cfg.loop_defaults if isinstance(cfg.loop_defaults, dict) else None,
    )
    try:
        ctrl = LoopController.start(
            xli_dir=project.xli_dir,
            goal=goal,
            judges=judges,
            max_cycles=args.max_cycles,
            test_command=args.test,
            budget_usd=args.budget,
            commit_mode=resolved_commit,
            push_mode=resolved_push,
            read_budget=getattr(args, "read_budget", 3),
            config_judges=judges_cfg,
            project_root=project.project_root,
            swarm_size=swarm_size,
            merge_mode=merge_mode,
            merge_judge=merge_judge,
        )
    except ValueError as e:
        print(f"loop: {e}", file=sys.stderr)
        return 1

    profiles = resolve_judges(judges, judges_cfg)
    loop_start_push_warnings(
        console=ui,
        profiles=profiles,
        push_mode=resolved_push,
        project_root=project.project_root,
        max_cycles=args.max_cycles,
    )

    outcome = run_loop_cli(
        controller=ctrl,
        project_root=project.project_root,
        run_turn=lambda p: agent.run_turn(p),
        console=ui,
        agent=agent,
        config_judges=judges_cfg,
    )
    print(outcome)
    return 0 if outcome == "LOOP_PASS" else 1
