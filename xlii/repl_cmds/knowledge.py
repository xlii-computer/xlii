"""Knowledge-layer slash commands: /recall, /plugin, /get
(+ the /doc and /ref logic that /attach + /detach ride — see repl_cmds/attach.py).

These wrap attachment state (persona memory refs + reference docs) and the
plugin library. The `_handle_*` functions predate the registry and take an
attachment owner (REPLState preferred, legacy Agent tolerated); the registry
adapters in `_make_knowledge_handler` bridge them to `(line, ctx) -> bool`.
`/plugin` (was `/lib`) and `/get` take the raw `(line, ctx)` so their direct-call
and panel paths reach the live state + console (the-fold Vector B).
"""

from __future__ import annotations

from typing import Any, Callable

from xlii.commands import REPLCommand, register_repl_command
from xlii.ui import console as _default_console


def _get_attachment_owner(obj):
    """Return something that has .attached_refs / .attached_docs and mutation methods."""
    if hasattr(obj, "attach_ref"):          # REPLState (preferred)
        return obj
    if hasattr(obj, "attached_refs"):       # Agent (legacy)
        return obj
    # Fallback: try to get from .agent
    if hasattr(obj, "agent"):
        return _get_attachment_owner(obj.agent)
    return obj


def _partition_docs(attached_docs):
    """Split attached docs into (real docs, skills) — Interaction-II seam #7.

    Skills ride the /doc attachment channel under the `skill:` prefix, so /doc
    listing, refresh, and detach must exclude them (they're managed by /skill).
    Otherwise "you `/doc` detach and it's a skill you didn't know was attached."

    Prefers A1's published `partition_attached_docs` (the one home of the split);
    falls back to the prefix rule using skills.py's own `SKILL_ATTACH_PREFIX`
    constant — the single source of truth for the magic string — until that seam
    lands, so there is no duplicated literal.
    """
    try:
        from xlii.skills import partition_attached_docs  # seam #7 (A1)
        return partition_attached_docs(attached_docs)
    except ImportError:
        from xlii.skills import SKILL_ATTACH_PREFIX
        docs, skills = [], []
        for entry in (attached_docs or []):
            n = entry[0]
            if isinstance(n, str) and n.startswith(SKILL_ATTACH_PREFIX):
                skills.append(entry)
            else:
                docs.append(entry)
        return docs, skills


def _recalled_points(owner) -> "list[tuple[str, str]]":
    """The recalled-point docs (``point:*``) attached this session."""
    docs = getattr(owner, "attached_docs", None) or []
    return [(n, c) for n, c in docs if isinstance(n, str) and n.startswith("point:")]


def _list_recalled_points(owner, console) -> bool:
    pts = _recalled_points(owner)
    if not pts:
        console.print("[dim](nothing recalled this session)[/dim]")
        console.print("[dim]usage: [/dim][cyan]/recall <mark>[/cyan]"
                      "[dim] to paste a bookmarked turn in · [/dim]"
                      "[cyan]/bookmarks --all[/cyan][dim] to browse[/dim]")
        return True
    console.print("[bold]recalled points:[/bold]")
    for name, _body in pts:
        console.print(f"  · [cyan]{name[len('point:'):]}[/cyan]")
    return True


def _drop_recalled_point(owner, mark: str, console) -> bool:
    name = mark if mark.startswith("point:") else f"point:{mark}"
    if hasattr(owner, "detach_doc"):
        removed = owner.detach_doc(name)
    else:
        docs = list(getattr(owner, "attached_docs", []) or [])
        owner.attached_docs = [(n, c) for n, c in docs if n != name]
        removed = len(owner.attached_docs) < len(docs)
    if removed:
        console.print(f"[green]✓[/green] dropped recalled point [cyan]{mark}[/cyan]")
    else:
        console.print(f"[dim]no recalled point named {mark!r}[/dim]")
    return True


def _recall_mark(owner, target: str, console) -> bool:
    """Paste a bookmarked turn's window into the conversation — a cut-and-paste of
    exactly the ``/mark --window`` span, nothing more. Global bookmark lookup (a bare
    name resolves across every persona; ``<persona>:<mark>`` only breaks a name tie).
    Never a persona's whole memory, never turns outside the window, and no persona
    label rides into what the agent sees."""
    from xlii.repl_cmds.chat import _build_recall_body, _pop_window, resolve_mark_global
    from xlii.transcript import get_marked_span

    if not hasattr(owner, "attach_doc"):
        console.print("[dim]/ref needs an active session[/dim]")
        return True

    tokens, window = _pop_window(target.split())
    addr = " ".join(tokens).strip()
    if not addr:
        console.print("[dim]usage: [/dim][cyan]/recall <mark> | <persona>:<mark> [--window M][/cyan]")
        return True

    turns_dir, mark, label = resolve_mark_global(owner, addr, console)
    if turns_dir is None:
        return True  # resolve_mark_global already explained (missing / ambiguous)
    span = get_marked_span(turns_dir, mark, window_override=window)
    if not span:
        console.print(f"[yellow]no mark named[/yellow] [cyan]{mark}[/cyan]")
        return True

    body, kept, note = _build_recall_body(label, span)
    owner.attach_doc(f"point:{label}", body)
    bits = []
    if kept > 1:
        bits.append(f"window of {kept} turns")
    if note:
        bits.append(note)
    extra = f" [dim]({'; '.join(bits)})[/dim]" if bits else ""
    console.print(
        f"[green]↳[/green] recalled [cyan]{label}[/cyan]{extra} — pasted in "
        f"[dim](/detach {label} to drop)[/dim]"
    )
    return True


