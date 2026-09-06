"""``Dock`` — the kernel-side layout: slots, focus, a pane-type registry, and the executor of
the bounded outcomes a pane offers.

This is the **surface/panes port**, headless. The design doc puts layout in the kernel —
*"Kernel owns session + turn + agent, **and** layout (which type in each slot, which slot
focused)."* A surface (the Textual front-end) is a **projection of the Dock**; the Dock never
imports the surface, so the same layout drives a TUI, a test, or a future GUI. Running the bare
REPL as a 1-slot degenerate Dock, then Slots A/B, is what kills the "TUI reimplements REPL
input" bug class — there is one owner of what's in each slot.

Two jobs:

1. **Pane-type registry** (design-doc open question #3). A pane type registers an ``accepts``
   predicate over the target :class:`~xlii.addressing.Node` and a factory. ``open_address``
   stats the address once and mounts the first type that accepts it — containers → an explorer,
   leaves → a viewer, by default. New pane types register here without touching the Dock.
2. **Outcome executor.** A pane never runs kernel effects; it returns :class:`~xlii.panes.Outcome`
   data. The Dock executes the two *layout* outcomes — ``NAVIGATE`` (re-mount this slot) and
   ``RETARGET_SLOT`` ("open in the other pane"). The non-layout outcomes (``ENQUEUE_TURN`` /
   ``MUTATE_VFS``) need the agent / VFS runner and belong to the kernel above the Dock —
   :meth:`dispatch` raises for them so the seam is explicit, not silently swallowed.
   ``SPAWN_JOB`` routes through :class:`~xlii.panes.JobSink` once the surface wires it
   (Story #1 mechanical half — same shape as the turn / session / media / input sinks).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from xlii.addressing import Address, Node, classify, vfs_stat
from xlii.panes import (
    ATTACH,
    CLAIM_INPUT,
    DETACH,
    ENQUEUE_TURN,
    NAVIGATE,
    PREFILL,
    RETARGET_SLOT,
    SHOW_MEDIA,
    SPAWN_JOB,
    InputSink,
    JobSink,
    MediaSink,
    Outcome,
    Pane,
    SessionSink,
    TurnSink,
)
from xlii.panes.bookmarks import BookmarksPane
from xlii.panes.explorer import ExplorerPane
from xlii.panes.gigwork import GigworkPane
from xlii.panes.git import GitPane
from xlii.panes.home import HomePane
from xlii.panes.image import ImageViewPane
from xlii.panes.pdf import PdfViewPane
from xlii.panes.artifacts import ArtifactsPane
from xlii.panes.canvas import CanvasPane
from xlii.panes.locker import LockerPane
from xlii.panes.plan import PlanPane
from xlii.panes.plan_items import PlanItemsPane
from xlii.panes.projects import ProjectsPane
from xlii.panes.results import ResultsRootPane, ResultsTablePane
from xlii.panes.sources import SourcesPane
from xlii.panes.jobs import JobsPane
from xlii.panes.farm import FarmPane
from xlii.panes.market import MarketPane
from xlii.panes.menu import MenuPane
from xlii.panes.skills import SkillsPane
from xlii.panes.plugins import PluginsPane
from xlii.panes.history import HistoryPane
from xlii.panes.face_config import FaceConfigPane
from xlii.panes.task_make import TaskMakePane
from xlii.panes.plugin_make import PluginMakePane
from xlii.panes.bind_make import BindMakePane
from xlii.panes.gig_make import GigMakePane
from xlii.panes.remote_make import RemoteMakePane
from xlii.panes.jid_make import JidMakePane
from xlii.panes.install import InstallPane
from xlii.panes.plugin_form import PluginFormPane
from xlii.panes.tasks import TasksPane
from xlii.panes.transcript import TranscriptPane
from xlii.panes.view import ViewPane
from xlii.panes.wiki import WikiPane


@dataclass(frozen=True)
class PaneType:
    """A registered pane type: a name, an ``accepts`` gate over the target node, and a factory."""

    name: str
    accepts: Callable[[Node], bool]
    factory: Callable[[], Pane]


class Dock:
    """Named slots over a pane-type registry, with a focus and an outcome executor."""

    def __init__(self, slots: "tuple[str, ...]" = ("A", "B")) -> None:
        if len(slots) < 1:
            raise ValueError("a Dock needs at least one slot")
        self._slot_ids = tuple(slots)
        self._slots: dict[str, Optional[Pane]] = {s: None for s in slots}
        self._focused = slots[0]
        self._registry: list[PaneType] = []
        self._turn_sink: "TurnSink | None" = None
        self._session_sink: "SessionSink | None" = None
        self._media_sink: "MediaSink | None" = None
        self._input_sink: "InputSink | None" = None
        self._job_sink: "JobSink | None" = None
        _register_defaults(self)

    # --- slots & focus -------------------------------------------------------

    @property
    def slot_ids(self) -> "tuple[str, ...]":
        return self._slot_ids

    @property
    def slots(self) -> "dict[str, Optional[Pane]]":
        return dict(self._slots)

    @property
    def focused(self) -> str:
        return self._focused

    def pane(self, slot: Optional[str] = None) -> Optional[Pane]:
        return self._slots[self._require(slot or self._focused)]

    def focus(self, slot: str) -> None:
        self._focused = self._require(slot)

    # --- pane-type registry --------------------------------------------------

    def register_pane_type(self, name: str, accepts: Callable[[Node], bool], factory: Callable[[], Pane]) -> None:
        """Register (or replace) a pane type. The first type whose ``accepts(node)`` is true
        wins; later registrations of the same name replace the earlier one."""
        self._registry = [t for t in self._registry if t.name != name]
        self._registry.append(PaneType(name, accepts, factory))

    def _pane_for(self, node: Node) -> Pane:
        for t in self._registry:
            if t.accepts(node):
                return t.factory()
        raise NotImplementedError(f"no pane type accepts {node.address} ({node.kind})")

    # --- the turn sink (who executes ENQUEUE_TURN) ---------------------------

    def set_turn_sink(self, sink: "TurnSink | None") -> None:
        """Install the kernel's turn owner. Until one is set, ``ENQUEUE_TURN`` raises (no agent
        to run it); once set, :meth:`dispatch` routes enqueued turns here."""
        self._turn_sink = sink

    def set_session_sink(self, sink: "SessionSink | None") -> None:
        """Install the kernel's attachment owner. Until one is set, ``ATTACH``/``DETACH`` raise
        (no session to mutate); once set, :meth:`dispatch` routes attach/detach here."""
        self._session_sink = sink

    def set_media_sink(self, sink: "MediaSink | None") -> None:
        """Install the kernel's media surface. Until one is set, ``SHOW_MEDIA`` raises; once set,
        :meth:`dispatch` routes a "render this image in Pane 1" here."""
        self._media_sink = sink

    def set_input_sink(self, sink: "InputSink | None") -> None:
        """Install the kernel's command-line owner. Until one is set, ``PREFILL`` and
        ``CLAIM_INPUT`` raise; once set, :meth:`dispatch` routes a "seed this command into the
        input" here — and, when the sink also implements :class:`~xlii.panes.ClaimSink`, a
        pane's one-line ask (``CLAIM_INPUT``) too."""
        self._input_sink = sink

    def set_job_sink(self, sink: "JobSink | None") -> None:
        """Install the kernel's job owner. Until one is set, ``SPAWN_JOB`` raises; once set,
        :meth:`dispatch` routes background work here (session :class:`~xlii.jobs.JobRegistry`)."""
        self._job_sink = sink

    # --- opening & navigating ------------------------------------------------

    def add_slot(self, slot: str) -> None:
        """Add a named slot after construction (the face's sticky PDF viewer).

        No-op when the name already exists. New slots start empty.
        """
        if slot in self._slots:
            return
        self._slot_ids = (*self._slot_ids, slot)
        self._slots[slot] = None

    def open_address(self, address: str, *, slot: Optional[str] = None, focus: bool = False) -> Pane:
        """Mount ``address`` into a slot, picking the pane type by the target node's kind. Stats
        once and reuses the normalized node address so the mounted pane round-trips."""
        slot = self._require(slot or self._focused)
        node = vfs_stat(address)
        pane = self._pane_for(node)
        pane.mount(node.address)
        self._slots[slot] = pane
        if focus:
            self._focused = slot
        return pane

    def place(self, slot: str, pane: Pane, *, focus: bool = False) -> Pane:
        """Put an already-built ``pane`` into ``slot`` (when the caller picked the type itself —
        e.g. a transcript over a conversation that may not exist on disk yet, so ``open_address``'s
        stat-based pick would trip). Returns the pane."""
        slot = self._require(slot)
        self._slots[slot] = pane
        if focus:
            self._focused = slot
        return pane

    def dispatch(self, outcome: Outcome, *, from_slot: Optional[str] = None) -> Optional[Pane]:
        """Execute a pane's bounded outcome. Layout outcomes retarget a slot and return the
        affected pane; ``ENQUEUE_TURN`` routes to the turn sink (returns ``None``); the remaining
        outcomes still belong to the kernel above the Dock."""
        from_slot = self._require(from_slot or self._focused)
        if outcome.kind == NAVIGATE:
            dest = str(getattr(outcome, "address", "") or "")
            if dest == "stream" or dest.startswith("stream://"):
                # Stream is a face visual slot, not a dock scheme.
                return self._slots[from_slot]
            # Re-mount the slot in place when staying WITHIN the pane's own scheme
            # (the explorer folder↔file morph keeps its type). Crossing schemes —
            # the home:// hub selecting git:// / tasks:// / … — needs a different
            # pane TYPE, so open a fresh pane picked by the target's kind rather
            # than asking the current pane to re-mount an address it can't render
            # (a HomePane handed "git://" just re-drew the home catalog → dead nav).
            pane = self._slots[from_slot]
            if pane is None:
                return self.open_address(outcome.address, slot=from_slot)
            node = vfs_stat(outcome.address)
            if node.address.split("://", 1)[0] != pane.address.scheme:
                return self.open_address(outcome.address, slot=from_slot, focus=True)
            target = self._pane_for(node)
            if isinstance(pane, target.__class__):
                pane.mount(node.address)
                return pane
            target.mount(node.address)
            self._slots[from_slot] = target
            self._focused = from_slot
            return target
        if outcome.kind == RETARGET_SLOT:
            return self.open_address(outcome.address, slot=self._other_slot(from_slot), focus=True)
        if outcome.kind == ENQUEUE_TURN:
            if self._turn_sink is None:
                raise NotImplementedError("ENQUEUE_TURN: no turn sink on the Dock (the kernel wires the agent)")
            self._turn_sink.submit(outcome.text, context=outcome.address)
            return None
        if outcome.kind in (ATTACH, DETACH):
            if self._session_sink is None:
                raise NotImplementedError(f"{outcome.kind}: no session sink on the Dock (the kernel wires the session)")
            if outcome.kind == ATTACH:
                self._session_sink.attach(outcome.address)
            else:
                self._session_sink.detach(outcome.address)
            return None
        if outcome.kind == SHOW_MEDIA:
            if self._media_sink is None:
                raise NotImplementedError("SHOW_MEDIA: no media sink on the Dock (the kernel wires the surface)")
            self._media_sink.show(outcome.address)
            return None
        if outcome.kind == PREFILL:
            if self._input_sink is None:
                raise NotImplementedError("PREFILL: no input sink on the Dock (the kernel wires the command line)")
            self._input_sink.prefill(outcome.text)
            return None
        if outcome.kind == CLAIM_INPUT:
            if self._input_sink is None:
                raise NotImplementedError("CLAIM_INPUT: no input sink on the Dock (the kernel wires the command line)")
            claim = outcome.claim
            if claim is None:
                raise ValueError("CLAIM_INPUT: the outcome carries no InputClaim (set Outcome.claim)")
            claimer = getattr(self._input_sink, "claim", None)
            if claimer is None:
                raise NotImplementedError(
                    "CLAIM_INPUT: this input sink answers no asks (no claim() — the body must answer its own way)"
                )
            if not claimer(claim) and claim.on_cancel is not None:
                # The single-tenant rule: a refused ask still ENDS — tell the asker. The sink
                # never invokes callbacks on refusal (ClaimSink contract), so this is the one call.
                claim.on_cancel()
            return None
        if outcome.kind == SPAWN_JOB:
            if self._job_sink is None:
                raise NotImplementedError("SPAWN_JOB: no job sink on the Dock (the kernel wires the JobRegistry)")
            self._job_sink.spawn(outcome.address, text=outcome.text)
            return None
        raise NotImplementedError(
            f"{outcome.kind}: a non-layout outcome — the kernel (vfs runner) owns this, not the Dock"
        )

    # --- internals -----------------------------------------------------------

    def _require(self, slot: str) -> str:
        if slot not in self._slots:
            raise KeyError(f"no slot {slot!r} (slots: {', '.join(self._slot_ids)})")
        return slot

    def _other_slot(self, slot: str) -> str:
        """The slot ``RETARGET_SLOT`` opens into — the next slot round-robin (the other one when
        there are two; itself when there is only one degenerate slot)."""
        i = self._slot_ids.index(slot)
        return self._slot_ids[(i + 1) % len(self._slot_ids)]


