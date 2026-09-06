"""The ambient active session — a module-global pointer to the live ``REPLState``.

Stateless address providers (``mark://``, ``jobs://``, ``locker://``) are resolved by
``open_in_dock`` with only an :class:`~xlii.addressing.Address` — no session in hand. But
listing marks needs the active profile's ``turns_dir``; listing jobs needs its job registry;
the locker is the session's ``attached_files``. This module is the seam they read through: the
surface that *owns* a session (the TUI on mount, the REPL loop) installs it here, and providers
read it defensively.

Exactly one session is "active" at a time (single working window), mirroring
:func:`xlii.jobs.set_job_listener`'s module-global. Nothing here imports the REPL — the state is
duck-typed via ``getattr`` so a fake/SimpleNamespace context works in tests and a bare resolve
outside a session degrades to empty rather than raising.
"""

from __future__ import annotations

from typing import Any, Optional

_ACTIVE: "Optional[Any]" = None


def set_active_session(state: "Optional[Any]") -> "Optional[Any]":
    """Install (or clear, with ``None``) the ambient session; returns the previous one so a
    caller can restore it on teardown. Idempotent."""
    global _ACTIVE
    prev = _ACTIVE
    _ACTIVE = state
    return prev


def active_session() -> "Optional[Any]":
    """The ambient session state, or ``None`` outside a live session."""
    return _ACTIVE


def turns_dir_of(state):
    """The memory ``turns_dir`` for a *given* state (any mode), falling back to a legacy
    ``state.persona``. ``None`` for a stateless/None context. Pure over ``state`` so the status
    surface (which holds the live state) and the providers (which read the ambient one) share one
    derivation. Mirrors ``repl_cmds.chat._active_turns_dir`` — kept standalone so providers don't
    import the REPL tier."""
    if state is None:
        return None
    mem = getattr(getattr(state, "profile", None), "memory", None)
    td = getattr(mem, "turns_dir", None)
    if td is not None:
        return td
    return getattr(getattr(state, "persona", None), "turns_dir", None)


def active_turns_dir():
    """The ambient session's memory ``turns_dir``, or ``None`` outside a session."""
    return turns_dir_of(_ACTIVE)


def xli_dir_of(state):
    """The project ``.xlii`` dir for a *given* state, or ``None`` outside a project. Pure over
    ``state`` (duck-typed), mirroring :func:`turns_dir_of` — the wiki store and any future
    project-scoped provider share one derivation."""
    if state is None:
        return None
    d = getattr(getattr(state, "project", None), "xli_dir", None)
    if d is None:
        return None
    from pathlib import Path

    return Path(str(d))


def active_xli_dir():
    """The ambient session's project ``.xlii`` dir, or ``None`` outside a session/project."""
    return xli_dir_of(_ACTIVE)


def cwd_of(state):
    """The working directory for a *given* state — the live shell ``cwd`` if set, else the
    project root. ``None`` for a stateless/None context. Pure over ``state`` (duck-typed), the
    seam the git provider/pane read to root themselves on the directory the user is actually in
    (so ``cd`` inside a subrepo is honored). Imports nothing heavy so registration stays cheap."""
    if state is None:
        return None
    from pathlib import Path

    cwd = getattr(state, "shell_cwd", None)
    if cwd is not None:
        return Path(str(cwd))
    root = getattr(getattr(state, "project", None), "project_root", None)
    return Path(str(root)) if root is not None else None


def active_cwd():
    """The ambient session's working directory (live shell cwd, else project root), or ``None``
    outside a session. The git provider/pane resolve the repo from this."""
    return cwd_of(_ACTIVE)


def active_registry():
    """The ambient session's :class:`~xlii.jobs.JobRegistry` (created on first use), or ``None``
    outside a session / for a context with no ``job_registry`` slot."""
    state = _ACTIVE
    if state is None:
        return None
    from xlii.jobs import get_registry

    return get_registry(state)


def active_cfg():
    """The ambient session's ``GlobalConfig``, or ``None`` outside a session.

    ``/edit`` and desk resolvers read this so Config → editor (pluma, …) wins
    over ``$EDITOR``. ``state.cfg`` first; ``agent.cfg`` if those ever diverge.
    """
    state = _ACTIVE
    if state is None:
        return None
    cfg = getattr(state, "cfg", None)
    if cfg is not None:
        return cfg
    return getattr(getattr(state, "agent", None), "cfg", None)
