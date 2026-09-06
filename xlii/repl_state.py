"""Per-session REPL state: attachments, workspaces, persistence, DeepContext."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from xlii.atomicio import write_text_atomic
from xlii.refs import sanitize_refs

if TYPE_CHECKING:
    from rich.console import Console

    from xlii.agent import Agent
    from xlii.config import GlobalConfig, ProjectConfig
    from xlii.context import DeepContext
    from xlii.persona import Persona
    from xlii.pool import ClientPool


# Process-local handoff for `xlii scratch` (Vector S — interaction-III). The
# `xlii scratch` launcher (cmds/project.py) sets this True right before it hands
# a freshly-built scratch session to cmd_code, so the new REPLState starts in
# scratch mode. Deliberately NOT an env var: that would leak the mode into any
# `xlii` subprocess launched from inside the scratch shell. Consumed exactly once
# at REPLState construction (see the `scratch` field's default_factory).
_PENDING_SCRATCH = False


def _consume_pending_scratch() -> bool:
    """Read-and-clear the pending-scratch handoff flag (one shot)."""
    global _PENDING_SCRATCH
    pending, _PENDING_SCRATCH = _PENDING_SCRATCH, False
    return pending


def arm_pending_scratch() -> None:
    """Set the one-shot handoff flag consumed at the next REPLState construction."""
    global _PENDING_SCRATCH
    _PENDING_SCRATCH = True


def queue_pending_input(state: Any, text: str, *, replace: bool = False) -> None:
    """Seed the next prompt on *state* (review-before-send; never executes).

    ``replace=True`` overwrites (``/git``, ``/xtool``). Default appends
    (``/browse --reference``, ``/howto``). Empty *text* is a no-op. Works on a
    real REPLState or a duck-typed test double.
    """
    if state is None:
        return
    incoming = (text or "").strip()
    if not incoming:
        return
    current = getattr(state, "pending_input", "") or ""
    if replace or not current:
        state.pending_input = incoming
    else:
        state.pending_input = f"{current} {incoming}".strip()


@dataclass
class REPLState:
    """Holds all mutable per-session state for a running REPL.

    This replaces the previous situation where attached docs/refs, plan_mode,
    yolo, temperature overrides, etc. were scattered across Agent, local
    variables in cmd_code / _chat_run_session, and ad-hoc dicts.

    Commands registered in the registry receive this object (via the context)
    and can read/write it cleanly.
    """

    console: "Console"
    agent: "Agent"
    project: "ProjectConfig"
    cfg: "GlobalConfig"
    pool: "ClientPool"

    # Chat-specific
    persona: Optional["Persona"] = None

    # RP2 — the live Profile + explicit dispatch scope. Set by the launchers and
    # mutated in place by the /code<->/chat switch (the active profile is now a
    # first-class, swappable object). `command_scope` is the single source the
    # dispatcher reads (commands.py); None falls back to persona-presence so
    # pre-RP2 callers stay correct. `_profile_stash` remembers each mode's
    # displaced Profile so the reverse /command can restore it.
    profile: Optional[Any] = None
    command_scope: Optional[str] = None
    _profile_stash: Optional[dict] = None
    # RP7: per-surface conversation threads (key → history[1:]). A /chat<->/code
    # switch parks the leaving surface's thread here and restores the target's,
    # so a chat persona never carries (or sees) the code conversation.
    _history_stash: Optional[dict] = None

    # Live shell working directory for the `code` REPL's shell-primary mode.
    # Ephemeral per session (deliberately NOT in save()/load() / session.json) —
    # seeded to project_root in cmd_code. A plain field, NOT a property proxying
    # agent.session: it is genuinely REPL-only with no Agent equivalent.
    shell_cwd: Optional[Path] = None

    # A2: a full-quit disposition flag. /exit·/quit (inline or in the TUI) set it
    # True so the session ends from *anywhere* — the inline loop sees it and
    # raises _QuitSession, which unwinds past every nested layer (e.g. a /tui
    # nested inside the inline REPL) to a single catch at the top-level command.
    # /terminal·/inline leave it False (return to the inline prompt, don't quit).
    # Ephemeral per session, like shell_cwd (never persisted).
    quit_requested: bool = False

    # Episode continuity (code-session-resume P0): the active episode id while
    # /session is on — None by default (place-memory only, no ceremony). The
    # kernel spine snapshots .xlii/sessions/<id>.json each turn while set.
    # Ephemeral per process; the RECORD persists, the flag does not.
    episode_id: Optional[str] = None

    # Flipmode button ([$]/[M], flipmode-visible-repl-shell-toggle v1): when
    # True, bare input goes to the agent instead of the live shell — the user
    # clicked the input prefix to ask-first. Session-scoped and never persisted
    # (a fresh session always starts shell-first, like auto_approve); talk-primary
    # modes (persona/plan/howto/…) stay ask-first regardless of this flag.
    ask_primary: bool = False

    # Typed workbenches (B0): the active workbench row (xlii.workbench.WorkbenchType),
    # resolved from project state at boot (session_boot) and re-pointed by
    # /workbench. Pure data — B1 (panes on the face) and B2 (posture/persona
    # application) read this field; nothing at B0 applies it. Session-scoped;
    # the RECORD is .xlii/workbench.json.
    workbench: Optional[Any] = None

    # --- Scratch mode (Vector S — interaction-III): the never-sync surfacing ---
    # `no_sync` is the never-sync flag — also flipped by `--no-sync`, preview, and
    # `/project switch --no-sync` on a normal project; `_end_of_turn_sync` reads
    # it (sessions.py). `scratch` marks the dedicated scratch mode: an ephemeral,
    # unbound, never-sync session surfaced as its own mode (status `scratch ·
    # no-sync`, placeholder_key/frame_mode). Scratch is *inherently* never-sync, so
    # entering it forces no_sync True (the locked Q3 contract); a dir becomes
    # syncable only via an explicit `xlii code init` (no auto-promote). Both are
    # ephemeral per session (never persisted). A spawned `xlii scratch` session
    # seeds `scratch` via the process-local handoff above; `/scratch` toggles it
    # live. This pairs with Vector J's JobRegistry field as the two pre-agreed
    # additions to this one co-owned state object.
    no_sync: bool = False
    scratch: bool = field(default_factory=_consume_pending_scratch)
    # mojo-keeper D6 sitting memory knob (memory_set door). Session-scoped.
    journal_mute: bool = False

    # Startup-task write/fire seam (proposals/startup-task.md). pending_input is
    # the review-before-send seed (consumed once by the inline prompt / TUI
    # mount). startup_off mutes fires until exit — session-scoped, never in
    # save()/load() (same as freeball). launch_hint is a one-shot competing-hint
    # stamp from boot (loop restore / unclean episode); apply_startup_task
    # consumes it. None of these persist.
    pending_input: str = ""
    startup_off: bool = False
    launch_hint: bool = False

    @property
    def last_shell(self) -> Any:
        return self.agent.session.last_shell

    @last_shell.setter
    def last_shell(self, v: Any) -> None:
        self.agent.session.last_shell = v

    # Vector B (seam #3): the generalized last-output buffer — a view of the same
    # SessionState slot, like last_shell. Storage is the single owner in agent.py.
    @property
    def last_output(self) -> Any:
        return self.agent.session.last_output

    @last_output.setter
    def last_output(self, v: Any) -> None:
        self.agent.session.last_output = v

    # Workspace support (named durable attachment sets)
    current_workspace: str = "main"
    _workspaces: dict = field(default_factory=dict)  # internal

    # Autonomous loop (L0+). Persisted in .xlii/loop-active.json; reference only.
    loop: Any = None

    # Project Shadow journal (JRN-1), code REPL only. A ProjectJournal recorder +
    # teacher; the status strip reads its LIVE state every render. None in chat
    # (the chat pipe is the separate JRN-2 compacter) and until cmd_code builds it.
    journal: Any = None

    # --- Vector J (interaction-iii): background jobs (J's half of the two-pointer) ---
    # The session-owned JobRegistry — long work (a /tasks pipeline, a /loop, a
    # harness session, or III's fleet) tracked as non-blocking jobs and surfaced
    # in the profile bar. Lazily created on first use (xlii.jobs.get_registry);
    # ephemeral + in-memory, NEVER persisted: jobs die with the session (the
    # "never an unsupervised daemon" contract). Per-pipeline durability stays
    # /tasks resume's on-disk TaskRun; only the registry is session-lived. S adds
    # `no_sync`/`scratch` surfacing as the second pre-agreed field on this object.
    job_registry: Any = None

    # --- Vector C: harness modes & orchestration (the one co-owned field) ---
    # The live HarnessSessionRegistry — named persistent ACP sessions plus the
    # foreground-mode pointer. Lazily created on first use
    # (xlii.harness.session.get_registry); ephemeral, never persisted (it holds
    # live subprocess handles). The integrator pairs this with B's `last_output`
    # — the two pre-agreed fields on this one co-owned state object.
    cursor_sessions: Any = None

    @property
    def harness_foreground(self) -> Optional[str]:
        """The foreground harness *name* when a harness is the active mode, else
        None — read like `howto_mode`. A1's `status.placeholder_key` consumes it
        (seam #5) to lead the input frame with the harness's label + color."""
        reg = self.cursor_sessions
        return reg.foreground if reg is not None else None

    # --- Session flags + attachments ---
    # Storage lives in agent.session (the SINGLE owner — see SessionState in
    # agent.py). These properties make REPLState and Agent views of the same
    # object, which is what killed the old sync_to_agent/sync_from_agent
    # round-trip and its reversion bug class.
    @property
    def attached_refs(self) -> list[tuple[str, str]]:
        return self.agent.session.attached_refs

    @attached_refs.setter
    def attached_refs(self, v: list[tuple[str, str]]) -> None:
        self.agent.session.attached_refs = v

    @property
    def attached_docs(self) -> list[tuple[str, str]]:
        return self.agent.session.attached_docs

    @attached_docs.setter
    def attached_docs(self, v: list[tuple[str, str]]) -> None:
        self.agent.session.attached_docs = v

    @property
    def attached_files(self) -> list[dict]:
        return self.agent.session.attached_files

    @attached_files.setter
    def attached_files(self, v: list[dict]) -> None:
        self.agent.session.attached_files = v

    @property
    def plan_mode(self) -> bool:
        return self.agent.plan_mode

    @plan_mode.setter
    def plan_mode(self, v: bool) -> None:
        self.agent.plan_mode = v

    @property
    def discovery_mode(self) -> bool:
        return self.agent.discovery_mode

    @discovery_mode.setter
    def discovery_mode(self, v: bool) -> None:
        self.agent.discovery_mode = v

    @property
    def ops_mode(self) -> bool:
        return self.agent.ops_mode

    @ops_mode.setter
    def ops_mode(self, v: bool) -> None:
        self.agent.ops_mode = v

    @property
    def active_role(self) -> Optional[str]:
        return self.agent.session.active_role

    @active_role.setter
    def active_role(self, v: Optional[str]) -> None:
        self.agent.session.active_role = v

    @property
    def howto_mode(self) -> bool:
        return self.agent.session.howto_mode

    @howto_mode.setter
    def howto_mode(self, v: bool) -> None:
        self.agent.session.howto_mode = v

    @property
    def image_preview_backend(self) -> str:
        return self.agent.session.image_preview_backend

    @image_preview_backend.setter
    def image_preview_backend(self, v: str) -> None:
        self.agent.session.image_preview_backend = v or "auto"

    @property
    def yolo(self) -> bool:
        return self.agent.session.yolo

    @yolo.setter
    def yolo(self, v: bool) -> None:
        self.agent.session.yolo = v

    # Freeball (the-fold Vector C): the trusted-run tier above yolo. A derived
    # view of the same TrustState.tier slot, like yolo above. DELIBERATELY absent
    # from save()/load()/as_context_dict — freeball is session-scoped and never
    # persists (the auto_approve non-persistence convention), so a fresh session
    # always starts safe. See the note in save().
    @property
    def freeball(self) -> bool:
        return self.agent.session.freeball

    @freeball.setter
    def freeball(self, v: bool) -> None:
        self.agent.session.freeball = v

    @property
    def auto_approve(self) -> set[str]:
        return self.agent.session.auto_approve

    @auto_approve.setter
    def auto_approve(self, v: set[str]) -> None:
        self.agent.session.auto_approve = set(v)

    @property
    def session_cost(self) -> float:
        return self.agent.session.session_cost

    @session_cost.setter
    def session_cost(self, v: float) -> None:
        self.agent.session.session_cost = v

    @property
    def session_tokens(self) -> int:
        return self.agent.session.session_tokens

    @session_tokens.setter
    def session_tokens(self, v: int) -> None:
        self.agent.session.session_tokens = v

    @property
    def budget_usd(self) -> Optional[float]:
        return self.agent.session.budget_usd

    @budget_usd.setter
    def budget_usd(self, v: Optional[float]) -> None:
        self.agent.session.budget_usd = v

    @property
    def next_turn_temp_override(self) -> Optional[float]:
        return self.agent.session.next_turn_temp_override

    @next_turn_temp_override.setter
    def next_turn_temp_override(self, v: Optional[float]) -> None:
        self.agent.session.next_turn_temp_override = v

    # --- First-class attachment management ---
    # We treat refs and docs as proper citizens of the session, not just
    # lists hanging off the Agent. This enables clean persistence, better
    # UX, and future features like named attachment sets.

    def attach_ref(self, name: str, collection_id: str) -> None:
        """Attach a persona's memory (collection) to search_project for this session."""
        if not any(n == name for n, _ in self.attached_refs):
            self.attached_refs.append((name, collection_id))
            self.save()  # make durable immediately

    def detach_ref(self, name: str) -> bool:
        """Remove a previously attached ref. Returns True if something was removed."""
        before = len(self.attached_refs)
        self.attached_refs = [(n, c) for n, c in self.attached_refs if n != name]
        if len(self.attached_refs) < before:
            self.save()
            return True
        return False

    def attach_doc(self, name: str, content: str) -> None:
        """Attach a reference document into the system prompt for this session."""
        if not any(n == name for n, _ in self.attached_docs):
            self.attached_docs.append((name, content))
            self.save()

    def detach_doc(self, name: str) -> bool:
        """Remove a previously attached doc. Returns True if something was removed."""
        before = len(self.attached_docs)
        self.attached_docs = [(n, c) for n, c in self.attached_docs if n != name]
        if len(self.attached_docs) < before:
            self.save()
            return True
        return False

    # --- Locker (staged local files; the upload-locker, U1) ---
    # Producers (today: /locker add; later: the /upload popup, mobile, XMPP) stage
    # files here; the agent is the single consumer (run_turn folds enabled entries
    # into the turn). The on/off `enabled` flag is the "share" toggle.

    def attach_file(self, path, *, once: bool = False) -> dict:
        """Stage a local file (enabled by default). Re-adding a known path re-enables
        it and refreshes the once flag. Returns the entry."""
        from pathlib import Path as _P
        from xlii.multimodal import classify_kind

        rp = _P(path).expanduser()
        resolved = str(rp.resolve()) if rp.exists() else str(rp)
        for e in self.attached_files:
            if e.get("path") == resolved:
                e.update(enabled=True, once=once, kind=classify_kind(rp))
                self.save()
                return e
        entry = {
            "name": rp.name or resolved,
            "path": resolved,
            "kind": classify_kind(rp),
            "enabled": True,
            "once": once,
        }
        self.attached_files.append(entry)
        self.save()
        return entry

    def remove_file(self, name_or_path: str) -> bool:
        """Drop an entry from the locker entirely. True if something was removed."""
        before = len(self.attached_files)
        self.attached_files = [
            e for e in self.attached_files
            if e.get("name") != name_or_path and e.get("path") != name_or_path
        ]
        if len(self.attached_files) < before:
            self.save()
            return True
        return False

    def set_file_enabled(self, name_or_path: str, enabled: bool) -> bool:
        """Flip the share toggle on a locker entry. True if a match was found."""
        hit = False
        for e in self.attached_files:
            if e.get("name") == name_or_path or e.get("path") == name_or_path:
                e["enabled"] = bool(enabled)
                hit = True
        if hit:
            self.save()
        return hit

    def live_attachment_paths(self) -> list[str]:
        """Paths of enabled locker entries — what run_turn folds into the turn."""
        return [e["path"] for e in self.attached_files if e.get("enabled")]

    def consume_once_attachments(self) -> None:
        """Disable enabled `--once` entries after they've ridden one turn."""
        changed = False
        for e in self.attached_files:
            if e.get("once") and e.get("enabled"):
                e["enabled"] = False
                changed = True
        if changed:
            self.save()

    def list_attachments(self) -> dict[str, list]:
        """Return a structured view of current attachments."""
        return {
            "refs": self.attached_refs[:],
            "docs": self.attached_docs[:],
            "files": [dict(e) for e in self.attached_files],
        }

    # Future: session persistence id, etc.
    # session_id: Optional[str] = None

    # NOTE: the Coding Rail controller (agent.rail) is intentionally NOT
    # mirrored here. It is a stateful controller object (not a simple flag) and
    # is owned solely by the Agent; the /rail commands mutate it directly via
    # ctx["agent"]. Like plan_mode, it is per-session only (not in session.json).

    def as_context_dict(self) -> dict[str, Any]:
        """Return a dict suitable for the current command registry handlers.
        We keep the dict form for now so existing handlers continue to work
        during the transition.
        """
        return {
            "console": self.console,
            "agent": self.agent,
            "project": self.project,
            "cfg": self.cfg,
            "pool": self.pool,
            "state": self,               # preferred way for new code
            "persona": self.persona,
            "command_scope": self.command_scope,
            "yolo": self.yolo,
            "active_role": self.active_role,   # roles R2; R4 dispatch reads this
        }

    # ------------------------------------------------------------------
    # Durable session state + Named Workspaces
    # ------------------------------------------------------------------

    SESSION_FILENAME = "session.json"
    SESSION_VERSION = 2

    @property
    def session_path(self) -> Path:
        return self.project.xli_dir / self.SESSION_FILENAME

    def save(self) -> None:
        """Persist the important parts of this session to disk (including workspaces)."""
        import time

        # `/project rm` can intentionally delete this session's .xlii/ tree.
        # Generic slash/exit save hooks must not recreate it on the way out.
        if getattr(self, "_project_removed_locally", False):
            return

        # Build workspaces dict
        workspaces = getattr(self, "_workspaces", {})
        current = getattr(self, "current_workspace", "main")

        # Always keep current attachments in the current workspace before saving
        if current not in workspaces:
            workspaces[current] = {}
        workspaces[current]["attached_refs"] = self.attached_refs
        workspaces[current]["attached_docs"] = [[name, content] for name, content in self.attached_docs]
        workspaces[current]["attached_files"] = [dict(e) for e in self.attached_files]
        # NOTE: model/temperature are deliberately NOT auto-mirrored here. They
        # travel with a workspace only via the EXPLICIT /workspace save|load|export
        # (see save_workspace). Auto-capturing the live override on every save()
        # would (a) leak a persona-pinned model into "main" and re-pin it on a
        # later session even after the frontmatter dropped it, and (b) pop a saved
        # model whenever a switch transiently cleared the override — both data-loss
        # foot-guns. The persona frontmatter stays authoritative for session start.

        # NOTE: `freeball` (and its one-shot `freeball_restore` ticket) are
        # deliberately NOT persisted — the trusted-run tier is session-scoped, so a
        # fresh session always starts safe (the auto_approve non-persistence
        # convention; the-fold Vector C). Do NOT add it here. `yolo` persists as
        # before; the freeball tier derives yolo True, so a reopened session
        # returns to at most plain-yolo, never freeball.
        # NOTE: `startup_off`, `pending_input`, and `launch_hint` are also
        # session-scoped — never persist them. A fresh session always starts
        # unmuted with an empty prompt.
        data = {
            "version": self.SESSION_VERSION,
            "current_workspace": current,
            "workspaces": workspaces,
            "yolo": self.yolo,
            "next_turn_temp_override": self.next_turn_temp_override,
            "last_persisted": time.time(),
        }

        write_text_atomic(
            self.session_path, json.dumps(data, indent=2, ensure_ascii=False), mode=0o644
        )

    def load(self) -> bool:
        """Load persisted state (with workspace support). Returns True if meaningful state was restored."""
        path = self.session_path
        if not path.exists():
            self.current_workspace = "main"
            self._workspaces = {}
            return False

        try:
            data = json.loads(path.read_text())
        except Exception:
            return False

        version = data.get("version", 1)

        # Migration from v1 (flat attachments)
        if version == 1:
            self.current_workspace = "main"
            self._workspaces = {
                "main": {
                    "attached_refs": sanitize_refs(data.get("attached_refs", [])),
                    "attached_docs": data.get("attached_docs", []),
                    "attached_files": data.get("attached_files", []),
                }
            }
        else:
            self.current_workspace = data.get("current_workspace", "main")
            self._workspaces = data.get("workspaces", {})

        # Load the *current* workspace's attachments into the live fields
        current_data = self._workspaces.get(self.current_workspace, {})
        self.attached_refs = sanitize_refs(current_data.get("attached_refs", []))
        self.attached_docs = current_data.get("attached_docs", [])
        self.attached_files = current_data.get("attached_files", [])
        # model/temperature are NOT auto-restored at session start (see save()):
        # the persona frontmatter is authoritative there; a workspace's pinned
        # model applies only on an explicit /workspace load.

        self.yolo = data.get("yolo", self.yolo)
        self.next_turn_temp_override = data.get("next_turn_temp_override", self.next_turn_temp_override)

        has_content = bool(
            self.attached_refs or self.attached_docs or self.attached_files
            or self.next_turn_temp_override is not None
        )
        return has_content

    # --- Loadout (model/temperature) capture/restore for workspaces ---
    # RP5: a workspace is a saved *loadout* — attachments PLUS the model and
    # temperature overrides. These move the overrides between the live agent and
    # a workspace dict, and run ONLY for explicit /workspace save|load|export
    # (never on the implicit per-turn save()/load() — see the notes there). Both
    # defend against a missing/fake agent so a bare REPLState never raises.

    def _capture_overrides(self, slot: dict) -> None:
        """Snapshot the live agent's model/temperature overrides into `slot`
        (absent override → key cleared, so a saved loadout reflects the live one)."""
        agent = getattr(self, "agent", None)
        mo = getattr(agent, "model_override", None)
        to = getattr(agent, "temperature_override", None)
        if mo:
            slot["model"] = mo
        else:
            slot.pop("model", None)
        if to is not None:
            slot["temperature"] = to
        else:
            slot.pop("temperature", None)

    def _restore_overrides(self, slot: dict) -> None:
        """Apply a saved loadout's model/temperature onto the live agent. Called
        only by the explicit /workspace load — the user asking for that loadout —
        so it restores unconditionally (absent keys are a no-op)."""
        agent = getattr(self, "agent", None)
        if agent is None:
            return
        model = slot.get("model")
        if model:
            agent.model_override = model
        temp = slot.get("temperature")
        if temp is not None:
            agent.temperature_override = temp

    # --- Workspace management (named durable attachment sets) ---

    def save_workspace(self, name: str) -> None:
        """Save the current loadout (attachments + model/temp) into a named workspace."""
        if not hasattr(self, "_workspaces"):
            self._workspaces = {}

        slot = {
            "attached_refs": list(self.attached_refs),
            "attached_docs": [[n, c] for n, c in self.attached_docs],
            "attached_files": [dict(e) for e in self.attached_files],
        }
        self._capture_overrides(slot)
        self._workspaces[name] = slot
        self.current_workspace = name
        self.save()

    def load_workspace(self, name: str) -> bool:
        """Load a named workspace's loadout (attachments + model/temp). True on success."""
        if not hasattr(self, "_workspaces"):
            self._workspaces = {}

        if name not in self._workspaces:
            return False

        ws = self._workspaces[name]
        self.attached_refs = sanitize_refs(ws.get("attached_refs", []))
        self.attached_docs = list(ws.get("attached_docs", []))
        self.attached_files = [dict(e) for e in ws.get("attached_files", [])]
        self._restore_overrides(ws)        # explicit load → the loadout's model/temp win
        self.current_workspace = name
        self.save()
        return True

    def list_workspaces(self) -> list[str]:
        if not hasattr(self, "_workspaces"):
            self._workspaces = {}
        return sorted(self._workspaces.keys())

    def delete_workspace(self, name: str) -> bool:
        if not hasattr(self, "_workspaces"):
            self._workspaces = {}
        if name in self._workspaces:
            del self._workspaces[name]
            if self.current_workspace == name:
                self.current_workspace = "main"
            self.save()
            return True
        return False

    def get_current_workspace(self) -> str:
        return getattr(self, "current_workspace", "main")

    # ------------------------------------------------------------------
    # DeepContext support (for Grok Build bridging)
    # ------------------------------------------------------------------

    def get_workspace_data(self, name: str) -> dict[str, Any] | None:
        """Return the raw data for any workspace (even if not currently loaded)."""
        workspaces = getattr(self, "_workspaces", {})
        return workspaces.get(name)

    def create_deep_context(
        self,
        workspace_name: str | None = None,
        name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> "DeepContext":
        """Create a DeepContext from any workspace (even if not currently loaded)."""
        from xlii.context import DeepContext
        import uuid

        ws_name = workspace_name or self.get_current_workspace()
        ws_data = self.get_workspace_data(ws_name) or {}

        # Get tools from the project (these are always current)
        from xlii.tools import _PROJECT_TOOLS
        tools = [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
                "parallel_safe": t.parallel_safe,
                "plan_mode_safe": t.plan_mode_safe,
                "worker_safe": t.worker_safe,
            }
            for t in _PROJECT_TOOLS.values()
        ]

        ctx_name = name or f"{self.project.name}:{ws_name}"

        return DeepContext(
            id=str(uuid.uuid4()),
            name=ctx_name,
            description=description,
            source_project_path=str(self.project.project_root),
            source_workspace_name=ws_name,
            attached_refs=sanitize_refs(ws_data.get("attached_refs", [])),
            attached_docs=ws_data.get("attached_docs", []),
            tools=tools,
            tags=tags or [],
        )

    # ------------------------------------------------------------------
    # Global / shareable loadouts (exported JSON bundles)
    # ------------------------------------------------------------------

    from xlii.loadout_paths import GLOBAL_LOADOUTS_DIR, GLOBAL_WORKSPACES_DIR  # noqa: F401

    @classmethod
    def _global_loadouts_dir(cls) -> Path:
        from xlii.loadout_paths import resolve_global_loadouts_dir
        return resolve_global_loadouts_dir(for_write=True)

    def export_workspace(self, name: str) -> bool:
        """Export the current loadout to ~/.config/xlii/loadouts/ for import elsewhere."""
        path = self._global_loadouts_dir() / f"{name}.json"
        data = {
            "version": 1,
            "attached_refs": list(self.attached_refs),
            "attached_docs": [[n, c] for n, c in self.attached_docs],
            "attached_files": [dict(e) for e in self.attached_files],
        }
        self._capture_overrides(data)   # RP5: a shared loadout carries its model/temp too
        write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False), mode=0o644)
        return True

    def import_workspace(self, global_name: str, as_name: str | None = None) -> bool:
        """Import a global loadout into this project's saved loadout slots."""
        from xlii.loadout_paths import find_global_loadout_path
        path = find_global_loadout_path(global_name)
        if path is None:
            return False

        try:
            data = json.loads(path.read_text())
        except Exception:
            return False

        local_name = as_name or global_name

        if not hasattr(self, "_workspaces"):
            self._workspaces = {}

        slot = {
            "attached_refs": sanitize_refs(data.get("attached_refs", [])),
            "attached_docs": data.get("attached_docs", []),
            "attached_files": data.get("attached_files", []),
        }
        if data.get("model"):
            slot["model"] = data["model"]
        if data.get("temperature") is not None:
            slot["temperature"] = data["temperature"]
        self._workspaces[local_name] = slot
        self.save()
        return True

    @classmethod
    def list_global_workspaces(cls) -> list[str]:
        from xlii.loadout_paths import global_loadout_read_dirs
        names: set[str] = set()
        for root in global_loadout_read_dirs():
            if root.exists():
                names.update(p.stem for p in root.glob("*.json"))
        return sorted(names)

    @classmethod
    def delete_global_workspace(cls, name: str) -> bool:
        from xlii.loadout_paths import global_loadout_read_dirs
        # Remove the name from EVERY root, not just the first match: a name that
        # exists in both the canonical and legacy dirs would otherwise survive in
        # the legacy shadow while delete reported success — and list_global_workspaces
        # (which unions across roots) would keep showing it.
        deleted = False
        for root in global_loadout_read_dirs():
            path = root / f"{name}.json"
            if path.exists():
                path.unlink()
                deleted = True
        return deleted

    def format_status(self, include_attachments: bool = True) -> str:
        """Return a nicely formatted multi-line status string for /status."""
        lines = []

        ws = self.get_current_workspace()
        if ws != "main":
            lines.append(f"  loadout slot:  [cyan]{ws}[/cyan]")

        lines.append(f"  yolo:          {'[red]ON[/red]' if self.yolo else 'off'}")
        if self.freeball:
            lines.append(
                "  freeball:      [bold white on red] ON [/bold white on red] "
                "[dim]— trusted-run tier: spend + per-action prompts auto-yes; "
                "delete-guard, /admin, /budget still hold. /safe to stand down.[/dim]"
            )
        if not self.yolo:
            aa = self.auto_approve
            if aa:
                lines.append(f"  auto-approve:  {', '.join(sorted(aa))}")
            else:
                lines.append("  auto-approve:  [dim](none — all gated categories prompt)[/dim]")

        if self.next_turn_temp_override is not None:
            lines.append(f"  temp override: [yellow]{self.next_turn_temp_override}[/yellow] (next turn only)")

        if include_attachments:
            refs = self.attached_refs
            docs = self.attached_docs

            if refs or docs:
                lines.append("")
                lines.append("[bold]Attachments (durable):[/bold]")

                if refs:
                    from xlii.refs import BOOKMARK, ref_view
                    lines.append("  Memory refs:")
                    for entry in refs:
                        v = ref_view(entry)
                        if v.target_type == BOOKMARK:
                            tail = "→ bookmark (saved turn)"
                        else:
                            tail = v.target[:24] + "…" if len(v.target) > 24 else v.target
                        lines.append(f"    · [cyan]{v.name}[/cyan]  [dim]{tail}[/dim]")

                if docs:
                    lines.append("  Reference docs:")
                    for name, content in docs:
                        size = len(content)
                        lines.append(f"    · [cyan]{name}[/cyan]  [dim]{size:,} bytes[/dim]")

        return "\n".join(lines) if lines else "  (no special session state)"

    def clear_persisted(self) -> None:
        """Delete the persisted session (nuclear option for the user)."""
        path = self.session_path
        if path.exists():
            path.unlink()

    def clear_attachments(self) -> None:
        """Remove all refs, docs, and locker files from this session and persist."""
        self.attached_refs.clear()
        self.attached_docs.clear()
        self.attached_files.clear()
        self.save()
