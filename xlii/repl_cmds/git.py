"""/gitpain — the review-before-run source-control mutator behind the ``git://`` Gitpanel doorway.

The write half of ``git://`` (browse/diff live in the :class:`~xlii.panes.git.GitPane` and
``xlii cat``): ``status · stage · unstage · discard · commit`` (plus ``stage-all`` / ``unstage-all``).
The Gitpanel's actions **seed these into the command line** (``/gitpain stage <file>``, ``/gitpain commit ``)
so nothing mutates the repo until the user presses Enter — the command line doubles as the commit
message box. A thin shell over the local ``git`` binary via :func:`xlii.loop_bundle.git_cmd`; the
repo is the one the session's cwd is in (:func:`xlii.active_session.cwd_of`). Forge-agnostic, no
network — the read side is :mod:`xlii.git_status`.

``/git`` remains a deprecated alias (still registered, still works).
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command

_PRIMARY = "gitpain"
_LEGACY = "git"


def _repo(ctx: dict[str, Any]) -> "Optional[Path]":
    from xlii.active_session import cwd_of
    from xlii.git_status import find_repo_root

    state = ctx.get("state")
    start = cwd_of(state) or Path.cwd()
    return find_repo_root(start)


def _prefill(ctx: dict[str, Any], text: str) -> None:
    from xlii.repl_state import queue_pending_input

    queue_pending_input(ctx.get("state"), text, replace=True)


def _run(console, repo: Path, args: "list[str]", *, ok: str, timeout: int = 30) -> bool:
    """Run one git command, print its output and the ``ok`` line; True on success.

    Success is ``git_cmd``'s notion (exit 0/1 — see :mod:`xlii.git_status`), so a
    caller that needs proof of a state change must verify it separately (the
    commit path checks HEAD actually moved before journaling)."""
    from xlii.loop_bundle import git_cmd

    out, err = git_cmd(repo, args, timeout=timeout)
    if err:
        console.print(f"[red]git {args[0]}:[/red] {err}")
        return False
    body = (out or "").strip()
    if body:
        console.print(body)
    console.print(f"[green]✓[/green] {ok}")
    return True


def _head(repo: Path) -> str:
    """The full HEAD sha, or "" (unborn HEAD / not a repo)."""
    from xlii.loop_bundle import git_cmd

    out, err = git_cmd(repo, ["rev-parse", "HEAD"])
    return "" if err else out.strip()


def _branches(repo: Path) -> "set[str]":
    from xlii.loop_bundle import git_cmd

    out, err = git_cmd(repo, ["branch", "--format=%(refname:short)"])
    if err:
        return set()
    return {line.strip() for line in out.splitlines() if line.strip()}


def _journal(ctx: dict[str, Any]):
    state = ctx.get("state")
    return getattr(state, "journal", None) if state is not None else None


def _optional_goal(ctx: dict[str, Any]) -> str:
    state = ctx.get("state")
    if state is None:
        return ""
    project = getattr(state, "project", None)
    xli_dir = getattr(project, "xli_dir", None) if project is not None else None
    if xli_dir is not None:
        try:
            from xlii.loop import LoopController

            ctrl = LoopController.load(xli_dir)
            if ctrl is not None and ctrl.state.goal.strip():
                return ctrl.state.goal.strip()
        except Exception:  # noqa: BLE001
            pass
        try:
            from xlii.loop import LoopController

            return LoopController.load_plan_goal(xli_dir).strip()
        except Exception:  # noqa: BLE001
            pass
    return ""


def _journal_context(journal) -> str:
    if journal is None:
        return ""
    parts: list[str] = []
    summary = journal.read_summary()
    if summary:
        parts.append("## Rolling activity summary\n\n" + summary)
    recent = journal._recent_local_entries(limit=8)  # noqa: SLF001 — read-only project journal
    if recent:
        parts.append("## Recent journal entries\n\n" + recent)
    return "\n\n---\n\n".join(parts)


_GEN_SYSTEM = (
    "You write git commit messages. Given a diff, output ONLY a single-line commit subject in the "
    "Conventional Commits style (e.g. 'feat(auth): add token refresh'), imperative mood, at most 72 "
    "characters. No body, no quotes, no backticks, no trailing period, no preamble."
)

_STASH_GEN_SYSTEM = (
    "You write one-line git stash messages. Given recent project activity and/or a diff, output ONLY "
    "a short stash label (at most 72 characters) that helps the developer recognize this stash later. "
    "No quotes, no backticks, no trailing period, no preamble."
)


def _clean_commit_subject(raw: str) -> str:
    """The model's reply → a clean single-line subject: first non-blank line, quotes/backticks
    stripped (models like to wrap the message)."""
    if not raw or not raw.strip():
        return ""
    line = next((ln.strip() for ln in raw.strip().splitlines() if ln.strip()), "")
    for ch in ("`", '"', "'"):
        line = line.strip(ch).strip()
    return line


def _staged_diff(repo: Path) -> tuple[str, str, Optional[str]]:
    from xlii.loop_bundle import git_cmd

    diff, err = git_cmd(repo, ["diff", "--cached"])
    scope = "staged"
    if err is None and not diff.strip():
        diff, err = git_cmd(repo, ["diff"])
        scope = "unstaged"
    if err:
        return "", scope, err
    return diff, scope, None


def _draft_message(
    console,
    ctx: dict,
    repo: Path,
    *,
    cmd_name: str,
    journal_aware: bool,
    seed_sub: str,
) -> None:
    """Draft a commit/stash message with the session model, then seed review on the command line."""
    diff, scope, err = _staged_diff(repo)
    if err:
        console.print(f"[red]git diff:[/red] {err}")
        return
    if not diff.strip() and not journal_aware:
        console.print("[dim]nothing to describe — stage or edit some files first[/dim]")
        return
    journal = _journal(ctx)
    if journal_aware and journal is None:
        console.print("[dim]the journal is available in the code REPL[/dim]")
        return
    jctx = _journal_context(journal) if journal_aware else ""
    if journal_aware and not diff.strip() and not jctx:
        console.print("[dim]nothing to draft from — stage changes or turn the code journal on[/dim]")
        return
    from xlii.wiki_author import session_completer

    complete = session_completer(ctx.get("state"))
    if complete is None:
        console.print("[red]no model available[/red] [dim](can't reach a chat client this session)[/dim]")
        return
    goal = _optional_goal(ctx) if journal_aware else ""
    clipped = diff if len(diff) <= 16000 else diff[:16000] + "\n…(diff truncated)…\n"
    user_parts = []
    if jctx:
        user_parts.append(jctx)
    if goal:
        user_parts.append(f"## Active goal\n\n{goal}")
    if diff.strip():
        user_parts.append(f"## {scope} diff\n\n{clipped}")
    system = _STASH_GEN_SYSTEM if seed_sub.startswith("stash") else _GEN_SYSTEM
    label = "stash message" if seed_sub.startswith("stash") else "commit message"
    try:
        with console.status(f"[cyan]generating {label}…[/cyan]"):
            raw = complete([
                {"role": "system", "content": system},
                {"role": "user", "content": f"Write a {label} for this work:\n\n" + "\n\n---\n\n".join(user_parts)},
            ])
    except Exception as e:  # noqa: BLE001 — never kill the REPL over a model call
        console.print(f"[red]generate failed: {type(e).__name__}: {e}[/red]")
        return
    message = _clean_commit_subject(raw)
    if not message:
        console.print("[yellow]the model returned an empty message[/yellow]")
        return
    if seed_sub == "stash":
        _prefill(ctx, f"/{cmd_name} stash -m {shlex.quote(message)}")
    else:
        _prefill(ctx, f"/{cmd_name} commit {message}")
    console.print(f"[green]✓[/green] drafted [dim]({scope or 'journal'})[/dim]: [bold]{message}[/bold]")
    console.print("[dim]review it on the command line, then Enter to run[/dim]")


def _generate(console, ctx: dict, repo: Path, *, cmd_name: str) -> None:
    _draft_message(console, ctx, repo, cmd_name=cmd_name, journal_aware=False, seed_sub="commit")


def _commit_journal(console, ctx: dict, repo: Path, *, cmd_name: str) -> None:
    _draft_message(console, ctx, repo, cmd_name=cmd_name, journal_aware=True, seed_sub="commit")


def _stash_journal(console, ctx: dict, repo: Path, *, cmd_name: str) -> None:
    _draft_message(console, ctx, repo, cmd_name=cmd_name, journal_aware=True, seed_sub="stash")


def _maybe_journal_commit(ctx: dict, repo: Path, subject: str) -> None:
    """Write the post-commit journal line (``committed <hash> — <subject>``).

    Only called after a *verified* commit — the caller checks both the run
    result and that HEAD moved, so a failed commit never journals the previous
    commit's hash as if it were new."""
    journal = _journal(ctx)
    if journal is None or not journal.is_recording():
        return
    from xlii.loop_bundle import git_cmd

    out, err = git_cmd(repo, ["rev-parse", "--short", "HEAD"])
    if err or not out.strip():
        return
    from types import SimpleNamespace

    journal.observe_turn(
        f"committed {out.strip()} — {subject}",
        [],
        SimpleNamespace(tool_calls=0),
        cwd=str(repo),
    )


