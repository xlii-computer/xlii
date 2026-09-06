"""The console/renderer SEAM between kernel and face (godzilla-mothra V1ab).

This module is the canonical import for kernel and cmd code that needs the
shared UI primitives: the one rich ``Console`` singleton, the destructive-y/N
``confirm`` gate, the ``format_turn_line`` turn-cost footer, the module-level
``renderer`` (the one print path), and the ``Renderer`` class for code that
binds its own Console. The objects are constructed face-side in ``xlii.tui``
(the rich terminal face); kernel modules import them FROM HERE, never from
``xlii.tui`` — faces import kernel, never the reverse.

``set_panel_host`` is the same seam for the panel host registry (``/panel``,
doorways): the face server installs :class:`~xlii.face_panes.FacePanelHost`
here; the TUI installs :class:`~xlii.tui.panels.AppPanelHost`.

``xlii.ui -> xlii.tui`` is the single documented boundary edge and stays
baselined in the import contract on purpose: this file IS the seam. New code
should emit typed events (``xlii.turn_events``) through ``renderer`` rather
than printing ad hoc.
"""

from __future__ import annotations

from xlii.tui import Renderer, confirm, console, format_turn_line, renderer
from xlii.tui.panels import set_panel_host

__all__ = [
    "console",
    "confirm",
    "format_turn_line",
    "renderer",
    "Renderer",
    "set_panel_host",
]
