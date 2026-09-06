"""``/nfo`` — generate an AI project-status card that becomes the startup splash.

Gathers a grounded project snapshot (git state, the rolling activity journal, the stack fingerprint,
the README head), asks the session model for a terse ".nfo" status card, and writes it where the
nfo-first splash resolver (``xlii.tui.splash.resolve_splash_nfo``) will render it on next launch.

Default target is the per-project ``.xlii/splash.nfo`` (highest precedence, gitignored, churns freely);
``--global`` / ``--root`` retarget the other two splash locations. No model or no nfo → the stock xlii
wordmark still shows, unchanged. Mirrors ``/git generate``'s one-shot completion via
``wiki_author.session_completer``.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command

_USAGE = '/nfo [--print] [--show] [--clear] [--full|--brief] [--focus "topic"] [--global|--root]'

# Stay comfortably under the splash reader's 200-line cap (xlii.tui.splash._NFO_MAX_LINES).
_MAX_LINES = 180

_CARD_SYSTEM = (
    'You write a terse project-status card — an old-school ".nfo" — shown centered on an otherwise\n'
    "empty terminal when the developer opens the project. It must be scannable at a glance, not a report.\n"
    "\n"
    "Rules:\n"
    "- Plain text only: no markdown headings (#), no **bold**, no code fences, no emoji.\n"
    "- At most 20 lines. Keep every line under 60 characters — lines are NOT wrapped (they get cropped).\n"
    '- Line 1 is a title: "<project> · <branch>". Line 2 is a rule of dashes.\n'
    "- Then a few short labelled lines — e.g. Now:, Recent:, Next:, Git: — one tight fact each. Use real\n"
    "  file paths, commands, and decisions from the snapshot; no hype, no generic advice, no invention.\n"
    "- Omit any section the snapshot doesn't support. Output ONLY the card — no preamble or sign-off."
)
_FULL_EXTRA = (
    "Override: you may use up to 32 lines and add a one- or two-line intro sentence under the rule, "
    "before the labelled lines. Still terse and grounded in the snapshot."
)


def _parse(line: str) -> tuple[dict, Optional[str]]:
    """Flag parse for /nfo. Returns (opts, error-or-None)."""
    try:
        toks = shlex.split(line)[1:]  # drop the command word itself
    except ValueError as e:
        return {}, f"parse error: {e}"
    opts = {"print": False, "show": False, "clear": False, "full": False,
            "scope": "project", "focus": None}
    i = 0
    while i < len(toks):
        t = toks[i].lower()
        if t in ("--print", "-n", "--dry-run"):
            opts["print"] = True
        elif t == "--show":
            opts["show"] = True
        elif t in ("--clear", "--reset"):
            opts["clear"] = True
        elif t == "--full":
            opts["full"] = True
        elif t == "--brief":
            opts["full"] = False
        elif t == "--global":
            opts["scope"] = "global"
        elif t == "--root":
            opts["scope"] = "root"
        elif t == "--focus":
            if i + 1 >= len(toks):
                return {}, '--focus needs a topic (e.g. --focus "the auth refactor")'
            opts["focus"] = toks[i + 1]
            i += 1
        else:
            return {}, f"unknown flag: {toks[i]!r}"
        i += 1
    return opts, None


def _target_path(project: Any, scope: str) -> Optional[Path]:
    """The .nfo file /nfo writes, by scope. project → .xlii/splash.nfo; global → the config dir;
    root → the committable repo-root xlii.nfo."""
    root = getattr(project, "project_root", None)
    xli = getattr(project, "xli_dir", None)
    if scope == "global":
        from xlii.config import global_config_dir
        return global_config_dir() / "splash.nfo"
    if scope == "root":
        return Path(root) / "xlii.nfo" if root else None
    return Path(xli) / "splash.nfo" if xli else None


def _gather(state: Any, project: Any) -> str:
    """A grounded project snapshot for the model — git state, rolling journal, stack, README head."""
    from xlii.git_status import git_snapshot
    from xlii.loop_bundle import git_cmd

    root = Path(getattr(project, "project_root", ".") or ".")
    out: list[str] = [f"project name: {getattr(project, 'name', '?')}"]

    snap = git_snapshot(root)
    if snap.is_repo:
        out.append(f"branch: {snap.branch}")
        if snap.last_commit:
            out.append(f"last commit: {snap.last_commit}")
        if snap.ahead_behind:
            out.append(f"upstream ahead/behind: {snap.ahead_behind[0]}/{snap.ahead_behind[1]}")
        out.append(f"working tree: {snap.changed_count} changed file(s)")
        for p, stt in list(snap.status.items())[:25]:
            out.append(f"  {stt} {p}")
        log, lerr = git_cmd(root, ["log", "-10", "--oneline"])
        if not lerr and log.strip():
            out.append("recent commits:")
            out.extend("  " + ln for ln in log.strip().splitlines()[:10])

    try:
        from xlii.project_fingerprint import (
            detect_project_fingerprint,
            load_project_profile,
            summary_line,
        )
        prof = load_project_profile(root) or detect_project_fingerprint(root)
        out.append("stack: " + summary_line(prof))
    except Exception:  # noqa: BLE001 — fingerprint is best-effort context
        pass

    journal = getattr(state, "journal", None)
    try:
        summ = journal.read_summary() if journal is not None else ""
    except Exception:  # noqa: BLE001
        summ = ""
    if summ:
        out.append("\n--- activity journal (rolling summary) ---")
        out.append(summ[:4000])

    for fn in ("README.md", "README.rst", "README.txt", "README"):
        rp = root / fn
        if rp.is_file():
            try:
                head = rp.read_text(encoding="utf-8", errors="replace").strip().splitlines()[:30]
                out.append(f"\n--- {fn} (head) ---")
                out.extend(head)
            except OSError:
                # An unreadable file is left out of the .nfo rather than aborting the whole gather.
                pass
            break

    return "\n".join(out)


def _clean(raw: str) -> str:
    """Strip a wrapping ``` fence the model may add, and cap total lines under the splash limit."""
    s = raw.strip()
    if s.startswith("```"):
        lines = s.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    lines = s.splitlines()
    if len(lines) > _MAX_LINES:
        lines = lines[:_MAX_LINES]
    return "\n".join(lines).rstrip("\n")