def _handle_ref_command(user_input: str, obj, console=_default_console) -> bool:
    """``/recall`` — paste a bookmarked turn (a ``/mark``) into the conversation.

    ``/recall``                  — list the points recalled this session
    ``/recall <mark>``           — paste that marked window in (global bookmark lookup)
    ``/recall <persona>:<mark>`` — qualify only when a name lives in more than one persona
    ``/detach <mark>``           — drop a recalled point (``/unref`` still works)

    A recall is a cut-and-paste of the marked window and nothing else — never a
    persona's whole memory (that shape is BANNED; see repl_cmds/attach.py's
    tombstone). ``/ref`` and ``/unref`` are legacy hidden aliases of ``/recall`` —
    the verb was historically mis-spelled ``/ref``; the Fold (Vector A) renamed it
    truthfully, and the word "ref" under ``/attach ref`` now names the marked-turn
    live-pointer type (the honest Bookmarks→Ref rename).
    """
    if not (user_input in ("/ref", "/recall", "/unref")
            or user_input.startswith(("/ref ", "/recall ", "/unref "))):
        return False

    owner = _get_attachment_owner(obj)
    parts = user_input.split(maxsplit=1)
    cmd = parts[0]
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/unref":
        if not arg:
            console.print("[dim]usage: [/dim][cyan]/unref <mark>[/cyan]")
            return True
        return _drop_recalled_point(owner, arg, console)

    # /recall (canonical) or hidden /ref alias
    if not arg:
        return _list_recalled_points(owner, console)
    return _recall_mark(owner, arg, console)


_EFFECT_COLORS = {
    "read-only": "green",
    "external-write": "yellow",
    "local-system": "magenta",
    "destructive": "red",
}


def _plugin_badges(p) -> str:
    """A markup-ready ``effect · trust`` badge string for a plugin — the same
    effect/trust the elevation gate keys on, so the risk a subscribe/call carries
    is always visible. A high-risk plugin gets a loud ``⚠`` prefix. The plugin id
    is trusted (validated on-disk name); effect/trust come from our own constant
    set, so this is safe to hand to console markup."""
    effect, trust = p.effect_trust()
    ec = _EFFECT_COLORS.get(effect, "white")
    warn = "[red]⚠ [/red]" if p.is_high_risk() else ""
    return f"{warn}[{ec}]{effect}[/{ec}][dim] · {trust}[/dim]"


