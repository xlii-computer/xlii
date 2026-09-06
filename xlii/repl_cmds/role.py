"""/role — list, show, and activate role descriptors (proposals/roles.md R1+R2).

R2 adds the **activation verb**, one verb with two modes (locked decision):
  • **code** → *equip*: apply the role's loadout (skills/docs/plugins/model) onto
    the current project session; identity/memory stay project-local.
  • **chat** → *become*: switch to the role as a persona (its own detached thread +
    Collection memory) via the shared persona-switch path; the loadout rides along.

`/role` lists (marking the active one), `/role <name>` activates, `/role off`
drops an equipped loadout in code. `/role default <name>` sets the project's
boot-equipped role (`/role default off` clears it). `/hire` is a registered alias.

Code-equip attaches the role's identity body as a doc (`role:<name>`), so the
agent adopts the role's *stance*, not just its loadout — detached on `/role off`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command
from xlii.persona import Persona
from xlii.role import (
    ROLE_ATTACH_PREFIX,
    format_role_summary,
    load_role,
    load_roles,
)


@dataclass
class RoleAsPersona(Persona):
    """A role activated in chat behaves as a persona: identity + loadout come from
    the role descriptor (via `prompt_path`), while memory/Collection live under
    `chat/<name>` like any persona (the locked 'a chat role IS a persona' decision)."""

    role_path: Optional[Path] = None

    @property
    def prompt_path(self) -> Path:
        return self.role_path


def _deactivate_role(state: Any, project: Any) -> None:
    """Reverse a code-equipped role's loadout by re-reading its descriptor, then
    clear the marker. Additive equip → best-effort undo: it detaches the docs /
    skills / refs the role declared and removes its plugin subscriptions + model
    pin (a doc the user *also* attached by hand is dropped too — acceptable for v1)."""
    name = state.active_role
    root = getattr(project, "project_root", None)
    role = load_role(name, root) if name else None
    if role is not None:
        from xlii.plugin import remove_subscription
        from xlii.skills import SKILL_ATTACH_PREFIX

        lo = role.loadout()
        # The identity doc code-equip attached (the role's stance, R2+).
        state.detach_doc(ROLE_ATTACH_PREFIX + str(name))
        for d in lo.get("docs") or []:
            state.detach_doc(str(d))
        for s in lo.get("skills") or []:
            state.detach_doc(SKILL_ATTACH_PREFIX + str(s))
        for r in lo.get("refs") or []:
            state.detach_ref(str(r))
        xli = getattr(project, "xli_dir", None)
        if xli is not None:
            for p in lo.get("plugins") or []:
                remove_subscription(xli, str(p))
        if lo.get("model"):
            state.agent.model_override = None

    from xlii.cmds.sessions import restore_loadout_profile

    restore_loadout_profile(state)
    state.active_role = None


def equip_role_in_code(state: Any, role: Any, project: Any) -> None:
    """Equip a role onto a live code session: apply its loadout AND attach its
    identity body as a doc so the agent adopts the role's stance, then mark it
    active. Shared by `/role <name>` (code arm) and the boot-time default-role
    equip so both take the identical path. Idempotent via attach_doc's dedupe."""
    from xlii.cmds.sessions import _apply_persona_loadout

    _apply_persona_loadout(state, role, project)
    identity = role.identity()
    if identity:
        state.attach_doc(ROLE_ATTACH_PREFIX + role.name, identity)
    state.active_role = role.name


def _equip_summary(role: Any, agent: Optional[Any]) -> str:
    lo = role.loadout()
    parts: list[str] = []
    profile = lo.get("profile")
    if isinstance(profile, str) and profile.strip():
        parts.append(f"profile={profile.strip()}")
    elif isinstance(lo.get("model"), str) and lo["model"].strip():
        parts.append(f"model={lo['model'].strip()}")
    elif agent is not None:
        try:
            effective, _ = agent.orchestrator_model_and_role()
            parts.append(f"model={effective}")
        except Exception:
            # The summary omits the model rather than failing the equip.
            pass
    skills = lo.get("skills") or []
    if skills:
        parts.append(f"{len(skills)} skill(s)")
    return f" ({', '.join(parts)})" if parts else ""


