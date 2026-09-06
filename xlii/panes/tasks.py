"""``TasksPane`` — a flat list of saved ``/tasks`` pipelines over ``tasks://``.

One row per saved pipeline (``.xlii/tasks/*.toml``), name only. The primary action **loads
``/tasks run <name>`` into the command line** — review-before-run, matching the F9 Task Builder's
contract that nothing executes from the surface; a secondary action views the pipeline's plan in the
other slot. The **New** action opens ``taskmake://`` (Task+ shape picker). The face has no
CLAIM_INPUT, so the old chained-ask composer is retired from this pane.
Reads the project's saved pipelines through the ambient session
(:mod:`xlii.active_session`), so the list stays live as tasks are saved.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node
from xlii.panes import PREFILL, RETARGET_SLOT, SPAWN_JOB, Action, Outcome, Rendered, RenderedRow, Selection


class TasksPane:
    """A read-only list of saved task names; the primary action seeds ``/tasks run <name>``."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="tasks")
        self._names: list[str] = []
        self._origins: dict[str, str] = {}
        self._classes: dict[str, str] = {}
        self._sel: int = 0
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        from xlii.active_session import active_xli_dir

        self._address = address if isinstance(address, Address) else Address.parse(address)
        xli_dir = active_xli_dir()
        if xli_dir is None:
            self._names = []
            self._origins = {}
            self._classes = {}
        else:
            from xlii import tasks as T

            entries = T.list_pipeline_entries(xli_dir)
            self._names = [n for n, _o in entries]
            self._origins = dict(entries)
            self._classes = {
                n: T.peek_task_class(T.pipeline_file(xli_dir, n)) for n in self._names
            }
        self._sel = 0
        from xlii.panes.select import select_target

        target = select_target(select, self._address)
        if target:
            for i, name in enumerate(self._names):
                if name == target or select == f"tasks://{name}":
                    self._sel = i
                    break

    def render(self) -> Rendered:
        bound = ""
        try:
            from xlii.active_session import active_session
            from xlii.session_boot import bound_startup_task

            st = active_session()
            root = getattr(getattr(st, "project", None), "project_root", None)
            bound = bound_startup_task(root)
        except Exception:
            bound = ""
        rows = []
        for i, name in enumerate(self._names):
            from xlii.tasks import listing_badge

            tag = listing_badge(
                self._origins.get(name, ""),
                self._classes.get(name, ""),
            )
            bits = [name]
            if tag:
                bits.append(tag)
            if bound and name == bound:
                bits.append("startup")
            label = " · ".join(bits)
            rows.append(
                RenderedRow(text=label, address=f"tasks://{name}", kind="leaf", selected=(i == self._sel))
            )
        return Rendered(title="tasks://", rows=tuple(rows), empty=not rows)

    def selection(self) -> Selection:
        if not self._names:
            return Selection(node=None)
        name = self._names[self._sel]
        return Selection(node=Node(address=f"tasks://{name}", name=name, kind="leaf",
                                   extra={"type": "task"}))

    def actions(self) -> "list[Action]":
        acts: list[Action] = [
            Action(
                "new", "New task…",
                Outcome(RETARGET_SLOT, address="taskmake://"),
            ),
            Action(
                "binds", "Bind chrome…",
                Outcome(RETARGET_SLOT, address="bindmake://"),
            ),
        ]
        if not self._names:
            return acts
        name = self._names[self._sel]
        addr = f"tasks://{name}"
        acts.extend([
            Action("load", "Load into command line", Outcome(PREFILL, addr, text=f"/tasks run {name}")),
            Action("run-bg", "Run in background", Outcome(SPAWN_JOB, addr, text=name)),
            Action("view", "View plan", Outcome(RETARGET_SLOT, addr)),
            Action("edit", "Edit in maker", Outcome(RETARGET_SLOT, address=f"taskmake://{name}")),
        ])
        return acts

    def handle(self, key: str) -> bool:
        if not self._names:
            return False
        if key == "down":
            self._sel = min(self._sel + 1, len(self._names) - 1)
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            return True
        if key == "home":
            self._sel = 0
            return True
        if key == "end":
            self._sel = len(self._names) - 1
            return True
        return False  # enter/back fall through to the surface (actions / close)

    def select_index(self, i: int) -> bool:
        """Select the task at index ``i`` (a mouse click's target row). Returns True if in range."""
        if 0 <= i < len(self._names):
            self._sel = i
            return True
        return False
