"""The pane-type contract — the one seam where the four ports compose.

Kernel-rebuild Vector #2 (the pane layer). The address space (Vector #1, :mod:`xlii.addressing`)
proved ``scheme://target`` + a browseable/writable VFS. A **pane** turns that VFS into something
you look at and act on. The design doc's contract (``kernel-rebuild.md`` §"The pane-type
contract"):

    mount(address)   → bind to a VFS address      (storage/VFS port)
    render()         → project to a renderable     (sink port)
    selection()      → expose current selection    (turn-context input)
    actions()        → selection-actions offered   (command port)
    handle(key)      → local nav, never the kernel

The load-bearing rule (``§"State ownership"``):

    **A pane instance is a pure projection of ``(address, selection)``.**
    Destroy and rebuild from those two values → byte-identical.

So a pane owns *only* its address and selection; everything it shows is recomputed by calling
``vfs_list``/``vfs_read``. There is no cached tree — re-projection *is* the refresh. That single
rule is what kills the "TUI reimplements the REPL / no single owner of a turn" desync class: a
surface never holds state the kernel must agree on.

This module is the **headless** contract — a pane is ``(address, selection) → rendered Nodes``,
testable with no terminal. The TUI surface adapts :class:`Rendered`/:class:`Action` to widgets
second; it must never reach into a pane's internals. :class:`~xlii.panes.explorer.ExplorerPane`
is client #1, mirroring how ``xlii/cmds/vfs.py`` was client #1 of addressing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol, runtime_checkable

from xlii.addressing import Address, Node

__all__ = [
    "Selection",
    "RenderedRow",
    "RenderedMedia",
    "Rendered",
    "InputClaim",
    "Outcome",
    "Action",
    "TurnSink",
    "SessionSink",
    "MediaSink",
    "InputSink",
    "ClaimSink",
    "JobSink",
    "Pane",
    # outcome kinds (the design doc's bounded set)
    "NAVIGATE",
    "RETARGET_SLOT",
    "ENQUEUE_TURN",
    "MUTATE_VFS",
    "SPAWN_JOB",
    "ATTACH",
    "DETACH",
    "SHOW_MEDIA",
    "PREFILL",
    "CLAIM_INPUT",
]

# --- selection: the turn-context a pane exposes ------------------------------


@dataclass(frozen=True)
class Selection:
    """What a pane currently has selected — the input the kernel turns into context.

    For an explorer this is the focused :class:`~xlii.addressing.Node` (or ``None`` when the
    listing is empty). It is intentionally tiny: the *address* of the focused node is enough to
    reconstruct the whole pane (the state-ownership rule)."""

    node: Optional[Node] = None

    @property
    def address(self) -> str:
        """The focused node's address, or ``""`` when nothing is selected."""
        return self.node.address if self.node is not None else ""


# --- render projection: a renderable tree, NOT terminal cells ----------------
# The design doc (§"encode no terminal-cell assumptions") fixes the render model as a
# renderable tree. For an explorer V1 a flat list of rows IS that tree. Pure, frozen data so
# two panes built from the same (address, selection) compare byte-identical.


@dataclass(frozen=True)
class RenderedRow:
    """One line of an explorer projection."""

    text: str
    address: str
    kind: str  # "container" | "leaf"
    selected: bool = False
    accent: bool = False  # "this row is live/active" — surfaces mark it (e.g. a green ● rider dot)
    tone: str = ""  # semantic status hint ("added"/"modified"/"removed"/"renamed"/"untracked") — the
    #                 surface maps it to a colour (git status letters); "" = the default row colour.
    # Face Config: native <select> instead of click-to-cycle.
    choices: tuple = ()  # ((value, label), ...)
    value: str = ""


@dataclass(frozen=True)
class RenderedMedia:
    """A non-text body a pane hands the surface to draw (an image). The headless pane produces
    this *descriptor* — it never rasterizes; the surface (terminal graphics) draws it and falls
    back to ``caption`` where graphics aren't available. ``kind`` is the content-class
    (``image``); ``path`` is a filesystem path the renderer can open (image renderers need a
    file — ``""`` when the address has none); ``caption`` is the always-safe text form."""

    kind: str
    address: str
    path: str = ""
    caption: str = ""
    b64: str = ""  # optional inline PNG (a scanned PDF page)


