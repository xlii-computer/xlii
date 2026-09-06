"""`xlii serve-inbox` (cursor-workflows.md B2.1) — a minimal localhost webhook
that queues POST bodies into the goal inbox.

It only **enqueues**: a POST writes a markdown goal into `.xlii/inbox/`; nothing
runs until you `xlii loop --drain-inbox`. So a webhook can't directly trigger
execution — it queues, and you (or cron) control when draining happens. Binds
`127.0.0.1` by default; gate with `--token` and keep it localhost (a reverse
proxy with auth is the user's job if exposed).
"""

from __future__ import annotations

import argparse
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from xlii.config import ProjectConfig
from xlii.inbox import enqueue_inbox

_MAX_INBOX_BODY = 256_000


def handle_post(
    xli_dir: Path,
    body: str,
    *,
    token_required: Optional[str] = None,
    provided_token: Optional[str] = None,
    name_hint: Optional[str] = None,
) -> tuple[int, str]:
    """Pure request core — validate + enqueue. Returns `(http_status, text)` so it
    is testable without a socket. The HTTP handler is a thin wrapper over this."""
    if token_required:
        if not provided_token or not _token_matches(provided_token, token_required):
            return 403, "forbidden: bad or missing X-XLII-Token\n"
    if not body or not body.strip():
        return 400, "empty body — POST a markdown goal\n"
    p = enqueue_inbox(xli_dir, body, stem=name_hint or "webhook", source="webhook")
    return 201, f"queued: {p.name}\n"


def _token_matches(provided: str, required: str) -> bool:
    if len(provided) != len(required):
        return False
    return secrets.compare_digest(provided, required)


def make_handler(xli_dir: Path, token: Optional[str]):
    class _Handler(BaseHTTPRequestHandler):
        server_version = "xlii-serve-inbox"

        def _reply(self, status: int, text: str) -> None:
            data = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802 (stdlib handler naming)
            self._reply(200, "xlii serve-inbox: POST a markdown goal to queue it; "
                             "drain with `xlii loop --drain-inbox`.\n")

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_INBOX_BODY:
                self._reply(413, f"payload too large (max {_MAX_INBOX_BODY} bytes)\n")
                return
            body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
            status, text = handle_post(
                xli_dir, body,
                token_required=token,
                provided_token=self.headers.get("X-XLII-Token"),
                name_hint=self.headers.get("X-XLII-Name"),
            )
            self._reply(status, text)

        def log_message(self, fmt, *args):  # keep server logs on stderr, terse
            sys.stderr.write("serve-inbox: " + (fmt % args) + "\n")

    return _Handler


def serve(project, *, host: str = "127.0.0.1", port: int = 8765,
          token: Optional[str] = None, expose: bool = False,
          insecure_no_token: bool = False) -> int:
    from xlii.bind_posture import LOOPBACK, prepare_bind

    host, refused = prepare_bind(host, expose=expose, surface="serve-inbox")
    if refused:
        print(f"error: {refused}", file=sys.stderr)
        return 1

    if insecure_no_token and (host or "").strip() not in LOOPBACK:
        print(
            "error: serve-inbox: --insecure-no-token is loopback-only",
            file=sys.stderr,
        )
        return 1
    if not token and not insecure_no_token:
        token = secrets.token_urlsafe(24)
        print(f"serve-inbox: generated X-XLII-Token: {token}", file=sys.stderr)
    httpd = ThreadingHTTPServer((host, port), make_handler(project.xli_dir, token))
    inbox = project.xli_dir / "inbox"
    gate = "  (X-XLII-Token required)" if token else "  (NO TOKEN — anyone who can reach this port can queue goals)"
    print(f"serve-inbox: POST goals to http://{host}:{port}/  → {inbox}{gate}", file=sys.stderr)
    print("serve-inbox: enqueues only — run `xlii loop --drain-inbox` to process. "
          "Ctrl-C to stop.", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nserve-inbox: stopped", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0


def cmd_serve_inbox(args: argparse.Namespace) -> int:
    from xlii.cmds.sessions import _resolve_project_target

    target = _resolve_project_target(getattr(args, "workspace", None))
    if target is None:
        print("serve-inbox: could not resolve workspace", file=sys.stderr)
        return 1
    project = ProjectConfig.load(target.resolve())
    if not project:
        print(f"serve-inbox: not an xlii project: {target}", file=sys.stderr)
        return 1
    return serve(
        project,
        host=args.host,
        port=args.port,
        token=args.token,
        expose=bool(getattr(args, "expose", False)),
        insecure_no_token=bool(getattr(args, "insecure_no_token", False)),
    )


def register(sub) -> None:
    p = sub.add_parser(
        "serve-inbox",
        help="Localhost webhook that queues POST bodies into the goal inbox (B2.1)",
        description=(
            "Run a localhost webhook that turns each POST body into a markdown goal in "
            ".xlii/inbox/. It only enqueues — nothing runs until you drain the queue with "
            "`xlii loop --drain-inbox` — so an external trigger (CI, a phone shortcut, "
            "another service) can stage work without ever executing code directly. Bound "
            "to 127.0.0.1 by default. A token is required (generated if omitted); "
            "pass --insecure-no-token only for a trusted loopback experiment."
        ),
    )
    p.add_argument("--host", default="127.0.0.1",
                   help="Bind host (default: 127.0.0.1 — localhost only)")
    p.add_argument("--expose", action="store_true",
                   help="Allow a non-loopback --host (specific tailnet/LAN address). "
                        "Wildcard binds are always refused.")
    p.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    p.add_argument("--token", default=None,
                   help="Require this shared secret in the X-XLII-Token header "
                        "(auto-generated if omitted)")
    p.add_argument("--insecure-no-token", action="store_true",
                   help="Allow unauthenticated POSTs (loopback only; still refuse wildcards)")
    p.add_argument("--workspace", metavar="NAME",
                   help="Project name (registry) or path (default: cwd / most-recent)")
    p.set_defaults(func=cmd_serve_inbox)
