"""Chat-REPL persona slash commands: /status, /personas, /persona, /edit, /forget.

Most are registered only in the chat REPL (repls=["chat"]). `_chat_list_personas`
lives here as the canonical persona listing; `cmd_chat --list` imports it upward.
Exceptions are the cross-mode knowledge commands defined here — bookmarks/recall
/mark, /bookmarks (was /marks; `marks` is a hidden alias), /recall (RP6) — which
register for repls=["code","chat"] and read
the active profile's loadout/turn store rather than assuming a chat persona.
"""

from __future__ import annotations

import shlex
from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command
from xlii.persona import (
    Persona,
    create_persona,
    is_valid_name,
    last_used,
    list_personas,
    open_in_editor,
)
from xlii.transcript import clear_turns, count_turns
from xlii.ui import confirm, console


def _chat_list_personas(current: Optional[str] = None, out=None) -> int:
    # `out` lets a live REPL pass its session console; the shell `xlii chat
    # --list` path passes nothing and prints to the module global (correct there,
    # buried under the Textual screen in the TUI — hence the seam).
    out = out or console
    from xlii.persona import list_bindable_personas

    personas = list_bindable_personas()
    if not personas:
        out.print(
            "[dim](no personas yet — create one with [cyan]xlii chat --new <name>[/cyan])[/dim]"
        )
        return 0
    # Mark the live session's persona when the caller knows it (bare /persona);
    # fall back to last-used for the shell-side `xlii chat --list`.
    if current is None:
        last = last_used()
        current = last.name if last else None
    for p in personas:
        marker = "[bold green]●[/bold green]" if p.name == current else " "
        try:
            first = p.first_line()
        except OSError:
            first = "(unreadable)"
        out.print(f"  {marker} [bold]{p.name}[/bold]  [dim]{first}[/dim]")
    return 0


def _chat_status_handler(line: str, ctx: dict[str, Any]) -> bool:
    state = ctx.get("state")
    persona = state.persona if state else ctx.get("persona")
    if not persona:
        ctx["console"].print("[dim](/status chat version only available in `xlii chat`)[/dim]")
        return True
    from xlii.transcript import count_turns as _count_turns
    n_turns = _count_turns(persona.turns_dir)
    console = ctx["console"]
    try:
        from xlii import status as _st

        console.print(f"[bold]{_st.format_primary_axes(state if state else None)}[/bold]")
    except Exception as exc:
        # Optional pretty status output: if unavailable/failing, continue with
        # the core status lines below rather than failing `/status`.
        console.print(f"[dim]status formatter unavailable; using fallback ({exc})[/dim]")
    console.print(f"persona: [bold magenta]{persona.name}[/bold magenta]")
    console.print(f"  prompt:        {persona.prompt_path}")
    console.print(f"  state dir:     {persona.project_root}")
    console.print(f"  turns on disk: {n_turns}")
    project = state.project if state else ctx.get("project")
    from xlii.repl_cmds.consult import consult_status_line
    console.print(consult_status_line(project.xli_dir if project else None))

    # Use the nice first-class status formatter
    extra = state.format_status(include_attachments=True) if state else ""
    if extra.strip():
        console.print(extra)

    return True


def _name_handler(line: str, ctx: dict[str, Any]) -> bool:
    """`/name <spelling>` — name the mobile journal. Not a persona switch."""
    console = ctx["console"]
    parts = line.split(maxsplit=1)
    wanted = parts[1].strip() if len(parts) > 1 else ""
    if not wanted:
        from xlii.persona import factory_persona_id

        state = ctx.get("state")
        cur = factory_persona_id(getattr(state, "cfg", None) if state is not None else None)
        console.print(
            f"[dim]mojo (mobile journal) is named [magenta]{cur}[/magenta]. "
            f"[cyan]/name stuart[/cyan] is just that spelling. "
            f"Other people: [cyan]/persona[/cyan].[/dim]"
        )
        return True
    from xlii.persona import (
        FactoryRenameError,
        Persona,
        factory_persona_id,
        rename_factory_persona,
    )

    state = ctx.get("state")
    cfg = getattr(state, "cfg", None) if state is not None else None
    old = factory_persona_id(cfg)
    try:
        new = rename_factory_persona(wanted, cfg=cfg)
    except FactoryRenameError as e:
        console.print(f"[red]{e}[/red]")
        return True
    live = getattr(state, "persona", None) if state is not None else None
    if live is not None and getattr(live, "name", None) == old:
        try:
            state.persona = Persona(new)
        except Exception:
            # The rename already landed on disk; a live state that refuses the swap picks it up on the next
            # load.
            pass
    console.print(
        f"[green]mobile journal is now [magenta]{new}[/magenta][/green] "
        f"[dim](same mojo, new name; other personas untouched)[/dim]"
    )
    return True


