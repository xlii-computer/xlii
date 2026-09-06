"""`/attach` + `/detach` — the consolidated attachment verbs (the Fold, Vector A).

Two types, two cost shapes — each taught in one line of per-type help so the
unified verb never blurs what an attachment *costs*:

- ``/attach doc <name>`` — a reference doc (the Rules channel) inlined into the
  system prompt every turn (always-on, re-sent each turn). Today's ``/doc``.
- ``/attach ref <mark>`` — a saved turn (a ``/mark``) as a live pointer: neither
  inlined nor searched, previewable on demand. ``bookmark`` is the accepted
  alias (the type's old name; the menu-families rename made Ref the honest one).

``/detach <name>`` removes any of them (plus a recalled ``/recall`` point) by name.

⚠ Tombstone — the persona-Collection attach is BANNED, again. ``/attach ref
<persona>`` (whole-Collection → ``search_project``) was deliberately killed in
``d96c9d35`` ("NEVER send/attach a full persona memory — EVER"), silently crawled
back in the Fold's unification, and is now dead for good: personas are sealed
islands, and the only bridges are DELIBERATE — a marked turn you chose (`ref`),
or an explicit cross-persona search (pending fabric work). The plumbing it fed
(``ToolContext.extra_collection_ids``) is deleted outright, so re-adding the
command would have nowhere to leak to. Do not read this as a lost feature.

Migration precedents (the Fold's governing principle — minimise outward-facing
commands, absorbed verbs survive as thin aliases):

- ``/doc`` rides ``/attach`` and ``/undoc`` rides ``/detach`` as **hidden aliases**
  (the ``/marks`` → ``/bookmarks`` play). The handler infers the doc type from the
  command token and delegates to the rich existing doc handler, so ``/doc``'s full
  grammar (list · ``--refresh`` · skill guard) is preserved unchanged.
- A ref rides ``make_bookmark_ref`` — an empty collection-id 2-tuple, which is
  RAG-inert by construction (nothing reads a collection id off it anymore).

The doc *logic* stays in :mod:`xlii.repl_cmds.knowledge` (A's lane); this module
imports it. Nothing here touches the ``/lib`` + ``/get`` hunks (B's).
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command
from xlii.repl_cmds.knowledge import (
    _drop_recalled_point,
    _get_attachment_owner,
    _partition_docs,
)
from xlii.ui import console as _default_console

_TYPES = ("doc", "ref", "bookmark")   # bookmark = the ref type's compat alias

# One line per type — the cost shape the docs work hard to keep distinct.
_COST = {
    "doc": "inlined into the system prompt every turn — always-on, re-sent each turn",
    "ref": "a live pointer to a saved turn — neither inlined nor searched",
}
_ARGHINT = {"doc": "<name>", "ref": "<mark>", "bookmark": "<mark>"}


def _ctx_target_console(ctx: dict[str, Any]):
    """Mirror ``_make_knowledge_handler``: prefer the REPLState, tolerate a bare
    Agent, and print through the session's console (the TUI global is buried)."""
    state = ctx.get("state")
    target = state if state is not None else ctx.get("agent")
    console = ctx.get("console") or _default_console
    return target, console


def _print_attach_usage(console) -> None:
    console.print("[dim]usage:[/dim]")
    console.print(f"  [cyan]/attach doc <name>[/cyan]  [dim]· {_COST['doc']}[/dim]")
    console.print(f"  [cyan]/attach ref <mark>[/cyan]  [dim]· {_COST['ref']} "
                  "(alias: bookmark)[/dim]")


# --------------------------------------------------------------------------- #
#  /attach
# --------------------------------------------------------------------------- #

def run_attach_command(line: str, ctx: dict[str, Any]) -> bool:
    """`/attach <type> <arg>` (+ the `/doc` hidden-alias form). Bare `/attach`
    lists what's attached, grouped by type with each type's cost shape."""
    target, console = _ctx_target_console(ctx)
    if target is None:
        console.print("[dim]/attach needs an active session[/dim]")
        return True

    parts = line.split()
    token = parts[0] if parts else "/attach"
    args = parts[1:]

    # Hidden alias: `/doc …` → the full doc grammar (list · attach · --refresh),
    # handled by the rich existing doc handler. `/doc` is `/attach doc` with the
    # type baked into the command token.
    if token == "/doc":
        from xlii.repl_cmds.knowledge import _handle_doc_command
        return _handle_doc_command(line, target, console)

    if not args:
        return _list_attachments(target, console)          # bare /attach (Decision)

    typ = args[0].lower()
    if typ not in _TYPES:
        console.print(
            f"[yellow]unknown attach type: {args[0]!r}[/yellow]  "
            "[dim](expected doc or ref)[/dim]"
        )
        _print_attach_usage(console)
        return True

    arg = " ".join(args[1:]).strip()
    if not arg:
        cost = _COST["doc" if typ == "doc" else "ref"]
        console.print(
            f"[dim]usage: [/dim][cyan]/attach {typ} {_ARGHINT[typ]}[/cyan]"
            f"[dim] — {cost}[/dim]"
        )
        return True

    if typ == "doc":
        from xlii.repl_cmds.knowledge import _handle_doc_command
        return _handle_doc_command(f"/doc {arg}", target, console)
    # ref | bookmark — one type, two spellings (ref is the honest name).
    return _attach_ref_mark(target, ctx, arg, console)


