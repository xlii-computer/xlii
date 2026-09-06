"""/mojo — one-shot ask to the mobile journal, grounded in this project's record.

`/mojo <question>` addresses mojo (unless the project binds another persona)
and answers in ONE shot: ask → answer → you stay exactly where you were.
Mojo replies from its long-term memory FUSED with this project's journal +
wiki, so "what did I work on recently?" is grounded in the real record.

The persona is spoken-when-spoken-to here: `/mojo` never takes a persona name
(the whole line is the question), and to sit and have a back-and-forth you use
`/chat` (bare = mojo, `/chat <name>` = a named persona). `/askjo` is a hidden
alias of this verb. Naming/switching into another persona is always the explicit
`/chat <name>` door, never `/mojo`.
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command



def _desk_banner(state: Any) -> str:
    """Hard fact: which folder this turn is on. Journal recall is not enough."""
    proj = getattr(state, "project", None)
    if proj is None:
        return ""
    name = (getattr(proj, "name", "") or "").strip() or "desk"
    root = getattr(proj, "project_root", None)
    try:
        from xlii.project_paths import is_home_desk_project

        home = bool(is_home_desk_project(proj))
    except Exception:
        home = bool(getattr(state, "scratch", False))
    if home:
        return (
            "[current desk: Home / scratch — no project folder yet; "
            "do not invent a repo. Switch into a folder to work on a tree.]\n"
        )
    kind = ""
    try:
        from xlii.config import project_kind

        kind = (project_kind(proj) or "").strip()
    except Exception:
        kind = ""
    bits = [name]
    if kind:
        bits.append(kind)
    if root:
        bits.append(str(root))
    return (
        f"[current desk: {' · '.join(bits)} — this is the folder to focus on; "
        "files and lab work belong here.]\n"
    )


def _persona_for_bearings(state: Any):
    """Best-effort persona island for the last-turn sidecar. Never raises."""
    try:
        from xlii.persona import Persona, talk_persona_id

        pid = talk_persona_id(
            state=state,
            project=getattr(state, "project", None),
            cfg=getattr(state, "cfg", None),
        )
        if not pid:
            return None
        p = Persona(pid)
        return p
    except Exception:
        return None


def build_mojo_ambient(
    state: Any, question: str, workbench: Any = None, *, surface: str = "repl",
) -> str:
    """This project's own record (journal + wiki) to FUSE into a mojo turn, so
    mojo grounds in what THIS project recorded — the observable timeline — not
    just its own chat memory. After the project wiki, ranked hits from the
    shipped self-wiki (``xwiki``, tagged "shipped self-doc") fold in under the
    same shard budget. Empty on a surface with no journal (e.g. a chat
    surface, where `state.journal` is None); the turn then runs persona-only,
    which is correct. Best-effort: a retrieval hiccup never sinks the ask.

    Public: `/mojo` and the face server's `[M]` posture (serve_face) both fuse
    through this one builder, so what mojo knows here can't drift between
    the one-shot verb and the conversational face.

    ``workbench`` (B2): the active workbench row (``state.workbench`` when the
    caller doesn't pass one). A non-chat type adds a one-line note naming the
    type and its ambient bundle, so the persona knows which desk it's on."""
    wb = workbench if workbench is not None else getattr(state, "workbench", None)
    note = ""
    try:
        from xlii.bearings import bearings_block

        note = bearings_block(
            state,
            surface=surface,
            cfg=getattr(state, "cfg", None),
            persona_project=_persona_for_bearings(state),
        )
    except Exception:
        note = ""
    note += _desk_banner(state)
    if wb is not None and getattr(wb, "name", "chat") != "chat":
        ambient = getattr(wb, "ambient", "") or "—"
        note += f"[active workbench: {wb.name} — ambient: {ambient}]\n"
    xli = getattr(getattr(state, "project", None), "xli_dir", None)
    wiki_ctx = ""
    shard_budget = 3  # wiki_context_block default — combined cap, project first
    if xli:
        try:
            from xlii.wiki_retrieval import search_self_wiki, wiki_context_block
            hits = search_self_wiki(xli, question, limit=4)
            wiki_ctx = wiki_context_block(xli, hits, limit=shard_budget) if hits else ""
        except Exception:
            wiki_ctx = ""
    try:
        from xlii.selfwiki import selfwiki_root
        from xlii.wiki_retrieval import search_self_wiki, wiki_context_block

        root = selfwiki_root()
        if root is not None:
            used = sum(1 for line in wiki_ctx.splitlines() if line.startswith("### "))
            remain = max(0, shard_budget - used)
            if remain:
                hits2 = [
                    h for h in search_self_wiki(root, question, limit=3, scheme="xwiki")
                    if h.score >= 1  # drop FTS5 stopword hits (~1e-6)
                ]
                extra = wiki_context_block(root, hits2, limit=remain) if hits2 else ""
                if extra:
                    wiki_ctx = f"{wiki_ctx}\n\n{extra}" if wiki_ctx else extra
    except Exception:
        pass
    jrnl = getattr(state, "journal", None)
    if jrnl is not None:
        try:
            return note + jrnl.recall_context(question, wiki_context=wiki_ctx)
        except Exception:
            return note + wiki_ctx
    return note + wiki_ctx


def h_mojo(line: str, ctx: dict[str, Any]) -> bool:
    state = ctx.get("state")
    console = ctx["console"]
    if state is None:
        console.print("[red]/mojo needs an interactive session[/red]")
        return True

    # The WHOLE line after `/mojo` is the question — never a persona name (the
    # old `/mojo NAME` footgun auto-minted junk personas). Bare `/mojo` explains.
    parts = line.split(maxsplit=1)
    question = parts[1].strip() if len(parts) > 1 else ""
    if not question:
        console.print(
            "[dim]usage: [cyan]/mojo <question>[/cyan] — ask mojo (its memory + "
            "this project's journal), answered in one shot. To sit and talk, use "
            "[cyan]/chat[/cyan].[/dim]"
        )
        return True

    from xlii.cmds.sessions.ask import PersonaProjectError, run_persona_oneshot
    from xlii.cmds.sessions.resolve import _lookup_persona, ensure_default_persona
    from xlii.persona import DEFAULT_PERSONA_ID, resolve_default_persona

    # Always the DEFAULT persona (project binding > cfg > shipped mojo) — one
    # source of truth, no name parsing. Fail loudly on a bad binding; only the
    # shipped default is auto-seeded (it must always exist).
    persona_id = resolve_default_persona(project=getattr(state, "project", None),
                                         cfg=getattr(state, "cfg", None))
    persona = _lookup_persona(persona_id)
    if persona is None:
        if persona_id == DEFAULT_PERSONA_ID:
            persona = ensure_default_persona()
        else:
            console.print(
                f"[red]/mojo: persona {persona_id!r} not found[/red] "
                "[dim](check the project's persona binding)[/dim]"
            )
            return True

    ambient = build_mojo_ambient(state, question, surface="repl")
    sitting = getattr(state, "project", None)
    from xlii.project_paths import is_home_desk_project
    hire = (
        "write" if sitting is not None and not is_home_desk_project(sitting)
        else "read"
    )

    def _hold(agent: Any) -> None:
        if sitting is not None:
            agent.lab_project = sitting
        agent.sitting = state
        try:
            agent.session.door_surface = "repl"
        except Exception:
            pass

    try:
        with console.status(f"[magenta]{persona.name}…[/magenta]"):
            # One-shot on a TRANSIENT persona agent (never the live code agent):
            # the surface is untouched, so we stay in code. persist=True keeps the
            # conversation continuous; drain=False keeps /mojo snappy (the persona
            # project's normal sync lifecycle pushes it later).
            from xlii.chat_tiers import session_chat_tier

            text = run_persona_oneshot(
                persona, question, pool=state.pool, cfg=state.cfg, console=console,
                ambient_context=ambient, persist=True, drain=False,
                yolo=getattr(state, "yolo", False),
                desk_xli_dir=getattr(sitting, "xli_dir", None),
                chat_tier=session_chat_tier(state),
                on_agent=_hold, hire=hire, surface="repl")
    except PersonaProjectError:
        console.print(f"[red]/mojo: couldn't open {persona.name}'s memory[/red]")
        return True
    except Exception as e:  # noqa: BLE001 — surface, never crash the REPL
        console.print(f"[red]/mojo error: {type(e).__name__}: {e}[/red]")
        return True

    console.print(f"\n[bold magenta]{persona.name}[/bold magenta]")
    console.print(text or "[dim](no answer)[/dim]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="mojo",
            handler=h_mojo,
            usage="/mojo <question>",
            description="Ask mojo in one shot — its memory fused with this project's journal + wiki",
            category="knowledge",
            repls=["code", "chat"],
            aliases=["askjo"],
        )
    )
