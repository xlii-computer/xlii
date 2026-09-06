"""``/editthis`` — open a file in xlii's own surface: read · live-edit · discuss.

The in-xlii counterpart to ``/edit`` (which shells out to ``$EDITOR``). Under the
Textual TUI it opens A2's preview/edit surface (seam #2): ``cat`` the file into a
focused view, flip to a ``TextArea`` to live-edit it and ``ctrl+s`` to save — no
external editor — or ``ctrl+d`` to *discuss* the file with the AI (it sits in
context; edits stay explicit). Off the TUI (inline REPL, headless) it falls back
to printing the file + ``$EDITOR``.

``--draft "<desc>"`` is the **author** loop: the agent drafts the starting
content, you live-edit it, then save — the same surface F's ``/tasks new --from``
reuses, never an auto-applied write.

    /editthis <file>                 read · edit · discuss an existing/new file
    /editthis --draft "<desc>" [f]   agent-draft → live-edit → save (to f, or a draft scratch)
    /editthis                        (no path, no --draft) edit the file the file-tab panel is showing

With no path argument and no ``--draft``, ``/editthis`` targets whatever the
two-pane file-tab panel is currently file-viewing (``panels.current_panel_target``,
Vector A's published seam). When the panel is on the tree/gallery/closed — or
we're off the TUI — there is no target and it falls back to printing usage.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command

_USAGE = (
    "[dim]usage: /editthis <file>  ·  /editthis --draft \"<desc>\" [<file>]  "
    "— read · live-edit · discuss a file in xlii[/dim]"
)


# --------------------------------------------------------------------------- #
#  AI seam (draft / discuss) — isolated one-shot calls, monkeypatchable in tests
# --------------------------------------------------------------------------- #

def _ai_text(state: Any, system: str, user: str) -> str:
    """One isolated secondary-AI turn → plain text. Kept tiny + indirected so the
    surface stays decoupled from the agent and tests can stub it."""
    from xlii import secondary_ai

    return secondary_ai.query_with_profile([{"role": "user", "content": user}], "", system=system).text


def _make_drafter(state: Any, rel: str):
    def _draft(desc: str) -> str:
        system = (
            "You are drafting the contents of a file for a developer to review and edit. "
            "Output ONLY the file contents — no commentary, no code fences."
        )
        user = f"Draft the contents of `{rel}`.\n\nDescription:\n{desc}"
        return _ai_text(state, system, user)

    return _draft


def _make_discusser(state: Any, rel: str, read_current):
    def _discuss(question: str) -> str:
        content = read_current()
        system = (
            "You are discussing one file with the user. Answer questions about it. "
            "Do not rewrite the file unless explicitly asked; edits stay the user's to make."
        )
        user = f"File: {rel}\n\n```\n{content}\n```\n\nQuestion: {question}"
        return _ai_text(state, system, user)

    return _discuss


# --------------------------------------------------------------------------- #
#  path resolution
# --------------------------------------------------------------------------- #

def _project_root(state: Any) -> Optional[Path]:
    project = getattr(state, "project", None)
    root = getattr(project, "project_root", None) if project is not None else None
    return Path(root) if root else None


def _resolve_target(console, state: Any, raw: str) -> Optional[Path]:
    """Resolve a user path, jailed to the project root (like ``/edit --file``)."""
    from xlii.project_paths import PathOutsideProject, resolve_project_path

    root = _project_root(state)
    if root is None:
        console.print("[dim]/editthis needs a project — run it in [cyan]xlii code[/cyan][/dim]")
        return None
    try:
        path = resolve_project_path(raw, root, cwd=getattr(state, "shell_cwd", None))
    except PathOutsideProject:
        console.print(f"[red]refused — outside the project root:[/red] {raw}")
        return None
    if path.is_dir():
        console.print(f"[red]not a file (it's a directory):[/red] {raw}")
        return None
    return path


def _scratch_path(state: Any) -> Path:
    """A draft scratch file under ``.xlii/drafts/`` (or a temp dir off-project)."""
    import time

    root = _project_root(state)
    base = (root / ".xlii" / "drafts") if root is not None else Path(__import__("tempfile").gettempdir())
    return base / f"draft-{time.strftime('%Y%m%d-%H%M%S')}.md"


def _rel(path: Path, state: Any) -> str:
    root = _project_root(state)
    if root is not None:
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            # The file lives outside the project root -- fall back to the absolute path below.
            pass
    return str(path)


def _write_file(path: Path, text: str) -> None:
    """Atomic write that preserves an existing file's permission bits."""
    from xlii.atomicio import write_text_atomic

    mode = 0o644
    try:
        if path.exists():
            mode = path.stat().st_mode & 0o777
    except OSError:
        # An unstattable path keeps the 0o644 default set above.
        pass
    write_text_atomic(path, text, mode=mode)


