"""`xlii serve --face` — CLI façade for the persistent-REPL face server.

The server lives in the kernel at ``xlii/serve_face.py`` (one live code
session projected over the protocol-v2 WebSocket wire — the backend of the
desktop/browser face). This file is argparse wiring only, mirroring
``cmds/serve_ws.py``. Spec: docs/ws-event-protocol.md
"""

from __future__ import annotations

import sys

from xlii.serve_face import serve_face


def cmd_serve_face(args) -> int:
    from xlii.cmds.sessions.resolve import _resolve_project_target
    from xlii.config import ProjectConfig

    target = _resolve_project_target(getattr(args, "workspace", None))
    if target is None:
        print("serve-face: could not resolve workspace", file=sys.stderr)
        return 1
    project = ProjectConfig.load(target.resolve())
    if not project:
        print(f"serve-face: not an xlii project: {target}", file=sys.stderr)
        return 1

    port = args.port
    if getattr(args, "handshake", False):
        port = 0
    else:
        # The face defaults to an EPHEMERAL port: its URL carries the token
        # (you copy it either way), and inheriting `serve`'s memorable 8042
        # just collided with the browser-TUI server. An explicit non-default
        # --port still pins one.
        from xlii.cmds.serve_web import _DEFAULT_PORT
        if port == _DEFAULT_PORT:
            port = 0

    return serve_face(
        target.resolve(),
        host=args.host,
        port=port,
        token=getattr(args, "token", None),
        handshake=getattr(args, "handshake", False),
        yolo=getattr(args, "yolo", False),
        force=getattr(args, "force", False),
        replace_instance=getattr(args, "face_replace", False),
        expose=bool(getattr(args, "expose", False)),
        view=str(getattr(args, "face_view", "desk") or "desk"),
    )
