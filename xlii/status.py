"""Session status readers — the single source for `mode · cwd · attachments`.

These are the prompt_toolkit-free readers the inline toolbar already used; they
live here so BOTH front-ends share one truth: the inline bottom toolbar skins
them in prompt_toolkit FormattedText ([input_chrome.py]), and the Textual `--tui`
renders `profile_bar()` (the RP3 `mode · id · loadout · model · meter` strip) as
a Rich strip. Pure reads of REPLState (defensive getattr), so a partial/fake
state never raises.

Leaf module: no prompt_toolkit, no rich — palette tokens and plain segment
strings only; Rich assembly lives in ``xlii.tui.status.profile_bar``.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

# Rich style per mode (distinct from input_chrome's prompt_toolkit color syntax).
# Face code maps these tokens to Rich markup; kernel stays rich-free.
_MODE_RICH = {
    "SHELL": "bold green",
    "PLAN": "bold yellow",
    "DISC": "bold cyan",
    "OPS": "bold green",
    "CHAT": "bold cyan",
    # ANSI `blue` (#0000ff) is near-unreadable on a dark bg, and `bright_blue`
    # parses in Rich but NOT in Textual (the frame border feeds this color in
    # verbatim). A hex is the one spelling legible AND valid on both surfaces.
    "HOWTO": "bold #5f9bff",
    "IMAGE": "bold magenta",
    "YOLO": "bold red",
    # Freeball (the-fold Vector C): the trusted-run tier ABOVE yolo — LOUDER than
    # yolo's red text on purpose. A filled red pill (white on red): unmissable, and
    # white/red are basic ANSI so it's legible AND valid on both Rich and Textual.
    # Used only by _affordance_flag for the profile-bar pill — NOT wired into
    # _FRAME_COLOR_KEY, so it never becomes a frame-border color.
    "FREEBALL": "bold white on red",
    "RAIL": "bold magenta",
    "DEBUG": "bold cyan",
    # scratch (Vector S): the ephemeral, never-sync mode. A warm tan — distinct
    # from every base/overlay tone above — spelled as a hex (like HOWTO) so it's
    # legible on a dark bg AND valid verbatim as a Textual frame-border color
    # (frame_mode feeds the bare token straight into the border).
    "SCRATCH": "bold #d19a66",
}

# Per-harness palette (seam #5 — the day-1 stub A1 ships; Vector C wires the
# state + HarnessSpec fields that select it). When a harness runs in the
# foreground it becomes the live input mode — `cursor` · `claude code` ·
# `grok build` · `codex` — so the frame/bar tint with the harness's own tone
# instead of a base-mode color. Keyed by HarnessSpec.name (detect.py). Each is a
# hex so it's legible on a dark bg AND valid as a Textual border color (the bare
# token feeds the frame, exactly like `_MODE_RICH`). An unknown community harness
# falls back to a neutral so a third-party harness mode is never invisible.
_HARNESS_RICH = {
    "cursor": "bold #7c9eff",   # cursor — indigo/blue
    "claude": "bold #d97757",   # claude code — terracotta
    "grok": "bold #b18aff",     # grok build — violet
    "codex": "bold #19c37d",    # codex — green
}
_HARNESS_FALLBACK = "bold white"


def _harness_foreground(state) -> tuple[str, str] | None:
    """`(label, name)` for the foreground harness, or None (seam #5 read).

    Vector C sets ``state.harness_foreground`` when a harness flips into a REPL
    mode (`/cursor on`, …); A1 only *reads* it, exactly like ``howto_mode`` — a
    pure forward hook that stays dormant until C lands. Tolerates either a
    ``HarnessSpec``-shaped object (``.name`` + C's new ``.label``) or a bare name
    string, and never raises on a partial/fake state."""
    fg = getattr(state, "harness_foreground", None)
    if not fg:
        return None
    if isinstance(fg, str):
        name = fg.strip()
        return (name, name) if name else None
    name = (getattr(fg, "name", None) or "").strip()
    label = (getattr(fg, "label", None) or name).strip()
    if not label:
        return None
    return (label, name)


def _active_mode_tag(agent) -> tuple[str, str] | None:
    """Status tag from the agent's unified mode slot, if any."""
    active = getattr(agent, "active_mode", None)
    if active is None:
        return None
    return active.status_tag()


def overlay_word(state) -> str:
    """Faux-mode that owns the face flip: howto / ops / plan / … or ``""``.

    Talk/lab (``?``/``$``) is the base switch. An /off-clearable overlay
    replaces that chip with ``<name> on/off`` so entering howto or ops
    *feels* like a mode, not leftover lab chrome.
    """
    if bool(getattr(state, "howto_mode", False)):
        return "howto"
    sess = getattr(getattr(state, "agent", None), "session", None)
    if sess is not None and bool(getattr(sess, "howto_mode", False)):
        return "howto"
    ex = exclusive_mode(state)
    if ex and ex != "—":
        return {"discovery": "disc"}.get(ex, ex)
    fg = _harness_foreground(state)
    if fg is not None:
        return (fg[1] or fg[0] or "ext")[:6]
    return ""


def exclusive_mode(state) -> str:
    """Single exclusive *work-mode* slot: plan|rail|debug|discovery|ops|— .

    Overlays (howto/image), trust (yolo/freeball), and surface (code/chat) are
    separate axes — see :func:`trust_axis` and :func:`surface_axis`. Grades plan
    Phase 1: one glance answers "what mode am I in?" without flag soup.
    """
    tag = _active_mode_tag(getattr(state, "agent", None))
    if tag is None:
        return "—"
    _label, key = tag
    return {
        "PLAN": "plan",
        "RAIL": "rail",
        "DEBUG": "debug",
        "DISC": "discovery",
        "OPS": "ops",
    }.get(key, (key or "—").lower())


def trust_axis(state) -> str:
    """Trust ladder: freeball > yolo > safe."""
    if getattr(state, "freeball", False):
        return "freeball"
    if getattr(state, "yolo", False):
        return "yolo"
    return "safe"


def surface_axis(state) -> str:
    """Base surface: scratch|chat|code (overlays do not change surface)."""
    if getattr(state, "scratch", False):
        return "scratch"
    if getattr(state, "persona", None) is not None:
        return "chat"
    prof = getattr(state, "profile", None)
    mode = getattr(prof, "mode", None) if prof is not None else None
    if mode in ("chat", "code", "scratch"):
        return mode
    return "code"


def primary_axes(state) -> tuple[str, str, str]:
    """``(exclusive_mode, trust, surface)`` — the three primary status axes."""
    return exclusive_mode(state), trust_axis(state), surface_axis(state)


def format_primary_axes(state) -> str:
    """Plain one-liner for /status: ``mode · trust · surface``."""
    m, t, s = primary_axes(state)
    return f"mode: {m}   trust: {t}   surface: {s}"


def _affordance_from_mode_tag(tag: tuple[str, str]) -> tuple[str, str]:
    """Map a controller status_tag to the profile-bar affordance segment."""
    label, key = tag
    style = _MODE_RICH.get(key, "")
    if key == "RAIL":
        return (label.replace("RAIL ", "rail ", 1), style)
    if key == "DEBUG":
        return (label.replace("DEBUG ", "debug ", 1), style)
    if key == "PLAN":
        return (label.lower(), style)
    return (label.lower(), style)


def profile_mode_tag(agent) -> str:
    """Bracketed tag for the code prompt prefix: [rail 2/5], [plan], [debug 0/5]."""
    tag = _active_mode_tag(agent)
    if tag is None:
        return ""
    _label, key = tag
    if key == "PLAN":
        return "[plan]"
    if key == "DISC":
        return "[disc]"
    if key == "RAIL":
        from xlii.rail import LAST_STAGE

        rail = getattr(agent, "rail", None)
        if rail is None:
            return ""
        return f"[rail {rail.current_stage.value}/{LAST_STAGE.value}]"
    if key == "DEBUG":
        from xlii.debug_mode import LAST_PHASE

        dbg = getattr(agent, "debug", None)
        if dbg is None:
            return ""
        return f"[debug {dbg.current_phase.value}/{LAST_PHASE.value}]"
    return ""


def mode(state) -> tuple[str, str]:
    """(label, mode-key) for the current input mode — gated mode wins, then
    howto, then chat (persona present), then yolo, else shell-primary."""
    tag = _active_mode_tag(getattr(state, "agent", None))
    if tag is not None:
        return tag
    if getattr(state, "howto_mode", False):
        return ("HOWTO", "HOWTO")
    # scratch is a base surface (a never-sync flavor of shell-primary code), so it
    # leads once the howto/image overlays are clear — a scratch session has no
    # persona, so it precedes the chat/yolo/shell fallbacks below.
    if getattr(state, "scratch", False):
        return ("SCRATCH", "SCRATCH")
    if getattr(state, "persona", None) is not None:
        tier = chat_tier(state)
        return (f"CHAT · {tier}" if tier else "CHAT", "CHAT")
    # Freeball (the-fold Vector C) sets yolo True too, so check it FIRST — else the
    # louder trusted-run tier would hide behind the plain yolo label.
    if getattr(state, "freeball", False):
        return ("FREEBALL", "FREEBALL")
    if getattr(state, "yolo", False):
        return ("YOLO", "YOLO")
    return ("SHELL", "SHELL")


def _has_foreground_harness(state) -> bool:
    """True when a foreground harness (cursor/claude/grok-build/codex) is active —
    the same signal /off's `_clear_harness` reads. (Distinct from the tuple-typed
    `_harness_foreground` above, which frame_mode uses for its label/color.)"""
    reg = getattr(state, "cursor_sessions", None)
    return reg is not None and getattr(reg, "foreground", None) is not None


def exit_hint(state) -> str:
    """The one-line "how do I get out of here" pointer for the input chrome — the
    discoverability fix for "I didn't even know I was in a mode."

    An /off-clearable overlay/mode is active → point at `/off`; else a persona
    (chat) surface → point at `/code`; empty on the plain code/scratch base
    surface (nothing to leave). Reads exactly the signals `/off` itself clears
    (xlii/repl_cmds/off.py), so the hint can never promise an exit `/off` won't
    perform."""
    agent = getattr(state, "agent", None)
    overlay = (
        _active_mode_tag(agent) is not None          # plan/rail/debug/discovery/ops
        or getattr(state, "howto_mode", False)       # howto overlay
        or _has_foreground_harness(state)            # cursor/claude/... harness
    )
    if overlay:
        return "/off to exit"
    # A chat/persona surface isn't an overlay (/off won't leave it) — the base
    # switch back is /code, so point there instead.
    if getattr(state, "persona", None) is not None:
        return "/code for code"
    return ""


def cwd(state) -> str:
    try:
        from xlii.repl import format_shell_cwd
        shown = format_shell_cwd(state)
        if shown:
            return shown
    except Exception:
        # Fall through to the project-root rendering below.
        pass
    return getattr(getattr(state, "project", None), "name", "") or ""


def _next_turn_model(state) -> Optional[str]:
    """Orchestrator model the next turn will use (override, else resolved slot)."""
    agent = getattr(state, "agent", None)
    cfg = getattr(state, "cfg", None)
    model = getattr(agent, "model_override", None) if agent else None
    if not model and agent is not None and cfg is not None:
        try:
            from xlii.agent import resolve_orchestrator_model

            session = getattr(agent, "session", None)
            model, _ = resolve_orchestrator_model(
                cfg=cfg,
                model_override=None,
                conversational=getattr(session, "conversational", False),
                howto_mode=getattr(state, "howto_mode", False),
            )
        except Exception:
            model = None
    return model


def attachments(state) -> str:
    refs = getattr(state, "attached_refs", None) or []
    docs = getattr(state, "attached_docs", None) or []
    files = getattr(state, "attached_files", None) or []
    bits = []
    if refs:
        bits.append(f"refs:{len(refs)}")
    if docs:
        bits.append(f"docs:{len(docs)}")
    enabled_imgs = sum(
        1 for e in files if e.get("kind") == "image" and e.get("enabled")
    )
    if files:
        bits.append(f"locker:{sum(1 for e in files if e.get('enabled'))}/{len(files)}")
    if enabled_imgs:
        from xlii.multimodal import model_supports_vision
        model = _next_turn_model(state)
        if not model_supports_vision(model):
            bits.append("img:!vision")
    return " ".join(bits)


def _affordance_flag(state) -> tuple[str, str]:
    """(label, rich-style) for an active code affordance — gated mode > yolo —
    or ('','') when none. Preserves the rail/plan/debug/yolo signal the profile
    bar's flat 'code'/'chat' mode segment doesn't carry on its own."""
    tag = _active_mode_tag(getattr(state, "agent", None))
    if tag is not None:
        return _affordance_from_mode_tag(tag)
    # Freeball is the tier above yolo and sets yolo True too — check it FIRST so the
    # LOUD filled-red FREEBALL pill wins over the plain yolo flag (the-fold Vector C).
    if getattr(state, "freeball", False):
        return ("FREEBALL", _MODE_RICH["FREEBALL"])
    if getattr(state, "yolo", False):
        return ("yolo", _MODE_RICH["YOLO"])
    return ("", "")


def placeholder_key(state) -> str:
    """The single mode word the input hint should lead with. /plan is a
    talk-primary *work* mode that overrides the base surface, so it wins when
    its controller is active; otherwise mirror the profile bar's lead — /howto
    overlay, else the Profile's code/chat surface.     Rail/debug aren't bare input
    modes of their own, so they keep their base surface's hint.

    A foreground harness (seam #5) is a *work* mode like /plan — bare input drives
    that harness session — so it leads with the harness's label when active (C
    registers the matching hint via register_hint)."""
    fg = _harness_foreground(state)
    if fg is not None:
        return fg[0]
    tag = _active_mode_tag(getattr(state, "agent", None))
    if tag is not None:
        tag_mode = tag[1]
        if tag_mode == "PLAN":
            return "plan"
        if tag_mode == "DISC":
            return "discovery"
        if tag_mode == "OPS":
            return "ops"
    if getattr(state, "howto_mode", False):
        return "howto"
    # scratch leads the bare-input contract once overlays are clear (it's the live
    # surface word, like code/chat); its hint is registered in hints.BUILTIN_HINTS.
    if getattr(state, "scratch", False):
        return "scratch"
    prof = getattr(state, "profile", None)
    persona = getattr(state, "persona", None)
    return getattr(prof, "mode", None) or ("chat" if persona is not None else "code")


# placeholder_key() word → _MODE_RICH palette key, so the input *frame* (border
# color + title) tints with the same tone the profile bar / inline toolbar use
# for that surface — one palette, three surfaces, no drift.
_FRAME_COLOR_KEY = {
    "code": "SHELL",
    "chat": "CHAT",
    "plan": "PLAN",
    "howto": "HOWTO",
    "discovery": "DISC",
    "ops": "OPS",
    "scratch": "SCRATCH",
}


def chat_tier(state) -> Optional[str]:
    """The active chat tier (fast|expert|heavy|auto) or None — the 'faux mode'
    within chat. Read defensively across REPLState (→ agent.session) and a bare
    session so every status surface can suffix it onto the chat word."""
    from xlii.chat_tiers import normalize_tier

    t = getattr(state, "chat_tier", None)
    if t is None:
        sess = getattr(getattr(state, "agent", None), "session", None)
        t = getattr(sess, "chat_tier", None)
    return normalize_tier(t)


def frame_mode(state) -> tuple[str, str]:
    """(label, border-color) for the TUI input frame (tui-frame follow-up).

    The label is the live input-surface word — `placeholder_key`'s, plus the
    chat tier as a faux-mode suffix (`chat · expert`) when one is set — so the
    profile-bar lead and the reply-frame chip both surface it. The color is the
    bare token from `_MODE_RICH` (the rich `bold <color>` weight stripped),
    reused so the frame matches the bar's tone for that mode.

    Front-end-agnostic (a plain color string); the Textual `--tui` applies it to
    the input's border + `border_title`. A foreground harness (seam #5) tints with
    its own palette tone rather than a base-mode color."""
    fg = _harness_foreground(state)
    if fg is not None:
        label, name = fg
        style = _HARNESS_RICH.get(name, _HARNESS_FALLBACK)
        return (label, style.split()[-1])
    key = placeholder_key(state)
    style = _MODE_RICH.get(_FRAME_COLOR_KEY.get(key, "SHELL"), "")
    color = style.split()[-1] if style else "green"
    # chat-tiers: the tier is a faux-mode within chat, so suffix it on the chat
    # word (color/key logic above already resolved off the bare `chat` key).
    if key == "chat":
        tier = chat_tier(state)
        if tier:
            return (f"{key} · {tier}", color)
    return (key, color)


def turn_record(state) -> tuple[str, str, str]:
    """(mode_word, color, role) snapshot for a finished turn's answer chip
    (tense-chrome). frame_mode already resolves harness > gated mode > overlay >
    scratch > surface with the right palette; role() already applies the
    silent-in-chat-become rule. Call this AT TURN END and store the result on
    the event — never re-derive at render time (records must not drift)."""
    word, color = frame_mode(state)
    return word, color, role(state)


# Frame-tab providers (seam #1): the public registry through which OTHER vectors
# contribute folder tabs from their OWN files — C registers a harness-session
# tab, a plugin a tab of its own kind — never by editing frame_tabs. A provider
# is `provider(state) -> list[(label, kind, payload)] | (label, kind, payload) |
# None`; it's consulted after the built-in kind tabs on every read. This is the
# "be client #1 of a public seam" shape: the built-in role/doc/ref/image/
# skill tabs are just the first clients of the same mechanism.
_FRAME_TAB_PROVIDERS: list[Callable[[Any], Any]] = []


def register_frame_tab(provider: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Register a folder-tab provider (seam #1). Idempotent; returns the provider
    so it can be used as a decorator. The provider runs on every frame_tabs()
    read and may return one ``(label, kind, payload)`` tuple, a list of them, or
    None/[] to contribute nothing this turn. A raising provider is skipped (one
    bad plugin can never blank the frame)."""
    if provider not in _FRAME_TAB_PROVIDERS:
        _FRAME_TAB_PROVIDERS.append(provider)
    return provider


def unregister_frame_tab(provider: Callable[[Any], Any]) -> None:
    """Drop a previously-registered provider (no-op if absent) — plugin teardown
    and test isolation, mirroring hints.unregister_hint."""
    try:
        _FRAME_TAB_PROVIDERS.remove(provider)
    except ValueError:
        # Per the docstring: removing an unregistered provider is a no-op.
        pass


def _normalize_tab(item: Any) -> tuple[str, str, Any] | None:
    """Coerce a provider's contribution to a ``(label, kind, payload)`` triple.
    Accepts a 2-tuple ``(label, kind)`` (payload defaults to None) or a 3-tuple;
    anything else is dropped so a malformed provider can't corrupt the row."""
    if not isinstance(item, (tuple, list)):
        return None
    if len(item) == 2:
        return (str(item[0]), str(item[1]), None)
    if len(item) >= 3:
        return (str(item[0]), str(item[1]), item[2])
    return None


def _doorways(state) -> "list[tuple[str, str, Any]]":
    """The content-type doorways: ``(label, scheme, count|None)``. A doorway rides the input frame
    ONLY when its ``count`` is a positive int (see :func:`frame_tabs`) — an empty kind stays off the
    row; its panel is still reachable via ``/panel``, the menu bar's Panel menu, or the
    ``Alt-<letter>`` hotkey. The count is what's riding the turn for that kind (``None`` only when
    the counter raised — treated as empty). A door's ``scheme`` is what a chip click opens
    (``scheme://``) in Pane 2."""
    return [
        ("skills", "skills", _safe_count(_skill_rider_count, state)),
        ("docs", "docs", _safe_count(_doc_attached_count, state)),
        ("bookmarks", "mark", _safe_count(_mark_attached_count, state)),
        ("images", "locker", _safe_count(_locker_image_count, state)),
        ("wiki", "wiki", _safe_count(_wiki_attached_count, state)),
    ]


def _safe_count(fn, state) -> "Any":
    try:
        return fn(state)
    except Exception:
        return None


def _doc_attached_count(state) -> int:
    """Real docs riding the turn (attached via /doc) — NOT the store size. Excludes skill:, mark:,
    wiki: and point: (a /ref'd bookmark) prefixed entries (those ride their own chips). The doorway
    still opens the full doc store."""
    docs = getattr(state, "attached_docs", None) or []
    return sum(1 for n, _ in docs
               if isinstance(n, str) and not n.startswith(("skill:", "mark:", "wiki:", "xwiki:", "point:")))


def _skill_rider_count(state) -> int:
    """Skills currently RIDING the turn (attached via the /skill channel) — NOT the store size. The
    chip is a badge of what's loaded now, so it shows a number only when a skill is actually attached
    (the doorway still opens the full palette to browse); the count hides at zero (see frame_tabs)."""
    from xlii.skills import active_skill_names

    return len(active_skill_names(getattr(state, "attached_docs", None)))


def _mark_attached_count(state) -> int:
    """Bookmarks currently pulled in via /ref (recalled spans, point: prefixed) — NOT the total
    bookmarks in the library. The doorway lists every bookmark to browse; the chip badges what's
    recalled right now."""
    docs = getattr(state, "attached_docs", None) or []
    return sum(1 for n, _ in docs if isinstance(n, str) and n.startswith("point:"))


def _locker_image_count(state) -> int:
    files = getattr(state, "attached_files", None) or []
    return sum(1 for e in files if isinstance(e, dict) and e.get("kind") == "image")


def _wiki_attached_count(state) -> int:
    """Wiki pages riding the turn (wiki: prefixed /doc entries) — NOT the store size. The doorway
    still opens the full page list; the chip badges what's loaded."""
    docs = getattr(state, "attached_docs", None) or []
    # Both tiers ride the wiki channel — the chip counts wiki-page attachments,
    # project (wiki:) and shipped (xwiki:) alike.
    return sum(1 for n, _ in docs
               if isinstance(n, str) and n.startswith(("wiki:", "xwiki:")))


def frame_tabs(state) -> list[tuple[str, str, Any]]:
    """The ordered folder tabs for the input frame cap — one tab per attachment
    *kind* present on the turn (seam #1).

    Type-driven: a tab per context KIND that's actually riding the turn — an
    equipped ``role``, attached ``doc``s, ``ref``s, ``image``s, and ``skill``s
    — each only when present, so the frame shows exactly what's loaded and
    nothing that isn't. Skills ride the ``/doc`` channel under the ``skill:``
    prefix, so a flat count mis-reads them as docs; we partition on
    ``skills.partition_attached_docs`` (seam #7) into a correct doc count plus a
    distinct ``skill`` tab.

    Returns ``(label, kind, payload)`` triples: the TUI styles by ``kind`` and
    draws them as joined folder tabs, while ``payload`` is what a tab's surface
    opens (A2's preview/edit seam reads it). After the built-ins, every
    registered provider (seam #1) appends its own tabs — the mechanism C's
    harness-session tab and third-party kinds slot into.

    Pure over REPLState (defensive getattr), reusing role()/the attachment lists
    the profile bar already reads — one source for "what's loaded", two surfaces.
    The image list has no session field yet (the multimodal path isn't built); the
    getattr is a forward hook that lights up the day attachments land."""
    tabs: list[tuple[str, str, Any]] = []

    badge = role(state)                       # 'role:<name>' in code, '' otherwise
    if badge:
        name = badge[len("role:"):] if badge.startswith("role:") else badge
        tabs.append((badge, "role", name))

    # Content-type DOORWAYS — shown ONLY when the kind is riding the turn (a positive count). Empty →
    # hidden, to keep the row lean; the panel is still reached via /panel, the menu bar's Panel menu,
    # or the Alt-<letter> doorway hotkey. The door's payload is its scheme; a click opens scheme:// in
    # Pane 2 (the file panel). The label carries the count as its badge — "no number unless there's
    # something" — and there's no chip at all when there's nothing.
    for door_label, scheme, count in _doorways(state):
        if isinstance(count, int) and count > 0:
            tabs.append((f"{door_label} {count}", "door", scheme))

    # NB: the LIVE [jobs N] chip + done-tabs are contributed by J's `job_frame_tab`
    # provider (seam #1, registered via register_job_seams) — kind="jobs" while work
    # is in flight, kind="job_done" per completed-unseen job. Pane-2 destinations are
    # jobs:// and jobs://<id>; the surface routes those clicks in _activate_tab.

    attached_files = list(getattr(state, "attached_files", None) or [])
    for entry in attached_files:
        if not isinstance(entry, dict):
            continue
        raw_path = entry.get("path")
        if not raw_path:
            continue
        try:
            from pathlib import Path as _Path

            name = entry.get("name") or _Path(str(raw_path)).name
        except Exception:
            name = str(entry.get("name") or raw_path)
        file_kind = entry.get("kind") or "file"
        tabs.append((name, "file", {"path": str(raw_path), "kind": file_kind}))

    # NB: no persistent "files" doorway chip on the row (it carried nothing for the turn). The file
    # explorer is opened via `/panel files`, the Panel menu's Files entry, or the Alt-f hotkey.

    for provider in list(_FRAME_TAB_PROVIDERS):
        try:
            extra = provider(state)
        except Exception:
            continue
        if not extra:
            continue
        items = [extra] if isinstance(extra, tuple) else extra
        if not isinstance(items, (list, tuple)):
            continue
        for item in items:
            norm = _normalize_tab(item)
            if norm is not None:
                tabs.append(norm)
    return tabs


def identity(state) -> str:
    """The profile bar's identity segment: the persona name in chat; else the
    code surface's location (project-anchored — see :func:`location`). Code does
    NOT surface a chat identity — the project's `bound_persona` is only which
    persona bare `/chat` opens, irrelevant to the code surface (RP7: code and
    chat are isolated)."""
    if getattr(state, "persona", None) is not None:
        return getattr(state.persona, "name", "") or ""
    return location(state)


def location(state) -> str:
    """The project-anchored location for the commander status bar: ``proj`` at
    the project root, ``proj/sub`` inside it, and ``proj (outside ~/x)`` when the
    shell wandered — the project name is NEVER lost to a cd (the bare
    ``(outside …)`` form hid *which* project you were in). '' with no project and
    no cwd, so the segment collapses."""
    proj = getattr(getattr(state, "project", None), "name", "") or ""
    shown = ""
    try:
        from xlii.repl import format_shell_cwd, shell_cwd_is_outside

        shown = format_shell_cwd(state) or ""
        if shown and proj and shell_cwd_is_outside(state):
            return f"{proj} {shown}"
    except Exception:
        shown = ""
    return shown or proj


def role(state) -> str:
    """The active role badge (roles R3) for the bar: `role:<name>` when a role is
    equipped in code (its loadout rides the project session, so nothing else in
    the bar names it). In chat a role is *become* — it IS the persona, so
    `identity` already prints its name; stay silent there to avoid `role:x · x`."""
    name = getattr(state, "active_role", None)
    if not name:
        return ""
    persona = getattr(state, "persona", None)
    if persona is not None and getattr(persona, "name", None) == name:
        return ""
    return f"role:{name}"


def loadout(state) -> str:
    """`[workspace · ]N docs · M refs · K plugins` for the bar — collapses to ''
    when empty. A non-default workspace name leads the segment (RP5: a workspace
    is a saved loadout), so the bar answers "which loadout, carrying what". The
    default "main" workspace stays silent, keeping a fresh session lean."""
    refs = getattr(state, "attached_refs", None) or []
    docs = getattr(state, "attached_docs", None) or []
    bits = []
    if docs:
        bits.append(f"{len(docs)} doc{'s' if len(docs) != 1 else ''}")
    if refs:
        bits.append(f"{len(refs)} ref{'s' if len(refs) != 1 else ''}")
    try:
        from xlii.plugin import load_subscriptions
        xli_dir = getattr(getattr(state, "project", None), "xli_dir", None)
        subs = load_subscriptions(xli_dir) if xli_dir is not None else []
        if subs:
            bits.append(f"{len(subs)} plugin{'s' if len(subs) != 1 else ''}")
    except Exception:
        # The plugin count is decorative — omit it when subscriptions can't load.
        pass
    try:
        if hasattr(state, "get_current_workspace"):
            ws = state.get_current_workspace()
            if ws and ws != "main":
                bits.insert(0, ws)
    except Exception:
        # The workspace prefix is decorative -- omit it when the workspace can't be read.
        pass
    return " · ".join(bits)


def model(state) -> str:
    """The orchestrator-slot model the *next* turn will use: loadout pin, else the
    role-appropriate config model. When a model-selectable harness (seam #5 —
    cursor) is in the foreground, its hosted model is appended
    (`<orchestrator> · <harness model>`): cursor is a *host* you run a chosen
    model through, so the model is its axis. The other harnesses ARE the model —
    their label already names them — so nothing is appended for a non-selectable
    foreground harness."""
    base = _next_turn_model(state) or ""

    spec = getattr(state, "harness_foreground", None)
    if spec and getattr(spec, "model_selectable", False):
        hm = getattr(spec, "model", None) or getattr(spec, "default_model", None)
        if hm:
            return f"{base} · {hm}" if base else str(hm)
    return base


def session_cost(state) -> str:
    """Compact session cost for the status strip, or '' when nothing to show."""
    from xlii.session_meter import session_cost_label

    return session_cost_label(state)


def journal(state) -> str:
    """Live journal badge (D8): always ``jrnl●`` (recording) or ``jrnl○`` (not)."""
    j = getattr(state, "journal", None)
    if j is None:
        return "jrnl○"
    try:
        return "jrnl●" if j.is_recording() else "jrnl○"
    except Exception:
        return "jrnl○"


def plugin_count(state) -> int:
    try:
        from xlii.plugin import load_subscriptions

        xli_dir = getattr(getattr(state, "project", None), "xli_dir", None)
        subs = load_subscriptions(xli_dir) if xli_dir is not None else []
        return len(subs)
    except Exception:
        return 0


def auto_approve(state) -> str:
    """Compact auto-approve label for the status strip, or '' when yolo/off."""
    if getattr(state, "yolo", False):
        return ""
    aa = getattr(state, "auto_approve", None)
    if not aa:
        return ""
    abbrev = {
        "read-only": "ro",
        "modifies-project": "proj",
        "network": "net",
        "modifies-system": "sys",
    }
    from xlii.shellgate import SEVERITY

    ordered = sorted(aa, key=lambda c: SEVERITY.get(c, 99))
    return "approve:" + ",".join(abbrev.get(c, c[:3]) for c in ordered)


_MODE_NEUTRAL = frozenset({"code", "chat", "scratch"})


def _mode_lead_style(word: str, color: str) -> str:
    """Style token for the lead mode word (D11): neutral surfaces stay dim."""
    base = word.split(" · ", 1)[0]
    if base in _MODE_NEUTRAL:
        return "dim"
    return f"mode:{color}"


def _loadout_parts(state) -> list[tuple[str, str]]:
    """``(text, style_key)`` loadout segments; ``style_key`` is ``plugin`` or ``dim``."""
    refs = getattr(state, "attached_refs", None) or []
    docs = getattr(state, "attached_docs", None) or []
    parts: list[tuple[str, str]] = []
    try:
        if hasattr(state, "get_current_workspace"):
            ws = state.get_current_workspace()
            if ws and ws != "main":
                parts.append((ws, "dim"))
    except Exception:
        # As above: the workspace chip is omitted when it can't be read.
        pass
    if docs:
        parts.append((f"{len(docs)} doc{'s' if len(docs) != 1 else ''}", "dim"))
    if refs:
        parts.append((f"{len(refs)} ref{'s' if len(refs) != 1 else ''}", "dim"))
    n_plugins = plugin_count(state)
    if n_plugins:
        parts.append((
            f"{n_plugins} plugin{'s' if n_plugins != 1 else ''}",
            "plugin",
        ))
    return parts


def profile_bar_segments(state, *, meter: str = "") -> list[dict[str, str]]:
    """Plain profile-bar segments for the face to render (no Rich)."""
    out: list[dict[str, str]] = []

    def _add(text: str, style: str = "dim", *, gap: str = "tight") -> None:
        if text:
            out.append({"text": text, "style": style, "gap": gap})

    word, color = frame_mode(state)
    base = word.split(" · ", 1)[0]
    _add(word, _mode_lead_style(word, color), gap="none")

    flag, flag_style = _affordance_flag(state)
    trust = trust_axis(state)
    flag_is_trust = flag.lower() in ("yolo", "freeball") or flag == "FREEBALL"
    if flag and not flag_is_trust and flag.split()[0] != base:
        _add(flag, flag_style or "dim")

    if trust == "freeball":
        _add("FREEBALL", _MODE_RICH["FREEBALL"])
    elif trust == "yolo":
        _add("yolo", _MODE_RICH["YOLO"])
    else:
        _add("safe", "dim")

    if getattr(state, "no_sync", False):
        _add("no-sync", "dim")

    episode = getattr(state, "episode_id", None)
    if episode:
        _add(f"sess {episode}", "sess")

    approve = auto_approve(state)
    if approve:
        _add(approve, "yellow")

    cost = session_cost(state)
    if cost:
        limit = getattr(state, "budget_usd", None)
        spent = float(getattr(state, "session_cost", 0.0) or 0.0)
        over = limit is not None and spent > limit
        _add(cost, "cost_over" if over else "dim")

    jrnl = journal(state)
    _add(jrnl, "journal_on" if jrnl.endswith("●") else "journal_off")

    ident = identity(state)
    if ident:
        _add(ident, "dim", gap="wide")
    role_txt = role(state)
    if role_txt:
        _add(role_txt, "dim", gap="wide")

    for i, (part, style_key) in enumerate(_loadout_parts(state)):
        if i == 0:
            _add(part, style_key, gap="wide")
        else:
            out.append({"text": " · " + part, "style": style_key, "gap": "inline"})

    model_txt = model(state)
    if model_txt:
        _add(model_txt, "dim", gap="wide")
    if meter:
        _add(meter, "dim", gap="wide")
    return out