def _persona_handler(line: str, ctx: dict[str, Any]) -> bool:
    """`/persona` bare-lists (house convention: `/doc`, `/loadout`, … all do);
    `/persona <name>` switches. `/personas` is a hidden alias of this command
    (the Fold, Vector A — the `/marks` → `/bookmarks` play): bare-vs-argument
    already disambiguates list-vs-switch, so a second visible verb bought nothing."""
    state = ctx.get("state")
    persona = state.persona if state else ctx.get("persona")
    parts = line.split(maxsplit=1)
    if len(parts) != 2:
        # Bare `/persona` (or `/personas`) — list every persona, current marked.
        _chat_list_personas(current=persona.name if persona else None, out=ctx["console"])
        ctx["console"].print("[dim]usage: [/dim][cyan]/persona <name>[/cyan][dim] to switch[/dim]")
        return True
    new_name = parts[1].strip()
    from xlii.persona import canonicalize_persona_id, is_reserved_persona_name

    if is_reserved_persona_name(new_name):
        new_name = canonicalize_persona_id(
            new_name, cfg=getattr(state, "cfg", None) if state is not None else None
        )
    if persona and new_name == persona.name:
        ctx["console"].print(f"[dim]already chatting as {new_name!r}[/dim]")
        return True
    # Always switch in place via the live-switch (RP7: each persona gets its own
    # detached thread — no carry — so persona↔persona stays isolated too; works
    # under BOTH the inline REPL and the TUI). The legacy restart contract
    # (pending_persona_switch) was only consumed by a launcher loop — never the
    # chat-TUI (RP4), where it silently no-op'd — and the code launcher would
    # exit the process. The in-place path (== `/chat NAME`) is correct everywhere.
    if state is not None and getattr(state, "profile", None) is not None:
        from xlii.repl_cmds.switch import h_chat
        return h_chat(f"/chat --id {new_name}", ctx)
    # Fallback for a pre-RP2 session with no live Profile: the legacy restart.
    ctx["console"].print(f"[dim]switching to [bold]{new_name}[/bold]…[/dim]")
    ctx["_switch_persona"] = new_name
    return False  # let caller handle the restart


_EDIT_USAGE = (
    "[dim]usage: /edit --id <name> | --file <path> | --doc <name> | "
    "--plugin <id>  [--new][/dim]"
)


