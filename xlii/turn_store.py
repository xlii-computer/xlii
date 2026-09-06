"""Turn-store policy: history seeding, streamed-reply recovery, turn persistence.

Kernel home for what used to live in the CLI face — ``cmds/sessions/memory.py``
(``_final_reply_from_history`` / ``seed_code_history`` / ``persist_code_turn``)
and ``cmds/sessions/ask.py``'s session-dir helpers (``_session_key`` /
``_session_turns_dir`` / ``SESSION_SEED_TURNS``). Promoted verbatim in the
godzilla-mothra Stage 1 opener (B1) so kernel modules (conversation.py,
profile.py) and alternate bodies (the WS serve head, the daemon) use them
without importing ``xlii.cmds.*``. The cmd files keep re-export façades.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from xlii.transcript import (
    load_recent_turns,
    turns_to_history,
    write_turn,
)


CHAT_RECENT_TURNS = 20  # how many past turns to inline as history at chat start

# Bounded inline seed for --session turns — same doctrine as the chat
# TurnStore's "last N turns as inline history" (memory, not a full replay).
SESSION_SEED_TURNS = 20


def final_reply_from_history(history: list[dict]) -> str:
    """The assistant reply produced by the *current* turn, recovered from history.

    `run_turn` returns empty text on streamed turns (the content was already
    streamed live), so the persisted reply must come from history. But scanning
    the whole history newest-first is wrong: a turn that produced no final
    answer — a genuinely empty model reply, or one that hit max_tool_iterations
    — leaves no assistant-with-content entry for this turn, and a naive scan
    would then resurface a reply from an *earlier* (possibly prior-session,
    seeded) turn, persisting a fabricated user↔assistant pairing.

    So bound the scan to this turn: run_turn appends the user message first
    (agent.py:585), then the assistant/tool entries, so the last `user` entry
    marks where this turn began. Return the last assistant-with-content after
    it, or "" if the turn produced none (→ persist no-ops; no phantom turn)."""
    for entry in reversed(history):
        role = entry.get("role")
        if role == "user":
            return ""  # reached this turn's prompt with no assistant reply after it
        if role == "assistant" and entry.get("content"):
            return entry["content"]
    return ""


def seed_history(agent, turns_dir: Path, limit: int = CHAT_RECENT_TURNS) -> int:
    """Append the last `limit` persisted turns after the system prompt.

    Code builds its own (code) system prompt at Agent construction, so on entry
    `agent.history` is just `[{system}]`; extending appends the turns after it
    — leaving `[system, user1, assistant1, …]`. Returns the count seeded so the
    caller can surface a memory banner."""
    recent = load_recent_turns(turns_dir, limit)
    agent.history.extend(turns_to_history(recent))
    return len(recent)


def persist_turn(turns_dir: Path, user_input: str, reply: str) -> bool:
    """Record this turn under the project's local turns dir. Returns whether a
    turn was written. No-op on an empty / whitespace-only reply (nothing worth
    remembering, and an empty assistant body would seed back as a junk message).

    Unlike chat, code turns live under `.xlii/turns/`, which is inside the
    ignored `.xlii/` tree (see ignore.py: only `.xlii/plans/` is force-included),
    so they are NOT synced to the Collection and are NOT reachable by
    search_project. That's intentional for RP0: code memory is local re-seeding
    (load_recent_turns reads this dir directly at startup), and whether
    conversational memory should *also* be RAG-searchable is the deferred
    "dual RAG" decision (proposals/repl-profiles.md, open decision #2). So we do
    NOT mark the turn dirty — doing so would trigger a no-op Collection sync on
    every pure-conversation turn for zero benefit. When dual-RAG lands it adds
    both a `.xlii/turns/` force-include and a sync marker here, together."""
    if reply.strip():
        write_turn(turns_dir, user_input, reply)
        return True
    return False


def session_key(session_id: str) -> str:
    """Filesystem-safe, collision-resistant directory name for a caller-chosen
    session id (daemon ids look like ``xmpp:me@host:alias``). Slug for
    readability plus a short digest for isolation: two ids that slug the same
    (``a:b`` vs ``a_b``) must NOT share a conversation — for the daemon that
    would be context bleed across JIDs, a trust-boundary bug."""
    slug = re.sub(r"[^a-zA-Z0-9_.@-]+", "_", session_id).strip("_")[:60] or "session"
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:10]
    return f"{slug}-{digest}"


def ask_session_turns_dir(project, session_id: str) -> Path:
    """Storage layout for ``ask --session`` continuity: turns for a caller-keyed
    session live at ``<project>.xli_dir/ask-sessions/<key>/turns``."""
    return project.xli_dir / "ask-sessions" / session_key(session_id) / "turns"
