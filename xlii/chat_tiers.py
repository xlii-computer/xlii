"""Chat tiers — a per-turn reasoning-depth selector for chat mode (chat-tiers R1).

A chat tier overlays the ``chat`` model role with a reasoning-depth choice —
``fast`` / ``expert`` / ``heavy`` — plus ``auto`` (a router picks one). Each
concrete tier maps to a model profile (reusing :mod:`xlii.model_profiles`) and a
turn *strategy*.

Unlike a :class:`~xlii.mode_controller.ModeController`, a tier never gates
write-scope or the tool palette — it only steers (1) which model the chat slot
resolves to and (2), for ``heavy``, which turn executor runs. That makes it the
same lightweight session-overlay family as ``conversational`` / ``howto_mode``,
which :func:`xlii.agent.resolve_orchestrator_model` already keys off.

``chat_tier is None`` reproduces today's behavior exactly (plain ``chat`` role),
so the machinery is a no-op until a user opts in via ``/tier``.

Vector boundaries (see ``proposals/chat-tiers.md``): this module is Vector A. The
``auto`` router is Vector B (it replaces :func:`_route_auto` here) and the
``deep-search`` executor is Vector C (it consumes :data:`STRATEGY_DEEP_SEARCH`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# Turn strategies — how the tier's turn actually runs.
STRATEGY_SINGLE = "single"          # one normal agent turn
STRATEGY_DEEP_SEARCH = "deep-search"  # coordinator → fan-out → synthesize (Vector C)

AUTO = "auto"


@dataclass(frozen=True)
class ChatTier:
    """One concrete chat tier — a model profile + a turn strategy + display."""

    name: str
    profile: str      # xlii.model_profiles key supplying the chat model
    strategy: str     # STRATEGY_SINGLE | STRATEGY_DEEP_SEARCH
    label: str        # short display label (with glyph)
    blurb: str        # one-line description for /tier listing


# The three concrete tiers. ``auto`` is deliberately NOT here — it is resolved by
# the router (:func:`_route_auto`) to one of these before model resolution runs.
CONCRETE_TIERS: dict[str, ChatTier] = {
    "fast": ChatTier(
        "fast", "economy", STRATEGY_SINGLE,
        "⚡ fast", "quick answers on the cheap fast model",
    ),
    "expert": ChatTier(
        "expert", "reason", STRATEGY_SINGLE,
        "🎓 expert", "deeper reasoning on the reasoning model",
    ),
    "heavy": ChatTier(
        "heavy", "reason", STRATEGY_DEEP_SEARCH,
        "🐘 heavy", "coordinated deep search — fans out, cites sources",
    ),
}

# Everything a user may type at ``/tier`` (``auto`` included).
VALID_TIERS: tuple[str, ...] = tuple(CONCRETE_TIERS) + (AUTO,)

# Per-message tier sigil (Vector D): ``>>heavy what's new in …`` steers ONE
# message without touching the sticky ``/tier`` pick. ``>>`` joins the existing
# sigil vocabulary (``!`` shell · ``?`` ask · ``?>`` post-process) — it reads as
# "crank it up" and collides with nothing the input routers dispatch on.
TIER_SIGIL = ">>"

# One-letter shorthands: ``>>f`` ``>>e`` ``>>h`` ``>>a``.
_SIGIL_SHORTHAND = {"f": "fast", "e": "expert", "h": "heavy", "a": "auto"}


def split_tier_sigil(text: str) -> tuple[Optional[str], str]:
    """Split a leading ``>>tier`` sigil off a chat message.

    Returns ``(tier, remainder)`` when *text* opens with ``>>`` + a tier name
    (full or one-letter shorthand, whitespace after ``>>`` tolerated) followed
    by a non-empty message. Anything else — including a bare ``>>heavy`` with
    no message (the sigil is one-shot steering, not a ``/tier`` synonym) and a
    ``>>`` that isn't a tier (``>>file.txt``) — returns ``(None, text)``
    untouched, so a false positive can never eat a user's words.
    """
    if not text.startswith(TIER_SIGIL):
        return None, text
    parts = text[len(TIER_SIGIL):].split(None, 1)
    if len(parts) < 2:
        return None, text
    key = _SIGIL_SHORTHAND.get(parts[0].lower(), parts[0].lower())
    if key not in VALID_TIERS:
        return None, text
    remainder = parts[1].strip()
    if not remainder:
        return None, text
    return key, remainder


def session_chat_tier(state: Any) -> Optional[str]:
    """The sticky ``/tier`` pick on a live desk (agent.session or state)."""
    if state is None:
        return None
    for obj in (state, getattr(state, "agent", None)):
        if obj is None:
            continue
        sess = getattr(obj, "session", None)
        if sess is not None:
            t = getattr(sess, "chat_tier", None)
            if t is not None:
                return t
        t = getattr(obj, "chat_tier", None)
        if t is not None:
            return t
    return None


def normalize_tier(name: Optional[str]) -> Optional[str]:
    """Lower/strip a tier name; return it if valid, else ``None``.

    ``None`` in → ``None`` out (no tier set). An unknown string → ``None`` so
    callers fall back to the plain ``chat`` role rather than crash.
    """
    if name is None:
        return None
    key = name.strip().lower()
    return key if key in VALID_TIERS else None


def _route_auto(user_message: Optional[str], cfg: Any) -> str:
    """Resolve ``auto`` to a concrete tier name.

    Delegates to the Vector B router (:mod:`xlii.chat_router`): Layer-1
    heuristics pick a confident tier from *user_message*, and the ambiguous tail
    defaults to ``expert`` (which self-escalates to ``heavy`` when it needs fresh
    data). With no message (e.g. a status-line probe) it resolves to ``expert``.
    """
    from xlii.chat_router import route

    return route(user_message, cfg=cfg)


def resolve_tier(
    name: Optional[str],
    *,
    user_message: Optional[str] = None,
    cfg: Any = None,
) -> Optional[ChatTier]:
    """Resolve a tier name to a concrete :class:`ChatTier`.

    ``auto`` routes to a concrete tier via :func:`_route_auto`. Returns ``None``
    when no tier is set or the name is unknown — the caller keeps the existing
    plain ``chat`` role behavior.
    """
    key = normalize_tier(name)
    if key is None:
        return None
    if key == AUTO:
        key = _route_auto(user_message, cfg)
    return CONCRETE_TIERS.get(key)


def resolve_tier_model(
    cfg: Any,
    name: Optional[str],
    *,
    user_message: Optional[str] = None,
) -> Optional[str]:
    """The chat-slot model id a tier resolves to, via its model profile.

    Returns ``None`` when no tier is set, the name is unknown, or the tier's
    profile can't be resolved — the caller falls back to
    ``cfg.get_model_for_role("chat")``.
    """
    tier = resolve_tier(name, user_message=user_message, cfg=cfg)
    if tier is None:
        return None
    from xlii.model_profiles import get_model_profile

    try:
        prof = get_model_profile(cfg, tier.profile)
    except KeyError:
        return None
    return prof.get("chat") or None


def tier_strategy(name: Optional[str], *, user_message: Optional[str] = None,
                  cfg: Any = None) -> str:
    """The turn strategy for a tier name (``single`` when unset/unknown).

    Vector C reads this to decide whether to run ``run_deep_search`` before the
    normal synthesis turn.
    """
    tier = resolve_tier(name, user_message=user_message, cfg=cfg)
    return tier.strategy if tier is not None else STRATEGY_SINGLE