# --------------------------------------------------------------------------- #
#  parsing
# --------------------------------------------------------------------------- #

def _parse(args: list[str]) -> tuple[Optional[str], Optional[str]]:
    """Return ``(draft_desc, file)`` from the tokenised args. ``--draft`` takes the
    next token as its description; the first remaining positional is the file."""
    draft_desc: Optional[str] = None
    positional: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--draft":
            if i + 1 < len(args):
                draft_desc = args[i + 1]
                i += 2
                continue
            i += 1
            continue
        if a.startswith("--draft="):
            draft_desc = a[len("--draft="):]
            i += 1
            continue
        positional.append(a)
        i += 1
    file = positional[0] if positional else None
    return draft_desc, file


# --------------------------------------------------------------------------- #
#  handler
# --------------------------------------------------------------------------- #

def _target_badge(path: Path, state: Any, existed: bool) -> str:
    """A short git/sync state badge for the surface — so you always know what you're
    opening: a *tracked* project file, an *untracked* throwaway, a *new* file you're
    about to create, and (in scratch) that it won't sync."""
    no_sync = " · scratch (no-sync)" if getattr(state, "no_sync", False) else ""
    if not existed:
        return f"new file{no_sync}"
    try:
        from xlii.git_status import find_repo_root

        repo = find_repo_root(path.parent)
        if repo is None:
            return f"untracked · no git{no_sync}"
        from xlii.loop_bundle import git_cmd

        # `ls-files` prints the path only when it's tracked (empty otherwise); and
        # git_cmd treats exit 1 as success — so test stdout, never the error slot.
        out, _err = git_cmd(repo, ["ls-files", "--", str(path)])
        return f"{'tracked' if out.strip() else 'untracked'}{no_sync}"
    except Exception:
        return f"file{no_sync}"


