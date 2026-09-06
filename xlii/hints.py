"""Input-hint registry — the one source for the input box's placeholder text.

The Textual `--tui` input used to carry a flat per-mode placeholder dict
(`_PLACEHOLDERS` in `tui_textual`). This promotes it to a small registry that
**three sources feed**, mirroring the interaction layer's "be client #1 of a
public seam" shape so a third party can add a hint the same way the built-ins do:

  • **built-ins** — the stock mode → hint contract (code / chat / plan / howto);
  • **roles** — a role descriptor's ``hint:`` key (seam #3): while a role is
    *equipped in code* its hint leads, so the input honestly reflects which role
    is riding the session;
  • **plugins / third parties** — :func:`register_hint` is the public seam:
    anyone can contribute (or override) a mode hint, occupying the same registry
    the built-ins do.

Pure functions over REPLState (defensive ``getattr``), with no Textual /
prompt_toolkit import at module scope, so either front-end can read it.
:func:`resolve_hint` is the single entry the input chrome calls.
"""

from __future__ import annotations

from typing import Optional

# Built-in per-mode hints — each is the mode's *bare-input contract*, no longer
# led by the mode word. The profile bar, one row below the input, carries the
# live mode word (tense-chrome); the placeholder stays the bare-input contract
# alone. Lab (code / scratch) is shell-primary: bare input runs; `?` is the
# worker (edits, bash) — not ixaac. Chat / plan / howto route bare input to talk.
BUILTIN_HINTS: dict[str, str] = {
    "code": "! to run · ? for the worker · / for commands · /exit to leave",
    "chat": "type to ask · ! to run · / for commands · /exit to leave",
    "plan": "describe the goal · plan writes → .xlii/plans/ · /execute · /cancel",
    "howto": "ask how xlii works · / for commands · /off",
    "ops": "investigate · /off",
    # scratch (Vector S): an ephemeral, never-sync, free-traversal session. It's a
    # shell-primary surface like `code` (bare input runs), so the contract mirrors
    # code's but leads with the never-sync nature and points at /scratch off.
    "scratch": "never-sync · ! to run · ? for the worker · /scratch off to leave",
}

# The conversational fallback: a surface that talks to the AI by default but has
# no specific hint (an unrecognised conversational mode, or a `code` surface
# with shell-primary off so even bare input asks).
TALK_HINT = BUILTIN_HINTS["chat"]

# Third-party / plugin registrations, keyed by mode word. Consulted *before* the
# built-ins so an extension can override a stock hint for its own mode — the
# public seam through which plugins and built-ins feed one registry.
_REGISTERED: dict[str, str] = {}


def register_hint(mode: str, text: str) -> None:
    """Register (or override) the input hint for a mode word.

    The public seam a plugin / third party uses to teach the input box about a
    mode it introduces — the same registry the built-ins occupy. A registered
    hint wins over the built-in for that mode word."""
    _REGISTERED[str(mode)] = str(text)


def unregister_hint(mode: str) -> None:
    """Drop a previously-registered hint (no-op if absent). For plugin teardown
    and test isolation."""
    _REGISTERED.pop(str(mode), None)


def registered_hints() -> dict[str, str]:
    """A copy of the live third-party registrations (introspection / tests)."""
    return dict(_REGISTERED)


def mode_hint(key: str, *, shell_primary: bool = True) -> str:
    """The hint for a bare mode word: a registered (plugin) hint wins, else the
    built-in, else the conversational fallback.

    A `code` surface whose shell-primary is OFF routes bare input to the AI, so
    it borrows the talk hint rather than the run-it-yourself one (mirrors the
    inline REPL's bare-input rule)."""
    if key == "code" and not shell_primary:
        return _REGISTERED.get("chat") or BUILTIN_HINTS["chat"]
    return _REGISTERED.get(key) or BUILTIN_HINTS.get(key) or TALK_HINT


def _hinting_role(state) -> Optional[str]:
    """The active role name *iff* it should re-skin the input hint — i.e. a role
    equipped in code. Silent in chat-`become` (there the role IS the persona and
    its name already leads the bar), matching `status.role()`'s rule, so the
    input hint never reads `role:x · x`."""
    name = getattr(state, "active_role", None)
    if not name:
        return None
    persona = getattr(state, "persona", None)
    if persona is not None and getattr(persona, "name", None) == name:
        return None
    return name


def _role_hint_text(state, name: str) -> Optional[str]:
    """The equipped role's ``hint:`` (seam #3), or None when it declares none /
    can't be loaded. Best-effort: a missing or malformed descriptor never breaks
    the input."""
    try:
        from xlii.role import load_role

        root = getattr(getattr(state, "project", None), "project_root", None)
        role = load_role(name, root)
    except Exception:
        return None
    if role is None:
        return None
    try:
        return role.hint() or None
    except Exception:
        return None


def resolve_hint(state, *, shell_primary: bool = True) -> str:
    """The input placeholder for the live session — the single entry the input
    chrome calls.

    Mode-aware (via ``status.placeholder_key``) and role-aware: while a role is
    equipped in code its ``hint:`` leads when it declares one (the honest
    "operating as role X" surface). A role *without* a custom hint just gets the
    plain mode hint — the frame's role tab (status.frame_tabs) already announces
    the role doc riding the session, so the placeholder no longer repeats
    ``role:<name>`` and stays lean. Otherwise the mode hint — plugin-registered,
    then built-in, then fallback."""
    try:
        from xlii.status import placeholder_key

        key = placeholder_key(state)
    except Exception:
        key = "code"
    base = mode_hint(key, shell_primary=shell_primary)

    name = _hinting_role(state)
    if name:
        custom = _role_hint_text(state, name)
        if custom:
            return custom
    return base
