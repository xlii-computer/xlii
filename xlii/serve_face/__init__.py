"""`xlii serve --face` — the persistent-REPL face server (protocol v2).

The backend of the desktop/browser face: ONE live code session per process
(assembled by ``session_boot.build_code_session``, exactly what the inline REPL
and TUI run on), projected over the W2 WebSocket wire. A connection is a VIEW
over the session, not a session of its own — reconnecting resumes where you
were. Contrast ``serve --ws`` (headless one-shot turns, fresh agent per turn,
confirms auto-denied): the face is client #2 of the real REPL — slash commands,
shell lines, modes, and a live approve/deny channel.

Routing has two POSTURES (the face's ``[$]``/``[M]`` flipmode button):

- ``chat`` (``[M]``, the default — the face is mojo-first): every line is a
  persona turn via ``run_persona_oneshot`` — the DEFAULT persona on its own
  turn store (the same memory the phone daemon writes), fused with this
  project's journal+wiki (``build_mojo_ambient``). The phone model on the
  desktop: per-turn one-shot, the persona turn store is the continuity.
- ``code`` (``[$]``): the full REPL — every line goes through
  ``repl.process_repl_input`` (slash → bang → shell/agent by mode), agent
  turns through ``conversation.drive_turn``. Never a reimplementation of the
  routing (the documented face regression trap).

Also answers plain HTTP ``GET /`` + ``GET /assets/*`` from the bundled face
assets so a bare browser can render the face without Tauri — the ASSETS are
ungated; the WebSocket (the shell) requires the ``?token=``.

Wire contract: docs/ws-event-protocol.md (protocol 2). Kernel-tier, stdlib +
rich only; imports no face packages.
"""

from xlii.serve_face.http import serve_face as serve_face
from xlii.serve_face.server import FaceServer as FaceServer
from xlii.serve_face.wire import (
    CONFIRM_TIMEOUT_S as CONFIRM_TIMEOUT_S,
    MAX_INLINE_IMAGE_BYTES as MAX_INLINE_IMAGE_BYTES,
    MAX_UPLOAD_BYTES as MAX_UPLOAD_BYTES,
    WireRenderer as WireRenderer,
    _ConfirmBridge as _ConfirmBridge,
    _WireFile as _WireFile,
    _is_scratch_desk_root as _is_scratch_desk_root,
    classify_user_kind as classify_user_kind,
    default_assets_dir as default_assets_dir,
    infer_meta_level as infer_meta_level,
)