def _edithere_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    try:
        args = shlex.split(line)[1:]
    except ValueError as exc:
        console.print(f"[red]invalid /editthis arguments: {exc}[/red]")
        return True

    draft_desc, file_arg = _parse(args)
    if not draft_desc and not file_arg:
        # No path and no --draft: act on whatever the two-pane file-tab panel is
        # currently file-viewing (Vector A's published seam). Imported and called
        # defensively so headless / inline / pre-seam builds keep working — any
        # failure (no [tui], no host, tree/gallery/closed, seam not yet shipped)
        # degrades to None, which preserves the original "print usage" behavior.
        try:
            from xlii.tui import panels

            target = panels.current_panel_target()
        except Exception:
            target = None
        if target is None:
            console.print(_USAGE)
            return True
        # Run the panel's file through the normal resolve/badge/edit path below.
        file_arg = str(target)

    # Resolve where the save goes.
    if file_arg:
        path = _resolve_target(console, state, file_arg)
        if path is None:
            return True
    else:
        path = _scratch_path(state)  # --draft with no file → a draft scratch file
    rel = _rel(path, state)
    existed = path.exists()
    badge = _target_badge(path, state, existed)

    # Guard accidental junk-file creation: `/editthis tell me about X` parses 'tell'
    # as the path → a stray file. A NEW target that isn't a --draft scratch must be
    # confirmed first — most acute in scratch mode, where it'd be silent, un-synced
    # litter; a sentence-y arg gets a "did you mean to ask?" nudge.
    if not existed and not draft_desc:
        from xlii.tools import _confirm

        words = [a for a in args if not a.startswith("--")]
        prose = " (looks like prose — to ask, use ? instead)" if len(words) > 1 else ""
        scratch = " · scratch: won't sync" if getattr(state, "no_sync", False) else ""
        try:
            ans = _confirm(f"create new file '{rel}'?{prose}{scratch} [y/N] ")
        except (OSError, EOFError):
            ans = ""  # non-interactive (no tty / captured stdin) → fail closed
        if str(ans).strip().lower() not in ("y", "yes"):
            console.print("[dim]/editthis: cancelled — no file created[/dim]")
            return True

    def _read_current() -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    seed = _read_current() if existed and not draft_desc else ""

    def _on_save(text: str) -> None:
        _write_file(path, text)
        tag = " [dim](new file)[/dim]" if not existed else ""
        try:
            console.print(f"[green]✓[/green] saved [cyan]{rel}[/cyan]{tag}")
        except Exception:
            # The write already succeeded; only the confirmation line is lost.
            pass

    # --- TUI path: open the live surface via the host seam ------------------
    from xlii.tui import preview

    if preview.current_surface_host() is not None:
        # Under the TUI we MUST use the in-app surface and must NEVER fall through
        # to the inline `$EDITOR` path below — a full-screen editor launched inside
        # Textual floods the input box and steals the keyboard (ctrl+c won't even
        # escape). So this branch always returns.
        try:
            read_as = "markdown" if path.suffix.lower() in (".md", ".markdown") else "text"
            screen = preview.open_editor(
                seed,
                _on_save,
                draft_prompt=draft_desc,
                on_draft=_make_drafter(state, rel) if draft_desc else None,
                on_discuss=_make_discusser(state, rel, _read_current),
                title=f"{rel}  ·  {badge}",
                state=state,
                read_as=read_as,
            )
            if preview.open_surface(screen):
                verb = "drafting" if draft_desc else "editing"
                console.print(
                    f"[dim]{verb} [cyan]{rel}[/cyan] — ctrl+e edit · ctrl+s save · "
                    f"ctrl+d discuss · esc close[/dim]"
                )
                return True
            console.print("[red]/editthis: the edit surface is unavailable[/red]")
        except Exception as exc:
            console.print(f"[red]/editthis: couldn't open the edit surface ({exc})[/red]")
        return True

    # --- inline fallback (genuine inline REPL / headless): cat + $EDITOR ----
    return _edithere_inline(console, state, path, rel, existed, draft_desc, seed, _on_save, badge)


def _edithere_inline(console, state, path: Path, rel: str, existed: bool,
                     draft_desc: Optional[str], seed: str, on_save, badge: str = "") -> bool:
    """No TUI host (inline REPL / headless): print the file, then hand to $EDITOR.
    ``--draft`` seeds the file with the agent draft first (review-before-commit is
    the $EDITOR save)."""
    from xlii.editor import open_for_edit

    if draft_desc:
        try:
            seed = _make_drafter(state, rel)(draft_desc) or ""
        except Exception as exc:
            console.print(f"[yellow]draft unavailable ({exc})[/yellow] — opening an empty buffer")
            seed = ""
        _write_file(path, seed)
        console.print(f"[dim]drafted [cyan]{rel}[/cyan] — review & edit, save to keep[/dim]")
    else:
        if not existed:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            except OSError as exc:
                console.print(f"[red]could not create {rel}: {exc}[/red]")
                return True
        elif seed:
            # cat the current contents so there's an inline preview before $EDITOR.
            console.print(f"[bold cyan]{rel}[/bold cyan] [dim]· {badge}[/dim]")
            try:
                from rich.syntax import Syntax

                console.print(Syntax(seed, "text", line_numbers=False, word_wrap=True))
            except Exception:
                console.print(seed)

    open_for_edit(path)
    tag = " [dim](new file)[/dim]" if not existed else ""
    console.print(f"[green]✓[/green] edited [cyan]{rel}[/cyan]{tag}")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="editthis",
            handler=_edithere_handler,
            # `edithere` (the original name) stays a hidden alias — aliases never
            # surface in /help, so muscle memory and shipped docs keep working.
            aliases=["edithere"],
            description="Read · live-edit · discuss a file inside xlii (TUI surface; $EDITOR fallback)",
            usage='/editthis <file>  |  /editthis --draft "<desc>" [<file>]',
            category="session",
            repls=["code", "chat"],
        )
    )