def _do_attach_bookmark(owner, label: str) -> None:
    """Append a bookmark ref (state path persists + de-dupes; legacy Agent path
    appends). Empty collection id — the bookmark discriminator, RAG-inert."""
    if hasattr(owner, "attach_ref"):
        owner.attach_ref(label, "")
    else:
        from xlii.refs import make_bookmark_ref
        owner.attached_refs.append(make_bookmark_ref(label))


def _attach_ref_mark(target, ctx: dict[str, Any], addr: str, console) -> bool:
    """`/attach ref <mark>` (alias: bookmark) — the live-pointer shape. Attaches a
    saved turn as a ref with an empty collection id. A persona name here fails
    loudly as "no such mark" — the old whole-Collection attach is banned (see the
    module tombstone)."""
    owner = _get_attachment_owner(target)
    state = ctx.get("state")
    if state is None:
        console.print("[dim]/attach ref needs an active session[/dim]")
        return True

    from xlii.repl_cmds.chat import resolve_mark_global

    turns_dir, _mark, label = resolve_mark_global(state, addr, console)
    if turns_dir is None:
        return True  # resolve_mark_global already explained (missing / ambiguous)

    if any(n == label for n, _ in (owner.attached_refs or [])):
        console.print(f"[dim](already attached: {label})[/dim]")
        return True

    _do_attach_bookmark(owner, label)
    console.print(
        f"[green]✓[/green] attached ref [cyan]{label}[/cyan] — "
        "a live pointer to the saved turn\n"
        f"  [dim]{_COST['ref']}[/dim] [dim](preview from the ref tab)[/dim]"
    )
    return True


def _list_attachments(target, console) -> bool:
    """Bare `/attach` — the unified list, grouped by type with each cost shape.
    Defers the full durable view (incl. locker files) to `/attachments`."""
    from xlii.refs import BOOKMARK, ref_view

    owner = _get_attachment_owner(target)
    all_docs = list(getattr(owner, "attached_docs", []) or [])
    docs, _skills = _partition_docs(all_docs)
    points = [(n, c) for n, c in docs if isinstance(n, str) and n.startswith("point:")]
    real_docs = [
        (n, c) for n, c in docs
        if not (isinstance(n, str) and n.startswith("point:"))
    ]
    refs = list(getattr(owner, "attached_refs", []) or [])
    books = [r for r in refs if ref_view(r).target_type == BOOKMARK]

    if not (real_docs or books or points):
        console.print("[dim](nothing attached this session)[/dim]")
        _print_attach_usage(console)
        return True

    console.print("[bold]attached[/bold] [dim](this session):[/dim]")
    if real_docs:
        console.print(f"  [cyan]docs[/cyan]  [dim]· {_COST['doc']}[/dim]")
        for n, c in real_docs:
            console.print(f"    · [cyan]{n}[/cyan]  [dim]{len(c):,} bytes[/dim]")
    if books:
        console.print(f"  [cyan]refs[/cyan]  [dim]· {_COST['ref']}[/dim]")
        for r in books:
            console.print(f"    · [cyan]{ref_view(r).name}[/cyan]  [dim]→ saved turn[/dim]")
    if points:
        console.print("  [cyan]recalled[/cyan]  [dim]· inlined marked turns (/recall)[/dim]")
        for n, _c in points:
            console.print(f"    · [cyan]{n[len('point:'):]}[/cyan]")
    console.print(
        "[dim]detach any with [/dim][cyan]/detach <name>[/cyan]"
        "[dim] · full durable view (incl. locker files): [/dim][cyan]/attachments[/cyan]"
    )
    return True


# --------------------------------------------------------------------------- #
#  /detach
# --------------------------------------------------------------------------- #

