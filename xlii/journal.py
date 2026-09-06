"""Project Shadow — the per-project journal (Theme B, JRN-1).

Project Shadow is **one identity** with two functions and a single steady voice:

- **journalist** — silently records how the project is actually being worked: one
  coarse, *turn-level* entry per completed code/OS task (never per-file). Entries
  roll into a tight rolling summary kept in active memory and archive into a
  dedicated journal xAI Collection for deeper RAG.
- **teacher** (`/askjo`) — the same voice, speaking only when asked, grounded
  strictly in what it has recorded in THIS project.

JRN-1 is fully **in-process**: no shell hook, no daemon. The recorder rides the
existing post-turn point (one entry per completed turn — see repl.run_repl_loop /
the kernel spine `drive_turn`) and a **batched** summarizer fires every N turns, so
cost scales with activity, not wall-clock; idle = zero cost. Session exit **defers**
the sub-batch tail instead of paying the LLM + upload flush (raw entries are already
durable on disk): the exiting session lists the tail in an explicit **deferred
spool**, and the next session's open claims the spool (atomic rename — one claimant,
even across processes) and summarizes it as a background job. Leaving is instant and
nothing is lost. flush() itself consumes ONLY its own buffer — on-disk entries are
never inferred to be abandoned (a live session's sub-batch tail looks identical),
only spooled ones are; and catch-up refuses to claim when it has no clients, so an
offline open leaves the spool for a better-equipped session.

Scope is the **code REPL turns + in-xlii shell only** — the project's real build
activity. The conversational `chat` REPL is never journaled here (its opt-in
compacter is a separate pipe; JRN-2). The journal is **off by default**, per
project; `--code-auto` opts a project in persistently. Every network/LLM op is
best-effort — the journal must never break a turn.

Lifecycle coupling: the journal Collection id is recorded on `project.json`
(`journal_collection_id`) so `xlii project rm` (A3) tears it down with the project.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from xlii.atomicio import write_text_atomic
from xlii.config import JOURNAL_COLLECTION_PREFIX

JOURNAL_DIR_NAME = "journal"
SUMMARY_FILENAME = "summary.md"
ENTRIES_DIRNAME = "entries"
CONFIG_FILENAME = "config.json"
# The deferred spool: entry filenames a fast exit left for the next session's
# catch-up job, one per line (append-only; claimed whole via atomic rename).
DEFERRED_SPOOL_FILENAME = "deferred-entries.txt"

# Batch size: summarize + archive after this many turns (or on exit). Default
# small so cost scales with activity; bounded so a typo can't make it runaway.
JOURNAL_BATCH_DEFAULT = 5
JOURNAL_BATCH_MIN = 1
JOURNAL_BATCH_MAX = 50

# Cap on raw `entry-*.md` files kept on disk. These are written per turn (and per
# bash command via the journal daemon) and are otherwise never pruned, so a
# long-lived opted-in project would grow the entries dir unbounded and make the
# `/askjo` recent-context glob O(N). Archival durability lives in the journal
# Collection / rolling summary; these raw files are only a short recency window.
JOURNAL_ENTRIES_KEEP = 200

# Journal-specific metadata declared at Collection-create time (xAI rejects
# undeclared fields on update_document — see sync.FIELD_DEFINITIONS).
JOURNAL_FIELD_DEFINITIONS = [
    {"key": "obs_kind", "required": False, "inject_into_chunk": False,
     "unique": False, "description": "journal entry kind"},
    {"key": "obs_cwd", "required": False, "inject_into_chunk": False,
     "unique": False, "description": "working directory at entry time"},
    {"key": "obs_ts", "required": False, "inject_into_chunk": False,
     "unique": False, "description": "ISO timestamp of the entry"},
]

# The rolling-summary template (distinct from the conversation-summary template
# in context_compact). Same grounded, tone-stripped notetaker voice as Shadow.
ACTIVITY_SUMMARY_SYSTEM = """You maintain a concise, factual rolling log of how a \
software project is being worked. Output markdown with EXACTLY these headings:
## Recent focus
## Files & areas touched
## Patterns & decisions
## Open threads

Be specific and grounded: real file paths, commands, and decisions only. No hype, \
no generic best practices, no speculation. Keep it tight — this is a running \
memory, not a transcript. Merge new activity into any prior log without dropping \
still-relevant focus, files, or open threads."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def journal_batch_size() -> int:
    """Batch size, honoring the XLII_JOURNAL_BATCH env override (clamped)."""
    raw = os.environ.get("XLII_JOURNAL_BATCH", "").strip()
    if raw:
        try:
            return max(JOURNAL_BATCH_MIN, min(JOURNAL_BATCH_MAX, int(raw)))
        except ValueError:
            # A non-numeric XLII_JOURNAL_BATCH falls through to the default below.
            pass
    return JOURNAL_BATCH_DEFAULT


