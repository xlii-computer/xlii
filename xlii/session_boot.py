"""Session boot — the session-assembly seam (godzilla-mothra B4).

One kernel home for "assemble a code or chat session": project load/init →
launch gate → nested-session guard → startup sync → agent/profile/REPLState →
journal → episode continuity → loop restore. The CLI faces
(``cmds/sessions/{code,chat}.py``) keep the argparse shapes, the interactive
prompts (injected as callbacks), the banners, and the REPL/TUI run tails;
``scratch``/``lifecycle`` call these typed verbs instead of fabricating an
``argparse.Namespace`` for cmd_code (the S2 convention, killed here).

Body test: every verb is headless-callable — no ``isatty``/``input()`` here;
interactive prompts arrive as ``ask_*`` callbacks and never fire for a
non-interactive caller. The TUI runner is INJECTED (``run_tui``) — the kernel
never imports the face. Extracted from cmds/sessions/{code,chat}.py,
verbatim where the seam allows.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from xlii.agent import Agent, SessionState
from xlii.client import MissingCredentials
from xlii.config import GlobalConfig, ProjectConfig, GLOBAL_CONFIG_DIR
from xlii.episode import apply_episode_continuity
from xlii.mode_contract import GateContext, GateVerdict, get_mode, trust_tier_rank
from xlii.pool import ClientPool
from xlii.profile import chat_profile, code_profile
from xlii.rail import RailController
from xlii.repl_state import REPLState, arm_pending_scratch
from xlii.session_state import detect_nested_session, mark_session_active
from xlii.sync import init_project, make_preview_project, startup_sync
from xlii.transcript import turns_to_history
from xlii.turn_store import CHAT_RECENT_TURNS
from xlii.ui import console as _shared_console


def _restore_chat_tier(state: Any, cfg: Any) -> None:
    """Copy the sticky ``/tier`` pick from config onto this session."""
    sess = getattr(getattr(state, "agent", None), "session", None)
    if sess is None:
        return
    raw = str(getattr(cfg, "chat_tier", "") or "").strip().lower()
    if raw in ("off", "none", "clear"):
        sess.chat_tier = None
        return
    if raw == "":
        return
    try:
        from xlii.chat_tiers import AUTO, normalize_tier

        key = AUTO if raw == AUTO else normalize_tier(raw)
        if key:
            sess.chat_tier = key
    except Exception:
        # An unresolvable tier leaves the session on its default.
        pass


def resolve_launch(
    root: Path,
    *,
    preview: bool = False,
    init: bool = False,
    launch: bool = False,
    project: Optional[ProjectConfig] = None,
    interactive: bool = False,
    ask: Optional[Callable[[Any, Path], str]] = None,
) -> str:
    """Decide how to launch: explicit flags win, then non-interactive defaults,
    else the crude gate. Returns 'launch' | 'preview' | 'init' | 'cancel' |
    'refuse' ('refuse' = non-project with no way to proceed).

    ``ask(project, root) -> choice`` is the interactive prompt seam (the
    face's rich gate); it fires only when ``interactive`` and no flag decided —
    a headless caller passes neither and gets the script-safe policy (launch an
    existing project, refuse a non-project), never a blocked prompt."""
    if preview:
        return "preview"
    if init:
        return "init"
    if launch:
        return "launch" if project else "refuse"
    if not interactive or ask is None:
        # Scripts/pipes/daemon: never block on a prompt. Behave like before —
        # launch an existing project, refuse a non-project (pass --preview/--init).
        return "launch" if project else "refuse"
    return ask(project, root)


@dataclass
class GateResult:
    """The code-mode entry gate's answer (V3a): the EntryGate verdict, the
    resolved choice ("launch" | "preview" | "init" | "cancel" | "refuse"), and
    the project to open (post init/preview when applicable)."""

    verdict: GateVerdict
    choice: str
    project: Optional[ProjectConfig]
    preview_state_dir: Optional[Path] = None


def gate_code_entry(
    root: Path,
    *,
    preview: bool = False,
    init: bool = False,
    launch: bool = False,
    interactive: bool = False,
    ask_launch: Optional[Callable[[Any, Path], str]] = None,
    trust_tier: str = "safe",
    loop_active: bool = False,
    surface: str = "code",
    console: Any = None,
    project: Optional[ProjectConfig] = None,
) -> GateResult:
    """THE code-mode entry gate (V3a) — one detect → init → resolve flow for
    both doors: `xlii code` (via build_code_session) and in-session `/code`.

    Expressed via ``mode_contract``'s EntryGate for the `code` mode
    (``requires_project=True``): a project at ``root`` satisfies it and the
    door proceeds; without one the verdict fails and the gate runs the
    remediation flow — explicit flags first, then the interactive ``ask_launch``
    prompt (preview / initialize / cancel), with the script-safe non-interactive
    policy (launch existing, refuse otherwise) when no prompt can fire.
    """
    con = console or _shared_console
    # Reuse a project the caller already loaded (build_code_session loads it for
    # the pool gate) rather than re-reading project.json a second time.
    if project is None:
        project = ProjectConfig.load(root.resolve())
    verdict = get_mode("code").entry.evaluate(GateContext(
        has_project=project is not None,
        trust_tier=trust_tier,
        loop_active=loop_active,
        surface=surface,
    ))
    # A satisfied gate launches — UNLESS the caller passed an explicit
    # --preview/--init, which resolve_launch must still honor (else --preview on
    # an existing synced project silently syncs the Collection it opted out of).
    if verdict.ok and not (preview or init):
        return GateResult(verdict, "launch", project)
    choice = resolve_launch(
        root, preview=preview, init=init, launch=launch, project=project,
        interactive=interactive, ask=ask_launch)
    if choice == "init":
        if project is None:
            # Fast and non-hanging: writes .xlii/project.json with no snapshot
            # and no Collection. The heavy paths stay opt-in via `/sync` or
            # `xlii init --snapshot`.
            project = init_project(None, root.resolve(), name=root.name, local_only=True)
            con.print(
                f"[green]✓[/green] initialized [bold]{project.name}[/bold] "
                "[dim](local-only — /sync to build a search index, or "
                "`xlii init` for a Collection)[/dim]"
            )
        else:
            con.print(f"[dim]already an xlii project ({project.name}) — launching[/dim]")
        return GateResult(verdict, "launch", project)
    if choice == "preview":
        preview_state_dir: Optional[Path] = None
        if project is None:
            # Non-project preview: ephemeral, redirected to a temp state dir the
            # caller cleans up when the session ends. (Preview on an EXISTING
            # project keeps the real project and only skips the startup sync.)
            project = make_preview_project(root)
            preview_state_dir = project.state_dir_override
        return GateResult(verdict, "preview", project, preview_state_dir)
    return GateResult(verdict, choice, project)


def nested_session_guard(con: Any, *, project_root: Optional[Path] = None,
                         force: bool = False) -> bool:
    """Return True to proceed, False to abort. Renders the verdict of the
    kernel's ``detect_nested_session`` (the XLII_SESSION protocol): same-project
    re-entry is blocked with a Panel unless `force`; cross-project nesting only
    warns. One owner of the rendering (was cmds/sessions/nesting.py, which now
    delegates here)."""
    verdict = detect_nested_session(project_root, force=force)
    if verdict.outcome == "blocked":
        from rich.panel import Panel

        con.print(Panel.fit(
            f"You're already inside an xlii session for this project (outer PID {verdict.outer_pid}).\n"
            "Launching another shares its on-disk state (attachments, workspaces,\n"
            "manifest) and races Collection syncs — changes can be silently lost.\n\n"
            "Exit this session first, or run [cyan]/tui[/cyan] to open the TUI in place.\n"
            "To override anyway, pass [cyan]--force[/cyan].",
            title="[yellow]nested session blocked[/yellow]", border_style="yellow"))
        return False
    if verdict.outcome == "forced":
        con.print("[dim]--force: entering a nested session for this project "
                  "(on-disk state may collide)[/dim]")
    elif verdict.outcome == "warn":
        con.print(f"[yellow]heads-up:[/yellow] nested xlii session (outer PID {verdict.outer_pid}) — "
                  "global config is shared; per-project state differs.")
    return True


@dataclass
class CodeSession:
    """Everything the code run-tail needs, assembled by build_code_session."""

    state: Any
    agent: Any
    project: Any
    profile: Any
    cfg: Any
    rail: Any
    total_turns: int
    seeded: int
    preview: bool
    preview_state_dir: Optional[Path]


@dataclass
class BootOutcome:
    """``status``: "ok" | "refused" | "cancelled". ``session`` is set on "ok".

    The face maps status to an exit code (refused → 1, cancelled → 0) — the
    kernel never decides process exits."""

    status: str
    session: Optional[CodeSession] = None
    reason: str = ""


def build_code_session(
    root: Path,
    *,
    cfg: Any = None,
    yolo: bool = False,
    rail: bool = False,
    discovery: bool = False,
    ops: bool = False,
    no_sync: bool = False,
    preview: bool = False,
    init: bool = False,
    launch: bool = False,
    force: bool = False,
    interactive: bool = False,
    resume_episode: Optional[str] = None,
    keep_session: bool = False,
    scratch: bool = False,
    no_startup: bool = False,
    ask_launch: Optional[Callable[[Any, Path], str]] = None,
    ask_episode: Optional[Callable[[str], bool]] = None,
    console: Any = None,
) -> BootOutcome:
    """Assemble a code session end to end (the cmd_code assembly, typed).

    ``root`` is the target directory (displayed as given; resolved on use).
    ``scratch=True`` marks the new REPLState as a scratch session through the
    one-shot process-local handoff (consumed at construction). Interactive
    prompts arrive as ``ask_launch`` (the launch gate) and ``ask_episode``
    (the keep-session offer); non-interactive callers get the script-safe
    policy, never a block.
    """
    con = console or _shared_console
    from xlii.repl_cmds import register_all

    register_all()  # ensure built-in slash commands are in the registry
    cfg = cfg if cfg is not None else GlobalConfig.load()

    project = ProjectConfig.load(root.resolve())

    # A local-only project (and any ephemeral preview, which is also local) never
    # touches Collections, so it does not need a management_api_key — only a synced
    # project does. Gate the requirement on the loaded project so an api-key-only
    # config can still open a local project.
    chat_backend = None
    try:
        pool = ClientPool.from_config(
            cfg, require_management=project is not None and not project.local_only)
    except MissingCredentials as e:
        # A gig-only limb has no xAI pool. The named jobs.gig is still Mojo's
        # mouth — same soul, different larynx. Do not refuse the glass.
        from xlii.farm import job_gig
        from xlii.chat_backend import GigError, resolve_gig_backend

        gig = job_gig(cfg)
        if not gig:
            con.print(f"[red]{e}[/red]")
            return BootOutcome("refused", reason=str(e))
        try:
            chat_backend = resolve_gig_backend(cfg, gig)
        except GigError as ge:
            con.print(f"[red]{e}; gig larynx: {ge}[/red]")
            return BootOutcome("refused", reason=f"{e}; gig larynx: {ge}")
        from types import SimpleNamespace as _NS

        stub = _NS(label=gig, chat=None, xai=None)
        pool = _NS(
            primary=lambda: stub,
            acquire=lambda: stub,
            report_success=lambda c: None,
            report_auth_failure=lambda c: None,
        )
        con.print(f"[dim]gig larynx {gig} — no xAI pool on this limb[/dim]")

    # The ONE code-mode entry gate (V3a): detect → init → resolve, shared with
    # the in-session /code door. Flags + non-interactive shells bypass the
    # prompt; see resolve_launch.
    gate = gate_code_entry(
        root, preview=preview, init=init, launch=launch,
        interactive=interactive, ask_launch=ask_launch, console=con,
        project=project)
    if gate.choice == "refuse":
        con.print(
            f"[red]not an xlii project: {root}[/red] — pass "
            "[cyan]--preview[/cyan] to open it ephemerally (no .xlii) or "
            "[cyan]--init[/cyan] to initialize a local project here."
        )
        return BootOutcome("refused")
    if gate.choice == "cancel":
        con.print("[yellow]cancelled.[/yellow]")
        return BootOutcome("cancelled")
    project = gate.project
    preview_mode = gate.choice == "preview"
    preview_state_dir = gate.preview_state_dir

    # Refuse a nested same-project session BEFORE syncing or taking the screen,
    # so the warning is visible (a TUI launches too fast to read a post-hoc one).
    if not nested_session_guard(con, project_root=project.project_root, force=force):
        return BootOutcome("refused")
    mark_session_active(project.project_root)

    if not preview_mode and not project.local_only and not no_sync:
        with con.status("[cyan]syncing...[/cyan]"):
            outcome = startup_sync(pool.primary(), project, cfg)
        if outcome.status == "failed":
            con.print(
                f"[yellow]startup sync failed ({type(outcome.error).__name__}: {outcome.error})[/yellow]\n"
                "[yellow]degraded mode:[/yellow] search_project will fall back to the "
                "local index; changes stay local until /sync succeeds."
            )

    rail_ctrl = RailController() if rail else None
    agent = Agent(pool=pool, project=project, cfg=cfg, console=con,
                  chat_backend=chat_backend,
                  session=SessionState.from_flat(yolo=yolo))
    if rail_ctrl is not None:
        agent.rail = rail_ctrl
    elif discovery:
        # --discovery and --rail are mutually exclusive (one mode slot); --rail wins.
        agent.discovery_mode = True
    elif ops:
        agent.ops_mode = True

    # Cross-session memory (RP0) via the code Profile (RP1). Re-seed the last N
    # turns so a restart answers "where did we leave off?" from context instead
    # of re-investigating. Code is project-scoped (no persona), so memory lives
    # under project.xli_dir/turns. Seeding the shared `agent` here covers both
    # the inline REPL and the TUI (which inherits this same agent).
    profile = code_profile(project, seed_limit=CHAT_RECENT_TURNS)
    seeded = profile.memory.seed_into(agent)
    total_turns = profile.memory.count()

    if scratch:
        # The scratch handoff flag is consumed once, at REPLState construction
        # below — set it as late as possible so a bail never leaves it stale.
        arm_pending_scratch()
    state = REPLState(console=con, agent=agent, project=project, cfg=cfg, pool=pool)
    state.profile = profile          # the live code Profile (RP2: swappable)
    # Kernel convergence: the ONE live Conversation for this session (panes + TUI reuse it).
    from xlii.conversation import ensure_conversation

    ensure_conversation(state)
    _restore_chat_tier(state, cfg)
    state.command_scope = "code"
    # Typed workbenches (B0): the active type is project state — resolve it
    # from .xlii/ and hang the row on the session so the face (B1) and the
    # posture/persona application (B2) read this same field.
    from xlii.workbench import resolve_active

    state.workbench = resolve_active(project.xli_dir)
    # Apply the loadout: ALWAYS a no-op for code (Loadout(persona=None)) — code
    # memory and loadout are project-local and isolated from every chat persona
    # (RP7); a project's bound_persona is a chat default only. Kept for symmetry
    # with _chat_run_session (where the loadout does materialize the persona's
    # frontmatter), and harmless here.
    profile.loadout.apply(state, project)
    # Preview means "open without syncing" — skip end-of-turn sync too, not just
    # the startup one (the whole point is not to touch the Collection this session).
    state.no_sync = no_sync or preview_mode
    # Live cwd for shell-primary. Home desk (scratch --tauri) keeps *config*
    # under ~/.xlii/scratch/home but the shell roams ~ — not the config dir.
    state.shell_cwd = project.project_root.resolve()
    if scratch:
        try:
            from xlii.project_paths import scratch_home_roam_cwd

            roam = scratch_home_roam_cwd(project)
            if roam is not None:
                state.shell_cwd = roam
        except Exception:  # noqa: BLE001 — shell default is best-effort
            pass

    # Project Shadow journal (JRN-1) — code REPL only. Off by default; honors the
    # per-project --code-auto preference. The status strip reads its live state.
    # dispatch_catchup summarizes any tail a previous session's fast exit deferred,
    # as a background job (visible in /jobs) while this session starts.
    from xlii.journal import build_project_journal, dispatch_catchup
    state.journal = build_project_journal(state)
    dispatch_catchup(state)

    # Episode continuity (code-session-resume P1): CLI doors + the soft offer.
    # One line or one question — never a boot wizard; non-interactive callers
    # get the silent auto-resume policy (never hang a script).
    apply_episode_continuity(
        state, project,
        resume=resume_episode,
        keep_session=keep_session,
        interactive=interactive,
        ask=ask_episode,
        console=con,
    )

    from xlii.loop import LoopController
    loaded_loop = LoopController.load(project.xli_dir, cfg.effective_judges())
    if loaded_loop is not None and loaded_loop.state.status in ("active", "paused", "interrupted"):
        state.loop = loaded_loop
        state.launch_hint = True
        con.print(
            f"[dim][loop] restored · {loaded_loop.state.status} · cycle "
            f"{loaded_loop.state.cycle}/{loaded_loop.state.max_cycles} — "
            "/loop resume to continue[/dim]"
        )

    apply_startup_task(
        state, project, console=con, preview=preview_mode, scratch=scratch,
        no_startup=no_startup, interactive=interactive,
    )

    _apply_default_role(state, project, con)

    try:
        from xlii.session_meter import kick_model_window_refresh

        kick_model_window_refresh()
    except Exception:
        # The window refresh is a warm-up; the meter falls back to cached or default windows.
        pass

    return BootOutcome("ok", CodeSession(
        state=state, agent=agent, project=project, profile=profile, cfg=cfg,
        rail=rail_ctrl, total_turns=total_turns, seeded=seeded,
        preview=preview_mode, preview_state_dir=preview_state_dir,
    ))


@dataclass
class ChatSession:
    """Everything the chat run-tail needs, assembled by start_chat_session."""

    state: Any
    agent: Any
    project: Any
    profile: Any
    cfg: Any
    persona: Any
    total_turns: int
    recent_count: int


def ensure_persona_project(persona: Any, pool: ClientPool, *, console: Any = None,
                           local_only: bool = False) -> Optional[ProjectConfig]:
    """Each persona is a real XLI project (Collection-backed) — first run
    initializes it; subsequent runs just load it. None on init failure (the
    caller bails with rc 1).

    ``local_only`` initializes without a Collection (no management key needed) —
    the fabric-node path, where the persona runs on its identity + local memory
    and the shared remote Collection is provisioned by the center."""
    con = console or _shared_console
    # Journal island may still claim chat/ixaac from the pre-split spelling.
    try:
        from xlii.persona import DEFAULT_PERSONA_ID, _adopt_legacy_unnamed_ixaac

        if getattr(persona, "name", "") == DEFAULT_PERSONA_ID:
            _adopt_legacy_unnamed_ixaac()
    except Exception:
        # Legacy adoption is a one-time convenience -- boot continues on the current persona.
        pass
    persona.project_root.mkdir(parents=True, exist_ok=True)
    project = ProjectConfig.load(persona.project_root)
    if project is None:
        con.print(f"[dim]initializing persona project for [bold]{persona.name}[/bold]…[/dim]")
        try:
            project = init_project(
                None if local_only else pool.primary(),
                persona.project_root,
                name=f"chat/{persona.name}",
                local_only=local_only,
            )
        except Exception as e:
            con.print(f"[red]could not init persona project: {e}[/red]")
            return None
    return project


def seed_chat_history(profile: Any, persona: Any) -> "tuple[list[dict], int, int]":
    """The chat agent's initial history via the chat Profile's TurnStore (RP1):
    persona's system prompt + last N turns, pre-built so Agent.__post_init__
    captures the persona prompt as its base (the chat seed-BEFORE-construction
    path). Returns ``(history, total_turns, recent_count)`` — the two counts
    feed the session banner (one ``recent_turns`` load serves both)."""
    recent = profile.memory.recent_turns()
    total_turns = profile.memory.count()
    history: list[dict] = [{"role": "system", "content": persona.system_prompt()}]
    history.extend(turns_to_history(recent))
    return history, total_turns, len(recent)


def start_chat_session(
    persona: Any,
    *,
    cfg: Any = None,
    yolo: bool = False,
    force: bool = False,
    is_restart: bool = False,
    console: Any = None,
) -> Optional[ChatSession]:
    """Assemble a chat session over ``persona`` (the _chat_run_session assembly,
    typed). None on refusal/failure (the face maps that to rc 1)."""
    con = console or _shared_console
    from xlii.repl_cmds import register_all

    register_all()  # ensure built-in slash commands are in the registry
    cfg = cfg if cfg is not None else GlobalConfig.load()
    try:
        pool = ClientPool.from_config(cfg)
    except MissingCredentials as e:
        con.print(f"[red]{e}[/red]")
        return None

    project = ensure_persona_project(persona, pool, console=con)
    if project is None:
        return None

    # Guard real entry only — an in-session /switch reuses this function in the
    # same process and must not be mistaken for a nested launch.
    if not is_restart:
        if not nested_session_guard(con, project_root=project.project_root, force=force):
            return None
    mark_session_active(project.project_root)

    # Other bodies' diary comes home before the memory sync + seed, so this
    # session recalls what you told the VM. No-op with no roster or if the
    # interval hasn't elapsed. A failed pull never blocks entry.
    try:
        from xlii.fabric import maybe_auto_pull

        batch = maybe_auto_pull(cfg, console=con)
        if batch is not None and batch.pulled_turns():
            con.print(f"[dim]fabric: pulled {batch.pulled_turns()} turn(s) from other bodies[/dim]")
    except Exception as e:
        con.print(
            f"[dim]fabric: auto-pull failed ({type(e).__name__}: {e}); continuing[/dim]"
        )

    # Sync any prior turn-files (catches edits made between sessions). A cloud
    # persona pushes new turns to its Collection; a local-only persona skips the
    # network but STILL rebuilds its local FTS index over the turn files, so
    # long-term memory (older turns, via search_project) is searchable offline —
    # sync_project treats a local_only project as an index-rebuild + return.
    with con.status("[cyan]syncing memory before chat…[/cyan]"):
        outcome = startup_sync(
            None if project.local_only else pool.primary(), project, cfg
        )
    if outcome.status == "ok" and not project.local_only:
        con.print(f"[dim]sync: {outcome.stats.summary()}[/dim]")
    if outcome.status == "failed":
        con.print(
            f"[yellow]startup sync failed ({type(outcome.error).__name__}: {outcome.error}) — "
            "continuing without sync; /sync to retry[/yellow]"
        )

    # Build the agent's initial history via the chat Profile's TurnStore (RP1):
    # persona's system prompt + last N turns, pre-built so Agent.__post_init__
    # captures the persona prompt as its base (the chat seed-BEFORE-construction
    # path).
    profile = chat_profile(persona, project, seed_limit=CHAT_RECENT_TURNS)
    history, total_turns, recent_count = seed_chat_history(profile, persona)

    agent = Agent(
        pool=pool,
        project=project,
        cfg=cfg,
        history=history,
        console=con,
        session=SessionState.from_flat(yolo=yolo, conversational=True),
    )

    state = REPLState(
        console=con,
        agent=agent,
        project=project,
        cfg=cfg,
        pool=pool,
        persona=persona,
    )
    state.profile = profile          # the live chat Profile (RP2: swappable)
    # Kernel convergence: the ONE live Conversation for this session (panes + TUI reuse it).
    from xlii.conversation import ensure_conversation

    ensure_conversation(state)
    _restore_chat_tier(state, cfg)
    state.command_scope = "chat"

    profile.loadout.apply(state, project)

    # Chat is conversational (persona ⇒ bare input is an agent turn, not shell),
    # so it deliberately leaves shell_cwd unset — the title then shows the persona
    # name, and the TUI's `!cmd` falls back to the project root.
    persona.touch_used()  # before any TUI branch so it runs in both paths
    return ChatSession(
        state=state, agent=agent, project=project, profile=profile, cfg=cfg,
        persona=persona, total_turns=total_turns, recent_count=recent_count,
    )


def _apply_default_role(state: Any, project: Any, con: Any) -> None:
    """Equip the project's `default_role` at boot, if set (roles.md).

    Best-effort and never blocks a session: an unset, unknown, or malformed
    default is a quiet skip (with a dim note for the malformed/unknown case).
    Off by default (default_role=None) — a body opts in per project."""
    name = getattr(project, "default_role", None)
    if not name:
        return
    try:
        from xlii.role import load_role
        from xlii.repl_cmds.role import equip_role_in_code

        role = load_role(name, getattr(project, "project_root", None))
        if role is None:
            con.print(f"[dim][role] default {name!r} not found — skipped[/dim]")
            return
        if role.validate():
            con.print(f"[dim][role] default {name!r} is malformed — skipped[/dim]")
            return
        equip_role_in_code(state, role, project)
        con.print(f"[dim][role] {name} equipped (project default)[/dim]")
    except Exception:
        # A default role must never be the reason a session fails to open.
        pass


def launch_tui_or_inline(
    state: Any,
    agent: Any,
    *,
    project_name: str,
    run_tui: Callable[[Any, Any, str], Any],
    console: Any = None,
) -> str:
    """The --tui probe-and-fallback, one owner for both session doors (D7).

    Returns "quit" (a full /exit·/quit fired in the TUI — the caller ends the
    process, running its own clean-exit policy) or "inline" (textual missing —
    warned and falling back — or /terminal·/inline dropped back: the caller
    continues into its inline REPL loop).

    ``run_tui(state, agent, project_name)`` is the face's TUI runner, INJECTED
    — the kernel never imports the face (import-linter). The textual probe
    itself is third-party and safe here."""
    con = console or _shared_console
    try:
        import textual  # noqa: F401
    except ImportError:
        con.print(
            "[yellow]--tui needs the Textual front-end[/yellow] — install with "
            "[cyan]pip install 'xlii[tui]'[/cyan]. Falling back to the standard REPL."
        )
        return "inline"
    run_tui(state, agent, project_name=project_name)
    # /exit·/quit in the TUI → "quit". /terminal·/inline drops back: "inline",
    # so the same session continues at the caller's inline prompt.
    return "quit" if getattr(state, "quit_requested", False) else "inline"


# --------------------------------------------------------------------------- #
# startup-task — per-machine binding fire (P0 capture + P1 --auto, D17)
# --------------------------------------------------------------------------- #

STARTUP_BINDINGS_FILE = GLOBAL_CONFIG_DIR / "startup.json"
STARTUP_MODE_CAPTURE = "capture"
STARTUP_MODE_AUTO = "auto"


@dataclass(frozen=True)
class StartupBinding:
    """Per-machine project-open ritual binding (never travels with a clone)."""

    task: str
    mode: str = STARTUP_MODE_CAPTURE
    bound_hash: str = ""
    tier: str = "safe"


@dataclass(frozen=True)
class StartupFireResult:
    """What :func:`apply_startup_task` decided to do at session boot."""

    action: str  # "none" | "prefill" | "auto_run" | "downgrade"
    command: str = ""
    notice: str = ""


def _load_startup_store() -> dict[str, dict]:
    if not STARTUP_BINDINGS_FILE.exists():
        return {}
    try:
        data = json.loads(STARTUP_BINDINGS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_startup_store(store: dict[str, dict]) -> None:
    from xlii.atomicio import write_text_atomic

    STARTUP_BINDINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        STARTUP_BINDINGS_FILE,
        json.dumps(store, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )


@contextmanager
def _startup_store_lock() -> Iterator[None]:
    lock_path = STARTUP_BINDINGS_FILE.with_suffix(
        STARTUP_BINDINGS_FILE.suffix + ".lock"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def startup_binding_key(project_root: Path) -> str:
    return str(project_root.resolve())


def load_startup_binding(project_root: Path) -> Optional[StartupBinding]:
    raw = _load_startup_store().get(startup_binding_key(project_root))
    if not raw:
        return None
    try:
        return StartupBinding(
            task=str(raw["task"]),
            mode=str(raw.get("mode", STARTUP_MODE_CAPTURE)),
            bound_hash=str(raw.get("bound_hash", "")),
            tier=str(raw.get("tier", "safe")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def save_startup_binding(project_root: Path, binding: StartupBinding) -> None:
    with _startup_store_lock():
        store = _load_startup_store()
        store[startup_binding_key(project_root)] = {
            "task": binding.task,
            "mode": binding.mode,
            "bound_hash": binding.bound_hash,
            "tier": binding.tier,
        }
        _save_startup_store(store)


def clear_startup_binding(project_root: Path, *, state: Any = None) -> None:
    with _startup_store_lock():
        store = _load_startup_store()
        key = startup_binding_key(project_root)
        if key not in store:
            return
        raw = store.get(key)
        task = ""
        if isinstance(raw, dict):
            task = str(raw.get("task") or "")
        del store[key]
        _save_startup_store(store)
    note_startup_journal(
        state,
        startup_journal_line("clear", task),
        cwd=str(Path(project_root).resolve()),
    )


def pipeline_file_hash(xli_dir: Path, name: str) -> str:
    from xlii import tasks as T

    path = T.pipeline_file(xli_dir, name)
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sanitize_startup_display(text: str) -> str:
    """Strip C0/C1 controls from untrusted TOML display (keep newline/tab)."""
    import unicodedata

    out: list[str] = []
    for ch in text:
        if ch in "\n\t":
            out.append(ch)
            continue
        if unicodedata.category(ch) in ("Cc", "Cf"):
            continue
        out.append(ch)
    return "".join(out)


def _confirm_startup_bind(name: str, *, auto: bool) -> bool:
    """Interactive bind confirm. Fails closed on n / EOF / auto-deny / bg wrap."""
    from xlii.tools import _confirm

    extra = " in auto mode" if auto else ""
    prompt = (
        f"bind startup task {name} for this project on this machine{extra}? [y/N] "
    )
    try:
        answer = _confirm(prompt)
    except (EOFError, KeyboardInterrupt):
        return False
    return (answer or "").strip().lower() in ("y", "yes")


def bind_startup_task(
    project: Any,
    name: str,
    *,
    console: Any,
    auto: bool = False,
    elevated: bool = False,
    tier: str = "safe",
    state: Any = None,
) -> bool:
    """Bind a per-machine startup task for *project*. True if written.

    Prints the pipeline plan (sanitized, labeled a snapshot) and requires an
    interactive confirm that fails closed in agent/background contexts. ``--auto``
    additionally demands elevation — loosening is admin-gated; a drafting turn
    that wrote the pipeline file cannot self-arm.
    """
    con = console or _shared_console
    name = (name or "").strip()
    if not name:
        con.print("[dim]usage: /project startup <task> [--auto][/dim]")
        return False
    if auto and not elevated:
        con.print(
            "[yellow]/project startup --auto needs the 'admin' capability — "
            "run /admin unlock to elevate this session[/yellow]"
        )
        return False
    xli_dir = getattr(project, "xli_dir", None)
    root = Path(str(getattr(project, "project_root", "") or "")).resolve()
    if xli_dir is None or not str(root):
        con.print("[red]startup bind needs an active project[/red]")
        return False
    from xlii import tasks as T
    from xlii.human_gate import is_foreground_console

    try:
        pipeline = T.load_pipeline(Path(xli_dir), name)
    except T.TaskNotFound:
        con.print(f"[red]no saved pipeline named {name!r}[/red]")
        return False
    except T.TaskParseError as e:
        con.print(f"[red]startup bind skipped — {e}[/red]")
        return False
    con.print("[dim]startup bind snapshot[/dim]")
    for ln in T.render_plan(pipeline):
        con.print(_sanitize_startup_display(ln), markup=False)
    if auto:
        con.print(
            "[dim]the approval hashes the pipeline TOML; "
            "shell steps run whatever the repo contains at fire time[/dim]"
        )
    if not is_foreground_console(con):
        con.print("[dim]startup bind requires a foreground confirm[/dim]")
        return False
    if not _confirm_startup_bind(name, auto=auto):
        con.print("[dim]startup bind cancelled[/dim]")
        return False
    live_hash = pipeline_file_hash(Path(xli_dir), name)
    binding = StartupBinding(
        task=name,
        mode=STARTUP_MODE_AUTO if auto else STARTUP_MODE_CAPTURE,
        bound_hash=live_hash,
        tier=(tier or "safe") if auto else "safe",
    )
    save_startup_binding(root, binding)
    mode = "auto" if auto else "capture"
    con.print(
        f"[green]startup task[/green] {name} "
        f"[dim]({mode} · this machine · this project)[/dim]"
    )
    note_startup_journal(
        state,
        startup_journal_line("bind", name, mode="auto" if auto else "confirm"),
        cwd=str(root),
    )
    return True


def startup_show_lines(project: Any) -> list[str]:
    """Human lines for ``/project startup --show`` (hash / missing flags)."""
    root = Path(str(getattr(project, "project_root", "") or "")).resolve()
    binding = load_startup_binding(root)
    if binding is None:
        return ["no startup task bound for this project"]
    xli_dir = getattr(project, "xli_dir", None)
    live_hash = ""
    exists = False
    if xli_dir is not None:
        from xlii import tasks as T

        try:
            T.pipeline_origin(Path(xli_dir), binding.task)
            exists = True
            live_hash = pipeline_file_hash(Path(xli_dir), binding.task)
        except T.TaskNotFound:
            exists = False
        except T.TaskParseError:
            exists = True
            live_hash = pipeline_file_hash(Path(xli_dir), binding.task)
    lines = [
        f"startup task: {binding.task}",
        f"  mode: {binding.mode}",
        f"  bound_hash: {binding.bound_hash or '(none)'}",
        f"  tier: {binding.tier}",
    ]
    if not exists:
        lines.append("  status: pipeline missing")
    elif binding.bound_hash and live_hash and binding.bound_hash != live_hash:
        lines.append("  status: pipeline changed since binding")
    else:
        lines.append("  status: ok")
    return lines


def mute_startup(state: Any) -> None:
    """Session-only mute (``/project startup --off``). Not persisted."""
    if state is None:
        return
    state.startup_off = True
    root = getattr(getattr(state, "project", None), "project_root", None)
    cwd = ""
    try:
        cwd = str(Path(str(root)).resolve()) if root else ""
    except Exception:
        cwd = ""
    note_startup_journal(
        state,
        startup_journal_line("mute", bound_startup_task(root)),
        cwd=cwd,
    )


def startup_who() -> str:
    """Best-effort local account for journal notes (never raises)."""
    try:
        import getpass

        name = (getpass.getuser() or "").strip()
        if name:
            return name
    except Exception:
        pass
    return "user"


def startup_journal_line(
    action: str,
    task: str = "",
    *,
    who: str = "",
    mode: str = "",
    reason: str = "",
) -> str:
    """Concise Project Shadow line: who / what / mode (confirm vs auto)."""
    actor = (who or startup_who()).strip() or "user"
    parts = [f"startup {action}"]
    name = (task or "").strip()
    if name:
        parts.append(name)
    parts.append(f"by {actor}")
    line = " ".join(parts)
    mode_s = (mode or "").strip()
    if mode_s:
        line = f"{line} ({mode_s})"
    reason_s = (reason or "").strip()
    if reason_s:
        line = f"{line} — {reason_s}"
    return line


def note_startup_journal(state: Any, text: str, *, cwd: str = "") -> None:
    """Best-effort journal note. Never raises; bind/fire still succeed."""
    if not (text or "").strip():
        return
    try:
        journal = getattr(state, "journal", None) if state is not None else None
        if journal is None:
            return
        if hasattr(journal, "is_recording"):
            recording = bool(journal.is_recording())
        else:
            recording = bool(getattr(journal, "code_on", False))
        if not recording:
            return
        from types import SimpleNamespace

        journal.observe_turn(
            text.strip(), [], SimpleNamespace(tool_calls=0), cwd=str(cwd or ""),
        )
    except Exception:
        pass


def bound_startup_task(project_root: Any) -> str:
    """Bound startup task name for *project_root*, or ``""``."""
    if project_root is None or not str(project_root).strip():
        return ""
    try:
        binding = load_startup_binding(Path(str(project_root)))
    except Exception:
        return ""
    return binding.task if binding else ""


def startup_run_command(name: str) -> str:
    """The exact ``/tasks run`` line startup-task fires (always printed for auto)."""
    return f"/tasks run {shlex.quote(name)}"


def resolve_startup_mode(
    binding: StartupBinding,
    *,
    session_trust_tier: str,
    live_hash: str,
    pipeline_exists: bool,
) -> tuple[str, str]:
    """Return ``(effective_mode, notice)`` per D17 tier + hash downgrade rules."""
    if not pipeline_exists:
        return STARTUP_MODE_CAPTURE, f"startup task {binding.task!r} missing"
    drifted = bool(
        binding.bound_hash and live_hash and binding.bound_hash != live_hash
    )
    drift_notice = (
        f"pipeline changed since binding — /tasks show {binding.task} to review"
        if drifted else ""
    )
    if binding.mode != STARTUP_MODE_AUTO:
        return STARTUP_MODE_CAPTURE, drift_notice
    if drifted:
        return STARTUP_MODE_CAPTURE, drift_notice
    if trust_tier_rank(session_trust_tier) < trust_tier_rank(binding.tier):
        return STARTUP_MODE_CAPTURE, (
            f"startup auto requires trust tier ≥ {binding.tier} "
            f"(session is {session_trust_tier})"
        )
    return STARTUP_MODE_AUTO, ""


def _startup_precheck(
    state: Any,
    project: Any,
    *,
    competing: bool,
    no_startup: bool = False,
    preview: bool = False,
    scratch: bool = False,
) -> StartupFireResult | None:
    """Evaluate ordered startup guard checks; return skip result when blocked."""
    if competing and state is not None:
        # One-shot: boot-only. Clearing here keeps switch-time firing coherent.
        state.launch_hint = False
    if no_startup or preview or scratch:
        if no_startup and not preview and not scratch:
            try:
                root = Path(str(getattr(project, "project_root", "") or "")).resolve()
                task = bound_startup_task(root)
                if task:
                    note_startup_journal(
                        state,
                        startup_journal_line("skip", task, reason="--no-startup"),
                        cwd=str(root),
                    )
            except Exception:
                pass
        return StartupFireResult("none")
    if getattr(state, "startup_off", False):
        return StartupFireResult("none")
    return None


def apply_startup_task(
    state: Any,
    project: Any,
    *,
    console: Any = None,
    no_startup: bool = False,
    preview: bool = False,
    scratch: bool = False,
    interactive: bool = False,
) -> StartupFireResult:
    """Fire the per-machine startup binding once per project per session."""
    con = console or _shared_console
    competing = bool(getattr(state, "launch_hint", False))
    blocked = _startup_precheck(
        state,
        project,
        competing=competing,
        no_startup=no_startup,
        preview=preview,
        scratch=scratch,
    )
    if blocked is not None:
        return blocked
    scope = getattr(state, "command_scope", None)
    profile_mode = getattr(getattr(state, "profile", None), "mode", None)
    if scope == "chat" or profile_mode == "chat":
        return StartupFireResult("none")
    from xlii import tasks as T

    if T.pipeline_in_flight():
        return StartupFireResult("none")
    root = Path(str(getattr(project, "project_root", "") or "")).resolve()
    binding = load_startup_binding(root)
    if binding is None:
        return StartupFireResult("none")
    xli_dir = getattr(project, "xli_dir", None)
    if xli_dir is None:
        return StartupFireResult("none")

    try:
        T.pipeline_origin(Path(xli_dir), binding.task)
        pipeline_exists = True
        pipeline = T.load_pipeline(Path(xli_dir), binding.task)
    except T.TaskNotFound:
        pipeline_exists = False
        pipeline = None
    except T.TaskParseError as e:
        # Fail-soft: a malformed bound .toml must not brick session boot.
        con.print(f"[dim]startup task {binding.task} skipped — {e}[/dim]")
        note_startup_journal(
            state,
            startup_journal_line("skip", binding.task, reason=str(e)),
            cwd=str(root),
        )
        return StartupFireResult("none")
    live_hash = pipeline_file_hash(Path(xli_dir), binding.task) if pipeline_exists else ""
    # D17: the session tier lives on agent.session (REPLState has no trust_tier
    # attribute — reading it there would pin the gate to "safe" forever).
    session_tier = getattr(
        getattr(getattr(state, "agent", None), "session", None), "trust_tier", "safe"
    ) or "safe"
    try:
        mode, notice = resolve_startup_mode(
            binding,
            session_trust_tier=session_tier,
            live_hash=live_hash,
            pipeline_exists=pipeline_exists,
        )
    except ValueError as e:
        # Fail-soft: an unknown tier string in the store must not brick boot.
        con.print(f"[dim]startup binding for {binding.task} skipped — {e}[/dim]")
        note_startup_journal(
            state,
            startup_journal_line("skip", binding.task, reason=str(e)),
            cwd=str(root),
        )
        return StartupFireResult("none")
    command = startup_run_command(binding.task)
    fired = getattr(state, "_startup_fired_roots", None)
    if fired is None:
        fired = set()
        state._startup_fired_roots = fired
    if root in fired:
        return StartupFireResult("none")
    if competing:
        con.print("[dim]startup task skipped — competing launch hint[/dim]")
        note_startup_journal(
            state,
            startup_journal_line("skip", binding.task, reason="competing launch hint"),
            cwd=str(root),
        )
        return StartupFireResult("none", notice="competing launch hint")
    if getattr(state, "pending_input", ""):
        con.print("[dim]startup task skipped — queued input[/dim]")
        if notice:
            con.print(f"[dim]{notice}[/dim]")
        note_startup_journal(
            state,
            startup_journal_line("skip", binding.task, reason="queued input"),
            cwd=str(root),
        )
        return StartupFireResult("none", notice=notice or "queued input")
    if not interactive:
        return StartupFireResult("none")
    if mode == STARTUP_MODE_AUTO and pipeline is not None:
        con.print(command, markup=False)
        if notice:
            con.print(f"[dim]{notice}[/dim]")
        fired.add(root)
        note_startup_journal(
            state,
            startup_journal_line("fire", binding.task, mode="auto"),
            cwd=str(root),
        )
        ctx = {"state": state, "console": con, "agent": getattr(state, "agent", None)}
        T.run_pipeline(pipeline, ctx, yes=True, confirm_shell=True)
        return StartupFireResult("auto_run", command=command, notice=notice)
    if not pipeline_exists:
        if notice:
            con.print(f"[dim]{notice}[/dim]")
        note_startup_journal(
            state,
            startup_journal_line("skip", binding.task, reason=notice or "missing"),
            cwd=str(root),
        )
        return StartupFireResult("none", notice=notice)
    from xlii.repl_state import queue_pending_input

    queue_pending_input(state, command, replace=True)
    fired.add(root)
    if notice:
        con.print(f"[dim]{notice}[/dim]")
    con.print(
        f"[dim]startup task {binding.task} ready — Enter to run · Ctrl-C to discard[/dim]"
    )
    action = "downgrade" if binding.mode == STARTUP_MODE_AUTO and mode == STARTUP_MODE_CAPTURE else "prefill"
    note_startup_journal(
        state,
        startup_journal_line(
            "fire", binding.task, mode="confirm",
            reason=notice if action == "downgrade" else "",
        ),
        cwd=str(root),
    )
    return StartupFireResult(action, command=command, notice=notice)