def _status(console, repo: Path, *, cmd_name: str) -> None:
    from xlii.git_status import ahead_behind, branch_name, porcelain_entries, stash_entries

    branch = branch_name(repo) or "?"
    ab = ahead_behind(repo)
    tag = ""
    if ab:
        ahead, behind = ab
        tag = (f" ↑{ahead}" if ahead else "") + (f" ↓{behind}" if behind else "")
    stashes = stash_entries(repo)
    if stashes:
        tag += f"  ⚑{len(stashes)}"
    console.print(f"[bold]Gitpanel[/bold] [cyan]{branch}[/cyan]{tag}")
    entries = porcelain_entries(repo)
    if not entries:
        console.print("[dim](working tree clean)[/dim]")
    else:
        for x, y, path in entries:
            if x == "?" and y == "?":
                console.print(f"  [yellow]?[/yellow]  {path}  [dim]untracked[/dim]")
            else:
                staged = f"[green]{x}[/green]" if x not in (" ", "?") else " "
                work = f"[yellow]{y}[/yellow]" if y not in (" ", "?") else " "
                console.print(f"  {staged}{work}  {path}")
    if stashes:
        console.print("[dim]stashes:[/dim]")
        for s in stashes:
            console.print(f"  [cyan]{s.ref}[/cyan]  {s.message}")
    console.print(
        f"[dim]stage: [/dim][cyan]/{cmd_name} stage <path>[/cyan][dim] · commit: [/dim]"
        f"[cyan]/{cmd_name} commit <msg>[/cyan][dim] · full git: type [/dim][cyan]git[/cyan][dim] in the shell[/dim]"
    )