def _register_defaults(dock: Dock) -> None:
    """The built-in pane types (first match wins, so the specific transcript rule precedes the
    generic container rule): a conv:// conversation browses as a transcript, other containers as
    an explorer, leaves (incl. a single conv:// turn) as a viewer."""
    # home:// hub (Track I) — before generic container/leaf rules.
    dock.register_pane_type(
        "home", lambda node: node.address.startswith("home://"), HomePane
    )
    dock.register_pane_type(
        "transcript", lambda node: node.address.startswith("conv://") and node.kind == "container", TranscriptPane
    )
    # The skills:// ROOT browses as the name list (SkillsPane, before the generic container rule); a
    # single skill (skills://<name>, a leaf) falls through to the text viewer so "view" shows its
    # full description.
    dock.register_pane_type(
        "skills", lambda node: node.address.startswith("skills://") and node.kind == "container", SkillsPane
    )
    # plugins:// catalog (subscribe + structured actions) — face/TUI panel, not a menubar.
    dock.register_pane_type(
        "plugins",
        lambda node: node.address.startswith("plugins://") and node.kind == "container",
        PluginsPane,
    )
    # locker:// ROOT browses as the attached-file name list (select → render in the REPL).
    dock.register_pane_type(
        "locker", lambda node: node.address.startswith("locker://") and node.kind == "container", LockerPane
    )
    # artifacts:// ROOT browses as the made-things gallery (newest-first; select →
    # render in the REPL). Leaves fall through to ImageViewPane via classify.
    dock.register_pane_type(
        "artifacts", lambda node: node.address.startswith("artifacts://") and node.kind == "container", ArtifactsPane
    )
    dock.register_pane_type(
        "canvas", lambda node: node.address.startswith("canvas://"), CanvasPane
    )
    # mark:// ROOT browses as the GLOBAL bookmark library — provenance-tagged, select → seed
    # /ref <mark> into the command line (the ref/recall recall path); a single mark (a leaf) falls
    # through to the text viewer so "view" shows its recall span.
    dock.register_pane_type(
        "bookmarks", lambda node: node.address.startswith("mark://") and node.kind == "container", BookmarksPane
    )
    # wiki:// ROOT browses as the trust-marked page list; a single page (a leaf) falls through
    # to the text viewer so "view" shows its markdown.
    dock.register_pane_type(
        "wiki", lambda node: node.address.startswith("wiki://") and node.kind == "container", WikiPane
    )
    # xwiki:// — the vendor tier (xlii's shipped self-docs) browses through the SAME pane,
    # scope-aware: rows wear ⌂ instead of the ✓/? trust ladder. Tiers never merge.
    dock.register_pane_type(
        "xwiki", lambda node: node.address.startswith("xwiki://") and node.kind == "container", WikiPane
    )
    # tasks:// ROOT browses as the saved-pipeline name list (select → seed /tasks run <name>); a
    # single task (a leaf) falls through to the text viewer so "view" shows its rendered plan.
    dock.register_pane_type(
        "taskmake", lambda node: node.address.startswith("taskmake://"), TaskMakePane
    )
    dock.register_pane_type(
        "pluginmake", lambda node: node.address.startswith("pluginmake://"), PluginMakePane
    )
    dock.register_pane_type(
        "bindmake", lambda node: node.address.startswith("bindmake://"), BindMakePane
    )
    dock.register_pane_type(
        "gigmake", lambda node: node.address.startswith("gigmake://"), GigMakePane
    )
    dock.register_pane_type(
        "remotemake", lambda node: node.address.startswith("remotemake://"), RemoteMakePane
    )
    dock.register_pane_type(
        "jidmake", lambda node: node.address.startswith("jidmake://"), JidMakePane
    )
    dock.register_pane_type(
        "install", lambda node: node.address.startswith("install://"), InstallPane
    )
    dock.register_pane_type(
        "pluginform", lambda node: node.address.startswith("pluginform://"), PluginFormPane
    )
    dock.register_pane_type(
        "tasks", lambda node: node.address.startswith("tasks://") and node.kind == "container", TasksPane
    )
    # The git:// ROOT browses as the sectioned source-control view (GitPane) — matched on the root
    # only (empty key) so the git://diff, git://staged and git://log sub-listings fall through to the
    # generic explorer, and a single diff/commit (a leaf) to the text viewer.
    dock.register_pane_type(
        "git",
        lambda node: node.kind == "container" and node.address.startswith("git://") and not Address.parse(node.address).key,
        GitPane,
    )
    # gigwork:// ROOT browses as the providers + jams view (GigworkPane) — root-only (git's
    # idiom), so a single provider/jam (a leaf) falls through to the text viewer's detail sheet.
    dock.register_pane_type(
        "gigwork",
        lambda node: node.kind == "container" and node.address.startswith("gigwork://") and not Address.parse(node.address).key,
        GigworkPane,
    )
    # plan:// ROOT browses as the plan list with progress + pending-amendment counts (PlanPane);
    # a single plan (plan://<name>, a leaf) falls through to the text viewer so "view" shows the
    # raw file. Root-only match (git's idiom) in case plan:// grows sub-containers later.
    dock.register_pane_type(
        "plan",
        lambda node: node.kind == "container" and node.address.startswith("plan://") and not Address.parse(node.address).key,
        PlanPane,
    )
    # A single plan (plan://<name>, a leaf) opens as its ITEM list — checkboxes,
    # receipts, the amendments queue (plan-surface T1) — not the raw file; the
    # items pane's "View raw file" action retargets to the file:// leaf.
    dock.register_pane_type(
        "plan-items",
        lambda node: node.kind == "leaf" and node.address.startswith("plan://") and bool(Address.parse(node.address).key),
        PlanItemsPane,
    )
    # projects:// ROOT browses as the launch list (B5) — one row per registry
    # entry, workbench-badged; select → seed /project switch <name>.
    dock.register_pane_type(
        "projects", lambda node: node.address.startswith("projects://") and node.kind == "container", ProjectsPane
    )
    # results:// — F2: the ROOT browses providers with stored runs; a named
    # run (results://<name>, a container with a key) opens as its table.
    dock.register_pane_type(
        "results-root",
        lambda node: node.address.startswith("results://") and node.kind == "container" and not Address.parse(node.address).key,
        ResultsRootPane,
    )
    dock.register_pane_type(
        "results-table",
        lambda node: node.address.startswith("results://") and node.kind == "container" and bool(Address.parse(node.address).key),
        ResultsTablePane,
    )
    # sources:// ROOT browses the typed source cards (F3; research's fifth pane).
    dock.register_pane_type(
        "sources", lambda node: node.address.startswith("sources://") and node.kind == "container", SourcesPane
    )
    # jobs:// ROOT is the job board (F3); job detail leaves fall to the viewer.
    dock.register_pane_type(
        "jobs", lambda node: node.address.startswith("jobs://") and node.kind == "container", JobsPane
    )
    # farm:// is the classifieds board (ads + node beacons). jobs:// stays
    # the session background-job chip.
    dock.register_pane_type(
        "farm", lambda node: node.address.startswith("farm://") and node.kind == "container", FarmPane
    )
    dock.register_pane_type(
        "market", lambda node: node.address.startswith("market://") and node.kind == "container", MarketPane
    )
    # menu:// ROOT is the command menu (F3 — menus-do-don't-type).
    dock.register_pane_type(
        "menu", lambda node: node.address.startswith("menu://") and node.kind == "container", MenuPane
    )
    # history:// — input-line scrollback (Track F / face Commands → Input history).
    dock.register_pane_type(
        "history", lambda node: node.address.startswith("history://"), HistoryPane
    )
    # faceconfig:// — Options → Config session knobs (face; full Config is TUI).
    dock.register_pane_type(
        "faceconfig",
        lambda node: node.address.startswith("faceconfig://"),
        FaceConfigPane,
    )
    dock.register_pane_type("explorer", lambda node: node.kind == "container", ExplorerPane)
    # Image / PDF leaves get their own panes (registered before the generic leaf
    # rule so they win); every other leaf falls through to the text viewer.
    dock.register_pane_type("image", lambda node: node.kind == "leaf" and classify(node) == "image", ImageViewPane)
    dock.register_pane_type("pdf", lambda node: node.kind == "leaf" and classify(node) == "pdf", PdfViewPane)
    dock.register_pane_type("view", lambda node: node.kind == "leaf", ViewPane)