def _detach_ref(owner, name: str) -> bool:
    """Drop a ref by name from ``attached_refs``. True if removed."""
    if hasattr(owner, "detach_ref"):
        return owner.detach_ref(name)
    refs = list(getattr(owner, "attached_refs", []) or [])
    kept = [(n, c) for n, c in refs if n != name]
    if len(kept) < len(refs):
        owner.attached_refs = kept
        return True
    return False


def _has_recalled_point(owner, name: str) -> bool:
    pname = name if str(name).startswith("point:") else f"point:{name}"
    docs = getattr(owner, "attached_docs", []) or []
    return any(n == pname for n, _ in docs)


def run_detach_command(line: str, ctx: dict[str, Any]) -> bool:
    """`/detach [type] <name>` — unifies `/undoc` + `/unref`. Removes a doc, a
    ref (marked turn), or a recalled point by name; an explicit type
    disambiguates a name that lives in more than one channel."""
    target, console = _ctx_target_console(ctx)
    if target is None:
        console.print("[dim]/detach needs an active session[/dim]")
        return True

    parts = line.split()
    token = parts[0] if parts else "/detach"
    args = parts[1:]

    # Hidden alias: `/undoc …` → doc-only detach (skill-guarded existing handler).
    if token == "/undoc":
        from xlii.repl_cmds.knowledge import _handle_doc_command
        return _handle_doc_command(line, target, console)

    if not args:
        console.print("[dim]usage: [/dim][cyan]/detach [doc|ref] <name>[/cyan]")
        return True

    typ = None
    if args[0].lower() in _TYPES:
        typ, name = args[0].lower(), " ".join(args[1:]).strip()
    else:
        name = " ".join(args).strip()
    if not name:
        console.print("[dim]usage: [/dim][cyan]/detach [doc|ref] <name>[/cyan]")
        return True

    return _detach_named(target, name, console, typ)


def _detach_named(target, name: str, console, typ) -> bool:
    owner = _get_attachment_owner(target)

    if typ == "doc":
        from xlii.repl_cmds.knowledge import _handle_doc_command
        return _handle_doc_command(f"/undoc {name}", target, console)

    if typ in ("ref", "bookmark"):
        if _detach_ref(owner, name):
            console.print(f"[green]✓[/green] detached ref [cyan]{name}[/cyan]")
        else:
            console.print(f"[dim]no ref attached named {name!r}[/dim]")
        return True

    # Untyped: search channels in priority — ref, then a recalled point, then a
    # reference doc. First match wins; add a type to disambiguate a name that
    # lives in more than one channel.
    if _detach_ref(owner, name):
        console.print(f"[green]✓[/green] detached [cyan]{name}[/cyan] [dim](ref)[/dim]")
        return True
    if _has_recalled_point(owner, name):
        return _drop_recalled_point(owner, name, console)
    # Doc channel: only delegate when the name IS an attached doc (or a skill) so
    # the skill-guard / success message speak; otherwise a channel-agnostic miss
    # (delegating unconditionally would mislabel a whole-session miss as doc-only).
    from xlii.skills import SKILL_ATTACH_PREFIX
    docs, skills = _partition_docs(getattr(owner, "attached_docs", []) or [])
    skill_names = {n for n, _ in skills}
    prefixed = name if name.startswith(SKILL_ATTACH_PREFIX) else SKILL_ATTACH_PREFIX + name
    if any(n == name for n, _ in docs) or name in skill_names or prefixed in skill_names:
        from xlii.repl_cmds.knowledge import _handle_doc_command
        return _handle_doc_command(f"/undoc {name}", target, console)
    console.print(f"[dim]nothing attached named {name!r}[/dim]")
    return True


# --------------------------------------------------------------------------- #

def register() -> None:
    register_repl_command(
        REPLCommand(
            name="attach",
            handler=run_attach_command,
            # `/doc` rides here as a hidden alias (the /marks → /bookmarks play):
            # aliases never surface in /help, so the flat command list stays lean.
            aliases=["doc"],
            description="Attach knowledge to the session — a doc (inlined every turn) or a ref (a marked turn as a live pointer)",
            usage="/attach doc <name> | ref <mark>",
            category="knowledge",
        )
    )
    register_repl_command(
        REPLCommand(
            name="detach",
            handler=run_detach_command,
            aliases=["undoc"],   # /undoc → doc-only detach (hidden alias)
            description="Detach any attachment by name — a doc, a ref (marked turn), or a recalled point",
            usage="/detach [doc|ref] <name>",
            category="knowledge",
        )
    )