def _edit_handler(line: str, ctx: dict[str, Any]) -> bool:
    """`/edit` dispatcher: open a KNOWN artifact in $EDITOR. Targets are facets
    (`--id`, `--file`, `--doc`, `--plugin`), not separate commands. Exactly one
    target per call; bare `/edit` opens the current persona (chat) or prints usage.
    Browse picks paths; /edit opens them; agent tools mutate."""
    console = ctx["console"]
    state = ctx.get("state")
    persona = state.persona if state else ctx.get("persona")
    try:
        args = shlex.split(line)[1:]
    except ValueError as e:
        console.print(f"[red]invalid /edit arguments: {e}[/red]")
        return True

    strict_new = "--new" in args
    args = [a for a in args if a != "--new"]

    if not args:
        if persona:
            if _editor_launched(open_in_editor(persona.prompt_path), console):
                _after_edit_current_persona(console, state, persona)
        else:
            console.print(_EDIT_USAGE)
        return True

    facet, rest = args[0], args[1:]
    # V3b: in the chat safe-REPL /edit is narrowed to your own persona/doc
    # store — the repo/filesystem-write facets are denied. User-typed only (not
    # injection-reachable), but the safe REPL stays write-free by definition.
    scope = ctx.get("command_scope") or ("chat" if persona else "code")
    if scope == "chat" and facet in ("--file", "--plugin"):
        console.print(
            f"[dim]/edit {facet} is not available in the chat REPL (safe mode) — "
            "/code for the full surface[/dim]"
        )
        return True
    if facet == "--id":
        if len(rest) != 1:
            console.print(_EDIT_USAGE)
            return True
        return _edit_persona(console, ctx, rest[0].strip(), strict_new)
    if facet == "--file":
        if len(rest) != 1:
            console.print("[dim]usage: /edit --file <path> [--new][/dim]")
            return True
        return _edit_file(console, ctx, rest[0], strict_new)
    if facet == "--doc":
        if len(rest) != 1:
            console.print("[dim]usage: /edit --doc <name> [--new][/dim]")
            return True
        return _edit_doc(console, rest[0].strip(), strict_new)
    if facet == "--plugin":
        if len(rest) != 1:
            console.print("[dim]usage: /edit --plugin <id> [--new][/dim]")
            return True
        return _edit_plugin(console, rest[0].strip(), strict_new)

    console.print(_EDIT_USAGE)
    return True


def _editor_launched(rc: int, console) -> bool:
    """True when the editor actually ran (or a GUI was detached).

    Refuses to claim success for: no editor, a TTY editor on the face, or a
    non-zero exit (nano-without-a-tty used to print ``✓ edited`` anyway).
    """
    from xlii.editor import (
        EDITOR_DETACHED,
        EDITOR_NEEDS_TTY,
        EDITOR_UNAVAILABLE,
        editor_needs_tty_hint,
        editor_unavailable_hint,
    )

    if rc == EDITOR_UNAVAILABLE:
        console.print(
            editor_unavailable_hint()
            + " [dim]Or use [cyan]/edithere <file>[/cyan] to edit inside xlii.[/dim]"
        )
        return False
    if rc == EDITOR_NEEDS_TTY:
        console.print(editor_needs_tty_hint())
        return False
    if rc == EDITOR_DETACHED:
        return True
    if rc != 0:
        console.print(f"[yellow]editor exited {rc}[/yellow] — file not edited")
        return False
    return True


def _reload_current_persona_prompt(state) -> bool:
    """Phase 4 hot-reload: point the live agent's base prompt at the just-edited
    current persona. run_turn rebuilds history[0] from base_system_prompt next
    turn (the same seam the live /code<->/chat swap uses), so no restart. Only the
    prompt is reloaded — frontmatter loadout (declared docs/attachments) is applied
    at session start and still needs a restart to re-apply."""
    agent = getattr(state, "agent", None) if state else None
    persona = getattr(state, "persona", None) if state else None
    if agent is None or persona is None:
        return False
    try:
        agent.base_system_prompt = persona.system_prompt()
        return True
    except Exception:
        return False


def _after_edit_current_persona(console, state, persona) -> None:
    if _reload_current_persona_prompt(state):
        console.print(
            "[green]✓[/green] prompt reloaded — applies on your next turn "
            "[dim](frontmatter/loadout changes still need a restart)[/dim]."
        )
    else:
        console.print(
            "[yellow]prompt edited[/yellow] — restart or "
            f"[cyan]/chat --id {persona.name}[/cyan] to reload."
        )


def _edit_persona(console, ctx: dict[str, Any], name: str, strict_new: bool) -> bool:
    state = ctx.get("state")
    persona = state.persona if state else ctx.get("persona")
    if not is_valid_name(name):
        console.print(f"[red]invalid persona name: {name!r}[/red]")
        return True

    target = Persona(name)
    created = False
    if target.exists():
        if strict_new:
            console.print(f"[yellow]persona {name!r} already exists[/yellow] — omit --new to edit it")
            return True
    else:
        create_persona(name)
        created = True
        console.print(f"[green]✓[/green] created persona [bold]{name}[/bold] at {target.prompt_path}")

    if not _editor_launched(open_in_editor(target.prompt_path), console):
        return True
    if persona and target.name == persona.name:
        _after_edit_current_persona(console, state, persona)
    elif created:
        console.print(f"[dim]ready. Run [cyan]/chat --id {target.name}[/cyan] to start using it.[/dim]")
    else:
        console.print(f"[dim]ready. Run [cyan]/chat --id {target.name}[/cyan] to switch to it.[/dim]")
    return True


