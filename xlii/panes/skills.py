"""``SkillsPane`` — a flat list of skills over ``skills://``.

One row per skill: **name · origin** (stock / global / project / grok / claude / …).
No description — the brief/full body crowded the list. The skill currently
**riding the turn** (attached via the ``/skill`` channel) gets a **green ● dot**.
When more than one origin is present, caption rows group them.

Reads the store directly (``skills.load_skills``, the full palette — same set as ``/skill``) and the
riding set from the ambient session (:mod:`xlii.active_session`), so the dot stays live as skills are
attached/detached.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node
from xlii.panes import ATTACH, DETACH, RETARGET_SLOT, Action, Outcome, Rendered, RenderedRow, Selection
from xlii.panes.select import select_target
from xlii.skills import XLII_SCOPES

# Low → high, then foreign imports. Groups the list so grok/claude dumps
# don't interleave with xlii's own.
_SCOPE_ORDER = ("project", "global", "stock", "grok", "claude", "claude-plugin")


def _scope_rank(scope: str) -> int:
    try:
        return _SCOPE_ORDER.index(scope)
    except ValueError:
        return len(_SCOPE_ORDER)


def skill_row_text(skill) -> str:
    """``name  · origin`` — origin is stock/global/project or the import tag."""
    name = getattr(skill, "name", "") or ""
    scope = str(getattr(skill, "scope", "") or "").strip()
    if not scope:
        return name
    return f"{name}  · {scope}"


class SkillsPane:
    """A read-only list of skill names, green-dotting the one(s) riding the turn."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="skills")
        self._skills: list = []          # list[Skill], sorted by name
        self._sel: int = 0               # index of the selected skill
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        from xlii.active_session import active_session
        from xlii.skills import load_skills

        self._address = address if isinstance(address, Address) else Address.parse(address)
        # The FULL palette (same set as /skill): honour the import_foreign config flag + the active
        # project root. Forcing import_foreign=False hid all imported skills, leaving only the one
        # native skill (grounded-analysis) — which read as a single stuck/attached entry.
        root = getattr(getattr(active_session(), "project", None), "project_root", None)
        self._skills = sorted(
            load_skills(root).values(),
            key=lambda s: (_scope_rank(str(getattr(s, "scope", "") or "")), s.name.lower()),
        )
        self._sel = 0
        target = select_target(select, self._address)
        if target:
            for i, s in enumerate(self._skills):
                if s.name == target:
                    self._sel = i
                    break

    def render(self) -> Rendered:
        riding = self._riding_names()
        scopes = [str(getattr(s, "scope", "") or "") for s in self._skills]
        group = len({s for s in scopes if s}) > 1
        rows = []
        last = object()
        for i, s in enumerate(self._skills):
            scope = str(getattr(s, "scope", "") or "")
            if group and scope and scope != last:
                label = scope if scope in XLII_SCOPES else f"import · {scope}"
                rows.append(RenderedRow(
                    text=f"── {label} ──",
                    address="",
                    kind="caption",
                    selected=False,
                ))
                last = scope
            rows.append(RenderedRow(
                text=skill_row_text(s),
                address=f"skills://{s.name}",
                kind="leaf",
                selected=(i == self._sel),
                accent=(s.name in riding),
            ))
        return Rendered(title="skills://", rows=tuple(rows), empty=not rows)

    def selection(self) -> Selection:
        if not self._skills:
            return Selection(node=None)
        s = self._skills[self._sel]
        return Selection(node=Node(address=f"skills://{s.name}", name=s.name, kind="leaf",
                                   extra={"type": "skill"}))

    def actions(self) -> "list[Action]":
        """view / attach / detach over the selected skill. ``view`` (first = the Enter default)
        morphs the pane to the skill's full description; ``attach``/``detach`` ride/unride it on the
        turn (the green dot follows). The surface renders these as the three per-pane buttons."""
        if not self._skills:
            return []
        addr = f"skills://{self._skills[self._sel].name}"
        return [
            Action("view", "View", Outcome(RETARGET_SLOT, addr)),
            Action("attach", "Attach", Outcome(ATTACH, addr)),
            Action("detach", "Detach", Outcome(DETACH, addr)),
        ]

    def handle(self, key: str) -> bool:
        if not self._skills:
            return False
        if key == "down":
            self._sel = min(self._sel + 1, len(self._skills) - 1)
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            return True
        if key == "home":
            self._sel = 0
            return True
        if key == "end":
            self._sel = len(self._skills) - 1
            return True
        return False  # enter/back fall through to the surface (actions / close)

    def select_index(self, i: int) -> bool:
        """Select the skill at index ``i`` (a mouse click's target row) — click selects like the
        arrow keys do. Returns True if in range."""
        if 0 <= i < len(self._skills):
            self._sel = i
            return True
        return False

    @staticmethod
    def _riding_names() -> "set[str]":
        """Names of skills attached to the live session (the /skill channel), for the green dot."""
        from xlii.active_session import active_session
        from xlii.skills import active_skill_names

        return active_skill_names(getattr(active_session(), "attached_docs", None))