def _plugin_subscriptions(
    rest: str, project, console, ctx: "dict[str, Any] | None" = None,
) -> bool:
    """The subscription surface of ``/plugin`` (was ``/lib``): list · all · subscribe ·
    unsubscribe · remove. Persists to ``<project>/.xlii/plugins.txt``."""
    from xlii.plugin import (
        Plugin, add_subscription, can_subscribe, delete_plugin, is_valid_id,
        list_plugins, load_subscriptions, remove_subscription,
    )

    parts = rest.split(maxsplit=1)
    sub = parts[0] if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if not sub:
        # /plugin  → list subscribed
        subs = load_subscriptions(project.xli_dir)
        if not subs:
            console.print(
                "[dim](no plugins subscribed for this project)[/dim]\n"
                "[dim]usage: [/dim][cyan]/plugin all[/cyan][dim] to browse, "
                "[/dim][cyan]/plugin subscribe <id>[/cyan][dim] to add, "
                "[/dim][cyan]/plugin panel[/cyan][dim] for the click-to-toggle catalog[/dim]"
            )
            return True
        console.print(f"[bold]subscribed plugins ({len(subs)}):[/bold]")
        for pid in subs:
            p = Plugin(id=pid)
            if p.exists():
                console.print(
                    f"  · [cyan]{pid}[/cyan]  {_plugin_badges(p)}  "
                    f"[dim]{p.description() or '(no description)'}[/dim]"
                )
            else:
                console.print(f"  · [yellow]{pid}[/yellow]  [red](orphan — file missing)[/red]")
        return True

    if sub == "all":
        plugins = list_plugins()
        subs = set(load_subscriptions(project.xli_dir))
        if not plugins:
            console.print("[dim](no plugins installed yet — [/dim]"
                          "[cyan]xlii plugin --new <id>[/cyan][dim])[/dim]")
            return True
        console.print(f"[bold]all installed plugins ({len(plugins)}):[/bold]  "
                      "[dim]● = subscribed in this project[/dim]")
        for p in plugins:
            mark = "[bold green]●[/bold green]" if p.id in subs else " "
            console.print(
                f"  {mark} [cyan]{p.id}[/cyan]  {_plugin_badges(p)}  "
                f"[dim]{p.description() or '(no description)'}[/dim]"
            )
        return True

    if sub == "subscribe":
        if not arg:
            console.print("[dim]usage: [/dim][cyan]/plugin subscribe <id>[/cyan]")
            return True
        pid = arg
        if not is_valid_id(pid):
            console.print(f"[red]invalid plugin id: {pid!r}[/red]")
            return True
        p = Plugin(id=pid)
        if not p.exists():
            console.print(
                f"[red]no such plugin: {pid!r}[/red]  "
                f"[dim](create with [/dim][cyan]xlii plugin --new {pid}[/cyan][dim])[/dim]"
            )
            return True
        state = (ctx or {}).get("state")
        allowed, reason = can_subscribe(state, p)
        if not allowed:
            console.print(f"[yellow]{reason}[/yellow]")
            return True
        added = add_subscription(project.xli_dir, pid)
        if added:
            console.print(
                f"[green]✓[/green] subscribed to [cyan]{pid}[/cyan]  {_plugin_badges(p)}"
            )
        else:
            console.print(f"[dim](already subscribed: {pid})[/dim]")
        return True

    if sub == "unsubscribe":
        if not arg:
            console.print("[dim]usage: [/dim][cyan]/plugin unsubscribe <id>[/cyan]")
            return True
        removed = remove_subscription(project.xli_dir, arg)
        if removed:
            console.print(f"[green]✓[/green] unsubscribed [cyan]{arg}[/cyan]")
        else:
            console.print(f"[dim]not subscribed: {arg!r}[/dim]")
        return True

    if sub == "remove":
        if not arg:
            console.print("[dim]usage: [/dim][cyan]/plugin remove <id>[/cyan]")
            return True
        pid = arg
        p = Plugin(id=pid)
        if not p.exists():
            console.print(f"[red]no such plugin: {pid!r}[/red]")
            return True
        # Also unsubscribe from this project, since the file's about to vanish.
        remove_subscription(project.xli_dir, pid)
        delete_plugin(pid)
        console.print(
            f"[green]✓[/green] removed plugin [cyan]{pid}[/cyan] "
            "[dim](other projects' subscriptions become orphan — cleaned on next /plugin list)[/dim]"
        )
        return True

    console.print(f"[red]unknown /plugin subcommand: {sub!r}[/red]")
    console.print("[dim]try: [/dim][cyan]/plugin[/cyan][dim], [/dim][cyan]/plugin all[/cyan][dim], "
                  "[/dim][cyan]/plugin new <id>[/cyan][dim], [/dim]"
                  "[cyan]/plugin subscribe <id>[/cyan][dim], [/dim]"
                  "[cyan]/plugin call <plugin>.<action> k=v[/cyan][dim], [/dim]"
                  "[cyan]/plugin panel[/cyan]")
    return True


def _plugin_call_repl(rest: str, ctx: "dict[str, Any]") -> bool:
    """``/plugin call <plugin>.<action> [k=v …]`` — rung 1: the user-facing direct
    rung. Params validate against the manifest, the call is direct HTTP with vault
    auth, and output honours the action's declared mode — all with ZERO model
    involvement. A ``destructive``/``always-confirm`` action gates identically to
    any other caller (the confirm keys on the manifest, not on who called)."""
    console = ctx.get("console") or _default_console
    project = ctx.get("project")
    if project is None:
        console.print("[red]no active project[/red]")
        return True

    if not rest.strip():
        console.print("[dim]usage: [/dim][cyan]/plugin call <plugin>.<action> [k=v …][/cyan]"
                      "[dim] — e.g. /plugin call open-meteo.geocode name=London[/dim]")
        return True

    from xlii.plugin import Plugin, load_subscriptions
    from xlii.plugin_call import invoke_action, parse_call_line

    try:
        parsed = parse_call_line("/plugin call " + rest)
        if parsed is None:
            raise ValueError("usage: /plugin call <plugin>.<action> [k=v …]")
        plugin_id, action_id, params = parsed
    except ValueError as e:
        from rich.markup import escape
        console.print(f"[red]{escape(str(e))}[/red]")
        return True

    if plugin_id not in load_subscriptions(project.xli_dir):
        console.print(
            f"[yellow]{plugin_id!r} is not subscribed[/yellow] — "
            f"[cyan]/plugin subscribe {plugin_id}[/cyan][dim] first[/dim]"
        )
        return True
    p = Plugin(id=plugin_id)
    if not p.exists():
        console.print(f"[red]plugin {plugin_id!r} file missing on disk[/red]")
        return True

    from xlii.plugin_form import action_needs_form, fill_form_tty

    try:
        manifest = p.manifest()
        action = manifest.get_action(action_id) if manifest is not None else None
    except Exception:
        action = None
    if action is not None and action_needs_form(action, params):
        params = fill_form_tty(action, params)
        if action_needs_form(action, params):
            console.print(
                f"[dim]{plugin_id}.{action_id} needs the face form "
                "(or fill the fields here). Secrets never go through the agent.[/dim]"
            )
            return True

    # Human confirm for a gated (always-confirm / destructive) action — the same
    # rail the agent tool honours, applied to the direct caller.
    if not _confirm_plugin_call(p, action_id, console):
        return True

    try:
        result = invoke_action(plugin_id, p.read_raw(), action_id, params, env=_plugin_env(p))
    except ValueError as e:
        from rich.markup import escape
        console.print(f"[red]{escape(str(e))}[/red]")
        return True

    _print_action_result(console, result)
    return True