def _edit_project_root(ctx: dict[str, Any]):
    state = ctx.get("state")
    project = getattr(state, "project", None) if state else None
    if project is None:
        project = ctx.get("project")
    return getattr(project, "project_root", None) if project else None


def _warn_if_ignored(console, root, path) -> None:
    """Advisory only — ignored paths may still be edited, but say so."""
    try:
        from xlii.ignore import load_ignore_spec

        rel = path.resolve().relative_to(root.resolve()).as_posix()
        spec = load_ignore_spec(root)
        if spec.match_file(rel) or spec.match_file(rel + "/"):
            console.print(f"[dim]note: {rel} is ignored (not synced/searched) — editing anyway[/dim]")
    except Exception:
        # Advisory only: a path outside the root or an unreadable ignore spec
        # just means the edit proceeds without the note.
        pass


def _edit_file(console, ctx: dict[str, Any], raw: str, strict_new: bool) -> bool:
    from pathlib import Path

    from xlii.editor import open_for_edit
    from xlii.project_paths import PathOutsideProject, resolve_project_path

    root = _edit_project_root(ctx)
    if root is None:
        console.print("[dim]/edit --file needs a project — run it in [cyan]xlii code[/cyan][/dim]")
        return True
    root = Path(root)
    state = ctx.get("state")
    try:
        path = resolve_project_path(raw, root, cwd=getattr(state, "shell_cwd", None))
    except PathOutsideProject:
        console.print(f"[red]refused — outside the project root:[/red] {raw}")
        return True
    if path.is_dir():
        console.print(f"[red]not a file (it's a directory):[/red] {raw}")
        return True

    existed = path.exists()
    if existed and strict_new:
        console.print(f"[yellow]{path.name} already exists[/yellow] — omit --new to edit it")
        return True
    if not existed and strict_new:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    _warn_if_ignored(console, root, path)
    cfg = ctx.get("cfg") or getattr(state, "cfg", None)
    from xlii.editor import EDITOR_DETACHED, resolve_editor

    rc = open_for_edit(path, cfg=cfg)
    if not _editor_launched(rc, console):
        return True
    rel = path.relative_to(root.resolve()).as_posix()
    tag = " [dim](new file)[/dim]" if not existed else ""
    if rc == EDITOR_DETACHED:
        name = resolve_editor(cfg) or "editor"
        console.print(f"[dim]opened[/dim] [cyan]{rel}[/cyan] [dim]in {name}[/dim]{tag}")
        return True
    console.print(f"[green]✓[/green] edited [cyan]{rel}[/cyan]{tag}")
    return True


def _edit_doc(console, name: str, strict_new: bool) -> bool:
    from xlii.doc import Doc, create_doc
    from xlii.doc import is_valid_name as _doc_valid
    from xlii.doc import open_in_editor as _open_doc

    if not _doc_valid(name):
        console.print(f"[red]invalid doc name: {name!r}[/red]")
        return True
    d = Doc(name)
    if d.exists():
        if strict_new:
            console.print(f"[yellow]doc {name!r} already exists[/yellow] — omit --new to edit it")
            return True
    else:
        try:
            create_doc(name)
        except (ValueError, FileExistsError, OSError) as e:
            console.print(f"[red]could not create doc {name!r}:[/red] {e}")
            return True
        console.print(f"[green]✓[/green] created doc [bold]{name}[/bold] at {d.path}")
    if not _editor_launched(_open_doc(d.path), console):
        return True
    console.print(
        f"[dim]edited doc [cyan]{name}[/cyan] — if it's attached, "
        f"[cyan]/doc --refresh {name}[/cyan] to pull the edit into the inlined copy.[/dim]"
    )
    return True


