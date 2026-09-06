"""/remote — named remote-filesystem connections, every wire, from inside a session.

THE one connection manager (proposals/remote-backends.md, the anti-sprawl rule):
**browsing is the address layer** — every backend is one more ``scheme://`` provider
(``ftp:// sftp:// dav:// smb://``) browsed with the same panes/commands as a local
dir, and no per-backend command ever exists — while ``/remote`` owns the *only*
backend-specific job, the one-time connection setup (credential + address). ``/ftp``
survives as a hidden alias (muscle memory).

The REPL surface over :mod:`xlii.remotefs`: connect/close the live sockets, browse a
host (``ls`` is sugar for ``xlii ls <scheme>://…``), manage the connection registry
(``add``/``rm``, with ``config …`` kept as the legacy spelling), and ``publish`` a
local site to a remote docroot. The frequent verbs are backend-uniform; only ``add``
diverges per wire, and when flags are omitted it goes **guided** — prompting for just
the chosen protocol's fields (secret straight to the vault, never the transcript).
Sub-verb dispatch mirrors ``/locker``.
"""

from __future__ import annotations

from typing import Any, Optional

from rich.markup import escape

from xlii.commands import REPLCommand, register_repl_command


def _addr_of(token: str) -> str:
    """Normalize a user token to a remote address (accepts ``name/path`` shorthand),
    using the connection's HONEST scheme (an sftp host normalizes to ``sftp://…``)."""
    if "://" in token:
        return token
    from xlii.remotefs import manager, scheme_for_protocol

    name = token.split("/", 1)[0]
    spec = manager.spec(name) or {}
    scheme = scheme_for_protocol(spec.get("protocol", "ftp"))
    return f"{scheme}://{token}"


def _honest(name: str, spec: dict) -> str:
    """``scheme://[user@]host[:port]`` display line for a connection (no secrets)."""
    from xlii.remotefs import scheme_for_protocol

    scheme = scheme_for_protocol(spec.get("protocol", "ftp"))
    user = f"{spec.get('user')}@" if spec.get("user") else ""
    port = f":{spec['port']}" if spec.get("port") else ""
    return f"{scheme}://{user}{spec.get('host') or spec.get('base_url', '?')}{port}"


def _print_connections(console) -> None:
    from xlii.remotefs import manager

    names = manager.names()
    if not names:
        console.print("[dim](no remote connections configured)[/dim]")
        console.print("[dim]add one: [/dim][cyan]/remote add <name>[/cyan][dim] (guided) or "
                      "[/dim][cyan]/remote add <name> --host <host> [--protocol ftp|ftps|sftp][/cyan]")
        return
    open_names = set(manager.open_names())
    console.print("[bold]remote connections[/bold] [dim](● connected · ○ idle)[/dim]")
    for n in names:
        spec = manager.spec(n) or {}
        mark = "[green]●[/green]" if n in open_names else "[dim]○[/dim]"
        console.print(f"  {mark} [cyan]{n}[/cyan]  [dim]{_honest(n, spec)} "
                      f"({spec.get('protocol', 'ftp')})[/dim]")
    console.print("[dim]browse: [/dim][cyan]/remote ls <name>[/cyan][dim] · connect: "
                  "[/dim][cyan]/remote connect <name>[/cyan][dim] · site up: "
                  "[/dim][cyan]/remote publish <local> <scheme>://<name>/<docroot>[/cyan]")


def _connect(console, name: str) -> None:
    from xlii.remotefs import manager, scheme_for_protocol

    try:
        conn = manager.get(name)
    except (OSError, RuntimeError, ValueError) as e:
        console.print(f"[red]connect failed:[/red] {escape(str(e))}")
        return
    scheme = scheme_for_protocol(conn.protocol)
    console.print(f"[green]✓[/green] connected [cyan]{name}[/cyan] "
                  f"[dim]({conn.protocol}://{conn.host}:{conn.port})[/dim] — "
                  f"browse with [cyan]/remote ls {name}[/cyan] or open "
                  f"[cyan]{scheme}://{name}[/cyan] in a pane")


def _ls(console, token: str) -> None:
    from xlii.addressing import vfs_list

    addr = _addr_of(token)
    try:
        nodes = vfs_list(addr)
    except (OSError, RuntimeError, NotImplementedError) as e:
        # Escape both: the address may be an IPv6 literal (sftp://[::1]/…) and the
        # error text carries pip's bracketed extra (…install 'xlii[remote]'). Rich
        # would eat either as markup — the install hint mangles to 'xlii' without.
        console.print(f"[red]{escape(addr)}[/red]: {escape(str(e))}")
        return
    if not nodes:
        console.print("[dim](empty)[/dim]")
        return
    for n in nodes:
        suffix = "/" if n.kind == "container" else ""
        size = "" if n.size is None else f"  [dim]{n.size}[/dim]"
        console.print(f"{n.name}{suffix}{size}")


