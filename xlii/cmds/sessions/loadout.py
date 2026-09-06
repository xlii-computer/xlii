"""Persona loadout and attachment helpers."""

from __future__ import annotations

from typing import Optional

from xlii.repl import REPLState
from xlii.ui import console


def restore_loadout_profile(state: REPLState) -> None:
    """Undo a persona/role `profile:` cfg mutation when its scope ends."""
    agent = getattr(state, "agent", None)
    session = getattr(agent, "session", None) if agent is not None else None
    snap = getattr(session, "loadout_cfg_snapshot", None) if session is not None else None
    if not snap or agent is None:
        return
    from xlii.model_profiles import restore_cfg_models

    cfg = getattr(agent, "cfg", None) or getattr(state, "cfg", None)
    if cfg is None:
        return
    restore_cfg_models(cfg, snap)
    session.loadout_cfg_snapshot = None


def _attachment_tag(obj) -> str:
    """
    Compact prompt-line indicator for /ref + /doc attachments.

    Accepts either an Agent (legacy) or a REPLState (preferred).
    Returns '+1r', '+2d', '+1r/2d', or ''.
    """
    refs = getattr(obj, "attached_refs", None) or getattr(getattr(obj, "agent", None), "attached_refs", [])
    docs = getattr(obj, "attached_docs", None) or getattr(getattr(obj, "agent", None), "attached_docs", [])

    parts = []
    if refs:
        parts.append(f"{len(refs)}r")
    if docs:
        parts.append(f"{len(docs)}d")
    return ("+" + "/".join(parts)) if parts else ""


def _apply_persona_loadout(state: REPLState, persona, project) -> None:
    """Materialize a persona's declared frontmatter loadout into session state.

    Additive + idempotent: declared plugins/docs/skills are ensured present;
    nothing the user attached or subscribed manually is removed. Unknown items
    warn and are skipped (a bad loadout entry never blocks the session).
    Model/temperature pinning is handled separately (PL4).
    """
    loadout = persona.loadout()
    if not loadout:
        return

    from xlii.doc import Doc
    from xlii.plugin import Plugin, add_subscription
    from xlii.role import gate_loadout_plugins

    applied: list[str] = []

    elevated = getattr(state, "elevated", False)
    allowed, gated = gate_loadout_plugins(loadout.get("plugins"), elevated=elevated)
    for pid in gated:
        console.print(
            f"[yellow]loadout: plugin {pid!r} is high-risk — /admin unlock to attach[/yellow]"
        )

    n = 0
    for pid in allowed:
        if Plugin(id=pid).exists():
            add_subscription(project.xli_dir, pid)
            n += 1
        else:
            console.print(f"[yellow]loadout: plugin {pid!r} not installed — skipped[/yellow]")
    if n:
        applied.append(f"{n} plugin(s)")

    n = 0
    for name in loadout.get("docs") or []:
        d = Doc(name)
        if not d.exists():
            console.print(f"[yellow]loadout: doc {name!r} not found — skipped[/yellow]")
            continue
        try:
            state.attach_doc(name, d.read())
            n += 1
        except OSError as e:
            console.print(f"[yellow]loadout: doc {name!r} unreadable ({e}) — skipped[/yellow]")
    if n:
        applied.append(f"{n} doc(s)")

    # Skills (roles R0) — resolve declared skills and attach each through the
    # existing skill: doc channel, exactly as `/skill <name>` does. A role is a
    # persona whose loadout names skills; this is the only new materialization.
    skill_model: Optional[str] = None
    if loadout.get("skills"):
        from xlii.skills import SKILL_ATTACH_PREFIX, load_skills, render_skill

        available = load_skills(getattr(project, "project_root", None))
        n = 0
        for name in loadout.get("skills") or []:
            sk = available.get(name)
            if sk is None:
                console.print(f"[yellow]loadout: skill {name!r} not found — skipped[/yellow]")
                continue
            state.attach_doc(SKILL_ATTACH_PREFIX + sk.name, render_skill(sk))
            sk_model = getattr(sk, "model", None)
            if sk_model:
                skill_model = sk_model
            n += 1
        if n:
            applied.append(f"{n} skill(s)")

    # Sticky per-persona model / temperature override (PL4). Role-level `model:`
    # wins; otherwise the last declared skill carrying `model:` pins the session.
    # `profile:` applies a named model triple in-memory before `model:` is resolved.
    profile_name = loadout.get("profile")
    if isinstance(profile_name, str) and profile_name.strip():
        from xlii.model_profiles import apply_model_profile, snapshot_cfg_models

        try:
            session = getattr(getattr(state, "agent", None), "session", None)
            baseline = None
            if session is not None and getattr(session, "loadout_cfg_snapshot", None) is None:
                baseline = snapshot_cfg_models(state.cfg)
            apply_model_profile(state.cfg, profile_name.strip(), persist=False)
            if baseline is not None:
                session.loadout_cfg_snapshot = baseline
            applied.append(f"profile={profile_name.strip()}")
        except KeyError:
            console.print(
                f"[yellow]loadout: profile {profile_name.strip()!r} not found — "
                "skipped (see `xlii models profile list`)[/yellow]"
            )

    model = loadout.get("model")
    if not (isinstance(model, str) and model.strip()) and skill_model:
        model = skill_model
    if isinstance(model, str) and model.strip():
        model = model.strip()
        known = {state.cfg.orchestrator(), state.cfg.worker()} | set(state.cfg.pricing)
        chat_fn = getattr(state.cfg, "chat", None)
        if callable(chat_fn):
            known.add(chat_fn())
        elif hasattr(state.cfg, "get_model_for_role"):
            known.add(state.cfg.get_model_for_role("chat"))
        help_fn = getattr(state.cfg, "help", None)
        if callable(help_fn):
            known.add(help_fn())
        elif hasattr(state.cfg, "get_model_for_role"):
            known.add(state.cfg.get_model_for_role("help"))
        state.agent.model_override = model
        if model not in known:
            console.print(
                f"[yellow]loadout: model {model!r} isn't in your configured set — "
                "applying anyway (it will error at call time if the id is invalid)[/yellow]"
            )
        applied.append(f"model={model}")

    temp = loadout.get("temperature")
    if temp is not None:
        try:
            t = float(temp)
        except (TypeError, ValueError):
            console.print(f"[yellow]loadout: temperature {temp!r} is not a number — skipped[/yellow]")
        else:
            if 0.0 <= t <= 2.0:
                state.agent.temperature_override = t
                applied.append(f"temp={t}")
            else:
                console.print(f"[yellow]loadout: temperature {t} out of range 0.0–2.0 — skipped[/yellow]")

    if applied:
        state.save()  # persist the attachments to .xlii/session.json
        console.print(f"[dim]loadout: {' · '.join(applied)}[/dim]")