def _edit_plugin(console, plugin_id: str, strict_new: bool) -> bool:
    from xlii.plugin import Plugin, create_plugin, is_valid_id
    from xlii.plugin import open_in_editor as _open_plugin

    if not is_valid_id(plugin_id):
        console.print(f"[red]invalid plugin id: {plugin_id!r}[/red]")
        return True
    p = Plugin(plugin_id)
    if p.exists():
        if strict_new:
            console.print(f"[yellow]plugin {plugin_id!r} already exists[/yellow] — omit --new to edit it")
            return True
    else:
        try:
            create_plugin(plugin_id)
        except (ValueError, FileExistsError, OSError) as e:
            console.print(f"[red]could not create plugin {plugin_id!r}:[/red] {e}")
            return True
        console.print(f"[green]✓[/green] created plugin [bold]{plugin_id}[/bold] at {p.path}")
    if not _editor_launched(_open_plugin(p.path), console):
        return True
    console.print(
        f"[dim]edited plugin [cyan]{plugin_id}[/cyan] — restart the session to reload it.[/dim]"
    )
    return True


def _forget_handler(line: str, ctx: dict[str, Any]) -> bool:
    state = ctx.get("state")
    persona = state.persona if state else ctx.get("persona")
    if not persona:
        return True
    n = count_turns(persona.turns_dir)
    if n == 0:
        ctx["console"].print("[dim]nothing to forget — no turns recorded yet[/dim]")
        return True
    if not confirm(f"  delete all {n} turns for {persona.name!r}? [y/N] "):
        ctx["console"].print("[dim]aborted[/dim]")
        return True
    removed = clear_turns(persona.turns_dir)
    agent = state.agent if state else ctx.get("agent")
    if agent:
        agent.history = agent.history[:1]
    ctx["console"].print(f"[green]✓[/green] removed {removed} turn(s); next sync propagates deletes")
    return True


# --------------------------------------------------------------------------- #
#  Marks — the cross-persona idea library (RP6). Un-gated from chat-only: they
#  operate on the ACTIVE profile's turn store (chat persona, bound or unbound
#  code), so a `/plan` session can bank its own insights and recall chat-born
#  ones. /recall reaches across identities via `<persona>:<mark>`.
# --------------------------------------------------------------------------- #

def _active_turns_dir(state):
    """The active profile's memory turns_dir (any mode), falling back to a legacy
    state.persona. None when the session has no turn store."""
    mem = getattr(getattr(state, "profile", None), "memory", None)
    td = getattr(mem, "turns_dir", None)
    if td is not None:
        return td
    return getattr(getattr(state, "persona", None), "turns_dir", None)


def _is_count(s: str) -> bool:
    """An ASCII non-negative integer (not Unicode digits, which int() accepts)."""
    return s.isascii() and s.isdigit()


def _pop_window(tokens: list[str]) -> tuple[list[str], Optional[int]]:
    """Split a `--window N` / `-w N` / `--window=N` flag out of a token list
    (so the rest can be a mark name with spaces). Returns (remaining, N|None)."""
    out: list[str] = []
    window: Optional[int] = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("--window", "-w") and i + 1 < len(tokens) and _is_count(tokens[i + 1]):
            window = int(tokens[i + 1]); i += 2; continue
        if t.startswith("--window=") and _is_count(t.split("=", 1)[1]):
            window = int(t.split("=", 1)[1]); i += 1; continue
        out.append(t); i += 1
    return out, window


def _mark_handler(line: str, ctx: dict[str, Any]) -> bool:
    """Tag the most recent turn as a named reference point. `--window N` records
    a recall span (N preceding turns travel with the mark as one idea-unit)."""
    state = ctx.get("state")
    console = ctx["console"]
    turns_dir = _active_turns_dir(state)
    if turns_dir is None:
        console.print("[dim](/mark needs an active session with a memory store)[/dim]")
        return True
    tokens, window = _pop_window(line.split()[1:])
    name = " ".join(tokens).strip()
    if not name:
        console.print("[dim]usage: /mark <name> [--window N]  — tag the last turn "
                      "(with N preceding turns)[/dim]")
        return True
    # A name ending in "(window: N)" would re-parse AS a windowed mark on reload
    # (the window is stored inside the bullet text) — corrupting the name and
    # conjuring a phantom span. Refuse it rather than silently mangle.
    import re as _re
    if _re.search(r"\(window:\s*\d+\)\s*$", name):
        console.print("[yellow]mark name can't end with a '(window: N)' suffix[/yellow] "
                      "[dim](use --window N instead)[/dim]")
        return True
    from xlii.transcript import mark_last_turn
    if mark_last_turn(turns_dir, name, window=window or 0):
        span = f" [dim](+{window} preceding)[/dim]" if window else ""
        console.print(
            f"[green]✓[/green] marked last turn as [cyan]{name}[/cyan]{span} "
            f"[dim](/recall {name} to pull it back)[/dim]"
        )
    else:
        console.print("[dim]no turns to mark yet — say something first[/dim]")
    return True