def _publish(console, local: str, remote: str, *, delete: bool = False) -> None:
    from pathlib import Path

    from xlii.addressing import Address
    from xlii.remotefs import REMOTE_SCHEMES, manager, publish

    addr = Address.parse(_addr_of(remote))
    if addr.scheme not in REMOTE_SCHEMES or not addr.key.strip():
        console.print(f"[red]{escape(remote)}[/red]: publish needs a <scheme>://<name>/<docroot> destination")
        return
    try:
        conn = manager.get(addr.key.strip())
        uploaded, skipped, deleted = publish(
            conn, Path(local), addr.subpath, delete=delete,
            progress=lambda rel, status: console.print(
                f"  [dim]{status:>8}[/dim]  {rel}") if status != "skipped" else None,
        )
    except (OSError, RuntimeError, ValueError) as e:
        console.print(f"[red]publish failed:[/red] {escape(str(e))}")
        return
    pruned = f" · {deleted} deleted" if delete else ""
    console.print(f"[green]✓[/green] published [cyan]{local}[/cyan] → [cyan]{addr}[/cyan] "
                  f"[dim]({uploaded} uploaded · {skipped} unchanged{pruned})[/dim]")


# --- add (guided + flags) ---------------------------------------------------------

# The shared prompts, then each wire's own (mirroring remotefs.SPEC_FIELDS). ONE
# table drives BOTH guided surfaces — the serial walk below and the Pane-2
# wizard (xlii/tui/remote_wizard.py) — so they can never drift; a new backend
# appends a row, not a code path.
GUIDED_COMMON = (
    ("host", "host (name or IP)", True),
    ("port", "port (empty for the protocol default)", False),
    ("user", "login user (empty for anonymous)", False),
)
GUIDED_EXTRA: "dict[str, tuple]" = {
    "ftps": (("insecure", "skip TLS certificate verification? y/N", False),),
    "sftp": (("key_path", "SSH private key file (empty for password auth)", False),),
    "webdav": (("base_url", "base URL (https://host/dav — replaces host)", False),
               ("auth", "auth mode: basic | digest | bearer (default basic)", False),
               ("insecure", "skip TLS certificate verification? y/N", False)),
    "smb": (("share", "share name", True),
            ("domain", "domain/workgroup (empty for none)", False)),
}
# Backwards-compatible private aliases (pre-wizard spelling).
_GUIDED_COMMON = GUIDED_COMMON
_GUIDED_EXTRA = GUIDED_EXTRA

_ADD_USAGE = ("/remote add <name>   (guided — prompts for the wire's fields)  ·  "
              "/remote add <name> sftp://[user@]host[:port] [--key p]  ·  "
              "/remote add <name> --host <host> [--protocol ftp|ftps|sftp] "
              "[--port n] [--user u] [--key-path p] [--insecure]")


def _guided_add(console, name: str) -> "Optional[dict]":
    """The guided walk: protocol first, then only that wire's fields. Returns the
    kwargs for ``add_connection`` (secret included) or ``None`` on abort.

    Every question rides :func:`xlii.console_prompt.request_line` — a modal in
    the full-screen TUI (blocking ``input()`` would deadlock under Textual: the
    guided-add hang), plain stdin in the inline REPL. ``None`` from any question
    (Esc / no interactive surface) aborts the whole walk; the flag form stays
    the scriptable path."""
    from xlii.console_prompt import can_prompt, request_line
    from xlii.remotefs import PROTOCOLS

    if not can_prompt(console):
        console.print("[yellow]guided add needs an interactive surface — use the flag form:[/yellow]")
        console.print(f"[cyan]{_ADD_USAGE}[/cyan]")
        return None
    raw = request_line(console, f"protocol {'/'.join(PROTOCOLS)} [ftp]")
    if raw is None:
        console.print("[dim](aborted)[/dim]")
        return None
    proto = raw.strip().lower() or "ftp"
    if proto not in PROTOCOLS:
        console.print(f"[red]unknown protocol {proto!r}[/red] [dim](one of {', '.join(PROTOCOLS)})[/dim]")
        return None
    opts: dict[str, Any] = {"protocol": proto}
    for field, prompt, required in _GUIDED_COMMON + _GUIDED_EXTRA.get(proto, ()):
        if field == "host" and proto == "webdav":
            required = False  # webdav may use base_url instead
        raw = request_line(console, prompt)
        if raw is None:
            console.print("[dim](aborted)[/dim]")
            return None
        raw = raw.strip()
        if not raw and required:
            console.print(f"[red]{field} is required[/red]")
            return None
        if raw:
            opts[field] = (raw.lower() in ("y", "yes", "true", "1")) if field == "insecure" else raw
    # The secret never crosses the transcript: masked prompt, straight to the vault.
    secret = request_line(console, f"password/passphrase for {name} (empty for none)", secret=True)
    if secret is None:
        console.print("[dim](aborted)[/dim]")
        return None
    opts["secret"] = secret or None
    return opts