def _parse_stash_flags(rest: list[str]) -> tuple[bool, str, list[str]]:
    """Split stash tokens into ``(include_untracked, -m message, positional tokens)``."""
    include_untracked = False
    message = ""
    positional: list[str] = []
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in ("-u", "--include-untracked"):
            include_untracked = True
            i += 1
            continue
        if tok == "-m":
            if i + 1 < len(rest):
                message = rest[i + 1]
                i += 2
            else:
                i += 1  # dangling -m: no value — never a positional message
            continue
        positional.append(tok)
        i += 1
    return include_untracked, message, positional


def _stash_push(console, repo: Path, rest: list[str], *, legacy: bool) -> None:
    """``stash push``: the message is positional (``stash <words>``) or ``-m <msg>``
    (``-m`` wins when both are given); required on the /gitpain path, optional on
    the legacy /git path."""
    include_untracked, message, positional = _parse_stash_flags(rest)
    if not message and positional:
        message = " ".join(positional)
    if not legacy and not message:
        console.print("[dim]usage: [/dim][cyan]/gitpain stash <message>[/cyan]"
                      "[dim] (or [/dim][cyan]/gitpain stash journal[/cyan][dim] to draft one)[/dim]")
        return
    args = ["stash", "push"]
    if include_untracked:
        args.append("-u")
    if message:
        args += ["-m", message]
    _run(console, repo, args, ok="stashed the working-tree changes")


def _sweep(console, ctx: dict, repo: Path, *, cmd_name: str) -> None:
    from xlii.git_status import sweep_cleanup_commands, sweep_report

    report = sweep_report(repo)
    if not report.merged_local and not report.stale_worktrees and not report.merged_remote:
        console.print("[dim](nothing to sweep — no merged branches or stale worktrees)[/dim]")
        return
    if report.merged_local:
        console.print("[bold]merged local branches[/bold]")
        for name in report.merged_local:
            console.print(f"  {name}")
    if report.stale_worktrees:
        console.print("[bold]stale worktrees[/bold]")
        for wt in report.stale_worktrees:
            console.print(f"  {wt.path}  [dim]({wt.branch})[/dim]")
    if report.merged_remote:
        console.print("[bold]merged remote branches[/bold]")
        for name in report.merged_remote:
            console.print(f"  {name}")
    cmds = sweep_cleanup_commands(report)
    if cmds:
        _prefill(ctx, " && ".join(cmds))
        console.print("[dim]cleanup commands seeded on the command line — review before Enter[/dim]")