def _all_mark_stores(state) -> list[tuple[str, Any, bool]]:
    """(label, turns_dir, addressable) for every persona + the active local store
    (deduped). Personas are addressable as `<persona>:<mark>`; a project-local
    code store is not (recall it by plain name from inside that session)."""
    stores: list[tuple[str, Any, bool]] = []
    seen: set[str] = set()
    persona_labels: set[str] = set()
    for p in list_personas():
        key = str(p.turns_dir)
        if key in seen:
            continue
        seen.add(key)
        persona_labels.add(p.name)
        stores.append((p.name, p.turns_dir, True))
    active = _active_turns_dir(state)
    if active is not None and str(active) not in seen:
        label = getattr(getattr(state, "project", None), "name", None) or "local"
        if label in persona_labels:          # disambiguate a name clash with a persona
            label = f"{label} (this project)"
        stores.append((label, active, False))
    # Persona.md gone, chat/<name>/turns still has marks — list them as ghosts.
    try:
        from xlii.persona import CHAT_STATE_DIR
        from xlii.transcript import list_marks

        root = CHAT_STATE_DIR
        if root.is_dir():
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                td = child / "turns"
                if str(td) in seen or not td.is_dir():
                    continue
                if not list_marks(td):
                    continue
                seen.add(str(td))
                stores.append((child.name, td, False))
    except Exception:
        # Ghost stores are a courtesy listing: an unreadable chat state dir
        # just means none are offered.
        pass
    return stores


def _marks_all(state, console) -> bool:
    from xlii.transcript import list_marks
    console.print("[bold]bookmarks — across all personas[/bold]")
    any_marks = False
    for label, turns_dir, addressable in _all_mark_stores(state):
        marks = list_marks(turns_dir)
        if not marks:
            continue
        any_marks = True
        suffix = "" if addressable else " [dim](local)[/dim]"
        console.print(f"  [magenta]{label}[/magenta]{suffix}")
        for name, ts in marks:
            addr = f"{label}:{name}" if addressable else name
            console.print(f"    · [cyan]{addr}[/cyan]  [dim]{ts}[/dim]")
    if not any_marks:
        console.print("  [dim](no marks anywhere yet — /mark <name> in a chat or code session)[/dim]")
    return True


def _marks_handler(line: str, ctx: dict[str, Any]) -> bool:
    """List marks for the active store, or `--all` to browse the cross-persona
    idea library (every persona's marks, addressable as `<persona>:<mark>`)."""
    state = ctx.get("state")
    console = ctx["console"]
    if "--all" in line.split()[1:]:
        return _marks_all(state, console)
    from xlii.transcript import list_marks
    turns_dir = _active_turns_dir(state)
    if turns_dir is None:
        console.print("[dim](/bookmarks needs an active session — or try /bookmarks --all)[/dim]")
        return True
    marks = list_marks(turns_dir)
    if not marks:
        console.print("[dim](no marks yet — /mark <name> to tag the last turn · "
                      "/bookmarks --all to browse every persona)[/dim]")
        return True
    console.print("[bold]bookmarks[/bold]")
    for name, ts in marks:
        console.print(f"  · [cyan]{name}[/cyan]  [dim]{ts}[/dim]")
    return True