def _open_wizard_panel(console, state, name: str) -> bool:
    """Dock the one-screen add wizard in Pane 2 when a panel host is live (the
    TUI). Returns True when the panel took it — the wizard then OWNS the flow
    (no blocked worker, no busy spinner; Esc just closes the pane). False falls
    back to the serial walk (request_line modals / stdin)."""
    if state is None:
        return False
    try:
        from xlii.tui import panels
    except Exception:
        return False
    if panels.current_panel_host() is None:
        return False
    try:
        from xlii.tui.remote_wizard import register_remote_wizard

        register_remote_wizard()  # idempotent (last-wins registry)
    except Exception:
        return False
    state._remote_wizard_prefill = name  # one-shot prefill (ephemeral-attr convention)
    if not panels.show_panel("right", "remote-add", state=state):
        return False
    console.print("[dim]add wizard docked — fill the form; Esc closes it[/dim]")
    return True


def _opts_from_url(url: str) -> "dict[str, Any]":
    """Parse a ``scheme://[user@]host[:port]`` positional into add_connection opts.

    The module's address form doubles as its add form — ``sftp://admin@host`` is
    the same wire spelling ``/remote`` uses everywhere else, so ``add`` accepts it
    in place of ``--host``/``--user``/``--protocol``/``--port``. The scheme maps to
    a protocol (``dav://`` → webdav); an embedded password is refused (it would be
    saved to the transcript / shell history — the secret is prompted to the vault)."""
    from urllib.parse import urlsplit

    from xlii.remotefs import PROTOCOLS, protocols_for_scheme

    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme in PROTOCOLS:
        protocol = scheme
    else:
        matches = protocols_for_scheme(scheme)
        if not matches:
            raise ValueError(
                f"unknown scheme {scheme + '://'!r} "
                f"(expected one of {', '.join(sorted(PROTOCOLS))})"
            )
        protocol = matches[0]
    if parts.password is not None:
        raise ValueError(
            "don't put a password in the URL — it would be saved to the transcript "
            "/ shell history; you'll be prompted for it (stored in the encrypted vault)"
        )
    opts: dict[str, Any] = {"protocol": protocol}
    if parts.hostname:
        opts["host"] = parts.hostname
    if parts.username:
        opts["user"] = parts.username
    if parts.port:
        opts["port"] = parts.port
    return opts


def _add(console, args: "list[str]", state=None) -> None:
    from xlii.remotefs import PROTOCOLS, add_connection, scheme_for_protocol

    if not args:
        console.print(f"[yellow]usage:[/yellow] [cyan]{_ADD_USAGE}[/cyan]")
        return
    name = args[0]
    flag_args = args[1:]
    if not flag_args:
        # Guided: prefer the one-screen Pane-2 wizard (TUI); fall back to the
        # serial question walk (modals in the TUI, stdin inline).
        if _open_wizard_panel(console, state, name):
            return
        opts = _guided_add(console, name)
        if opts is None:
            return
    else:
        opts = {}
        # A scheme://[user@]host[:port] positional — the module's own address
        # form — is accepted in place of --host/--user/--protocol/--port. Later
        # flags still apply (and win), so `sftp://admin@h --key k` just works.
        if "://" in flag_args[0]:
            try:
                opts.update(_opts_from_url(flag_args[0]))
            except ValueError as e:
                console.print(f"[red]{escape(str(e))}[/red]")
                return
            flag_args = flag_args[1:]
        i = 0
        flags = {"--host": "host", "--port": "port", "--user": "user",
                 "--protocol": "protocol", "--key-path": "key_path",
                 "--key": "key_path",
                 "--base-url": "base_url", "--auth": "auth",
                 "--share": "share", "--domain": "domain"}
        while i < len(flag_args):
            tok = flag_args[i]
            if tok == "--insecure":
                opts["insecure"] = True
                i += 1
                continue
            field = flags.get(tok)
            if field is None or i + 1 >= len(flag_args):
                console.print(f"[red]bad flag:[/red] {tok}")
                if "://" in tok:
                    console.print("[dim]tip: put the URL first — "
                                  "[/dim][cyan]/remote add <name> sftp://user@host[:port] "
                                  "[--key <path>][/cyan]")
                return
            opts[field] = flag_args[i + 1]
            i += 2
        if not opts.get("host") and not opts.get("base_url"):
            console.print("[yellow]--host is required[/yellow] [dim](or --base-url for webdav)[/dim]")
            return
        if opts.get("protocol") and opts["protocol"] not in PROTOCOLS:
            console.print(f"[red]unknown protocol {opts['protocol']!r}[/red] "
                          f"[dim](one of {', '.join(PROTOCOLS)})[/dim]")
            return
        # The secret never crosses the transcript: masked prompt (modal in the
        # TUI, getpass inline), straight to the vault. Cancel aborts the add.
        from xlii.console_prompt import request_line

        secret = request_line(console, f"password/passphrase for {name} (empty for none)", secret=True)
        if secret is None:
            console.print("[dim](aborted — no secret entered)[/dim]")
            return
        opts["secret"] = secret or None
    try:
        entry = add_connection(name, **opts)
    except (OSError, RuntimeError, ValueError) as e:
        console.print(f"[red]add failed:[/red] {escape(str(e))}")
        return
    scheme = scheme_for_protocol(entry.get("protocol", "ftp"))
    console.print(f"[green]✓[/green] saved [cyan]{name}[/cyan] "
                  f"[dim](secret {'in the vault' if opts.get('secret') else 'none'})[/dim] — "
                  f"test with [cyan]/remote connect {name}[/cyan] or open "
                  f"[cyan]{scheme}://{name}[/cyan] in a pane")


