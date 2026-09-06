"""`xlii serve --ws` — JSON event protocol over WebSocket (browser-ui W2).

CLI façade: argparse wiring (``cmd_serve_ws``) plus back-compat re-exports.
The server (RFC6455 codec / upgrade / accept loop) lives in the kernel at
``xlii/ws_server.py``, the turn engine at ``xlii/agent_dispatch.py``
(``run_headless_turn``), the confirm swap at ``xlii/tools.py``
(``confirm_override`` / ``auto_deny``), and the event serialization at
``xlii/ws_protocol.py`` — godzilla-mothra B5. Stdlib WebSocket only.

Spec: docs/ws-event-protocol.md
"""

from __future__ import annotations

import contextlib
import sys

from xlii.agent_dispatch import run_headless_turn
from xlii.tools import _CONFIRM_SWAP_LOCK, auto_deny, confirm_override
from xlii.ws_server import (
    _accept_key,
    _parse_client_message,
    _read_frame,
    _send_text,
    serve_ws,
)

__all__ = [
    "_CONFIRM_SWAP_LOCK",
    "_accept_key",
    "_parse_client_message",
    "_read_frame",
    "_send_text",
    "_ws_auto_deny",
    "_ws_confirm_swap",
    "cmd_serve_ws",
    "run_ws_turn",
    "serve_ws",
]

# Back-compat aliases for the pre-relocation import paths (tests, embedders).
run_ws_turn = run_headless_turn
_ws_auto_deny = auto_deny


@contextlib.contextmanager
def _ws_confirm_swap():
    """Hold ``xlii.tools._confirm`` at ``auto_deny`` for one WS turn.

    Kept for the old import path; new code uses ``tools.confirm_override``.
    """
    with confirm_override(auto_deny):
        yield


def cmd_serve_ws(args) -> int:
    from xlii.cmds.sessions.resolve import _resolve_project_target
    from xlii.config import ProjectConfig

    target = _resolve_project_target(getattr(args, "workspace", None))
    if target is None:
        print("serve-ws: could not resolve workspace", file=sys.stderr)
        return 1
    project = ProjectConfig.load(target.resolve())
    if not project:
        print(f"serve-ws: not an xlii project: {target}", file=sys.stderr)
        return 1

    port = args.port
    if getattr(args, "handshake", False):
        port = 0
    elif port == 0:
        # Ephemeral bind without handshake is fine for tests.
        pass

    return serve_ws(
        project,
        host=args.host,
        port=port,
        token=getattr(args, "token", None),
        handshake=getattr(args, "handshake", False),
        yolo=getattr(args, "yolo", False),
        expose=bool(getattr(args, "expose", False)),
    )