def _handler(line: str, ctx: dict[str, Any], *, legacy: bool) -> bool:
    console = ctx.get("console")
    repo = _repo(ctx)
    if repo is None:
        console.print("[dim]not inside a git repository[/dim]")
        return True
    from xlii.loop_bundle import git_cmd

    cmd_name = _LEGACY if legacy else _PRIMARY
    try:
        parts = shlex.split(line)
    except ValueError:
        parts = line.split()
    tokens = parts[1:]
    sub = tokens[0] if tokens else "status"
    rest = tokens[1:]

    if sub in ("status", "st", "tree"):
        _status(console, repo, cmd_name=cmd_name)
    elif sub in ("stage", "add"):
        if rest:
            _run(console, repo, ["add", "--", *rest], ok=f"staged {', '.join(rest)}")
        else:
            console.print(f"[dim]usage: [/dim][cyan]/{cmd_name} stage <path>…[/cyan]"
                          f"[dim] (or /{cmd_name} stage-all)[/dim]")
    elif sub in ("stage-all", "add-all"):
        from xlii.git_status import changed_paths

        paths = changed_paths(repo)
        if not paths:
            console.print("[dim]nothing to stage[/dim]")
        else:
            _run(console, repo, ["add", "--", *paths], ok=f"staged {len(paths)} path(s)")
    elif sub == "unstage":
        if rest:
            _run(console, repo, ["restore", "--staged", "--", *rest], ok=f"unstaged {', '.join(rest)}")
        else:
            console.print(f"[dim]usage: [/dim][cyan]/{cmd_name} unstage <path>…[/cyan]"
                          f"[dim] (or /{cmd_name} unstage-all)[/dim]")
    elif sub == "unstage-all":
        _run(console, repo, ["reset", "-q", "HEAD"], ok="unstaged all changes")
    elif sub == "discard":
        if rest:
            _run(console, repo, ["restore", "--", *rest], ok=f"discarded changes to {', '.join(rest)}")
        else:
            console.print(f"[dim]usage: [/dim][cyan]/{cmd_name} discard <path>…[/cyan][dim] (drops unstaged edits)[/dim]")
    elif sub == "commit":
        if rest and rest[0] == "journal":
            _commit_journal(console, ctx, repo, cmd_name=cmd_name)
        elif rest and rest[0] == "summary":
            _generate(console, ctx, repo, cmd_name=cmd_name)
        else:
            message = " ".join(rest).strip()
            if not message:
                console.print(f"[dim]usage: [/dim][cyan]/{cmd_name} commit <message>[/cyan][dim] — commits the staged set "
                              f"(or [/dim][cyan]/{cmd_name} commit journal[/cyan][dim] / "
                              f"[cyan]/{cmd_name} commit summary[/cyan][dim] to draft one)[/dim]")
            else:
                head_before = _head(repo)
                committed = _run(console, repo, ["commit", "-m", message], ok="committed")
                # Journal only a verified commit: run reported success AND HEAD moved
                # (git_cmd treats exit 1 — e.g. nothing staged — as success, so the
                # HEAD check is load-bearing, not belt-and-braces).
                if committed and (head_after := _head(repo)) and head_after != head_before:
                    _maybe_journal_commit(ctx, repo, message)
    elif sub in ("generate", "gen"):
        _generate(console, ctx, repo, cmd_name=cmd_name)
    elif sub == "push":
        _run(console, repo, ["push"], ok="pushed", timeout=120)
    elif sub == "pull":
        _run(console, repo, ["pull"], ok="pulled", timeout=120)
    elif sub == "sync":
        out, err = git_cmd(repo, ["pull"], timeout=120)
        if err:
            console.print(f"[red]git pull:[/red] {err}")
        else:
            if out.strip():
                console.print(out.strip())
            _run(console, repo, ["push"], ok="synced", timeout=120)
    elif sub == "branch":
        name = " ".join(rest).strip()
        if not name:
            _run(console, repo, ["branch"], ok="branches")
        elif name in _branches(repo):
            _run(console, repo, ["switch", name], ok=f"switched to {name}")
        else:
            _run(console, repo, ["switch", "-c", name], ok=f"created and switched to {name}")
    elif sub == "stash":
        if rest and rest[0] in ("-m", "-u", "--include-untracked"):
            _stash_push(console, repo, rest, legacy=legacy)
        elif not rest:
            if legacy:
                _run(console, repo, ["stash", "push"], ok="stashed the working-tree changes")
            else:
                console.print("[dim]usage: [/dim][cyan]/gitpain stash <message>[/cyan]"
                              "[dim] (or [/dim][cyan]/gitpain stash journal[/cyan][dim] to draft one)[/dim]")
        else:
            op = rest[0]
            tail = rest[1:]
            if op == "journal":
                _stash_journal(console, ctx, repo, cmd_name=cmd_name)
            elif op == "list":
                from xlii.git_status import stash_entries

                rows = stash_entries(repo)
                if not rows:
                    console.print("[dim](no stashes)[/dim]")
                else:
                    for s in rows:
                        console.print(f"{s.ref}: {s.message}")
            elif op in ("pop", "apply", "drop"):
                if not legacy and not tail:
                    _prefill(ctx, f"/{cmd_name} stash {op} 0")
                    console.print(f"[dim]review [/dim][cyan]/{cmd_name} stash {op} 0[/cyan][dim] on the command line[/dim]")
                else:
                    git_args = ["stash", op]
                    if tail:
                        git_args.append(f"stash@{{{tail[0]}}}")
                    ok = {"pop": "popped stash", "apply": "applied stash", "drop": "dropped stash"}[op]
                    _run(console, repo, git_args, ok=ok)
            elif op == "push":
                _stash_push(console, repo, tail, legacy=legacy)
            else:
                # No reserved verb, no flag: the rest of the line IS the stash
                # message (`/gitpain stash pause: auth race`).
                _stash_push(console, repo, rest, legacy=legacy)
    elif sub == "sweep":
        _sweep(console, ctx, repo, cmd_name=cmd_name)
    else:
        console.print(f"[dim]usage:[/dim] [cyan]/{cmd_name}[/cyan] [dim](status|tree) ·[/dim] "
                      "[cyan]stage|unstage|discard <path>…[/cyan][dim] ·[/dim] "
                      "[cyan]stage-all|unstage-all[/cyan][dim] ·[/dim] "
                      "[cyan]commit <message>|commit journal|commit summary[/cyan][dim] ·[/dim] "
                      "[cyan]generate[/cyan][dim] ·[/dim] "
                      "[cyan]push|pull|sync[/cyan][dim] ·[/dim] "
                      "[cyan]branch [name][/cyan][dim] ·[/dim] "
                      "[cyan]stash <msg> [-u]|stash journal|stash list|stash pop|apply|drop[/cyan][dim] ·[/dim] "
                      "[cyan]sweep[/cyan]")
    return True