# --------------------------------------------------------------------------- #
#  LLM seam — a single one-shot xAI chat completion (mocked in tests).
#  The journal rides the user's OWN xAI key (no cross-vendor judge required):
#  the rolling summary uses a cheap model, /askjo uses the conversational model.
# --------------------------------------------------------------------------- #

def _journal_complete(clients: Any, model: str, messages: list[dict],
                      *, temperature: float = 0.3) -> str:
    """One-shot completion over the xAI OpenAI-compatible chat client. Returns the
    text (or "" on an empty reply). Raises on transport errors — callers treat the
    whole thing as best-effort and swallow failures."""
    resp = clients.chat.chat.completions.create(
        model=model, messages=messages, temperature=temperature,
    )
    try:
        return (resp.choices[0].message.content or "").strip()
    except (AttributeError, IndexError):
        return ""


@dataclass
class JournalEntry:
    ts: str
    goal: str
    files: list[str]
    tool_calls: int
    cwd: str = ""
    # Per-entry uid so the raw filename is unique even when several entries share
    # a second (the JRN-2 daemon can ingest many bash commands per second — without
    # this they'd collide on doc_name and overwrite each other).
    uid: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def to_markdown(self) -> str:
        lines = [f"# {self.ts}", "", f"**goal:** {self.goal or '(none)'}"]
        if self.cwd:
            lines.append(f"**cwd:** {self.cwd}")
        lines.append(f"**tool calls:** {self.tool_calls}")
        if self.files:
            lines.append("")
            lines.append("**files touched:**")
            lines += [f"- {f}" for f in self.files]
        return "\n".join(lines) + "\n"

    def doc_name(self) -> str:
        safe = self.ts.replace(":", "").replace("+", "Z")
        return f"entry-{safe}-{self.uid}.md"


@dataclass(frozen=True)
class _PendingEntry:
    """A raw on-disk entry awaiting catch-up (deferred by a fast exit, or left
    by a kill). Duck-types the slice of JournalEntry that flush's consumers use
    (doc_name/to_markdown/ts/cwd), so archive + summary code needs no branches."""

    name: str
    text: str
    ts: str = ""
    cwd: str = ""

    def to_markdown(self) -> str:
        return self.text

    def doc_name(self) -> str:
        return self.name


def _parse_entry_header(text: str) -> tuple[str, str]:
    """Best-effort (ts, cwd) from a raw ``entry-*.md`` written by to_markdown() —
    enough to rebuild upload metadata for catch-up. Missing fields stay ''."""
    ts, cwd = "", ""
    for line in text.splitlines():
        if not ts and line.startswith("# "):
            ts = line[2:].strip()
        elif not cwd and line.startswith("**cwd:** "):
            cwd = line[len("**cwd:** "):].strip()
        if ts and cwd:
            break
    return ts, cwd


