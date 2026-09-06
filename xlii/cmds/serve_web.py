"""`xlii serve` — the substrate in a browser tab (proposals/serve-web.md W1).

textual-serve wraps the existing Textual TUI: a small aiohttp server spawns
``xlii code --tui`` as a subprocess per browser session and pipes its terminal
stream over a WebSocket to xterm.js in the page. The app is unmodified — the
browser *becomes* the terminal — so every TUI improvement ships to the browser
for free. One subprocess per tab: each tab is its own session.

SECURITY — the first paragraph, not a footnote: textual-serve has NO
AUTHENTICATION. Anyone who can reach the port gets the REPL, and the REPL
passes bare input to the project shell — the port IS a shell on this machine.
Default bind is 127.0.0.1. To reach it from another device, bind your
tailnet interface address (same posture the fabric documents for Prosody).
Do not bind 0.0.0.0.

Public exposure (`--public`) is a different threat model (proposals/serve-public.md):
loopback bind only, Caddy for TLS, pairing-code grant gate before any terminal
stream. See ``xlii.serve_public``.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

# Memorable default; xlii in a tab (the browser-ui exploration's pick).
_DEFAULT_PORT = 8042

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_WILDCARD = {"0.0.0.0", "::", ""}


def _served_command(target: str, *, preview: bool = False) -> str:
    """The subprocess textual-serve spawns per browser session: the normal TUI
    launch over `target` (the invocation directory, like the REPL itself)."""
    parts = ["xlii", "code", target]
    if preview:
        parts.append("--preview")
    parts.append("--tui")
    return shlex.join(parts)


def banner_lines(host: str, port: int, command: str) -> list[str]:
    """The loud startup banner. Pure so tests can pin it offline."""
    lines = [
        f"serve: http://{host}:{port}/  →  {command}",
        "serve: NO AUTH — anyone who can reach this port has SHELL ACCESS on "
        "this machine (bare REPL input runs in the project shell).",
    ]
    if host in _WILDCARD:
        lines.append(
            "serve: DANGER — binding all interfaces exposes a shell to every "
            "network this machine is on. Bind 127.0.0.1 or your tailnet "
            "interface address instead."
        )
    elif host not in _LOOPBACK:
        lines.append(
            f"serve: binding {host} (non-localhost) — make sure this address is "
            "a private/tailnet interface, never a public one."
        )
    else:
        lines.append(
            "serve: localhost only. To reach it from another device, bind your "
            "tailnet interface address with --host."
        )
    lines.append("serve: one session per browser tab. Ctrl-C to stop.")
    return lines


def cmd_serve(args: argparse.Namespace) -> int:
    if getattr(args, "face", False):
        from xlii.cmds.serve_face import cmd_serve_face

        return cmd_serve_face(args)
    if getattr(args, "ws", False):
        from xlii.cmds.serve_ws import cmd_serve_ws

        return cmd_serve_ws(args)

    from rich.console import Console

    err = Console(stderr=True, highlight=False)

    public = bool(getattr(args, "public", False))
    if not public:
        from xlii.bind_posture import prepare_bind

        host, refused = prepare_bind(
            args.host, expose=bool(getattr(args, "expose", False)), surface="serve",
        )
        if refused:
            err.print(f"[red]✗[/red] {refused}")
            return 1
        args.host = host
    if public:
        from xlii.serve_public import public_bind_error, resolve_public_settings

        bind_err = public_bind_error(args.host)
        if bind_err:
            err.print(f"[red]✗[/red] {bind_err}")
            return 1

        settings = resolve_public_settings(args)
        base_url = settings["base_url"]
        if not base_url:
            err.print(
                "[red]✗[/red] serve --public requires a base URL "
                "(set [bold]\\[serve.public] base_url[/bold] or pass [cyan]--base-url[/cyan])."
            )
            return 1
        if settings["closed_door"] and not settings["redirect_off_url"]:
            err.print(
                "[red]✗[/red] serve --public: closed_door is on but "
                "[bold]\\[serve.public] redirect_off_url[/bold] is unset."
            )
            return 1

    # Lazy import: the aiohttp/xterm.js server only loads with the [web] extra.
    try:
        from textual_serve.server import Server
    except ImportError:
        err.print("[red]✗[/red] serve requires the optional [bold]web[/bold] extra (textual-serve).")
        err.print("  [cyan]pip install \"xlii\\[web]\"[/cyan]")
        err.print("  (editable source tree: [cyan]pip install -e \".\\[web]\"[/cyan])")
        return 1

    target = str(Path.cwd())
    command = _served_command(target, preview=getattr(args, "preview", False))
    preview_command = _served_command(target, preview=True)

    if public:
        from xlii.serve_public import (
            default_audit_log,
            default_state_dir,
            make_public_server,
            public_banner_lines,
        )
        from xlii.serve_gate import GateStore

        # D3: public mode always binds loopback (normalize localhost → 127.0.0.1).
        host = "127.0.0.1"
        state_dir = default_state_dir()
        audit_log = default_audit_log(state_dir)
        for line in public_banner_lines(
            base_url=str(base_url),
            host=host,
            port=args.port,
            audit_log=audit_log,
        ):
            print(line, file=sys.stderr)

        server = make_public_server(
            command,
            gate=GateStore(),
            base_url=str(base_url),
            preview_command=preview_command,
            state_dir=state_dir,
            idle_timeout_s=settings["idle_timeout_s"],
            session_ttl_s=settings["session_ttl_s"],
            code_ttl_s=settings["code_ttl_s"],
            max_sessions=settings["max_sessions"],
            audit_log=audit_log,
            host=host,
            port=args.port,
            title="xlii",
            public_url=str(base_url).rstrip("/"),
            closed_door=settings["closed_door"],
            redirect_off_url=settings["redirect_off_url"],
            face_default=settings["face_default"],
        )
    else:
        for line in banner_lines(args.host, args.port, command):
            print(line, file=sys.stderr)
        server = Server(command, host=args.host, port=args.port, title="xlii")

    try:
        server.serve()
    except KeyboardInterrupt:
        print("\nserve: stopped", file=sys.stderr)
    return 0


def register(sub) -> None:
    p = sub.add_parser(
        "serve",
        help="Serve the full-screen TUI in a browser tab (requires the [web] extra; "
             "NO AUTH — localhost/tailnet only; use --public for code-gated public entry).",
        description=(
            "Serve the existing Textual TUI over HTTP: a browser tab becomes the "
            "terminal (xterm.js over a WebSocket), one session per tab, running "
            "`xlii code --tui` for the current directory. SECURITY: there is NO AUTH — "
            "anyone who can reach the port has a shell on this machine (unless "
            "--public, which code-gates the entry). The default bind is 127.0.0.1; "
            "to use it from another device, bind your tailnet interface address. "
            "Never bind 0.0.0.0. With --public, bind stays loopback and Caddy owns TLS."
        ),
    )
    p.add_argument("--host", default="127.0.0.1",
                   help="Bind host (default: 127.0.0.1 — localhost only; "
                        "'tailnet' binds this machine's Tailscale IPv4; "
                        "a specific address still works. Non-loopback requires "
                        "--expose. Ignored/refused under --public which always "
                        "binds loopback)")
    p.add_argument("--expose", action="store_true",
                   help="Allow a non-loopback --host (specific tailnet/LAN address). "
                        "Wildcard binds (0.0.0.0 / ::) are always refused.")
    p.add_argument("--port", type=int, default=_DEFAULT_PORT,
                   help=f"Bind port (default: {_DEFAULT_PORT}; --face defaults to an "
                        "ephemeral port instead — pass --port to pin one)")
    p.add_argument("--preview", action="store_true",
                   help="Serve a read-only exploration session "
                        "(`xlii code --preview --tui`) — natural for a shared screen")
    p.add_argument("--public", action="store_true",
                   help="Code-gated public entry (serve-public): loopback bind only, "
                        "pairing codes via grant cookie; put Caddy in front for TLS")
    p.add_argument("--base-url", default=None, metavar="URL",
                   help="(with --public) public base URL for the entry page "
                        "(or set [serve.public] base_url); required in --public mode")
    p.add_argument("--ws", action="store_true",
                   help="WebSocket JSON event protocol (W2) instead of textual-serve. "
                        "Uses ask --session; token auth mandatory.")
    p.add_argument("--face", action="store_true",
                   help="The face server (protocol v2): ONE live REPL session over the "
                        "WebSocket wire + the face page over plain HTTP — the backend "
                        "of the desktop (Tauri) and browser face. Token auth mandatory.")
    p.add_argument("--handshake", action="store_true",
                   help="(with --ws/--face) bind ephemeral port, print one JSON handshake "
                        "line on stdout, exit when stdin closes (Tauri sidecar contract)")
    p.add_argument("--token", default=None,
                   help="(with --ws/--face) auth token for WebSocket connections "
                        "(auto-generated if omitted)")
    p.add_argument("--workspace", metavar="PATH",
                   help="(with --ws/--face) project directory (default: cwd / most-recent)")
    p.add_argument("--yolo", action="store_true",
                   help="(with --ws/--face) skip per-intent confirmation gates for agent turns")
    p.add_argument("--force", action="store_true",
                   help="(with --face) proceed even when launched from within another "
                        "xlii session for this project (shared on-disk state — same "
                        "risk as `xlii code --force`)")
    p.add_argument(
        "--replace", dest="face_replace", action="store_true",
        help="(with --face) if another face is already running on this machine, "
             "stop it and take over (one face per environment)",
    )
    p.add_argument(
        "--view", dest="face_view", choices=("desk", "phone"), default="desk",
        help="(with --face) desk chrome (default) or phone glass — public "
             "webcode spawns phone so daemon xsu/webcode lands the thumb face",
    )
    p.set_defaults(func=cmd_serve)

    # Public-gate admin verbs (serve-public V3): optional subcommands, so bare
    # `xlii serve` keeps serving while `xlii serve mint|sessions|revoke` routes
    # to the admin handlers (their set_defaults(func=…) override the parent's).
    from xlii.cmds.serve_admin import register_serve_admin

    actions = p.add_subparsers(
        dest="serve_action", required=False, metavar="{mint,sessions,revoke}"
    )
    register_serve_admin(actions)