def h_role(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    project = ctx.get("project")
    root = getattr(project, "project_root", None) if project is not None else None
    is_chat = bool(ctx.get("persona"))

    parts = line.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""

    # --- list ---------------------------------------------------------------
    if not arg:
        roles = load_roles(root)
        if not roles:
            console.print("[dim](no roles — add a descriptor to .xlii/roles/ or "
                          "~/.config/xlii/roles/)[/dim]")
            return True
        active = getattr(state, "active_role", None) if state is not None else None
        console.print("[bold]roles[/bold] [dim](/role <name> to activate, "
                      "/role off to unequip)[/dim]")
        for name in sorted(roles):
            r = roles[name]
            scope = "" if r.scope == "global" else " [cyan](project)[/cyan]"
            warn = " [yellow]⚠ malformed[/yellow]" if r.validate() else ""
            mark = " [green]● active[/green]" if name == active else ""
            console.print(f"  [bold]{name}[/bold]{scope} — {r.description()}{warn}{mark}")
        return True

    # --- deactivate ---------------------------------------------------------
    if arg.lower() in ("off", "drop", "unequip"):
        if state is None:
            console.print("[red]/role off needs an interactive session[/red]")
            return True
        if is_chat:
            console.print("[dim]/role off: in chat, /code parks the role persona[/dim]")
            return True
        if not state.active_role:
            console.print("[dim](no role equipped)[/dim]")
            return True
        name = state.active_role
        _deactivate_role(state, project)
        console.print(f"[dim]role {name} unequipped[/dim]")
        return True

    # --- set/clear the project's default role (equipped at every boot) ------
    if arg.lower().split(maxsplit=1)[0] in ("default", "--default"):
        rest = arg.split(maxsplit=1)
        target = rest[1].strip() if len(rest) > 1 else ""
        if project is None or not hasattr(project, "save"):
            console.print("[red]/role default needs a project[/red]")
            return True
        if not target:
            cur = getattr(project, "default_role", None)
            console.print(f"[bold]default role:[/bold] {cur or 'none'} "
                          "[dim](/role default <name> to set · /role default off to clear)[/dim]")
            return True
        if target.lower() in ("off", "none", "clear"):
            project.default_role = None
            project.save()
            console.print("[dim]default role cleared[/dim]")
            return True
        if load_role(target, root) is None:
            console.print(f"[yellow]no such role: {target}[/yellow] — /role to list")
            return True
        project.default_role = target
        project.save()
        console.print(f"[cyan]default role set: {target}[/cyan] "
                      "[dim](equipped on every code session for this project)[/dim]")
        return True

    # --- activate / show ----------------------------------------------------
    role = load_role(arg, root)
    if role is None:
        console.print(f"[yellow]no such role: {arg}[/yellow] — /role to list")
        return True
    err = role.validate()
    if err:
        console.print(f"[red]role {arg} is malformed:[/red] {err}")
        return True

    if state is None:  # non-interactive (e.g. a fake ctx) — show, don't mutate
        for ln in format_role_summary(role):
            console.print(ln)
        return True

    if is_chat:
        # become: switch to the role as a persona; its loadout applies inside
        # _live_switch (which clears active_role — we re-set it after).
        from xlii.repl_cmds.switch import switch_to_persona

        if not switch_to_persona(ctx, RoleAsPersona(name=role.name, role_path=role.path)):
            return True
        state.active_role = role.name
        console.print(f"[magenta]now in role {role.name}[/magenta] "
                      "[dim](chat persona · its own thread · /code to leave)[/dim]")
        return True

    # equip (code): apply the loadout + adopt the role's stance on the session.
    equip_role_in_code(state, role, project)
    summary = _equip_summary(role, state.agent)
    console.print(f"[cyan]role {role.name} equipped{summary}[/cyan] "
                  "[dim](stance + loadout applied · /role off to drop)[/dim]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="role",
            handler=h_role,
            aliases=["hire"],
            usage="/role [name|off|default <name>]",
            description="List/activate role descriptors (equip in code, become in chat)",
            category="mode",
            repls=["code", "chat"],
        )
    )