@dataclass(frozen=True)
class Rendered:
    """A pane's projection — what a surface draws, with no surface knowledge of the pane.

    ``rows`` is the text body (listings, file lines); ``media`` is an optional non-text body
    (an image) the surface draws instead — the "rich body" path a pane needs beyond plain rows
    (the image pane today, the styled transcript later)."""

    title: str  # the mounted address
    rows: tuple[RenderedRow, ...] = ()
    empty: bool = False
    media: Optional[RenderedMedia] = None
    form: Optional[dict] = None  # pluginform closed-HTML spec ({html, plugin, action, fields})


# --- actions: bounded outcomes over the selection ----------------------------
# The doc fixes the outcome set: enqueue a turn · mutate the VFS · retarget a slot · spawn a
# job (plus NAVIGATE — re-mount this same pane). A pane RETURNS these as data; it never executes
# kernel effects itself (that's "handle(key) → local nav, never the kernel"). The kernel/surface
# interprets the outcome.

NAVIGATE = "navigate"  # re-mount THIS pane onto outcome.address (local-ish; offered as an action)
RETARGET_SLOT = "retarget_slot"  # open outcome.address into another slot ("open in other pane")
ENQUEUE_TURN = "enqueue_turn"  # feed the selection to the kernel as a turn (AI context-grab)
MUTATE_VFS = "mutate_vfs"  # a VFS write/delete/mkdir over the selection
SPAWN_JOB = "spawn_job"  # background work — the bridge into the automation stack
ATTACH = "attach"  # attach outcome.address to the session so it rides the turn (skill/doc/mark)
DETACH = "detach"  # detach outcome.address from the session (the inverse of ATTACH)
SHOW_MEDIA = "show_media"  # render outcome.address (an image) in the PRIMARY surface (Pane 1 / REPL)
PREFILL = "prefill"  # seed outcome.text into the command line (review-before-run) — no execution
CLAIM_INPUT = "claim_input"  # ask ONE line via the claimed input line (outcome.claim — the minibuffer rule)


@dataclass(frozen=True)
class InputClaim:
    """A one-line ask a pane hands the input-line owner — the :data:`CLAIM_INPUT` payload.

    The minibuffer rule (campaign Fleet rule 3): a panel item that needs input never pops a
    modal — THE input line morphs into that item's input for one ask. This is the ask as pure
    data, the same shape as the XMPP X3 approval protocol: a record any body answers its own
    way (the TUI morphs its input line; a fabric body replies over chat; a test answers
    inline). No surface knowledge here — kernel-side, callback-carried:

    * ``prompt`` — what the claimed line asks for (the relabel text, e.g. ``rename to:``);
    * ``on_submit(text)`` — the completion callback, given the submitted line;
    * ``initial`` — optional text pre-seeded into the claimed line;
    * ``on_cancel()`` — optional; called when the ask ends WITHOUT an answer (the user
      released the line, or the claim was refused). Never called after ``on_submit``.

    The release contract: Esc ALWAYS releases the line back to the normal REPL input,
    restoring its prompt and any stashed draft. The double-claim rule: the input line is
    single-tenant — a claim while one is active is REJECTED, never queued; the dispatcher
    reports the refusal through ``on_cancel`` so the asker always learns the outcome.
    Single-tenancy cuts both ways: another actor seeding the line mid-claim (a
    :data:`PREFILL`) releases the ask first, exactly as Esc would — a seeded command must
    never silently become the ask's answer."""

    prompt: str
    on_submit: Callable[[str], None]
    initial: str = ""
    on_cancel: Optional[Callable[[], None]] = None


@dataclass(frozen=True)
class Outcome:
    """A bounded effect an action asks the kernel to perform.

    ``address`` is the selection the effect acts on; ``text`` carries a prompt for
    :data:`ENQUEUE_TURN` (e.g. "Summarize this file.") or the command line to seed for
    :data:`PREFILL` (e.g. "/tasks run nightly"); ``claim`` carries the
    :class:`InputClaim` ask for :data:`CLAIM_INPUT` (and nothing else)."""

    kind: str
    address: str = ""
    text: str = ""
    claim: Optional[InputClaim] = None


@dataclass(frozen=True)
class Action:
    """A selection-action a pane offers (the command port / F2 menu)."""

    name: str
    label: str
    outcome: Outcome