@dataclass
class ProjectJournal:
    """Per-session journal recorder + teacher for one project (code pipe).

    Holds only what flush/askjo need (project + pool/cfg/agent/console), so it is
    decoupled from the full REPLState interface and easy to construct in tests.
    """

    project: Any
    pool: Any = None
    cfg: Any = None
    agent: Any = None
    console: Any = None
    code_on: bool = False
    wiki_auto: bool = False  # ride flush to draft new wiki pages (create-only, born unverified)
    batch_size: int = field(default_factory=journal_batch_size)
    buffer: list[JournalEntry] = field(default_factory=list)
    archived: int = 0  # entries archived this session (status surface)
    caught_up: int = 0  # deferred entries from earlier sessions folded in (status surface)
    wiki_drafts: int = 0  # wiki pages auto-drafted this session (status surface)
    # Serializes ONLY the rolling-summary read-merge-write (and wiki drafts)
    # between a batch flush and the concurrent catch-up job. Never held around
    # buffer swaps or anywhere on the exit path — defer_flush must stay instant
    # even while a catch-up is mid-LLM-call.
    _lock: Any = field(default_factory=threading.RLock, repr=False)
    # Entry filenames mid-catch-up (after claim, before summarize). Prune must
    # not drop these — the spool file is already gone once claim runs.
    _catchup_protecting: set[str] = field(default_factory=set, repr=False)

    # --- paths ---
    @property
    def journal_dir(self) -> Path:
        return self.project.xli_dir / JOURNAL_DIR_NAME

    @property
    def summary_path(self) -> Path:
        return self.journal_dir / SUMMARY_FILENAME

    @property
    def entries_dir(self) -> Path:
        return self.journal_dir / ENTRIES_DIRNAME

    @property
    def config_path(self) -> Path:
        return self.journal_dir / CONFIG_FILENAME

    @property
    def deferred_spool_path(self) -> Path:
        return self.journal_dir / DEFERRED_SPOOL_FILENAME

    # --- live state for the status strip (always truthful) ---
    def is_recording(self) -> bool:
        return bool(self.code_on)

    def pending(self) -> int:
        return len(self.buffer)

    def has_history(self) -> bool:
        """Whether anything has ever been journaled (so /askjo has ground to stand
        on) — a rolling summary, archived entries, or a live buffer."""
        if self.buffer or self.archived:
            return True
        if self.summary_path.exists():
            return True
        return self.entries_dir.is_dir() and any(self.entries_dir.glob("*.md"))

    # --- toggles ---
    def set_code(self, on: bool, *, persist_auto: Optional[bool] = None) -> None:
        """Flip the code journal for this session. `persist_auto` (when not None)
        also writes the per-project auto-enable preference to disk."""
        was = self.code_on
        self.code_on = bool(on)
        if persist_auto is not None:
            self._write_auto(bool(persist_auto))
        # Turning off mid-batch: archive what we have so nothing is lost.
        if was and not self.code_on:
            self.flush()

    def _read_config(self) -> dict:
        try:
            data = json.loads(self.config_path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_config(self, **updates: Any) -> None:
        """Merge ``updates`` into the persisted config (key-preserving, so code_auto and wiki_auto
        never clobber each other). Best-effort only — a toggle write failure must not break the
        session."""
        try:
            self.journal_dir.mkdir(parents=True, exist_ok=True)
            data = self._read_config()
            data.update(updates)
            write_text_atomic(self.config_path, json.dumps(data, indent=2))
        except OSError:
            # Best-effort per the docstring: a toggle that fails to persist
            # still applies to the live session.
            pass

    def _write_auto(self, on: bool) -> None:
        self._write_config(code_auto=bool(on))

    def set_wiki_auto(self, on: bool) -> None:
        """Flip the self-building wiki for this project (persisted). Takes effect on the next flush
        while the code journal is recording — the autobuilder is a rider on the journalist."""
        self.wiki_auto = bool(on)
        self._write_config(wiki_auto=bool(on))

    # --- recording (the journalist) ---
    def observe_turn(self, user_input: str, dirty: Any, turn_stats: Any,
                     *, cwd: str = "") -> bool:
        """Ride the post-turn point: one coarse entry per completed turn. No-op
        unless recording. Never raises — journaling must not break a turn.
        Returns whether the raw local entry was durably recorded."""
        if not self.code_on:
            return False
        try:
            entry = JournalEntry(
                ts=_now_iso(),
                goal=(user_input or "").strip()[:2000],
                files=sorted(str(f) for f in (dirty or []))[:50],
                tool_calls=int(getattr(turn_stats, "tool_calls", 0) or 0),
                cwd=str(cwd or ""),
            )
        except Exception:
            # Defensive: a journaling hiccup must never surface as a turn failure.
            return False
        if not self._write_raw_local(entry):
            return False
        self.buffer.append(entry)
        try:
            if len(self.buffer) >= self.batch_size:
                self.flush()
        except Exception:
            # The raw entry is already durable; summary/archive failures are best-effort.
            pass
        return True

    def _write_raw_local(self, entry: JournalEntry) -> bool:
        try:
            self.entries_dir.mkdir(parents=True, exist_ok=True)
            write_text_atomic(self.entries_dir / entry.doc_name(), entry.to_markdown())
            self._prune_raw_entries()
        except OSError:
            # Best-effort journaling: local raw entry write failures must not break turns.
            return False
        return True

    def _prune_raw_entries(self, *, keep: int = JOURNAL_ENTRIES_KEEP) -> int:
        """Keep only the ``keep`` most-recent raw ``entry-*.md`` files; return how many
        were removed. Bounds unbounded growth and the O(N) `/askjo` recency glob.
        Filenames sort chronologically (ISO ts prefix), so drop the lexical head —
        but never an entry still listed in the deferred spool: it hasn't been
        summarized/archived yet, and its raw file is the only copy until catch-up
        runs."""
        files = sorted(self.entries_dir.glob("entry-*.md"))
        spooled = self._protected_entry_names()
        removed = 0
        for p in files[:-keep] if keep > 0 else files:
            if p.name in spooled:
                continue  # deferred, not yet summarized — protected until catch-up
            try:
                p.unlink()
                removed += 1
            except OSError:
                # Best-effort pruning: inability to remove one file must not fail journaling.
                pass
        return removed

    def flush(self) -> bool:
        """Batched summarize + archive of the buffered entries. Idempotent (no-op
        on an empty buffer) and fully best-effort. Returns True if it did work.
        Consumes ONLY this instance's buffer — never other on-disk entries: a
        live session's sub-batch tail is indistinguishable on disk from an
        abandoned one, so a concurrent daemon tick or second session must not
        infer and steal it. Deferred tails move through the explicit spool
        (defer_flush → catch_up) instead."""
        if not self.buffer:
            return False
        entries, self.buffer = self.buffer, []
        self.archived += len(entries)
        self._process_batch(entries)
        return True

    def _process_batch(self, batch: list[Any]) -> None:
        """Archive + summarize (+ wiki-draft) one batch. Each stage independent +
        best-effort. Only the summary/wiki stage runs under the lock — the
        read-merge-write of summary.md must not interleave between a batch flush
        and the catch-up job — so nothing here ever blocks a buffer swap or the
        exit path."""
        try:
            self._archive_to_collection(batch)
        except Exception:
            # Best-effort journaling: archive failures must not interrupt runtime flow.
            pass
        with self._lock:
            try:
                self._update_summary(batch)
            except Exception:
                # Best-effort summarization: never fail flush if summary update errors.
                pass
            if self.wiki_auto:
                try:
                    self._autobuild_wiki(batch)
                except Exception:
                    # Best-effort: a wiki draft hiccup must never interrupt flush.
                    pass

    def defer_flush(self) -> int:
        """The fast-exit arm of the batched summarizer: leave the buffered tail on
        disk (raw entries are already durable), list it in the deferred spool for
        the next session's catch-up job, and return instantly — no LLM, no
        network, no lock (an in-flight catch-up must never stall exit). Returns
        how many entries were deferred."""
        deferred = self.buffer
        if not deferred:
            return 0
        if not self._spool_append([e.doc_name() for e in deferred]):
            return 0
        self.buffer = []
        return len(deferred)

    def catch_up(self) -> int:
        """Summarize + archive the entries a previous session deferred at exit —
        the open-time background job's body. Gated on having clients BEFORE
        claiming, so an offline or key-less open leaves the spool for a
        better-equipped session (local-only projects need none for their SQLite
        archive). Claiming is an atomic rename, so a racing second session
        processes nothing twice. Returns how many entries were caught up."""
        if self._clients() is None and not getattr(self.project, "local_only", False):
            return 0  # can't archive or summarize — leave the spool intact
        names, claim = self._spool_claim()
        if not names:
            orphans = self._orphaned_claim_paths()
            if not orphans:
                return 0
            claim = orphans[0]
            names = self._names_from_claim_file(claim)
            if not names:
                self._spool_finish_claim(claim)
                return 0
        self._catchup_protecting = set(names)
        try:
            batch = self._entries_from_disk(names)
            if not batch:
                self._spool_finish_claim(claim)
                return 0
            self.archived += len(batch)
            self.caught_up += len(batch)
            self._process_batch(batch)
            self._spool_finish_claim(claim)
            return len(batch)
        finally:
            self._catchup_protecting.clear()

    def has_pending_catchup(self) -> bool:
        """Cheap disk check: did a previous session defer entries at exit?
        Drives the open-time catch-up job dispatch."""
        return bool(self._protected_entry_names())

    # --- the deferred spool (fast exit's hand-off to the next session) ---
    def _spooled_names(self) -> set[str]:
        try:
            text = self.deferred_spool_path.read_text()
        except OSError:
            return set()
        return {ln.strip() for ln in text.splitlines() if ln.strip()}

    def _orphaned_claim_paths(self) -> list[Path]:
        return sorted(self.journal_dir.glob(f"{DEFERRED_SPOOL_FILENAME}.claim-*"))

    def _names_from_claim_file(self, path: Path) -> set[str]:
        try:
            return {ln.strip() for ln in path.read_text().splitlines() if ln.strip()}
        except OSError:
            return set()

    def _protected_entry_names(self) -> set[str]:
        """Entry filenames that must survive prune until catch-up finishes."""
        names = set(self._spooled_names()) | set(self._catchup_protecting)
        for claim in self._orphaned_claim_paths():
            names |= self._names_from_claim_file(claim)
        return names

    def _spool_append(self, names: list[str]) -> bool:
        """Append deferred entry names, one per line. O_APPEND keeps two sessions
        exiting the same project from clobbering each other's tails; catch-up
        de-dupes on read."""
        payload = "".join(f"{n}\n" for n in names)
        if not payload:
            return True
        try:
            self.journal_dir.mkdir(parents=True, exist_ok=True)
            with self.deferred_spool_path.open("a") as f:
                f.write(payload)
            return True
        except OSError:
            # Best-effort: worst case the tail lives only in its raw files,
            # exactly as before fast exit existed.
            return False

    def _spool_claim(self) -> tuple[set[str], Path | None]:
        """Atomically claim the whole spool: rename it aside and read names.

        The claim file is kept until :meth:`_spool_finish_claim` so a crash
        mid-catch-up still leaves recoverable state and prune protection.
        """
        spool = self.deferred_spool_path
        claim = spool.with_name(f"{spool.name}.claim-{uuid.uuid4().hex[:8]}")
        try:
            os.replace(spool, claim)
        except OSError:
            return set(), None  # nothing spooled, or a racer claimed first
        return self._names_from_claim_file(claim), claim

    def _spool_finish_claim(self, claim: Path | None) -> None:
        if claim is None:
            return
        try:
            claim.unlink()
        except OSError:
            pass  # best-effort: a stray claim file is inert residue

    def _entries_from_disk(self, names: set[str]) -> list[_PendingEntry]:
        """Load spooled entries back from their raw files, oldest first. Missing
        or unreadable files are skipped (pruned or hand-deleted — nothing left
        to do for them)."""
        out: list[_PendingEntry] = []
        for name in sorted(names):
            p = self.entries_dir / name
            try:
                text = p.read_text()
            except OSError:
                continue
            ts, cwd = _parse_entry_header(text)
            out.append(_PendingEntry(name=name, text=text, ts=ts, cwd=cwd))
        return out

    # --- the self-building wiki (rides flush when wiki_auto is on) ---
    def _autobuild_wiki(self, entries: list[Any]) -> None:
        """Draft NEW wiki pages from the just-flushed activity + rolling summary. Create-only:
        never touches an existing page (so nothing you verified or hand-edited is clobbered);
        every draft is born unverified for you to promote or prune. Silent — discovered via
        ``/wiki list``."""
        clients = self._clients()
        if clients is None:
            return
        model = self._cheap_model()
        if not model:
            return
        xli_dir = getattr(self.project, "xli_dir", None)
        if xli_dir is None:
            return
        from xlii import wiki as W
        from xlii import wiki_author

        existing = {p.name for p in W.list_pages(xli_dir)}
        summary = self.read_summary()
        activity = "\n\n".join(e.to_markdown() for e in entries)
        corpus = (f"Rolling summary:\n\n{summary}\n\n" if summary else "") + f"Latest turns:\n\n{activity}"

        def complete(messages: list) -> str:
            return _journal_complete(clients, model, messages, temperature=0.3)

        for pr in wiki_author.propose_pages(sorted(existing), corpus, complete):
            if pr.name in existing or not W.is_valid_name(pr.name):
                continue  # create-only — skip anything that already exists
            try:
                W.write_page(xli_dir, pr.name, pr.body, sources=pr.sources, verified=False)
            except (ValueError, OSError):
                continue
            existing.add(pr.name)
            self.wiki_drafts += 1

    # --- storage: rolling summary (active memory) ---
    def read_summary(self) -> str:
        try:
            return self.summary_path.read_text().strip()
        except OSError:
            return ""

    def _update_summary(self, entries: list[Any]) -> None:
        clients = self._clients()
        if clients is None:
            return  # no key/pool — keep raw entries; summary stays as-is
        model = self._cheap_model()
        if not model:
            return
        prior = self.read_summary()
        body = "\n\n".join(e.to_markdown() for e in entries)
        system = ACTIVITY_SUMMARY_SYSTEM
        if prior:
            system += "\n\nExisting log to merge into:\n\n" + prior
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "New activity:\n\n" + body},
        ]
        text = _journal_complete(clients, model, messages, temperature=0.3)
        if text:
            try:
                self.journal_dir.mkdir(parents=True, exist_ok=True)
                write_text_atomic(self.summary_path, text + "\n")
            except OSError:
                pass  # best-effort summary persistence; keep journaling if local write fails

    # --- storage: durable RAG (journal Collection, or the local store offline) ---
    def _local_backend(self):
        """The journal's own offline document store (SQLite FTS), used when the
        project is local-only so entries stay searchable without a Collection.

        Its own db under the journal dir — a separate store from the project's
        LocalBackend, so journal noise never mixes with project documents (mirrors
        the journal having its own *Collection* in cloud mode)."""
        from xlii.storage_backend import LocalBackend
        return LocalBackend(self.project, db_path=self.journal_dir / "journal_backend.db")

    def _archive_to_collection(self, entries: list[Any]) -> None:
        if getattr(self.project, "local_only", False):
            # Local-only project: no remote Collection. Persist the batch into the
            # journal's own LocalBackend so `/askjo` can still search it offline
            # (the raw .md entries live under the ignored .xlii/ tree, invisible to
            # the LocalIndex floor — this store is what makes them recallable).
            backend = self._local_backend()
            for e in entries:
                try:
                    backend.upload(
                        e.doc_name(), e.to_markdown().encode("utf-8"),
                        {"obs_kind": "turn", "obs_cwd": e.cwd, "obs_ts": e.ts},
                    )
                except Exception:
                    pass  # best-effort; one bad upload must not abort the batch
            return
        clients = self._clients()
        if clients is None:
            return
        cid = self._ensure_collection(clients)
        if not cid:
            return
        from xlii.storage_backend import CollectionsBackend

        backend = CollectionsBackend(clients, [cid])
        for e in entries:
            try:
                backend.upload(
                    e.doc_name(), e.to_markdown().encode("utf-8"),
                    {"obs_kind": "turn", "obs_cwd": e.cwd, "obs_ts": e.ts},
                )
            except Exception:
                pass  # one bad upload must not abort the batch

    def _ensure_collection(self, clients: Any) -> Optional[str]:
        """Lazily create this project's journal Collection and record its id on
        project.json (so A3 `project rm` finds it). Returns the id, or None."""
        cid = getattr(self.project, "journal_collection_id", None)
        if cid:
            return cid
        from xlii.storage_backend import CollectionsBackend

        try:
            meta = CollectionsBackend.create_collection(
                clients,
                JOURNAL_COLLECTION_PREFIX + self.project.name,
                JOURNAL_FIELD_DEFINITIONS,
            )
        except Exception:
            return None
        cid = getattr(meta, "collection_id", None)
        if not cid:
            return None
        self.project.journal_collection_id = cid
        try:
            self.project.save()
        except Exception:
            pass  # best-effort metadata persistence; keep using created collection id
        return cid

    # --- the teacher (/askjo) ---
    def recall_context(self, question: str, *, wiki_context: str = "") -> str:
        """Assemble everything this project has recorded that's relevant to
        `question`: the rolling summary + recent raw entries + a RAG search of the
        journal (episodic), plus ``wiki_context`` — the project wiki's distilled
        sections (semantic) when supplied. Returns the joined context block, or
        ``""`` when nothing is recorded.

        The SHARED read half (persona-cleanup): ``/askjo`` completes over it in the
        Shadow voice; a fused ``/mojo`` turn injects it so iXaac answers grounded in
        the same project record without a second identity. Client-less (no chat key)
        degrades gracefully to summary + entries + wiki — RAG is simply skipped."""
        context_parts: list[str] = []
        summary = self.read_summary()
        if summary:
            context_parts.append("## Rolling activity summary\n\n" + summary)
        recent = self._recent_local_entries(limit=8)
        if recent:
            context_parts.append("## Recent entries\n\n" + recent)
        clients = self._clients()
        if clients is not None:
            rag = self._rag_search(clients, question, limit=8)
            if rag:
                context_parts.append("## Retrieved from the journal\n\n" + rag)
        if wiki_context:
            context_parts.append("## From the project wiki (semantic memory)\n\n" + wiki_context)
        return "\n\n---\n\n".join(context_parts)

    def askjo(self, question: str, *, wiki_context: str = "") -> str:
        """Answer `question` in the single Shadow voice, grounded strictly in what
        this project has recorded: the rolling summary + recent raw entries + a RAG
        search of the journal Collection (episodic), plus ``wiki_context`` — the
        project wiki's distilled sections (semantic) when the caller supplies them.
        Independent of the recorder toggle: the wiki alone is enough to answer, so
        Shadow can teach from the semantic tier even before the journal records."""
        question = (question or "").strip()
        if not question:
            return "ask me something about how this project has been worked."
        clients = self._clients()
        if clients is None:
            return ("I can't reach a model right now — no chat key available.")

        context = self.recall_context(question, wiki_context=wiki_context)
        if not context:
            return ("I haven't recorded anything for this project yet — turn the "
                    "journal on with `/journal --code-on` and do some work first.")

        system = self._shadow_prompt()
        user = (
            "Here is everything I have recorded for this project:\n\n"
            + context
            + f"\n\n---\n\nQuestion: {question}\n\n"
            "Answer only from the record above, citing the specific files, commands, "
            "decisions, or wiki pages (as `wiki://page#section`) it shows. If the "
            "record doesn't cover it, say so plainly."
        )
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        model = self._teacher_model()
        try:
            text = _journal_complete(clients, model, messages, temperature=0.4)
        except Exception as e:  # noqa: BLE001 — surface, don't crash the REPL
            return f"(couldn't reach the model: {type(e).__name__})"
        return text or "(no answer)"

    def _recent_local_entries(self, *, limit: int) -> str:
        # Buffered entries are ALSO on disk (written by _write_raw_local, not removed
        # on flush), so read from buffer OR disk — never both — or the most recent
        # entries appear twice and out of order. Emit older on-disk entries first, then
        # the live buffer, for a chronological window.
        buffered = self.buffer[-limit:]
        seen = {e.doc_name() for e in buffered}
        parts: list[str] = []
        backfill = limit - len(buffered)
        if backfill > 0 and self.entries_dir.is_dir():
            files = [p for p in sorted(self.entries_dir.glob("entry-*.md")) if p.name not in seen]
            for p in files[-backfill:]:
                try:
                    parts.append(p.read_text())
                except OSError:
                    # Best-effort context loading: skip unreadable entry files.
                    pass
        parts.extend(e.to_markdown() for e in buffered)
        return "\n\n".join(parts).strip()

    def _rag_search(self, clients: Any, query: str, *, limit: int) -> str:
        if getattr(self.project, "local_only", False):
            # Offline: search the journal's own LocalBackend (was previously dark —
            # a local-only project's journal returned nothing to `/askjo`).
            try:
                hits = self._local_backend().search(query, limit=limit)
            except Exception:
                return ""
            return "\n\n".join(h.text.strip() for h in hits if h.text.strip())
        cid = getattr(self.project, "journal_collection_id", None)
        if not cid:
            return ""
        from xlii.storage_backend import CollectionsBackend

        try:
            mode = getattr(self.cfg, "retrieval_mode", "hybrid")
            hits = CollectionsBackend(clients, [cid]).search(
                query, limit=limit, retrieval_mode=mode,
            )
        except Exception:
            return ""
        out: list[str] = []
        for h in hits:
            if h.text.strip():
                out.append(h.text.strip())
        return "\n\n".join(out)

    def _shadow_prompt(self) -> str:
        from xlii.turn_prompt import load_prompt
        xli_dir = getattr(self.project, "xli_dir", None)
        try:
            return load_prompt("journal-shadow", xli_dir)
        except OSError:
            return "You are Project Shadow, a grounded journalist and teacher."

    # --- model + client resolution ---
    def _clients(self) -> Any:
        """The client the journal bills to. Prefers the dedicated journal key
        (isolated, auditable spend — `xlii journal key`); falls back to the
        primary key when none is provisioned (spend shared, still works)."""
        pool = self.pool
        if pool is None:
            return None
        getter = getattr(pool, "journal_client", None)
        if callable(getter):
            try:
                jc = getter()
            except Exception:
                jc = None
            if jc is not None:
                return jc
        try:
            return pool.primary()
        except Exception:
            return None

    def using_dedicated_key(self) -> bool:
        """Whether a dedicated journal key is active (so spend is isolated)."""
        pool = self.pool
        getter = getattr(pool, "journal_client", None) if pool is not None else None
        if not callable(getter):
            return False
        try:
            return getter() is not None
        except Exception:
            return False

    def _cheap_model(self) -> str:
        cfg = self.cfg
        if cfg is None:
            return ""
        try:
            return cfg.worker()
        except Exception:
            return ""

    def _teacher_model(self) -> str:
        cfg = self.cfg
        if cfg is None:
            return ""
        try:
            return cfg.chat()
        except Exception:
            return self._cheap_model()

    # --- status surface ---
    def status_badge(self) -> str:
        """Compact, live status-strip segment, or '' when nothing is recording.

        Lights while the in-process code journal is recording OR the bash-wide
        daemon is live (queried fresh each render — always truthful: it clears if
        the daemon was stopped or died). Presence alone is the signal — no
        pending count: it reset every session, so it always under-reported what
        the journal knows (bare `/journal` shows the real numbers)."""
        if not self.is_recording() and not _daemon_live():
            return ""
        return "jrnl●"

    def status_lines(self) -> list[str]:
        """Multi-line status for bare `/journal`."""
        state = "[green]ON[/green]" if self.is_recording() else "off"
        auto = " [dim](auto-enabled for this project)[/dim]" if self._read_auto() else ""
        lines = [f"  code journal:  {state}{auto}"]
        if self.wiki_auto:
            drafted = f" [dim]({self.wiki_drafts} drafted this session)[/dim]" if self.wiki_drafts else ""
            lines.append(f"  self-wiki:     [green]ON[/green]{drafted} [dim](drafts pages on flush → /wiki list)[/dim]")
        else:
            lines.append("  self-wiki:     [dim]off — `/journal --wiki-on` to auto-draft wiki pages as you work[/dim]")
        lines.append(f"  pending:       {self.pending()} entr{'y' if self.pending() == 1 else 'ies'} "
                     f"[dim](batch every {self.batch_size})[/dim]")
        lines.append(f"  archived:      {self.archived} this session")
        if self.caught_up:
            lines.append(f"  caught up:     {self.caught_up} deferred from earlier sessions")
        if self.summary_path.exists():
            lines.append(f"  summary:       {self.summary_path}")
        cid = getattr(self.project, "journal_collection_id", None)
        lines.append(f"  collection:    {cid or '[dim](not created yet)[/dim]'}")
        if self.using_dedicated_key():
            lines.append("  cost key:      [green]dedicated[/green] [dim](spend isolated for auditing)[/dim]")
        else:
            lines.append("  cost key:      [dim]shared (primary) — `xlii journal key` to isolate spend[/dim]")
        if _daemon_live():
            from xlii import journal_daemon
            lines.append(f"  bash-wide:     [green]daemon live[/green] [dim](pid {journal_daemon.read_pid()}, "
                         "captures commands outside xlii)[/dim]")
        else:
            lines.append("  bash-wide:     [dim]off — `xlii journal install` to capture commands "
                         "run outside xlii[/dim]")
        return lines

    def _read_auto(self) -> bool:
        return read_journal_auto(self.project)


