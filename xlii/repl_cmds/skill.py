"""/skill — list user-authored skills, or attach one to the session (A1).

Attaching rides the same seam as /doc (state.attach_doc), so the skill's steps
inline into the system prompt on the next turn. The skill INDEX (names +
descriptions) is injected into the agent preamble separately, so the agent knows
what's available without paying for every body.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xlii.commands import REPLCommand, register_repl_command
from xlii.skills import (
    SKILL_ATTACH_PREFIX,
    XLII_SCOPES,
    active_skill_names,
    load_skills,
    render_skill,
    skill_detail_lines,
)


def _project_root(ctx: dict[str, Any]):
    state = ctx.get("state")
    project = getattr(state, "project", None) if state else ctx.get("project")
    root = getattr(project, "project_root", None) if project else None
    return Path(root) if root else None


def _cmd_skill(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    skills = load_skills(_project_root(ctx))

    # Everything after the command word is the argument. Skill names can contain
    # spaces, so treat the remainder literally (only the leading off/detach verb
    # is special) rather than tokenizing the name.
    split = line.split(None, 1)
    rest = split[1].strip() if len(split) > 1 else ""

    verb, _, tail = rest.partition(" ")
    if verb in ("off", "detach", "remove"):
        name = tail.strip()
        if not name:
            console.print("[yellow]usage:[/yellow] /skill off <name>")
            return True
        if state is not None and state.detach_doc(SKILL_ATTACH_PREFIX + name):
            sk = skills.get(name)
            agent = getattr(state, "agent", None)
            if sk and sk.model and agent is not None:
                stack = getattr(agent.session, "model_pin_stack", None)
                # Only unwind a pin THIS skill pushed. Attach always pushes the
                # stack before overriding (see _cmd_skill attach), so an empty
                # stack means the current override came from a role loadout /
                # persona / manual /model — leave it alone even if it happens to
                # equal this skill's model, or we'd silently drop that pin.
                if stack:
                    prior = stack.pop()
                    if getattr(agent, "model_override", None) == sk.model:
                        agent.model_override = prior
            console.print(f"[green]✓[/green] detached skill [cyan]{name}[/cyan]")
        else:
            console.print(f"[dim]skill {name} was not attached[/dim]")
        return True

    if verb in ("show", "info", "view"):
        name = tail.strip()
        if not name:
            console.print("[yellow]usage:[/yellow] /skill show <name>")
            return True
        sk = skills.get(name)
        if sk is None:
            console.print(f"[red]no skill named {name!r}[/red] [dim](/skill to list)[/dim]")
            return True
        for ln in skill_detail_lines(sk):
            console.print(ln)
        console.print(f"\n[dim]/skill {sk.name} to attach it to the session[/dim]")
        return True

    sub = rest  # the (possibly multi-word) skill name, or "" to list
    if not sub:
        if not skills:
            console.print("[dim](no skills found)[/dim]")
            console.print(
                "[dim]Author one at [/dim][cyan].xlii/skills/<name>/SKILL.md[/cyan]"
                "[dim] — YAML frontmatter (name, description) + a markdown body. "
                "Global skills live under <config>/skills/.[/dim]"
            )
            return True
        active = active_skill_names(getattr(state, "attached_docs", []))
        console.print("[bold]Skills[/bold] [dim](● attached · /skill <name> to attach)[/dim]")
        for s in sorted(skills.values(), key=lambda s: s.name):
            mark = "[green]●[/green]" if s.name in active else " "
            origin = "" if s.scope in XLII_SCOPES else f" [dim]\\[{s.scope}][/dim]"
            model_note = f" [dim]model={s.model}[/dim]" if s.model else ""
            console.print(
                f"  {mark} [cyan]{s.name}[/cyan]{origin} [dim]—[/dim] "
                f"{s.short_description or s.description or '(no description)'}{model_note}"
            )
        console.print(
            "[dim]/skill show <name> for the full description · "
            "/skill off <name> to detach[/dim]"
        )
        return True

    # attach: /skill <name>
    sk = skills.get(sub)
    if sk is None:
        console.print(f"[red]no skill named {sub!r}[/red] [dim](/skill to list)[/dim]")
        return True
    if state is None:
        console.print("[dim]/skill needs an active session[/dim]")
        return True
    doc_name = SKILL_ATTACH_PREFIX + sk.name
    already_attached = any(n == doc_name for n, _ in state.attached_docs)
    state.attach_doc(doc_name, render_skill(sk))
    if sk.model and not already_attached:
        agent = state.agent
        agent.session.model_pin_stack.append(agent.model_override)
        agent.model_override = sk.model
        console.print(
            f"[green]✓[/green] attached skill [cyan]{sk.name}[/cyan] "
            f"[dim]— model pinned to [cyan]{sk.model}[/cyan] "
            "(/skill off to detach; /model to clear pin)[/dim]"
        )
    else:
        console.print(
            f"[green]✓[/green] attached skill [cyan]{sk.name}[/cyan] "
            "[dim]— its steps ride your next turn (/skill off to detach)[/dim]"
        )
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="skill",
            handler=_cmd_skill,
            description="List skills (brief), show one in full, or attach it to the session",
            usage="/skill [<name> | show <name> | off <name>]",
            category="knowledge",
            repls=["code", "chat"],
        )
    )
