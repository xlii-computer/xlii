"""The Profile seam (RP1 of proposals/repl-profiles.md).

A `Profile` is the live triple {mode · identity · loadout} that one REPL/TUI
surface renders. RP1 factors the *divergent construction logic* of the two
launchers (`cmd_code` and `_chat_run_session`, both in xlii/cmds/sessions.py)
into three small value/policy types so the launchers can build one object and
hand its pieces to the shared `run_repl_loop`.

Scope discipline (this is a PURE refactor — no behavior change):
- A Profile owns NO mutable session state. Live state stays on
  Agent / SessionState / REPLState (the single-owner invariant in agent.py).
  These types carry construction inputs + per-mode *policy*, nothing more.
- Byte-sensitive, test-unguarded output (the two banners, the startup-sync
  messages, the prompt-prefix closures, the post-turn print order, the chat
  history assembly) stays INLINE and verbatim in each launcher. The Profile
  routes only things with genuine divergent logic: the turns dir + seed/persist
  policy (`TurnStore`) and the loadout dispatch (`Loadout`).
- The four pinned helpers (`seed_code_history`, `persist_code_turn`,
  `_final_reply_from_history`, `_apply_persona_loadout`) stay defined in
  xlii.cmds.sessions with identical names/signatures — tests and
  xlii/tui_textual.py import them from there. We reach them via function-local
  imports (matching `_apply_persona_loadout`'s lazy-import style) so there is no
  import cycle (sessions.py imports this module at top level).

`Profile` is a pure data bundle in RP1 (the thing RP2 swaps on the live
agent+state). RP2 adds the accessor methods the proposal sketches
(`system_prompt()`, `prompt_prefix()`, `command_scope()`) when the live switch
needs them; RP1 deliberately ships none of them rather than dead stubs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class TurnStore:
    """Where a surface's turns/ live + how a turn is recovered and persisted.

    Two independent policy flags carry the code/chat divergence (today they pair
    one way per mode, but each is read on its own — see persist()):

      bounded_reply_scan — True (code): recover the reply via the turn-bounded
        `_final_reply_from_history` and persist via `persist_code_turn` (which
        guards `reply.strip()`). False (chat): an UNBOUNDED newest-first scan +
        bare `if reply:` truthiness (a whitespace-only reply IS persisted today;
        preserved verbatim).

      mark_rescan — True (chat): a written turn file is marked `__rescan__` so it
        reaches the Collection. False (code): never marked — code turns live
        under the ignored .xlii/ tree and are local-only (RP0).
    """

    turns_dir: Path
    mark_rescan: bool
    bounded_reply_scan: bool
    seed_limit: int

    def recent_turns(self) -> list:
        """The last `seed_limit` turns (chronological). One disk read — callers
        derive both the seeded history and the banner count from this single
        list (parity with the old `recent = load_recent_turns(...)` call)."""
        from xlii.transcript import load_recent_turns
        return load_recent_turns(self.turns_dir, self.seed_limit)

    def seed_into(self, agent) -> int:
        """Code path: append the last `seed_limit` turns after the system prompt
        the Agent built at construction. Returns the count seeded."""
        from xlii.turn_store import seed_history
        return seed_history(agent, self.turns_dir, self.seed_limit)

    def count(self) -> int:
        from xlii.transcript import count_turns
        return count_turns(self.turns_dir)

    def persist(self, history: list[dict], user_input: str, dirty: set[str]) -> set[str]:
        """Persist this turn per the mode's policy; return the dirty set to sync.

        Behavior matches the two old post-turn bodies exactly:
        - code (bounded): turn-bounded reply + persist_code_turn (.strip guard).
        - chat (unbounded): newest-first scan + write_turn under bare truthiness.
        A written turn is marked `__rescan__` iff `mark_rescan` (chat only).
        """
        if self.bounded_reply_scan:
            from xlii.turn_store import final_reply_from_history, persist_turn
            wrote = persist_turn(self.turns_dir, user_input,
                                 final_reply_from_history(history))
        else:
            from xlii.transcript import write_turn
            reply = ""
            for entry in reversed(history):
                if entry.get("role") == "assistant" and entry.get("content"):
                    reply = entry["content"]
                    break
            wrote = bool(reply)  # bare truthiness — whitespace persists, as before
            if wrote:
                write_turn(self.turns_dir, user_input, reply)
        if wrote and self.mark_rescan:
            return set(dirty) | {"__rescan__"}  # new turn file must reach the Collection
        return dirty


@dataclass(frozen=True)
class Loadout:
    """The declared loadout + the act of applying it. Owns no storage (the sinks
    are SessionState attachments + the on-disk subscriptions file). Code's
    persona is None → apply() is a no-op → byte-identical to today."""

    persona: Optional[Any] = None

    def apply(self, state, project) -> None:
        if self.persona is None:
            return
        from xlii.cmds.sessions import _apply_persona_loadout
        _apply_persona_loadout(state, self.persona, project)


@dataclass(frozen=True)
class Profile:
    """The assembled surface: mode + identity + memory + loadout + affordances.

    Pure data in RP1 — the bundle RP2 swaps on the live agent+state. `identity`,
    `project`, and `affordances` are descriptive (RP2/RP3 read them: the profile
    bar, mode-state clear); the launchers use `memory` and `loadout`.
    """

    mode: str                       # "code" | "chat"
    identity: Any                   # ProjectConfig (code) | Persona (chat)
    project: Any                    # resolved ProjectConfig
    memory: TurnStore
    loadout: Loadout
    affordances: frozenset

    # --- RP2 accessors: read by the live /code<->/chat switch + the loop ---
    def system_prompt(self) -> str:
        """The base system prompt for this surface — becomes
        `agent.base_system_prompt` on a live swap (run_turn rebuilds history[0]
        from it next turn, leaving history[1:] intact). chat → the persona
        prompt; code → the shared `build_code_system_prompt` (same source the
        Agent uses at construction, so the two can never drift)."""
        if self.mode == "chat":
            return self.identity.system_prompt()
        from xlii.agent import build_code_system_prompt
        return build_code_system_prompt(self.project)

    def prompt_prefix(self, state) -> str:
        """The live REPL prompt prefix. Reads LIVE state (rail/plan/yolo/cwd for
        code; the live persona name + workspace for chat) so a mid-session swap
        is reflected on the next prompt. Bodies are the launchers' former
        closures, verbatim."""
        from xlii.cmds.sessions import _attachment_tag
        attach = _attachment_tag(state)
        if self.mode == "chat":
            ws = state.get_current_workspace() if hasattr(state, "get_current_workspace") else "main"
            ws_tag = f":{ws}" if ws != "main" else ""
            name = getattr(state.persona, "name", None) or self.identity.name
            return f"[{name}{ws_tag}]{(' ' + attach) if attach else ''} › "
        # code — byte-identical to the former _code_prefix closure
        from xlii.repl import format_shell_cwd, terminal_title_enabled
        from xlii.status import profile_mode_tag

        loop_ctrl = getattr(state, "loop", None)
        if loop_ctrl is not None and loop_ctrl.is_active:
            tag = f"[loop {loop_ctrl.state.cycle}/{loop_ctrl.state.max_cycles}]"
        else:
            tag = profile_mode_tag(state.agent)
            if not tag and state.yolo:
                tag = "[yolo]"
        cwd = "" if terminal_title_enabled() else format_shell_cwd(state)
        segments = [s for s in (cwd, tag, attach) if s]
        return (" ".join(segments) + " › ") if segments else "› "

    def command_scope(self) -> str:
        """The dispatch scope (commands.py filters cmd.repls on it)."""
        return "chat" if self.mode == "chat" else "code"


def code_profile(project, *, seed_limit: int) -> Profile:
    """Build the code surface's Profile from a resolved project.

    Code is the sole codebase-aware surface, and its conversational memory is its
    OWN — project-local `.xlii/turns`, never a persona's store. A project's
    `bound_persona` is NOT consulted here: it only names the persona that bare
    `/chat` opens in this project (see `repl_cmds/switch.h_chat`), a chat-side
    default with no effect on code. Keeping the two stores separate is the whole
    point — chat personas stay oblivious to the code, and code memory can't leak
    into (or be polluted by) any persona. The one-way bridge from code into chat
    is `/recall <persona>:<mark>` (RP6), never a shared store.

    `mark_rescan=False`: code turns live under the ignored `.xlii/` tree and are
    local-only (RP0)."""
    return Profile(
        mode="code",
        identity=project,
        project=project,
        memory=TurnStore(turns_dir=project.xli_dir / "turns",
                         mark_rescan=False, bounded_reply_scan=True,
                         seed_limit=seed_limit),
        loadout=Loadout(persona=None),
        affordances=frozenset({"rail", "plan", "cwd"}),
    )


def chat_profile(persona, project, *, seed_limit: int) -> Profile:
    """Build the chat surface's Profile from a resolved persona + its project."""
    return Profile(
        mode="chat",
        identity=persona,
        project=project,
        memory=TurnStore(turns_dir=persona.turns_dir,
                         mark_rescan=True, bounded_reply_scan=False,
                         seed_limit=seed_limit),
        loadout=Loadout(persona=persona),
        affordances=frozenset({"marks"}),
    )