def _confirm_plugin_call(p, action_id: str, console) -> bool:
    """Gate a high-risk action behind a y/N. Returns True to proceed. A read-only /
    subscription plugin sails through; a local-system/destructive/always-confirm one
    asks first — the direct rung must never be a softer gate than the agent path."""
    if not p.is_high_risk():
        return True
    effect, trust = p.effect_trust()
    from xlii.tools import _confirm

    prompt = f"run {p.id}.{action_id}? [{effect} · {trust}]\n[y/N] "
    try:
        answer = _confirm(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer != "y":
        console.print("[dim]cancelled[/dim]")
        return False
    return True


def _plugin_env(p):
    """Vault-resolved env for a plugin's auth vars, or None to inherit the
    parent env unchanged (no vault / no matching var).

    L2 plugin_call expands ``${VAR}`` from the manifest — there is no shell
    command to scan, so we load every declared ``auth_env_vars`` entry.
    """
    try:
        import os as _os

        from xlii.vault import env_for_plugins
        overrides = env_for_plugins([p])
        if overrides:
            return {**_os.environ, **overrides}
    except Exception:  # noqa: BLE001 — vault unavailable: run without the secret
        return None
    return None


def _print_action_result(console, result) -> None:
    """Print an :class:`~xlii.plugin_call.ActionResult` to the user safely — the API
    body is UNTRUSTED, so it renders through ``Text()`` and is never interpolated
    into console markup."""
    from rich.text import Text

    from xlii.plugin_manifest import OUTPUT_SCHEMA
    tag = "rendered" if result.mode == OUTPUT_SCHEMA else result.mode
    status = f"HTTP {result.code}" if result.code else "no response"
    color = "green" if result.ok else "red"
    console.print(f"[{color}]{result.plugin_id}.{result.action_id}[/{color}]"
                  f"[dim]  {status} · {tag}[/dim]")
    if result.error and not result.body:
        console.print(Text(result.error, style="red"))
        return
    console.print(Text(result.user_text.rstrip("\n")))


def _plugin_new(rest: str, ctx: "dict[str, Any]") -> bool:
    """``/plugin new <id> [--effect …] [--trust …] [--auth …] [--output …] [--subscribe]``.

    Writes ``~/.config/xlii/plugins/<id>.md`` from a stub manifest. No $EDITOR.
    ``--subscribe`` adds it to this project's ``plugins.txt``.
    """
    import re

    console = ctx.get("console") or _default_console
    s = (rest or "").strip()
    subscribe = bool(re.search(r"(?:^|\s)--subscribe(?:\s|$)", s))
    s = re.sub(r"(?:^|\s)--subscribe(?:\s|$)", " ", s).strip()

    def _eat(flag: str) -> str:
        nonlocal s
        m = re.search(rf"--{re.escape(flag)}\s+(\S+)", s)
        if not m:
            return ""
        val = m.group(1)
        s = (s[: m.start()] + s[m.end():]).strip()
        return val

    effect = _eat("effect") or "read-only"
    trust = _eat("trust") or "subscription"
    auth = _eat("auth") or "none"
    output = _eat("output") or "interpret"
    pid = s.split()[0] if s else ""
    if not pid:
        console.print(
            "[dim]usage:[/dim] [cyan]/plugin new <id>[/cyan] "
            "[dim][--effect read-only] [--trust subscription] "
            "[--auth none|header|query] [--output interpret] [--subscribe][/dim]"
        )
        return True
    from xlii.plugin import Plugin, add_subscription, can_subscribe, create_plugin, is_valid_id
    from xlii.plugin_scaffold import render_plugin

    if not is_valid_id(pid):
        console.print(f"[red]invalid plugin id: {pid!r}[/red]")
        return True
    if Plugin(id=pid).exists():
        console.print(
            f"[yellow]plugin {pid!r} already exists[/yellow] — "
            f"[cyan]/plugin subscribe {pid}[/cyan]"
        )
        return True
    try:
        text = render_plugin(pid, effect=effect, trust=trust, auth=auth, output=output)
        p = create_plugin(pid, content=text)
    except (ValueError, FileExistsError, OSError) as e:
        console.print(f"[red]{e}[/red]")
        return True
    console.print(f"[green]✓[/green] wrote {p.path} [dim]— no editor[/dim]")
    if subscribe:
        project = ctx.get("project")
        xli = getattr(project, "xli_dir", None) if project is not None else None
        if xli is None:
            console.print("[yellow]--subscribe needs a project[/yellow]")
        else:
            state = ctx.get("state")
            allowed, reason = can_subscribe(state, p)
            if not allowed:
                console.print(f"[yellow]{reason}[/yellow]")
            else:
                add_subscription(xli, pid)
                console.print(f"[green]✓[/green] subscribed [cyan]{pid}[/cyan] in this folder")
    else:
        console.print(f"[dim]subscribe with[/dim] [cyan]/plugin subscribe {pid}[/cyan]")
    return True


def _plugin_show(rest: str, ctx: "dict[str, Any]") -> bool:
    """``/plugin show <id>`` — print the written markdown (like ``/tasks show``)."""
    console = ctx.get("console") or _default_console
    pid = (rest or "").strip().split()[0] if rest.strip() else ""
    if not pid:
        console.print("[dim]usage:[/dim] [cyan]/plugin show <id>[/cyan]")
        return True
    from xlii.plugin import Plugin

    p = Plugin(id=pid)
    if not p.exists():
        console.print(f"[red]no such plugin: {pid!r}[/red]")
        return True
    raw = p.read_raw() or ""
    from rich.text import Text

    console.print(Text(raw.rstrip("\n")))
    return True


def _plugin_panel(ctx: "dict[str, Any]") -> bool:
    """``/plugin panel`` — dock the click-to-subscribe catalog (TUI). Off the TUI
    there is no panel host, so it nudges toward ``--tui`` (mirrors ``/theme``)."""
    console = ctx.get("console") or _default_console
    state = ctx.get("state")
    from xlii.tui import panels

    if panels.current_panel_host() is None:
        console.print(
            "[dim]/plugin panel needs the full-screen TUI — run [cyan]xlii code --tui[/cyan] "
            "(or [cyan]/tui[/cyan]) first. Meanwhile [cyan]/plugin all[/cyan] lists the catalog.[/dim]"
        )
        return True
    if panels.show_panel("right", "plugins", state=state):
        console.print("[green]✓[/green] plugin catalog docked — click a plugin to subscribe/unsubscribe")
    else:
        console.print("[yellow]couldn't open the plugin panel[/yellow]")
    return True


def _handle_plugin_command(line: str, ctx: "dict[str, Any]") -> bool:
    """``/plugin`` — the canonical plugin verb. ``/lib`` is a hidden alias (aliases
    never surface in ``/help``). Routes: bare/all/subscribe/unsubscribe/remove →
    subscriptions; ``call`` → the direct rung; ``panel`` → the catalog panel."""
    parts = line.strip().split(maxsplit=1)
    rest = parts[1].strip() if len(parts) > 1 else ""
    console = ctx.get("console") or _default_console
    project = ctx.get("project")

    verb = rest.split(maxsplit=1)[0] if rest else ""
    if verb == "call":
        return _plugin_call_repl(rest[len("call"):].strip(), ctx)
    if verb == "panel":
        return _plugin_panel(ctx)
    if verb == "new":
        return _plugin_new(rest[len("new"):].strip(), ctx)
    if verb == "show":
        return _plugin_show(rest[len("show"):].strip(), ctx)
    if project is None:
        console.print("[red]no active project[/red]")
        return True
    return _plugin_subscriptions(rest, project, console, ctx)


def _doc_staleness_marker(name: str, content: str) -> str:
    """A '● edited on disk' hint when the live doc differs from the inlined snapshot."""
    from xlii.doc import Doc
    d = Doc(name)
    if not d.exists():
        return "  [dim](source gone)[/dim]"
    try:
        if d.read() != content:
            return "  [yellow]● edited on disk — /doc --refresh[/yellow]"
    except OSError:
        return "  [dim](source unreadable)[/dim]"
    return ""


def _refresh_docs(owner, console, name) -> None:
    """Re-read attached docs from disk and replace their inlined snapshots.

    `/doc --refresh` refreshes every attached doc; `/doc --refresh <name>` one.
    The doc name is the re-resolvable handle (Doc(name).read()), so no source
    path needs to be stored — refresh just re-runs what /doc <name> read.
    """
    from xlii.doc import Doc
    current = list(owner.attached_docs)
    # Refresh only real docs; skills (the `skill:` channel) are managed by /skill
    # and have no Doc(name) source to re-read. They pass through untouched in the
    # reassignment below because they stay in `current` but never in `updates`.
    real_docs, _skills = _partition_docs(current)
    if not real_docs:
        console.print("[dim](no docs attached this session)[/dim]")
        return
    if name is not None and not any(n == name for n, _ in real_docs):
        console.print(f"[dim]no doc attached named {name!r}[/dim]")
        return
    targets = [name] if name else [n for n, _ in real_docs]

    updates: dict[str, str] = {}
    unchanged: list[str] = []
    gone: list[str] = []
    failed: list[tuple[str, str]] = []
    for n in targets:
        d = Doc(n)
        if not d.exists():
            gone.append(n)
            continue
        try:
            fresh = d.read()
        except OSError as e:
            failed.append((n, str(e)))
            continue
        old = next(c for nn, c in current if nn == n)
        if fresh == old:
            unchanged.append(n)
        else:
            updates[n] = fresh

    if updates:
        # In-place replace (preserves order), then reassign through the setter.
        owner.attached_docs = [(n, updates.get(n, c)) for n, c in current]

    for n in targets:
        if n in updates:
            console.print(
                f"[green]✓[/green] refreshed [cyan]{n}[/cyan] "
                f"[dim]({len(updates[n]):,} bytes re-inlined)[/dim]"
            )
    if unchanged:
        console.print(f"[dim]unchanged: {', '.join(unchanged)}[/dim]")
    for n in gone:
        console.print(
            f"[yellow]⚠ {n}: source doc no longer exists — kept the cached copy[/yellow]"
        )
    for n, e in failed:
        console.print(f"[red]{n}: read failed: {e}[/red]")
    if not updates and not gone and not failed:
        console.print("[dim]all attached docs are up to date[/dim]")


def _handle_doc_command(user_input: str, obj, console=_default_console) -> bool:
    owner = _get_attachment_owner(obj)
    is_state = hasattr(owner, "attach_doc")
    """Handle `/doc` and `/undoc` slash commands.

    `/doc`            — list currently-attached docs (in-session state)
    `/doc <name>`     — attach <name>'s content to the system prompt
    `/undoc <name>`   — detach

    Mirrors `_handle_ref_command` shape but operates on agent.attached_docs
    (which feeds the system prompt) instead of attached_refs (bookmark
    pointers).
    """
    from xlii.doc import Doc, INLINE_SOFT_CAP_BYTES, is_valid_name as _is_valid_doc_name

    if not (user_input == "/doc" or user_input.startswith("/doc ") or
            user_input == "/undoc" or user_input.startswith("/undoc ")):
        return False

    parts = user_input.split(maxsplit=1)
    cmd = parts[0]

    if cmd == "/doc":
        arg = parts[1].strip() if len(parts) > 1 else ""
        if arg.startswith("--refresh"):
            _refresh_docs(owner, console, arg[len("--refresh"):].strip() or None)
            return True
        if not arg:
            # Skills ride attached_docs under `skill:` but are NOT docs — exclude
            # them so /doc only ever surfaces (and lets you detach) real docs.
            docs, skills = _partition_docs(owner.attached_docs)
            if not docs:
                console.print("[dim](no docs attached this session)[/dim]")
                console.print("[dim]usage: [/dim][cyan]/doc <name>[/cyan]"
                              "[dim] to attach a reference doc[/dim]")
            else:
                console.print("[bold]attached docs:[/bold]")
                for name, content in docs:
                    size = len(content)
                    marker = _doc_staleness_marker(name, content)
                    console.print(
                        f"  · [cyan]{name}[/cyan]  [dim]{size:,} bytes[/dim]{marker}"
                    )
            if skills:
                n = len(skills)
                console.print(
                    f"[dim]({n} skill{'' if n == 1 else 's'} attached — "
                    "manage with [/dim][cyan]/skill[/cyan][dim])[/dim]"
                )
            return True

        name = arg
        if not _is_valid_doc_name(name):
            from xlii.doc import list_docs as _list_docs
            existing = _list_docs()
            console.print(
                f"[red]invalid doc name: {name!r}[/red]  "
                "[dim](names are single tokens — letters, digits, _ . - only)[/dim]"
            )
            if existing:
                console.print(
                    "[dim]available docs: [/dim]"
                    + ", ".join(f"[cyan]{d.name}[/cyan]" for d in existing)
                )
            return True
        d = Doc(name)
        if not d.exists():
            from xlii.doc import list_docs as _list_docs
            from difflib import get_close_matches
            existing = _list_docs()
            existing_names = [doc.name for doc in existing]
            close = get_close_matches(name, existing_names, n=3, cutoff=0.4)
            msg_lines = [f"[red]no such doc: {name!r}[/red]"]
            if close:
                msg_lines.append(
                    "[dim]did you mean: [/dim]"
                    + ", ".join(f"[cyan]{c}[/cyan]" for c in close) + "?"
                )
            elif existing_names:
                msg_lines.append(
                    "[dim]available docs: [/dim]"
                    + ", ".join(f"[cyan]{n}[/cyan]" for n in existing_names)
                )
            else:
                msg_lines.append(
                    "[dim](no docs exist yet — create with [/dim]"
                    f"[cyan]xlii doc --new {name}[/cyan][dim])[/dim]"
                )
            for line in msg_lines:
                console.print(line)
            return True
        if any(n == name for n, _ in owner.attached_docs):
            console.print(f"[dim](already attached: {name})[/dim]")
            return True
        try:
            content = d.read()
        except OSError as e:
            console.print(f"[red]read failed: {e}[/red]")
            return True
        size = len(content)
        if is_state:
            owner.attach_doc(name, content)
        else:
            owner.attached_docs.append((name, content))
        warn = ""
        if size > INLINE_SOFT_CAP_BYTES:
            warn = (
                f"  [yellow]⚠ {size:,} bytes is large for inline mode — "
                "very long reference material wants a persona you query, not an "
                "always-on inline doc[/yellow]"
            )
        console.print(
            f"[green]✓[/green] attached doc [cyan]{name}[/cyan] "
            f"[dim]({size:,} bytes inlined into system prompt)[/dim]"
            + (f"\n{warn}" if warn else "")
        )
        return True

    if cmd == "/undoc":
        if len(parts) != 2:
            console.print("[dim]usage: [/dim][cyan]/undoc <name>[/cyan]")
            return True
        name = parts[1].strip()

        # Refuse to detach a skill via /undoc — skills are a /skill concern, even
        # though they share the attached_docs channel under the `skill:` prefix.
        from xlii.skills import SKILL_ATTACH_PREFIX
        _docs, skills = _partition_docs(owner.attached_docs)
        skill_names = {n for n, _ in skills}
        prefixed = name if name.startswith(SKILL_ATTACH_PREFIX) else SKILL_ATTACH_PREFIX + name
        if name in skill_names or prefixed in skill_names:
            bare = name[len(SKILL_ATTACH_PREFIX):] if name.startswith(SKILL_ATTACH_PREFIX) else name
            console.print(
                f"[yellow]{bare} is a skill, not a doc[/yellow] — detach it with "
                f"[cyan]/skill off {bare}[/cyan] [dim](/doc only manages reference docs)[/dim]"
            )
            return True

        if is_state:
            removed = owner.detach_doc(name)
        else:
            before = len(owner.attached_docs)
            owner.attached_docs = [(n, c) for n, c in owner.attached_docs if n != name]
            removed = len(owner.attached_docs) < before

        if not removed:
            console.print(f"[dim]no doc attached named {name!r}[/dim]")
        else:
            console.print(f"[green]✓[/green] detached [cyan]{name}[/cyan]")
        return True

    return False


def _make_knowledge_handler(handle_fn: Callable, key: str) -> Callable[[str, dict], bool]:
    """Adapter so the old _handle_* functions can be used as registry handlers.

    When a REPLState is available we prefer passing it (first-class attachments).
    The legacy handlers still accept an agent for backward compatibility.
    """
    def handler(line: str, ctx: dict[str, Any]) -> bool:
        state = ctx.get("state")
        # Print via the session's console, not the module global — in the TUI
        # they differ and the global is buried under the Textual screen.
        console = ctx.get("console") or _default_console
        if key == "agent":
            target = state if state is not None else ctx.get("agent")
            return handle_fn(line, target, console)
        elif key == "project":
            return handle_fn(line, ctx["project"], console)
        return False
    return handler


def _parse_json_object(raw: str) -> "dict | None":
    """Best-effort extraction of a single JSON object from a model reply (tolerates
    ```json fences and prose around it). Returns None when nothing parses — the
    caller then degrades to the model-orchestration path rather than guessing."""
    import json

    if not raw:
        return None
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _resolve_action_call(complete, manifest, intent: str) -> "tuple[str, dict] | None":
    """The ONE cheap structured call: translate a natural-language intent into
    ``{action, params}`` against the manifest's *deterministic* (raw/schema)
    actions only. Returns (action_id, params) or None (no fit / unparseable) — the
    model translates at the front, then it is gone; it never orchestrates."""
    lines = []
    for a in manifest.actions:
        if not a.is_deterministic:
            continue
        pd = []
        for name, spec in a.params.items():
            if spec.const is not None:
                continue  # injected automatically — the user never supplies it
            bits = [name]
            if spec.required:
                bits.append("required")
            if spec.enum:
                bits.append("one of: " + "|".join(spec.enum))
            if spec.default is not None:
                bits.append(f"default={spec.default}")
            if spec.description:
                bits.append(spec.description)
            pd.append("      - " + " — ".join(bits))
        lines.append(f"  - {a.id}: {a.description}")
        lines.extend(pd)
    spec_text = "\n".join(lines)
    system = (
        "You translate a user request into ONE plugin action call. Reply with a "
        "single JSON object and nothing else: {\"action\": \"<action-id>\", "
        "\"params\": {<key>: <value>, …}}. Use only the listed actions and their "
        "params. If no action fits, reply {\"action\": null}."
    )
    user = f"Actions:\n{spec_text}\n\nRequest: {intent}"
    try:
        raw = complete([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ])
    except Exception:  # noqa: BLE001 — model unreachable: fall back to orchestration
        return None
    obj = _parse_json_object(raw)
    if not obj:
        return None
    action_id = obj.get("action")
    if not action_id or not isinstance(action_id, str):
        return None
    params = obj.get("params")
    if not isinstance(params, dict):
        params = {}
    return action_id, params


def _get_fast_path(intent: str, ctx: dict[str, Any]) -> bool:
    """Rung 3: when a subscribed plugin's matched action declares raw/schema,
    resolve {action, params} in ONE structured call and execute deterministically
    — the model never orchestrates a tool loop. Returns True when it handled the
    request (executed or user-cancelled), False to fall through to today's
    model-orchestration path (no plugin, no deterministic action, no model, or an
    unparseable resolve)."""
    project = ctx.get("project")
    state = ctx.get("state")
    console = ctx.get("console") or _default_console
    if project is None:
        return False
    try:
        from xlii.plugin import Plugin, load_subscriptions, search_plugins

        subs = [Plugin(id=pid) for pid in load_subscriptions(project.xli_dir)]
        subs = [p for p in subs if p.exists()]
        if not subs:
            return False
        matches = search_plugins(intent, subs, limit=1)
        if not matches:
            return False
        plugin = matches[0][0]
        manifest = plugin.manifest()
        if manifest is None or not any(a.is_deterministic for a in manifest.actions):
            return False  # no raw/schema action → the fast path does not apply

        from xlii.wiki_author import session_completer

        complete = session_completer(state)
        if complete is None:
            return False  # no model to translate → let the orchestration path try

        resolved = _resolve_action_call(complete, manifest, intent)
        if resolved is None:
            return False
        action_id, params = resolved
        action = manifest.get_action(action_id)
        if action is None or not action.is_deterministic:
            return False  # model picked an interpret/absent action → orchestrate instead
    except Exception:  # noqa: BLE001 — any fast-path failure degrades to orchestration
        return False

    if not _confirm_plugin_call(plugin, action_id, console):
        return True  # gated + user declined — handled, no model turn
    from xlii.plugin_call import invoke_action

    try:
        result = invoke_action(plugin.id, plugin.read_raw(), action_id, params, env=_plugin_env(plugin))
    except ValueError:
        return False  # config error surfaced late — let the model path explain
    console.print(f"[dim]↳ /get → [/dim][cyan]/plugin call {plugin.id}.{action_id}[/cyan]"
                  f"[dim] (deterministic — no model orchestration)[/dim]")
    _print_action_result(console, result)
    return True


# /get is special: on the slow path it rewrites the prompt and falls through to
# the model; on the fast path (rung 3) it executes a raw/schema action itself.
def _get_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx.get("console") or _default_console
    if not (line == "/get" or line.startswith("/get ")):
        return False
    intent = line[len("/get "):].strip() if line.startswith("/get ") else ""
    if not intent:
        console.print("[dim]usage: [/dim][cyan]/get <intent>[/cyan]"
                      "[dim] — e.g. /get the weather in seattle[/dim]")
        return True

    # Fast path: a proven, deterministic action resolves + runs with zero
    # orchestration. Only when it doesn't apply do we spend a full model turn.
    if _get_fast_path(intent, ctx):
        return True

    ctx["_get_rewritten"] = (
        "Use plugin_search to find a subscribed plugin matching this "
        f"intent, then plugin_call the action. If a form opens, stop — "
        "do not ask for secrets and do not compose curl via bash. If no "
        "plugin matches (NO_PLUGIN_MATCH), tell me — do not fabricate "
        f"output. Intent: {intent}"
    )
    return False  # fall through with rewrite


def register() -> None:
    # `/recall` is the canonical verb for "inline a marked turn"; `/ref` + `/unref`
    # are hidden legacy aliases (the verb was historically mis-named `/ref`). The
    # word "ref" under `/attach ref` now names the marked-turn pointer type (the
    # persona-Collection meaning is banned). The handler is unchanged — it still recalls / lists /
    # drops recalled points — only the command's public name flipped.
    register_repl_command(
        REPLCommand(
            name="recall",
            handler=_make_knowledge_handler(_handle_ref_command, "agent"),
            aliases=["ref", "unref"],
            description="Inline a bookmarked turn (a /mark) into the conversation — a cut-and-paste, never a persona's whole memory",
            usage="/recall <mark> | <persona>:<mark> | /detach <mark>",
            category="knowledge",
        )
    )
    # /doc + /undoc are NOT registered here anymore — they ride /attach + /detach
    # as hidden aliases (see xlii/repl_cmds/attach.py). The doc *logic*
    # (_handle_doc_command and helpers) still lives in this module; attach.py
    # imports it. This keeps the doc/ref/attach regions in one file (A's lane)
    # while the /lib + /get hunks below stay B's, byte-untouched.

    # `/plugin` is the canonical verb (the CLI already says `xlii plugin`); `/lib`
    # is a hidden legacy alias — aliases never surface in /help, so the two
    # surfaces agree on one noun and "lib" is freed (it collided with the
    # cross-persona bookmark "library"). The handler takes the raw (line, ctx) so
    # `call`/`panel` can reach state + console directly, like /get.
    register_repl_command(
        REPLCommand(
            name="plugin",
            handler=_handle_plugin_command,
            aliases=["lib"],
            description="Plugins: subscribe, call an action directly, or open the catalog panel",
            usage="/plugin [all | new <id> | show <id> | subscribe <id> | unsubscribe <id> | remove <id> "
                  "| call <plugin>.<action> k=v … | panel]",
            category="knowledge",
        )
    )
    register_repl_command(
        REPLCommand(
            name="get",
            handler=_get_handler,
            description="Invoke a subscribed plugin by natural-language intent",
            usage="/get <intent>",
            category="knowledge",
        )
    )
    # Register the click-to-subscribe catalog panel as a published panel view
    # (additive, last-wins) — a function call from our own module, never a shared
    # edit to tui/panels.py. Best-effort: import-light without [tui].
    try:
        from xlii.tui.plugins_panel import register_plugins_panel
        register_plugins_panel()
    except Exception:  # noqa: BLE001 — the command works without the panel view
        pass