def _handler_gitpain(line: str, ctx: dict[str, Any]) -> bool:
    return _handler(line, ctx, legacy=False)


def _handler_git(line: str, ctx: dict[str, Any]) -> bool:
    return _handler(line, ctx, legacy=True)


_GITPAIN_USAGE = (
    "/gitpain [status|tree] | stage|unstage|discard <path>… | stage-all|unstage-all | "
    "commit <msg>|commit journal|commit summary | generate | push|pull|sync | branch [name] | "
    "stash <msg>|-m <msg> [-u]|stash journal|stash list|stash pop|apply|drop [n] | sweep"
)

_GITPAIN_DESC = (
    "Gitpanel source control: status, stage/unstage/discard, commit (journal-aware drafts), "
    "push/pull/sync, branch, stash-with-message, sweep — the git:// doorway's write side. "
    "For full git porcelain, type git in the shell."
)

_GITPAIN_REGISTERED = False
_GIT_REGISTERED = False


def register_gitpain() -> None:
    """Register ``/gitpain`` (primary). Idempotent."""
    global _GITPAIN_REGISTERED
    if _GITPAIN_REGISTERED:
        return
    register_repl_command(
        REPLCommand(
            name=_PRIMARY,
            handler=_handler_gitpain,
            description=_GITPAIN_DESC,
            usage=_GITPAIN_USAGE,
            category="project",
        )
    )
    _GITPAIN_REGISTERED = True


def register() -> None:
    """Register ``/git`` (deprecated alias). Idempotent."""
    global _GIT_REGISTERED
    if _GIT_REGISTERED:
        return
    register_repl_command(
        REPLCommand(
            name=_LEGACY,
            handler=_handler_git,
            description=f"Deprecated — use /{_PRIMARY}. {_GITPAIN_DESC}",
            usage=_GITPAIN_USAGE.replace("/gitpain", "/git"),
            category="project",
        )
    )
    _GIT_REGISTERED = True