@runtime_checkable
class TurnSink(Protocol):
    """The kernel's turn owner — what executes an :data:`ENQUEUE_TURN` outcome.

    A pane never runs a turn; it emits the intent and the Dock routes it here. The real sink
    drives the agent and appends to the conversation (which a :class:`~xlii.panes.transcript`
    pane then re-projects); tests use a fake that just appends a turn file. Kept to one verb so
    the seam stays small."""

    def submit(self, prompt: str, *, context: str = "") -> None: ...


@runtime_checkable
class SessionSink(Protocol):
    """The kernel's attachment owner — what executes an :data:`ATTACH` / :data:`DETACH` outcome.

    A pane never mutates the session; it emits the intent (attach/detach *this* address) and the
    Dock routes it here. The real sink dispatches by scheme onto the live ``REPLState`` (attach a
    skill's steps, a doc, a mark's span) via :mod:`xlii.attach`; tests use a fake that records the
    calls. Two verbs, mirroring :class:`TurnSink`'s one — the whole session-mutation seam."""

    def attach(self, address: str) -> None: ...

    def detach(self, address: str) -> None: ...


@runtime_checkable
class MediaSink(Protocol):
    """The kernel's media surface — what executes a :data:`SHOW_MEDIA` outcome.

    A pane never renders pixels; it emits "show *this* image" and the Dock routes it here. The real
    sink draws it in the PRIMARY surface (Pane 1 / the REPL transcript), NOT in the pane — the locker
    is a name list you browse in Pane 2, but the picture appears in the REPL like normal. One verb."""

    def show(self, address: str) -> None: ...


@runtime_checkable
class InputSink(Protocol):
    """The kernel's command-line owner — what executes a :data:`PREFILL` outcome.

    A pane never runs a command; some actions instead *seed* the Dock's input with a command for the
    user to review and run (a saved task → ``/tasks run <name>``, a bookmark → ``/recall <mark>``). The
    pane emits the text; the surface drops it into the input un-executed, cursor at the end. One verb —
    the review-before-run counterpart to :class:`TurnSink`'s fire-and-run."""

    def prefill(self, text: str) -> None: ...


@runtime_checkable
class ClaimSink(Protocol):
    """The input-line owner's second verb — what answers a :data:`CLAIM_INPUT` ask.

    An optional capability on the Dock's input sink (routing duck-types on ``claim``, so a
    prefill-only sink stays a valid :class:`InputSink`): a sink that can morph its input line
    implements it and returns ``True`` when the ask was granted, ``False`` to refuse it (line
    already claimed — the single-tenant rule — or nothing to morph). A refusing sink must NOT
    invoke the claim's callbacks; the dispatcher owns refusal notification (``on_cancel``).
    A body with no input line answers the same ask-shape its own way (the XMPP X3 analog
    answers over the fabric); a sink with no ``claim`` at all makes ``CLAIM_INPUT`` dispatch
    raise — the seam stays explicit, never silently swallowed."""

    def claim(self, claim: InputClaim) -> bool: ...


@runtime_checkable
class JobSink(Protocol):
    """The kernel's job owner — what executes a :data:`SPAWN_JOB` outcome.

    A pane never runs background work; it emits the intent (``address`` = what to run,
    e.g. ``tasks://nightly``; ``text`` = optional label) and the Dock routes it here.
    The real sink dispatches onto the session :class:`~xlii.jobs.JobRegistry`; tests
    use a fake that records the call. One verb — mirrors :class:`TurnSink`."""

    def spawn(self, address: str, *, text: str = "") -> None: ...


# --- the contract ------------------------------------------------------------


@runtime_checkable
class Pane(Protocol):
    """The one interface every pane type implements — the intersection of the four ports.

    A conforming pane is a pure projection of ``(address, selection)``: its only authoritative
    state is the mounted address and the current selection; everything ``render`` shows is
    recomputed from the VFS."""

    @property
    def address(self) -> Address: ...

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        """Bind to ``address``; optionally restore a prior selection (the reconstruct hook)."""
        ...

    def render(self) -> Rendered: ...

    def selection(self) -> Selection: ...

    def actions(self) -> "list[Action]": ...

    def handle(self, key: str) -> bool:
        """Apply a local navigation key. Returns ``True`` if handled, ``False`` if the surface
        should fall through to :meth:`actions` (e.g. Enter on a leaf is not local nav)."""
        ...