def _resolve_persona_store(persona_name: str, console):
    """A persona's turns_dir for cross-persona recall, or None (with a helpful
    message) when the name is invalid or unknown."""
    if not is_valid_name(persona_name):
        console.print(f"[red]invalid persona name: {persona_name!r}[/red]")
        return None
    persona = Persona(persona_name)
    if not persona.exists():
        from difflib import get_close_matches
        names = [p.name for p in list_personas()]
        close = get_close_matches(persona_name, names, n=3, cutoff=0.4)
        console.print(f"[red]no such persona: {persona_name!r}[/red]")
        if close:
            console.print("[dim]did you mean: [/dim]"
                          + ", ".join(f"[cyan]{c}[/cyan]" for c in close) + "?")
        elif names:
            console.print("[dim]personas: [/dim]" + ", ".join(f"[cyan]{n}[/cyan]" for n in names))
        return None
    return persona.turns_dir


def resolve_mark_global(state, target: str, console):
    """Resolve a bookmark address against the GLOBAL namespace (the /ref+/recall merge).

    Bookmarks are one flat namespace, not per-persona:

    - ``<persona>:<mark>`` — an explicit tie-breaker addressing one identity's store.
    - ``<mark>`` — searched across *every* store (all personas + the active one).
      Unique → that store. A name in more than one place → the candidates are listed
      as ``<persona>:<mark>`` and ``(None, None, None)`` is returned so the caller can
      ask the user to qualify.

    Returns ``(turns_dir, mark, label)`` or ``(None, None, None)`` (after printing a
    reason). ``label`` is ALWAYS the bare mark — a recalled window carries no persona
    baggage into the agent's context; provenance is a ``/bookmarks`` concern only.
    """
    from xlii.transcript import list_marks

    # Explicit qualifier: <persona>:<mark>, but only when the left side is a REAL
    # persona (so a local mark like "ratio 3:1" stays a bare name).
    if ":" in target:
        left, _, right = target.partition(":")
        left, right = left.strip(), right.strip()
        if left and is_valid_name(left) and Persona(left).exists():
            turns_dir = _resolve_persona_store(left, console)
            if turns_dir is None:
                return None, None, None
            if right not in {n for n, _ in list_marks(turns_dir)}:
                console.print(f"[yellow]no mark named[/yellow] [cyan]{right}[/cyan] "
                              f"[dim]for {left}[/dim] [dim](/bookmarks --all to browse)[/dim]")
                return None, None, None
            return turns_dir, right, right

    mark = target.strip()
    matches: list[tuple[str, Any, bool]] = []
    for label, turns_dir, addressable in _all_mark_stores(state):
        if mark in {n for n, _ in list_marks(turns_dir)}:
            matches.append((label, turns_dir, addressable))

    if not matches:
        console.print(f"[yellow]no mark named[/yellow] [cyan]{mark}[/cyan] "
                      "[dim](/bookmarks to list · /bookmarks --all to browse)[/dim]")
        return None, None, None
    if len(matches) == 1:
        return matches[0][1], mark, mark

    console.print(f"[yellow]bookmark[/yellow] [cyan]{mark}[/cyan] "
                  f"[yellow]exists in {len(matches)} places — qualify which:[/yellow]")
    for label, _td, addressable in matches:
        if addressable:
            console.print(f"  · [cyan]/recall {label}:{mark}[/cyan]")
        else:
            console.print(f"  · [dim]{label} (local — not persona-addressable)[/dim]")
    return None, None, None


def global_bookmarks(state) -> "list[tuple[str, str, bool, str]]":
    """Every bookmark across all stores, with provenance — ``(mark, persona, addressable, ts)``.

    ``persona`` is the source label (a persona name, or the active project / 'local'); ``addressable``
    says whether it can be addressed ``<persona>:<mark>`` (a real persona) vs the local active store.
    Powers the bookmarks Panel view: provenance is *shown*, and only folded into ``/recall`` as a
    ``<persona>:<mark>`` qualifier when a name lives in more than one place (see [[resolve_mark_global]])."""
    from xlii.transcript import list_marks

    out: list[tuple[str, str, bool, str]] = []
    for label, turns_dir, addressable in _all_mark_stores(state):
        for name, ts in list_marks(turns_dir):
            out.append((name, label, addressable, ts))
    return out


