"""F10 — the Task Builder: compose a ``/tasks`` pipe step by step, then run or save it.

The commander face of the automation stack (handoff §5 Vector A). Tasks are the automation
unit — the ladder is *action → recipe → routine → Conductor* — and this wizard is the assisted
on-ramp: pick steps (shell · ``?agent`` · ``/slash``), build the ``|>`` chain, then **run**
(optionally ``--background``), **save** it as a named ``.xlii/tasks/*.toml`` recipe, or hand the
intent to xlii to **draft** the steps (``/tasks new --from``).

V2c (godzilla-mothra): the builder is a **pane**, not a popup — the minibuffer rule. Steps
are rows; Enter claims THE input line (CLAIM_INPUT) to edit the selected step; the footer
actions add / run / run-bg / save / draft ride the Dock's outcome seams (PREFILL for the
run/draft exits — review-before-run; CLAIM_INPUT for add/edit/save-name/draft-intent).
Backspace removes the selected step. The exits never execute from the surface: run/draft
seed the ONE command line; save writes the recipe via the app's sink.

Two pieces, split so the model is testable without a terminal (the ``menu_bar`` pattern):

* :class:`PipeDraft` + :func:`quote_arg` + :func:`intent_slug` + :func:`draft_command` — pure.
  The draft owns the ordered step list and renders the three exits (inline command · TOML ·
  agent-draft command).
* :class:`TaskBuilderPane` — the headless pane over a draft. The app injects its sinks
  (``on_change`` repaint · ``on_error`` toast · ``on_save`` recipe writer · ``on_prefill``
  command-line seeder) when it places the pane into the Dock; the pane itself stays a pure
  composer. The engine stays :mod:`xlii.tasks`; the builder is only a composer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from xlii import tasks as T
from xlii.addressing import Address, Node
from xlii.panes import (
    CLAIM_INPUT,
    PREFILL,
    Action,
    InputClaim,
    Outcome,
    Rendered,
    RenderedRow,
    Selection,
)

# TOML step key per step kind (mirrors the loader's run/ask/slash schema).
_TOML_KEY = {T.KIND_SHELL: "run", T.KIND_AGENT: "ask", T.KIND_SLASH: "slash"}


def quote_arg(text: str) -> str:
    """Wrap ``text`` for the ``/tasks`` argument parser: prefer single quotes, fall back to
    double when the pipe itself contains a ``'``. (The parser matches one outer pair and does
    not un-escape, so a pipe containing BOTH quote chars can't be quoted losslessly — save it
    as TOML instead; single quotes are used as the least-bad fallback.)"""
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    return f"'{text}'"


def intent_slug(intent: str) -> str:
    """A pipeline-name slug from a free-form intent (first few words, task-file safe)."""
    s = re.sub(r"[^a-z0-9]+", "-", (intent or "").lower()).strip("-")
    return "-".join(s.split("-")[:4])[:32].strip("-") or "task"


def draft_command(intent: str) -> str:
    """The agent-drafted exit: seed ``/tasks new <slug> --from <intent>`` (Mode 3 of the tasks
    proposal — the agent writes the ``.toml``, opens it for review, never auto-runs)."""
    return f"/tasks new {intent_slug(intent)} --from {quote_arg(intent.strip())}"


@dataclass
class PipeDraft:
    """The ordered step list being composed. Steps classify by the flip-mode prefix grammar
    (``?…`` agent · ``/…`` slash · else shell) via the one engine chokepoint
    (:func:`xlii.tasks.classify_step`) so the builder can never drift from the runner."""

    steps: list[T.Step] = field(default_factory=list)

    def add(self, raw: str) -> T.Step:
        text = (raw or "").strip()
        if T.PIPE_SEP in text:
            raise T.TaskParseError(
                f"one step at a time — add each {T.PIPE_SEP} stage separately"
            )
        step = T.classify_step(text)  # raises TaskParseError on an empty step
        self.steps.append(step)
        return step

    def remove(self, index: int) -> None:
        if 0 <= index < len(self.steps):
            del self.steps[index]

    def pop(self, index: int) -> Optional[T.Step]:
        """Remove and return a step (the edit-in-place affordance: pop → re-edit → re-add)."""
        if 0 <= index < len(self.steps):
            return self.steps.pop(index)
        return None

    def move(self, index: int, delta: int) -> int:
        """Swap a step ``delta`` places; returns its new index (unchanged when out of range)."""
        j = index + delta
        if 0 <= index < len(self.steps) and 0 <= j < len(self.steps):
            self.steps[index], self.steps[j] = self.steps[j], self.steps[index]
            return j
        return index

    def rows(self) -> list[str]:
        return [f"{i} [{s.kind}] {s.raw or s.body}" for i, s in enumerate(self.steps, 1)]

    def inline(self) -> str:
        """The one-line ``a |> b |> c`` form (round-trips through ``parse_inline``)."""
        return f" {T.PIPE_SEP} ".join(s.raw or s.body for s in self.steps)

    def command(self, *, background: bool = False) -> str:
        """The runnable command line this draft compiles to."""
        cmd = f"/tasks run {quote_arg(self.inline())}"
        return cmd + (" --background" if background else "")

    def toml(self, name: str, description: str = "") -> str:
        """The saved-recipe form (``.xlii/tasks/<name>.toml``) — loadable by
        :func:`xlii.tasks.load_pipeline`. JSON string escaping is valid TOML basic-string
        escaping, so bodies with quotes/newlines survive the round trip."""
        lines = [
            f"# .xlii/tasks/{name}.toml — composed with the F10 task builder.",
            f"# Run it with: /tasks run {name}",
            f"name = {json.dumps(name)}",
        ]
        if description:
            lines.append(f"description = {json.dumps(description)}")
        for s in self.steps:
            lines += ["", "[[step]]", f"{_TOML_KEY[s.kind]} = {json.dumps(s.body)}"]
        return "\n".join(lines) + "\n"


class ClaimPrefillBridge:
    """Sink bridge so pane claim callbacks can chain asks (wired by AppInputSink)."""

    _claim: Optional[Callable[[InputClaim], bool]] = None
    _prefill: Optional[Callable[[str], None]] = None

    @classmethod
    def bind(
        cls,
        *,
        claim: Optional[Callable[[InputClaim], bool]] = None,
        prefill: Optional[Callable[[str], None]] = None,
    ) -> None:
        if claim is not None:
            cls._claim = claim
        if prefill is not None:
            cls._prefill = prefill

    @classmethod
    def chain_claim(cls, claim: InputClaim) -> bool:
        fn = cls._claim
        return fn(claim) if fn is not None else False

    @classmethod
    def prefill(cls, text: str) -> None:
        fn = cls._prefill
        if fn is not None:
            fn(text)


def save_prefill_command(name: str, draft: PipeDraft) -> str:
    """Review-before-save line the TasksPane seeds (writes nothing).

    ``/tasks new <name> --from '<pipe>'`` is the only persist verb in the ``/tasks``
    grammar that carries content: it saves ``.xlii/tasks/<name>.toml`` via the
    agent-draft path and opens it for review. There is no deterministic
    save-with-steps verb; the composed pipe rides as the ``--from`` description.
    """
    if draft.steps:
        return f"/tasks new {name} --from {quote_arg(draft.inline())}"
    return f"/tasks new {name}"


class TaskComposeFlow:
    """Chained CLAIM_INPUT for TasksPane ``new`` (name → steps → PREFILL)."""

    def __init__(
        self,
        *,
        claim: Callable[[InputClaim], bool],
        prefill: Callable[[str], None],
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._claim = claim
        self._prefill = prefill
        self._on_error = on_error or (lambda _msg: None)
        self._name = ""
        self._draft = PipeDraft()

    def start_action(self) -> Action:
        return Action("new", "New task…", Outcome(CLAIM_INPUT, claim=InputClaim(
            prompt="save task pipe as:",
            on_submit=self._on_name,
            on_cancel=self._reset,
        )))

    def _reset(self) -> None:
        self._name = ""
        self._draft = PipeDraft()

    def _on_name(self, text: str) -> None:
        name = (text or "").strip()
        err = ""
        if not name:
            err = "task name required"
        elif not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            err = "task names are letters/digits/._- only"
        if err:
            # Re-ask (like _on_step) with the error visible in the re-claim
            # prompt — the pane wires no on_error sink, so the prompt is the
            # one channel the user actually sees.
            self._on_error(err)
            self._ask_name(err)
            return
        self._name = name
        self._draft = PipeDraft()
        self._ask_step(1)

    def _ask_name(self, note: str = "") -> None:
        prompt = "save task pipe as:"
        self._claim(InputClaim(
            prompt=f"{note} — {prompt}" if note else prompt,
            on_submit=self._on_name,
            on_cancel=self._reset,
        ))

    def _ask_step(self, n: int, note: str = "") -> None:
        prompt = f"step {n} — shell · ?agent · /slash (. to finish):"
        self._claim(InputClaim(
            prompt=f"{note} — {prompt}" if note else prompt,
            on_submit=lambda text, _n=n: self._on_step(_n, text),
            on_cancel=self._reset,
        ))

    def _on_step(self, n: int, text: str) -> None:
        raw = (text or "").strip()
        if raw in ("", "."):
            if not self._draft.steps:
                self._on_error("at least one step required")
                self._ask_step(n, "at least one step required")
                return
            self._prefill(save_prefill_command(self._name, self._draft))
            self._reset()
            return
        try:
            self._draft.add(raw)
        except T.TaskParseError as e:
            self._on_error(str(e))
            self._ask_step(n, str(e))
            return
        self._ask_step(n + 1)


def new_task_action(
    *,
    claim: Optional[Callable[[InputClaim], bool]] = None,
    prefill: Optional[Callable[[str], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
) -> Action:
    """TasksPane ``new`` action — chained asks ending in PREFILL review."""
    return TaskComposeFlow(
        claim=claim or ClaimPrefillBridge.chain_claim,
        prefill=prefill or ClaimPrefillBridge.prefill,
        on_error=on_error,
    ).start_action()


class TaskBuilderPane:
    """The builder as a Dock pane (V2c — the minibuffer rule; replaces TaskBuilderModal).

    Steps are the rows; Enter claims THE input line to edit the selected step (or to add
    the first one); Backspace removes the selected step. The footer actions:

    * ``add`` / ``edit`` — CLAIM_INPUT: the line morphs for the step text;
    * ``run`` / ``run-bg`` — PREFILL: the compiled ``/tasks run '…'`` seeds the command
      line, review-before-run (nothing executes from the surface);
    * ``save`` — CLAIM_INPUT for the recipe name, then the app's writer sink persists
      ``.xlii/tasks/<name>.toml``;
    * ``draft`` — CLAIM_INPUT for the intent, then PREFILLs ``/tasks new … --from …``.

    A composer, not a browser: it is the one sanctioned impure pane (its draft is
    ephemeral app state, not a VFS projection). The app injects the effect sinks at
    placement — ``on_change`` (repaint), ``on_error`` (toast), ``on_save`` (TOML writer),
    ``on_prefill`` (seed the command line) — so the pane itself needs no app knowledge.
    """

    def __init__(
        self,
        draft: "Optional[PipeDraft]" = None,
        *,
        saved: "Optional[list[str]]" = None,
        on_change: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_save: Optional[Callable[[PipeDraft, str], None]] = None,
        on_prefill: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.draft = draft or PipeDraft()
        self._saved = list(saved or [])
        self._sel = 0
        self._on_change = on_change or (lambda: None)
        self._on_error = on_error or (lambda _msg: None)
        self._on_save = on_save or (lambda _draft, _name: None)
        self._on_prefill = on_prefill or (lambda _text: None)
        self._address = Address.parse("tasks://builder")

    # --- the pane contract -----------------------------------------------------

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        # A composer mounts nowhere — its address is a stable synthetic identifier, and
        # the draft is the state (re-mounting must NOT wipe a half-built pipe).
        self._address = address if isinstance(address, Address) else Address.parse(address)

    def render(self) -> Rendered:
        rows = tuple(
            RenderedRow(
                text=f"{i} [{s.kind}] {s.raw or s.body}",
                address=f"tasks://builder/{i}",
                kind="leaf",
                selected=(i - 1 == self._sel),
            )
            for i, s in enumerate(self.draft.steps, 1)
        )
        title = "task builder — Enter edits · ⌫ removes"
        if self._saved:
            title += " · saved: " + " · ".join(self._saved[:8])
        return Rendered(title=title, rows=rows, empty=not rows)

    def selection(self) -> Selection:
        if not self.draft.steps:
            return Selection(node=None)
        i = min(self._sel, len(self.draft.steps) - 1)
        return Selection(node=Node(address=f"tasks://builder/{i + 1}",
                                   name=f"step {i + 1}", kind="leaf"))

    def actions(self) -> "list[Action]":
        acts: list[Action] = []
        if self.draft.steps:
            i = min(self._sel, len(self.draft.steps) - 1)
            step = self.draft.steps[i]
            acts.append(Action("edit", "Edit step", Outcome(CLAIM_INPUT, claim=InputClaim(
                prompt=f"step {i + 1}:",
                initial=step.raw or step.body,
                on_submit=lambda text: self._replace_step(i, text),
            ))))
            acts.append(self._add_action())
            acts.append(Action("run", "Run", Outcome(PREFILL, text=self.draft.command())))
            acts.append(Action("run-bg", "Run in background",
                               Outcome(PREFILL, text=self.draft.command(background=True))))
            acts.append(Action("save", "Save as…", Outcome(CLAIM_INPUT, claim=InputClaim(
                prompt="save task pipe as:",
                on_submit=self._save_as,
            ))))
        else:
            acts.append(self._add_action())
        acts.append(Action("draft", "Draft with xlii", Outcome(CLAIM_INPUT, claim=InputClaim(
            prompt="describe the task — xlii writes the steps:",
            on_submit=lambda text: self._on_prefill(draft_command(text)),
        ))))
        return acts

    def handle(self, key: str) -> bool:
        if key == "down":
            self._sel = min(self._sel + 1, max(0, len(self.draft.steps) - 1))
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            return True
        if key == "home":
            self._sel = 0
            return True
        if key == "end":
            self._sel = max(0, len(self.draft.steps) - 1)
            return True
        if key == "back" and self.draft.steps:
            # Backspace deletes the selected step in the composer (list-editing idiom);
            # with no steps left it falls through so the surface steps out of the pane.
            self.draft.remove(self._sel)
            self._sel = min(self._sel, max(0, len(self.draft.steps) - 1))
            self._on_change()
            return True
        return False  # enter → the surface runs the primary action (edit/add)

    def select_index(self, i: int) -> bool:
        """Select step ``i`` (a mouse click's target row). Returns True if in range."""
        if 0 <= i < len(self.draft.steps):
            self._sel = i
            return True
        return False

    # --- the claim callbacks (fired by the input line after release) -----------

    def _add_action(self) -> Action:
        return Action("add", "Add step", Outcome(CLAIM_INPUT, claim=InputClaim(
            prompt="new step — shell · ?agent · /slash:",
            on_submit=self._add_step,
        )))

    def _add_step(self, text: str) -> None:
        try:
            self.draft.add(text)
        except T.TaskParseError as e:
            self._on_error(str(e))
            return
        self._sel = len(self.draft.steps) - 1
        self._on_change()

    def _replace_step(self, i: int, text: str) -> None:
        try:
            step = T.classify_step(text.strip())
        except T.TaskParseError as e:
            self._on_error(str(e))
            return
        if 0 <= i < len(self.draft.steps):
            self.draft.steps[i] = step
        self._on_change()

    def _save_as(self, name: str) -> None:
        self._on_save(self.draft, name)  # the app validates + writes the TOML
        self._on_change()


__all__ = [
    "ClaimPrefillBridge",
    "PipeDraft",
    "TaskBuilderPane",
    "TaskComposeFlow",
    "draft_command",
    "intent_slug",
    "new_task_action",
    "quote_arg",
    "save_prefill_command",
]