def _rm(console, name: "str | None") -> None:
    from xlii.remotefs import remove_connection

    if not name:
        console.print("[yellow]usage:[/yellow] /remote rm <name>")
        return
    if remove_connection(name):
        console.print(f"[green]✓[/green] removed [cyan]{name}[/cyan] (config + vault secret)")
    else:
        console.print(f"[dim]no connection named {name}[/dim]")


def _config(console, tokens: "list[str]", state=None) -> None:
    """Legacy spelling — ``config list|add|rm`` routes to the top-level verbs."""
    sub = tokens[0].lower() if tokens else "list"
    if sub == "list" or not tokens:
        _print_connections(console)
    elif sub == "add":
        _add(console, tokens[1:], state=state)
    elif sub in ("rm", "remove", "del"):
        _rm(console, tokens[1] if len(tokens) > 1 else None)
    else:
        console.print(f"[red]unknown:[/red] /remote config {sub} [dim](list · add · rm)[/dim]")


def _close(console, name: "str | None") -> None:
    from xlii.remotefs import manager

    if name:
        if manager.close(name):
            console.print(f"[green]✓[/green] closed [cyan]{name}[/cyan]")
        else:
            console.print(f"[dim]{name} wasn't connected[/dim]")
        return
    n = manager.close_all()
    console.print(f"[green]✓[/green] closed {n} connection(s)" if n else "[dim](nothing connected)[/dim]")


def _cmd_remote(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    tokens = line.split()[1:]
    sub = tokens[0].lower() if tokens else "list"

    if sub in ("list", "status"):
        _print_connections(console)
    elif sub == "connect":
        if len(tokens) < 2:
            console.print("[yellow]usage:[/yellow] /remote connect <name>")
        else:
            _connect(console, tokens[1])
    elif sub == "ls":
        if len(tokens) < 2:
            console.print("[yellow]usage:[/yellow] /remote ls <name>/<path>")
        else:
            _ls(console, tokens[1])
    elif sub == "publish":
        if len(tokens) < 3:
            console.print("[yellow]usage:[/yellow] /remote publish <local-dir> <scheme>://<name>/<docroot> [--delete]")
        else:
            _publish(console, tokens[1], tokens[2], delete="--delete" in tokens[3:])
    elif sub == "add":
        _add(console, tokens[1:], state=state)
    elif sub in ("rm", "remove", "del"):
        _rm(console, tokens[1] if len(tokens) > 1 else None)
    elif sub == "config":
        _config(console, tokens[1:], state=state)
    elif sub == "close":
        _close(console, tokens[1] if len(tokens) > 1 else None)
    else:
        console.print(f"[red]unknown:[/red] /remote {sub} "
                      "[dim](list · connect · ls · publish · add · rm · config · close)[/dim]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="remote",
            handler=_cmd_remote,
            # `/ftp` is the hidden legacy alias (aliases never surface in /help) —
            # the verb consolidated when the backend set outgrew the FTP name.
            aliases=["ftp"],
            description="Remote hosts (FTP/FTPS/SFTP + growing): one manager for every connection; browse via ftp:// sftp:// dav:// smb:// addresses",
            usage="/remote [list | connect <name> | ls <name>/<path> | publish <local> <scheme>://<name>/<docroot> | add <name> [flags] | rm <name> | close [<name>]]",
            category="knowledge",
        )
    )
