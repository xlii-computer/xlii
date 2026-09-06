"""SkillsProvider provider."""
from __future__ import annotations

from pathlib import Path

from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)


def _skills_project_root():
    """The active session's project root (for project-scoped skills), or ``None`` outside a
    session — so the ``skills://`` doorway sees the *same* skill set as ``/skill``."""
    from xlii.active_session import active_session

    root = getattr(getattr(active_session(), "project", None), "project_root", None)
    return Path(str(root)) if root else None


class SkillsProvider:
    """``skills://`` — the skills (stock + imported + global + project) as a browseable list.

    ``skills://`` lists every skill (nodes carry ``type=skill`` and a ``brief``); ``skills://<name>``
    reads that skill's full description. Read-only — skills are authored on disk / via ``/skill``.
    The rich wiki-tree rendering (brief → expand → full, green-dot rider) lives in
    :class:`~xlii.panes.skills.SkillsPane`; this provider is the address behind it.

    Mirrors ``/skill``: honours the ``import_foreign_skills`` config flag (default on) and the active
    session's project root, so the doorway is the FULL palette — not just the one native skill, which
    read as a single stuck/attached entry (a doorway is a palette to explore, not an attachment list).
    """

    scheme = "skills"

    def resolve(self, address: Address) -> Resolution:
        name = address.target.strip()
        if not name:
            return Resolution(ok=True, address=address, kind="skills")
        from xlii.skills import load_skills

        ok = name in load_skills(_skills_project_root())
        return Resolution(ok=ok, address=address, kind="skills",
                          reason="" if ok else f"no skill named {name!r}")

    def stat(self, address: Address) -> Node:
        name = address.target.strip()
        return Node(address=str(address), name=name or "skills",
                    kind="container" if not name else "leaf", extra={"type": "skill"})

    def list(self, address: Address) -> "list[Node]":
        if address.target.strip():
            return []
        from xlii.skills import load_skills

        skills = sorted(load_skills(_skills_project_root()).values(), key=lambda s: s.name.lower())
        return [
            Node(address=f"skills://{s.name}", name=s.name, kind="leaf",
                 extra={"type": "skill", "brief": s.short_description})
            for s in skills
        ]

    def read(self, address: Address) -> bytes:
        name = address.target.strip()
        if not name:
            raise IsADirectoryError("skills://: a skills root — use ls")
        from xlii.skills import load_skills

        skills = load_skills(_skills_project_root())
        if name not in skills:
            raise FileNotFoundError(f"skills://{name}: no such skill")
        return (skills[name].description or "").encode()

    def exists(self, address: Address) -> bool:
        name = address.target.strip()
        if not name:
            return True
        from xlii.skills import load_skills

        return name in load_skills(_skills_project_root())

    def shell_export(self, address: Address) -> ShellExport:
        name = address.target.strip()
        if not name:
            return ShellExport(kind="address")  # a multi-scope palette — no one dir behind it
        from xlii.skills import load_skills

        skills = load_skills(_skills_project_root())
        if name not in skills:
            raise FileNotFoundError(f"skills://{name}: no such skill")
        # The SKILL.md is the canonical artifact (fuller than read()'s description-only view).
        return ShellExport(kind="path", path=skills[name].path)


