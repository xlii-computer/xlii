"""`xlii ask` one-shot command (+ opt-in session continuity, xmpp-next X1)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

from xlii.agent import Agent, SessionState
from xlii.client import MissingCredentials
from xlii.config import GlobalConfig, ProjectConfig
from xlii.pool import ClientPool
from xlii.turn_store import SESSION_SEED_TURNS, ask_session_turns_dir, session_key

from .resolve import _resolve_project_target

# Façade re-exports — the session-dir helpers live in the kernel
# (xlii/turn_store.py, godzilla-mothra B1); these keep the old import paths
# (serve_ws.py, tests) working.
_session_key = session_key
_session_turns_dir = ask_session_turns_dir


def cmd_ask(args: argparse.Namespace) -> int:
    """One-shot: run a single agent turn and print the reply to stdout.

    Headless and capture-friendly — the live UI (streaming, tool previews) goes
    to stderr; only the final reply text reaches stdout, so callers (scripts, the
    XMPP daemon's agent fallback) can capture it cleanly.

    Stateless by default: the turn is not persisted and nothing is synced.
    ``--session <id>`` opts into a persistent conversation keyed by the
    caller-chosen id: the last turns seed this turn's context, and the new turn
    is written back — the multi-turn primitive for the daemon's per-JID
    sessions and any future serve head. ``--new-session`` resets it first.
    """
    from rich.console import Console as _Console

    # --persona routes the turn through the persona (mojo read, fabric F2): the
    # turn runs AS the persona, over the persona's own Collection-backed project,
    # so it recalls the persona's long-term memory (search_project) and wears its
    # identity (system prompt). This is what makes every surface the SAME iXaac.
    # Read-only recall here — memory accrual/write is a later fabric phase.
    persona_name = (getattr(args, "persona", None) or "").strip()
    if persona_name:
        return _cmd_ask_persona(args, persona_name)

    target = _resolve_project_target(getattr(args, "workspace", None))
    if target is None:
        print("ask: could not resolve workspace", file=sys.stderr)
        return 1
    project = ProjectConfig.load(target.resolve())
    if not project:
        print(f"ask: not an xlii project: {target}", file=sys.stderr)
        return 1

    session_id = (getattr(args, "session", None) or "").strip()
    turns_dir: Path | None = None
    if session_id:
        turns_dir = _session_turns_dir(project, session_id)
        if getattr(args, "new_session", False):
            from xlii.transcript import clear_turns
            clear_turns(turns_dir)
    elif getattr(args, "new_session", False):
        print("ask: --new-session requires --session <id>", file=sys.stderr)
        return 1

    cfg = GlobalConfig.load()
    try:
        pool = ClientPool.from_config(cfg)
    except MissingCredentials as e:
        print(f"ask: {e}", file=sys.stderr)
        return 1

    # Live output streams to stderr; stdout carries only the final reply.
    ui = _Console(stderr=True)
    agent = Agent(pool=pool, project=project, cfg=cfg, console=ui,
                  session=SessionState.from_flat(yolo=getattr(args, "yolo", False),
                                                 outbox_dir=_outbox_dir(args)))
    if turns_dir is not None:
        # Seed the conversation so-far after the system prompt; run_turn appends
        # this turn's user message after it (the existing transcript machinery:
        # bounded, chronological, tool noise excluded by design).
        from xlii.turn_store import seed_history
        seed_history(agent, turns_dir, SESSION_SEED_TURNS)
    prompt, attachments = _resolve_voice(
        args.prompt, getattr(args, "attach", None), cfg=cfg, pool=pool, ui=ui)
    try:
        text, _dirty, _stats = agent.run_turn(prompt, attachments=attachments)
    except Exception as e:
        print(f"ask: agent error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if not text:
        from xlii.turn_store import final_reply_from_history
        text = final_reply_from_history(agent.history)
    if turns_dir is not None:
        from xlii.turn_store import persist_turn
        persist_turn(turns_dir, prompt, text)
    print(text or "")
    return 0


def _cmd_ask_persona(args: argparse.Namespace, persona_name: str) -> int:
    """One-shot turn AS a persona — the `/mojo` / daemon path (fabric F2 read + F3
    write).

    Runs over the persona's own project (so `search_project` recalls the
    persona's long-term memory) seeded with its system prompt and recent turns,
    then **persists the turn to the persona's memory** so texting it is a
    *continuing* conversation and its memory grows (journal-local-first). On a
    body that holds the management key (the center) the turn is then **drained
    to the persona's shared remote Collection**, so every other surface recalls
    it; a keyless node keeps it local until the center pulls it. Reuses the
    chat-session assembly headlessly. Reply → stdout, live UI → stderr."""
    from rich.console import Console as _Console

    from xlii.cmds.sessions.resolve import _lookup_persona

    ui = _Console(stderr=True)

    persona = _lookup_persona(persona_name)
    if persona is None:
        print(f"ask: no such persona: {persona_name}", file=sys.stderr)
        return 1

    cfg = GlobalConfig.load()
    # The mojo read is search + chat — both ride the normal (inference) key; only
    # Collection *creation* needs the management key. A fabric node holds only the
    # capped inference key, so the persona turn must NOT demand management (the
    # design's whole premise: nodes read, the center writes).
    # A gig-only limb has no xAI pool: the named jobs.gig is the larynx, still
    # the same Mojo (one body, many mouths).
    chat_backend = None
    try:
        pool = ClientPool.from_config(cfg, require_management=False)
    except MissingCredentials as e:
        from xlii.chat_backend import GigError, resolve_gig_backend
        from xlii.farm import job_gig

        gig = job_gig(cfg)
        if not gig:
            print(f"ask: {e}", file=sys.stderr)
            return 1
        try:
            chat_backend = resolve_gig_backend(cfg, gig)
        except GigError as ge:
            print(f"ask: {e}; gig larynx: {ge}", file=sys.stderr)
            return 1
        pool = None

    # Voice-in: an audio attachment is the user TALKING — transcribe it and let
    # the transcript be the turn's message (and what accrues to memory below).
    prompt, attachments = _resolve_voice(
        args.prompt, getattr(args, "attach", None), cfg=cfg, pool=pool, ui=ui)
    # The CLI/daemon path DRAINS to the shared Collection (the center's F3 write);
    # both persist and drain are gated by --no-accrue, exactly as before.
    accrue = not getattr(args, "no_accrue", False)
    try:
        text = run_persona_oneshot(
            persona, prompt, pool=pool, cfg=cfg, console=ui,
            persist=accrue, drain=accrue,
            yolo=getattr(args, "yolo", False),
            no_sync=getattr(args, "no_sync", False),
            attachments=attachments, outbox_dir=_outbox_dir(args),
            chat_backend=chat_backend)
    except PersonaProjectError:
        print(f"ask: could not open persona project for {persona_name}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ask: agent error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(text or "")
    return 0


class PersonaProjectError(RuntimeError):
    """The persona's project could not be opened (init failed) — the caller maps
    it to its own surface's error (CLI exit 1, REPL message)."""


def run_persona_oneshot(
    persona, prompt: str, *, pool, cfg, console,
    ambient_context: str = "", persist: bool = True, drain: bool = False,
    yolo: bool = False, no_sync: bool = False, attachments=None, outbox_dir=None,
    on_agent=None, cancelled: Callable[[], bool] | None = None,
    desk_xli_dir=None,
    chat_tier: str | None = None,
    chat_backend=None,
    hire: str = "read",
    surface: str = "repl",
) -> str:
    """One-shot turn AS a persona over its OWN memory — the shared engine behind
    `xlii ask --persona` (CLI + daemon) and the REPL `/mojo` verb.

    The caller has already resolved `persona` and loaded `pool`/`cfg`. Runs a
    single turn over the persona's project (so `search_project` recalls its
    long-term memory), seeded with its system prompt + recent turns.

    `desk_xli_dir`, when set, is this folder's ``.xlii`` — talk / ``/mojo``
    union its ``plugins.txt`` with the persona project's so the Plugins pane
    dots match what the agent can call.

    `hire` is the worker-dispatch flag (mojo-keeper K1): ``"read"`` (default)
    advertises ``dispatch_subagent``; ``"write"`` also lets ``role=lab`` run
    against the caller's ``lab_project``; ``"none"`` is talk-only.

    `surface` is the bearings/sidecar label (desk face · phone glass ·
    xmpp door · repl). Written to ``turns/.last_turn.json`` after persist.

    `ambient_context`, when set, is prepended to the message the MODEL sees
    (`ambient_context + separator + prompt`) — the fusion seam: `/mojo` injects
    the project journal + wiki here so iXaac grounds in the project record
    WITHOUT a second identity. Crucially, memory persistence records the RAW
    `prompt` (not the augmented message), so the injected dump never pollutes the
    persona's turn store. `persist` accrues the turn locally (immediate
    continuity); `drain` additionally pushes it to the shared remote Collection
    (the center's mgmt-key write — a no-op on a keyless node). Returns the reply.
    Raises `PersonaProjectError` if the persona project can't be opened."""
    from xlii.profile import chat_profile
    from xlii.session_boot import ensure_persona_project, seed_chat_history
    from xlii.turn_store import CHAT_RECENT_TURNS, final_reply_from_history

    # Open an existing persona project as-is (its collection_id drives the shared
    # recall). When one must be *created* without a management key, make it
    # local-only so the turn still runs (persona identity + local memory) — the
    # shared remote Collection is layered in by the center that owns the mgmt key.
    # A gig-only limb has no xAI pool; the named jobs.gig is still Mojo's mouth.
    no_mgmt = not (getattr(cfg, "management_api_key", None) or "") or pool is None
    if pool is None:
        from types import SimpleNamespace as _NS

        pool = _NS(primary=lambda: _NS(label="gig", chat=None, xai=None))
    project = ensure_persona_project(persona, pool, console=console, local_only=no_mgmt)
    if project is None:
        raise PersonaProjectError(persona.name)

    # Throne auto-pull: due-gated, so most turns no-op. Face talk and /mojo
    # share this oneshot — that's how the desktop diary stays current without
    # a cron. Failures stay quiet; the turn still runs on whatever is local.
    try:
        from xlii.fabric import maybe_auto_pull

        maybe_auto_pull(cfg, console=console)
    except Exception as e:
        console.print(f"[yellow]warn:[/yellow] auto-pull skipped: {type(e).__name__}: {e}")

    # persona system prompt + recent turns → the agent's seed history (RP1), the
    # same pre-built history the interactive chat session captures as its base.
    profile = chat_profile(persona, project, seed_limit=CHAT_RECENT_TURNS)
    history, _total, _recent = seed_chat_history(profile, persona)
    if cancelled is not None and cancelled():
        return "(turn cancelled)"

    from xlii.persona import limb_addendum

    limb = limb_addendum(cfg)
    if limb and history and history[0].get("role") == "system":
        history[0]["content"] = history[0]["content"].rstrip() + "\n\n" + limb

    extra = (desk_xli_dir,) if desk_xli_dir is not None else ()
    # Copy the desk's /tier pick. A fresh SessionState defaults to ``auto``,
    # which routes short [M] turns onto the economy/build model and ignores
    # the Face chrome the user just set.
    agent = Agent(pool=pool, project=project, cfg=cfg, console=console, history=history,
                  chat_backend=chat_backend,
                  session=SessionState.from_flat(
                      yolo=yolo, conversational=True, outbox_dir=outbox_dir,
                      plugin_xli_dirs=extra, chat_tier=chat_tier, hire=hire))
    if on_agent is not None:
        # Hand the transient agent to the caller BEFORE the turn — a live
        # surface (the face server) routes its cancel button here, since this
        # agent exists only for the duration of this call.
        on_agent(agent)

    # The MODEL sees ambient + prompt; the turn store (below) sees only prompt.
    # ``tier_text`` keeps the chat-tier router honest too: it must classify the
    # RAW prompt, never the injected ambient (whose journal/wiki URLs read as a
    # fresh-data ask and mis-routed even a greeting into a heavy deep search).
    message = f"{ambient_context}\n\n---\n\n{prompt}" if ambient_context else prompt
    text, _dirty, _stats = agent.run_turn(message, attachments=attachments,
                                          tier_text=prompt, cancelled=cancelled)
    if not text:
        text = final_reply_from_history(agent.history)

    # F3 (the mojo write, journal-local-first): accrue the turn into the persona's
    # own memory so the next text recalls it — `recent_turns()` reads turn files
    # directly, so continuity is immediate with no index rebuild. Best-effort: a
    # persist failure must never swallow a reply the user already earned.
    if persist:
        try:
            dirty = profile.memory.persist(agent.history, prompt, set())
        except Exception as e:
            console.print(f"ask: (memory not saved: {type(e).__name__}: {e})")
            dirty = set()

        # F3, the CENTER's half ("drain from the throne"): archive the accrued
        # turn to the persona's SHARED remote Collection, so every OTHER surface
        # recalls it (the mojo travels). Fires ONLY on a body that holds a mgmt
        # key: `ensure_persona_project` opened a Collection-backed project exactly
        # then, so `end_of_turn_sync` reconciles on the center and no-ops on a
        # keyless node. `/mojo` in the REPL passes drain=False — accrue locally,
        # let the persona project's normal sync lifecycle push it.
        if drain:
            from types import SimpleNamespace

            from xlii.sync import end_of_turn_sync
            end_of_turn_sync(
                SimpleNamespace(project=project, cfg=cfg, pool=pool, console=console,
                                no_sync=no_sync),
                dirty,
            )

        try:
            from xlii.bearings import stamp_last_turn

            stamp_last_turn(
                profile.memory.turns_dir,
                cfg=cfg,
                surface=surface,
                desk=getattr(agent, "lab_project", None),
            )
        except Exception:
            pass

    return text or ""


def _outbox_dir(args) -> Path | None:
    """The --outbox delivery dir as a Path (created if missing), else None.

    Creating it here is caller-friendliness for scripts; the daemon always
    hands over a dir it already made (and owns the cleanup)."""
    raw = (getattr(args, "outbox", None) or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"ask: --outbox unusable ({e}); continuing without delivery", file=sys.stderr)
        return None
    return p


def _resolve_voice(prompt: str, attachments, *, cfg, pool, ui):
    """Voice-in: transcribe audio attachments; the transcript becomes the message.

    A voice note isn't an attachment to look at — it IS the user talking. Each
    audio attachment is transcribed via the xAI STT API (``xlii/stt.py``): with
    no real caption (empty, or the media-in default "look at it" prompt) the
    transcript REPLACES the prompt; with a caption it is appended as a labeled
    block. Transcribed audio is dropped from the attachment list (it would only
    add a useless ``[attachment]`` note); an audio file that fails to transcribe
    stays attached and the turn proceeds — never sink a reply over STT.
    Non-audio attachments and plain text turns pass through byte-identical."""
    if not attachments:
        return prompt, attachments
    from xlii import stt
    audio = [a for a in attachments if stt.is_audio(a)]
    if not audio:
        return prompt, attachments
    rest = [a for a in attachments if not stt.is_audio(a)]

    transcripts: list[str] = []
    for a in audio:
        try:
            transcripts.append(
                stt.transcribe(a, api_key=_raw_api_key(cfg, pool),
                               base_url=cfg.api_base_url()))
            ui.print(f"[dim]voice note transcribed ({len(transcripts[-1])} chars)[/dim]")
        except Exception as e:
            ui.print(f"[yellow]voice note not transcribed ({e}) — attached as a file[/yellow]")
            rest.append(a)
    if not transcripts:
        return prompt, (rest or None)

    from xlii.media_in import DEFAULT_MEDIA_PROMPT
    voice = "\n".join(transcripts)
    stripped = (prompt or "").strip()
    # The [voice note] marker is load-bearing: without it the model has no idea
    # the text arrived as speech and may claim it "can't receive voice" — the
    # live failure mode of the first phone test. Marked, the persona knows it
    # HEARD this (and the marker persists honestly into memory).
    if not stripped or stripped == DEFAULT_MEDIA_PROMPT:
        prompt = f"[voice note] {voice}"                # the voice note IS the message
    else:
        prompt = f"{prompt}\n\n[voice note]\n{voice}"   # caption + spoken content
    return prompt, (rest or None)


def _raw_api_key(cfg, pool) -> str:
    """The raw inference key for direct REST calls (the imagine-path precedent:
    the pool's primary chat client, else the first configured key pair)."""
    try:
        return pool.primary().chat.api_key
    except Exception:
        # No primary chat client in the pool -- fall through to the first configured key pair below.
        pass
    pairs = cfg.key_pairs()
    if pairs:
        return pairs[0].api_key
    raise RuntimeError("no xAI API key configured — run xlii setup")
