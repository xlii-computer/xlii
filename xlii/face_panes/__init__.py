"""Panes on the face (typed-workbenches.md, phase B1) — the face's pane deck.

The active workbench type's pane set, mounted headless and projected onto the
wire as ``pane_deck`` snapshots; client ``pane_action`` ops (select / key /
action) come back in and are executed against the same headless
:class:`~xlii.panes.dock.Dock` the TUI projects — one layout engine, two
surfaces, no reimplementation. Re-projection IS the refresh (the pane
state-ownership rule): after any op or turn, the deck re-renders and re-sends.

Sinks route the bounded outcomes to the face's own channels:

- ``ENQUEUE_TURN`` → :meth:`FaceServer.submit` — the ONE input queue, so a
  pane's turn echoes, runs, and persists exactly like a typed one (the TUI's
  AppTurnSink rule: never drive the Conversation directly).
- ``PREFILL`` → the ``prefill`` wire event — wired since protocol 2, first
  emitted here (pane action → seed the input box, review-before-run).
- ``SHOW_MEDIA`` → a ``file_out`` event (images inline as b64, the outbox
  drain's shape).
- ``ATTACH``/``DETACH`` → :mod:`xlii.attach` on the live REPLState.
- ``SPAWN_JOB`` → ``spawn_saved_task`` (tasks://), same path as the TUI.
- ``CLAIM_INPUT`` → refused with a meta note (the face input box can't morph
  yet — a later B-phase); never silently swallowed.
- ``RETARGET_SLOT`` ("open in other pane") → the face visual slots (stream /
  pane) are occupancy of *views*; a text/image leaf still projects to the
  feed. A ``canvas://`` address opens the canvas slot as the focused work
  (one image or PDF, rendered). A PDF leaf opens the sticky ``pdf`` viewer
  in the other slot (listing | PDF). A maker address (``taskmake://`` /
  ``pluginmake://``) opens a closed HTML form in the other slot (listing |
  form, or stream parked skinny). A container remounts the same dock slot
  (not a third split).

The deck exists when the active workbench row (``state.workbench``, B0)
names a pane set. Slot dropdown + Keep menu list that pack (plus stream), not
every known pane kind.
"""

from __future__ import annotations

from .deck import FaceDeck
from .host import FacePanelHost
from .sinks import (
    _FailedPane,
    _FaceInputSink,
    _FaceJobSink,
    _FaceMediaSink,
    _FaceSessionSink,
    _FaceTurnSink,
)
from .views import (
    FACE_PANE_LABELS,
    PANEL_ORDER,
    STREAM_VIEW,
    _CAPTION,
    _HTML_SLOT_PANES,
    _IMAGE_SUFFIXES,
    _MAX_INLINE_IMAGE_BYTES,
    _MAX_ROWS,
    _PANEL_RANK,
    _SCHEME_TO_PANE,
    _SUBPATH_SCHEMES,
    _TURN_CONTEXT_CAP,
    _VIEW_TO_PANE,
    _ordered_view_ids,
    _parent_address,
    _refresh,
    _root_addresses,
    _with_context,
    is_stream_view,
)

__all__ = [
    "FACE_PANE_LABELS",
    "FaceDeck",
    "FacePanelHost",
    "PANEL_ORDER",
    "STREAM_VIEW",
    "_CAPTION",
    "_FailedPane",
    "_FaceInputSink",
    "_FaceJobSink",
    "_FaceMediaSink",
    "_FaceSessionSink",
    "_FaceTurnSink",
    "_HTML_SLOT_PANES",
    "_IMAGE_SUFFIXES",
    "_MAX_INLINE_IMAGE_BYTES",
    "_MAX_ROWS",
    "_PANEL_RANK",
    "_SCHEME_TO_PANE",
    "_SUBPATH_SCHEMES",
    "_TURN_CONTEXT_CAP",
    "_VIEW_TO_PANE",
    "_ordered_view_ids",
    "_parent_address",
    "_refresh",
    "_root_addresses",
    "_with_context",
    "is_stream_view",
]