def _daemon_live() -> bool:
    """Whether the JRN-2 bash-wide daemon is live. Lazy import avoids a cycle
    (journal_daemon imports this module); best-effort so status never raises."""
    try:
        from xlii import journal_daemon
        return journal_daemon.daemon_running()
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  per-project auto-enable preference (persisted in .xlii/journal/config.json)
# --------------------------------------------------------------------------- #

def _read_journal_config(project: Any) -> dict:
    try:
        path = project.xli_dir / JOURNAL_DIR_NAME / CONFIG_FILENAME
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def read_journal_auto(project: Any) -> bool:
    """Whether the code journal is set to auto-enable for this project."""
    return bool(_read_journal_config(project).get("code_auto"))


def read_wiki_auto(project: Any) -> bool:
    """Whether the self-building wiki (journal→wiki autobuilder) is enabled for this project."""
    return bool(_read_journal_config(project).get("wiki_auto"))


def build_project_journal(state: Any) -> ProjectJournal:
    """Construct the journal for a code session from a REPLState, honoring the
    persisted per-project auto-enable preference. Code REPL only — callers in
    chat must not build one (the chat pipe is the separate JRN-2 compacter)."""
    journal = ProjectJournal(
        project=state.project,
        pool=getattr(state, "pool", None),
        cfg=getattr(state, "cfg", None),
        agent=getattr(state, "agent", None),
        console=getattr(state, "console", None),
    )
    journal.code_on = read_journal_auto(state.project)
    journal.wiki_auto = read_wiki_auto(state.project)
    return journal


def dispatch_catchup(state: Any) -> Optional[str]:
    """Fast exit's other half: summarize what a previous session deferred as a
    background job at session open — here the job registry has the whole
    session's lifetime ahead of it, unlike at exit where it is already tearing
    down. Runs regardless of the CURRENT session's recording toggle: the spooled
    entries were recorded under an explicit opt-in (session `--code-on` or
    persisted auto), and catching them up completes that already-authorized
    work — otherwise the exit line "summarized at next open" would lie to
    session-only users. catch_up itself gates on having clients. Best-effort;
    returns the job id, or None when there is nothing to do (or no registry)."""
    journal = getattr(state, "journal", None)
    if journal is None:
        return None
    try:
        if not journal.has_pending_catchup():
            return None
        from xlii import jobs
        registry = jobs.get_registry(state)
        if registry is None:
            return None
        return registry.dispatch(jobs.KIND_JOURNAL, "journal catch-up", journal.catch_up)
    except Exception:
        return None  # best-effort: catch-up dispatch must never break session open