def _build_recall_body(label: str, span: list) -> tuple[str, int, str]:
    """Render a recalled span as an inline reference doc. Trims the OLDEST
    preceding turns if the whole span exceeds INLINE_SOFT_CAP_BYTES (the marked
    turn — span[-1] — is always kept). Returns (body, kept_turn_count, note),
    where `note` is "" | "trimmed to fit" | "exceeds the inline cap" — the count
    and note describe what the model will ACTUALLY see, not the requested span."""
    from xlii.doc import INLINE_SOFT_CAP_BYTES

    def render(turns: list) -> str:
        if len(turns) == 1:
            t = turns[0]
            return (f"# recalled point: {label}  ({t.timestamp})\n\n"
                    f"## User\n{t.user}\n\n## Assistant\n{t.assistant}\n")
        head = (f"# recalled point: {label}  —  window of {len(turns)} turns "
                f"({turns[0].timestamp} … {turns[-1].timestamp})\n")
        parts = [head]
        for i, t in enumerate(turns, 1):
            parts.append(f"\n## turn {i}/{len(turns)} — {t.timestamp}\n\n"
                         f"### User\n{t.user}\n\n### Assistant\n{t.assistant}\n")
        return "".join(parts)

    turns = list(span)
    trimmed = False
    while len(turns) > 1 and len(render(turns)) > INLINE_SOFT_CAP_BYTES:
        turns = turns[1:]          # drop oldest preceding; keep the marked turn
        trimmed = True
    body = render(turns)
    note = "trimmed to fit" if trimmed else (
        "exceeds the inline cap" if len(body) > INLINE_SOFT_CAP_BYTES else "")
    return body, len(turns), note


def _recall_handler(line: str, ctx: dict[str, Any]) -> bool:
    """Compat entry for ``/recall`` — the canonical mark-paste verb.

    ``/recall`` is canonical; ``/ref`` and ``/unref`` are hidden legacy aliases
    (``/attach ref`` owns the word ``ref`` for the marked-turn pointer type; the
    old persona-Collection meaning is banned — see repl_cmds/attach.py's
    tombstone). Routes to the merged handler so there
    is a single implementation. Kept as a named function for callers/tests that
    invoke recall directly.
    """
    from xlii.repl_cmds.knowledge import _handle_ref_command

    state = ctx.get("state")
    console = ctx["console"]
    if state is None:
        console.print("[dim]/recall needs an active session[/dim]")
        return True
    return _handle_ref_command(line, state, console)


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="status",
            handler=_chat_status_handler,
            description="Show current persona state and attached memory/docs",
            category="session",
            repls=["chat"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="name",
            handler=_name_handler,
            description="Name the mobile journal. Unnamed = mojo. Not a switch",
            usage="/name <spelling>",
            category="session",
            repls=["code", "chat"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="persona",
            handler=_persona_handler,
            # `personas` stays a hidden alias (aliases never surface in /help) so
            # muscle memory and existing docs keep working after the fold. Bare
            # `/persona` now lists; `/persona <name>` switches.
            aliases=["personas"],
            description="List personas (bare) or switch to one mid-session",
            usage="/persona [name]",
            category="session",
            repls=["chat"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="edit",
            handler=_edit_handler,
            description="Open a known artifact in $EDITOR: persona, project file, doc, or plugin",
            usage="/edit [--id <name> | --file <path> | --doc <name> | --plugin <id>] [--new]",
            category="session",
            repls=["code", "chat"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="forget",
            handler=_forget_handler,
            description="Wipe current persona's conversation history (with confirm)",
            category="session",
            repls=["chat"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="mark",
            handler=_mark_handler,
            description="Tag the last turn as a named reference point (--window N for a span)",
            usage="/mark <name> [--window N]",
            category="knowledge",
            repls=["code", "chat"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="bookmarks",
            handler=_marks_handler,
            # `marks` stays as a hidden alias (aliases never surface in /help) so
            # muscle memory and existing docs keep working after the rename.
            aliases=["marks"],
            description="List bookmarks here, or --all to browse the cross-persona library",
            usage="/bookmarks [--all]",
            category="knowledge",
            repls=["code", "chat"],
        )
    )