def _cmd_nfo(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    project = ctx.get("project") or getattr(state, "project", None)

    opts, err = _parse(line)
    if err:
        console.print(f"[red]{err}[/red]")
        console.print(f"[dim]{_USAGE}[/dim]")
        return True
    if project is None:
        console.print("[red]/nfo needs a project[/red] [dim](open one with xlii code / chat)[/dim]")
        return True

    # --show: print whatever the splash would actually render (the resolved nfo), no model call.
    if opts["show"]:
        from xlii.tui.splash import read_nfo, resolve_splash_nfo
        p = resolve_splash_nfo(
            project_root=getattr(project, "project_root", None),
            xli_dir=getattr(project, "xli_dir", None),
        )
        if p is None:
            console.print("[dim]no project .nfo — the stock xlii splash shows.[/dim]")
        else:
            console.print(f"[dim]current splash → {p}[/dim]")
            console.print(read_nfo(p) or "")
        return True

    target = _target_path(project, opts["scope"])
    if target is None:
        console.print("[red]couldn't resolve a write path for this project[/red]")
        return True

    # --clear: remove the target so the splash falls back (bird / stock wordmark).
    if opts["clear"]:
        try:
            if target.exists():
                target.unlink()
                console.print(
                    f"[green]✓[/green] removed [cyan]{target}[/cyan] "
                    "[dim](splash reverts to the next fallback)[/dim]"
                )
            else:
                console.print(f"[dim]nothing to clear at {target}[/dim]")
        except OSError as e:
            console.print(f"[red]couldn't remove: {e}[/red]")
        return True

    # Generate.
    from xlii.wiki_author import session_completer
    complete = session_completer(state, temperature=0.4)
    if complete is None:
        console.print(
            "[red]no model available[/red] [dim](can't reach a chat client this session)[/dim]"
        )
        return True

    snapshot = _gather(state, project)
    system = _CARD_SYSTEM + (("\n\n" + _FULL_EXTRA) if opts["full"] else "")
    focus = f"\n\nEmphasize where relevant: {opts['focus']}." if opts["focus"] else ""
    try:
        with console.status("[cyan]summarizing the project…[/cyan]"):
            raw = complete([
                {"role": "system", "content": system},
                {"role": "user",
                 "content": f"Project snapshot:\n\n{snapshot}{focus}\n\nWrite the .nfo card now."},
            ])
    except Exception as e:  # noqa: BLE001 — never crash the REPL over a model call
        console.print(f"[red]generate failed: {type(e).__name__}: {e}[/red]")
        return True

    card = _clean(raw)
    if not card:
        console.print("[yellow]the model returned an empty card[/yellow]")
        return True

    if opts["print"]:
        console.print("[dim]preview — not written ([cyan]/nfo[/cyan] to save it):[/dim]")
        console.print(card)
        return True

    try:
        from xlii.atomicio import write_text_atomic
        write_text_atomic(target, card + "\n", mode=0o644)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]couldn't write {target}: {e}[/red]")
        return True

    console.print(f"[green]✓[/green] wrote project .nfo → [cyan]{target}[/cyan]")
    console.print(
        "[dim]it becomes your startup splash next time you open the TUI · "
        "[cyan]/nfo --show[/cyan] to view · [cyan]/nfo --clear[/cyan] to revert[/dim]"
    )
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="nfo",
            handler=_cmd_nfo,
            description="Generate an AI project-status .nfo that becomes the startup splash",
            usage=_USAGE,
            category="project",
        )
    )
