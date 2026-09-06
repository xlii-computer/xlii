"""``TaskMakePane`` — face-native Task+ author. No CLAIM_INPUT.

Click-to-cycle the decisions the TUI builder skipped (shape, params, verdict
labels, split policy). Exits PREFILL a ``/tasks new`` line — review-before-write.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node
from xlii.panes import PREFILL, RETARGET_SLOT, Action, Outcome, Rendered, RenderedRow, Selection
from xlii.task_shapes import (
    SHAPES,
    arm_ring,
    branch_ring,
    draft_intent,
    param_ring,
    policy_ring,
    stock_clone_names,
)

_CAPTION = "caption"


def _next(ring: tuple | list, cur) -> object:
    seq = list(ring)
    if not seq:
        return cur
    try:
        i = seq.index(cur)
    except ValueError:
        return seq[0]
    return seq[(i + 1) % len(seq)]


class TaskMakePane:
    """Knob pane: pick a Task+ shape, seed ``/tasks new``."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="taskmake")
        self._shape: str = "linear"
        self._clone: str = ""
        self._param: str = "base"
        self._branches: str = "yes,no"
        self._arms: str = "a,b"
        self._policy: str = "all"
        self._rows: list[tuple[str, str, str, str]] = []
        self._sel: int = 0
        self._edit_name: str = ""
        self._form_cache: Optional[tuple] = None
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        del select
        self._address = address if isinstance(address, Address) else Address.parse(str(address))
        edit = (
            (self._address.key or "").strip()
            or str(getattr(self._address, "target", "") or "").strip()
        )
        if edit != self._edit_name:
            self._form_cache = None
        self._edit_name = edit
        self._reload()

    def _cached_form(self):
        """Reuse the closed HTML until this task file (or name) actually changes.

        ``render`` runs on every deck snapshot. Rebuilding the srcdoc each time
        shipped a new iframe and the editor blinked.
        """
        edit = getattr(self, "_edit_name", "") or ""
        mtime = 0
        try:
            from pathlib import Path

            from xlii.active_session import active_xli_dir

            xli = active_xli_dir()
            if edit and xli is not None:
                p = Path(xli) / "tasks" / f"{edit}.toml"
                if p.is_file():
                    mtime = p.stat().st_mtime_ns
        except Exception:
            mtime = 0
        key = (edit, mtime)
        cached = getattr(self, "_form_cache", None)
        if cached and cached[0] == key:
            return cached[1]
        form = None
        try:
            from xlii.active_session import active_xli_dir
            from xlii.task_form import form_spec

            form = form_spec(active_xli_dir(), edit)
        except Exception:
            form = None
        self._form_cache = (key, form)
        return form

    def suggested_name(self) -> str:
        if self._clone:
            return f"my-{self._clone}"
        return self._shape

    def scaffold_command(self) -> str:
        name = self.suggested_name()
        if self._clone:
            return f"/tasks new {name} --clone {self._clone}"
        extra = ""
        if self._shape == "params":
            extra = f" --param {self._param}"
        elif self._shape == "verdict":
            extra = f" --branches {self._branches}"
        elif self._shape == "split":
            extra = f" --arms {self._arms} --policy {self._policy}"
        return f"/tasks new {name} --shape {self._shape}{extra}"

    def draft_command(self) -> str:
        intent = draft_intent(
            self._shape,
            param=self._param,
            branches=self._branches,
            arms=self._arms,
            policy=self._policy,
        )
        return f'/tasks new {self.suggested_name()} --from "{intent}"'

    def _reload(self) -> None:
        rows: list[tuple[str, str, str, str]] = []
        rows.append(("hdr:make", "── task maker ──", _CAPTION, ""))
        rows.append((
            "name",
            f"name · {self.suggested_name()}  (edit in the seeded line)",
            "leaf",
            f"prefill:{self.scaffold_command()}",
        ))
        clone_s = self._clone or "none"
        rows.append(("clone", f"start from · {clone_s}", "leaf", "cycle:clone"))
        if not self._clone:
            rows.append(("shape", f"shape · {self._shape}", "leaf", "cycle:shape"))
            if self._shape == "params":
                rows.append(("param", f"param · {self._param}", "leaf", "cycle:param"))
            elif self._shape == "verdict":
                rows.append((
                    "branches",
                    f"verdict labels · {self._branches}",
                    "leaf",
                    "cycle:branches",
                ))
            elif self._shape == "split":
                rows.append(("arms", f"split arms · {self._arms}", "leaf", "cycle:arms"))
                rows.append(("policy", f"join policy · {self._policy}", "leaf", "cycle:policy"))
            elif self._shape == "rc":
                rows.append((
                    "note-rc",
                    "rc · on failure also triage (not either/or)",
                    "leaf",
                    "prefill:" + self.scaffold_command(),
                ))
            elif self._shape == "linear":
                rows.append((
                    "note-lin",
                    "linear · no params / no branches",
                    "leaf",
                    "prefill:" + self.scaffold_command(),
                ))
        rows.append(("hdr:go", "── write ──", _CAPTION, ""))
        rows.append(("scaffold", "scaffold · seed /tasks new (writes on send)",
                     "leaf", f"prefill:{self.scaffold_command()}"))
        rows.append(("draft", "ask xlii to invent a toml (/tasks new --from)",
                     "leaf", f"prefill:{self.draft_command()}"))
        rows.append(("back", "◂ tasks", "leaf", "back"))
        self._rows = rows
        if self._sel >= len(self._rows):
            self._sel = 0
        while self._rows and self._rows[self._sel][2] == _CAPTION:
            self._sel = (self._sel + 1) % len(self._rows)
            if self._sel == 0:
                break

    def render(self) -> Rendered:
        self._reload()
        out = []
        for i, (rid, label, kind, verb) in enumerate(self._rows):
            out.append(RenderedRow(
                text=label,
                address=f"taskmake://{rid}",
                kind=kind,
                selected=(i == self._sel and kind != _CAPTION),
                tone="knob" if verb.startswith("cycle:") else "",
            ))
        form = self._cached_form()
        edit = getattr(self, "_edit_name", "") or ""
        return Rendered(
            title="taskmake:// — the pipe" if not edit else f"taskmake://{edit}",
            rows=tuple(out),
            empty=not out,
            form=form,
        )

    def selection(self) -> Selection:
        if not self._rows:
            return Selection(node=None)
        rid, label, kind, verb = self._rows[self._sel]
        return Selection(node=Node(
            address=f"taskmake://{rid}",
            name=label,
            kind=kind,
            extra={"verb": verb},
        ))

    def actions(self) -> list[Action]:
        if not self._rows:
            return []
        rid, _label, kind, verb = self._rows[self._sel]
        if kind == _CAPTION or not verb:
            return []
        if verb.startswith("cycle:"):
            return [Action("apply", "Cycle", Outcome(PREFILL, "taskmake://", text=""))]
        if verb == "back":
            return [Action("back", "◂ Tasks", Outcome(RETARGET_SLOT, address="tasks://"))]
        if verb.startswith("prefill:"):
            return [Action(
                "seed",
                "Seed into input",
                Outcome(PREFILL, f"taskmake://{rid}", text=verb.split(":", 1)[1]),
            )]
        return []

    def apply_selection(self) -> bool:
        if not self._rows:
            return False
        verb = self._rows[self._sel][3]
        if verb.startswith("cycle:"):
            self._cycle(verb.split(":", 1)[1])
            return True
        return False

    def handle(self, key: str) -> bool:
        if not self._rows:
            return False
        if key == "enter":
            return self.apply_selection()
        if key == "down":
            self._sel = min(self._sel + 1, len(self._rows) - 1)
            while self._sel < len(self._rows) - 1 and self._rows[self._sel][2] == _CAPTION:
                self._sel += 1
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            while self._sel > 0 and self._rows[self._sel][2] == _CAPTION:
                self._sel -= 1
            return True
        return False

    def select_index(self, index: int) -> bool:
        leaves = [i for i, r in enumerate(self._rows) if r[2] != _CAPTION]
        if 0 <= index < len(leaves):
            self._sel = leaves[index]
            return True
        return False

    def _cycle(self, name: str) -> None:
        if name == "shape":
            self._shape = str(_next(SHAPES, self._shape))
        elif name == "clone":
            ring = [""] + stock_clone_names()
            self._clone = str(_next(ring, self._clone))
        elif name == "param":
            self._param = str(_next(param_ring(), self._param))
        elif name == "branches":
            self._branches = str(_next(branch_ring(), self._branches))
        elif name == "arms":
            self._arms = str(_next(arm_ring(), self._arms))
        elif name == "policy":
            self._policy = str(_next(policy_ring(), self._policy))
