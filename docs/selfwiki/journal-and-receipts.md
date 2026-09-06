---
sources: file://xlii/journal.py, file://xlii/prompts/journal-shadow.md, file://xlii/repl_cmds/journal.py, file://xlii/cmds/journal.py, file://xlii/repl_cmds/git.py#L223-252, file://xlii/session_boot.py, file://xlii/turn_receipt.py, file://proposals/turn-receipts-claim-gates.md, file://xlii/status.py#L681-689, file://proposals/done/lifecycle-and-journal.md
verified: false
---
# journal-and-receipts

Two independent, best-effort pipes over code-REPL turns. **Project Shadow** (the `/journal`) records what the project's work has *been*; **turn receipts** record what each turn actually *did* versus what its text claimed. Both ride the kernel's one post-turn finalize point and must never break a turn.

## Project Shadow (the /journal)

One identity, one function now: the **journalist** silently records one coarse, turn-level entry per completed code/OS task (never per-file) — a pure background observer. The old separate **teacher** voice (`/askjo`, `journal-shadow.md`) is retired: asking about the project is now `/mojo`, where iXaac answers with its own memory fused with this project's journal + wiki (`ProjectJournal.recall_context`). `/askjo` survives only as a hidden alias of `/mojo`. Renamed from "observer" (proposal shipped PR #91).

Scope is **code-REPL turns + in-xlii shell only** — the project's real build activity. The conversational `chat` REPL is never journaled here (D3): chat has a *separate* opt-in persona-memory compacter (the `--chat-*` flags, JRN-2), not Shadow. **Off by default, per project**; `/journal --code-auto` opts a project in persistently (state in `.xlii/journal/config.json`).

**Storage tiers**: raw `entry-*.md` files under `.xlii/journal/entries/` (a short recency window, capped at 200); a rolling `summary.md` merged by a cheap model every N turns (`JOURNAL_BATCH_DEFAULT=5`, `XLII_JOURNAL_BATCH` override); durable RAG in a dedicated journal xAI Collection (id recorded on `project.json` as `journal_collection_id`, so `xlii project rm` tears it down with the project), or a private SQLite `LocalBackend` for local-only projects. `recall_context()` fuses rolling summary + recent raw entries + a RAG search + the project wiki — the block `/mojo` injects into iXaac's turn.

**Fast exit**: session exit *defers* the un-summarized tail to a `deferred-entries.txt` spool instead of paying an LLM flush (raw entries are already durable); the next session's open claims the spool via atomic rename (exactly one claimant, even across processes) and summarizes it as a background job (`dispatch_catchup`). Leaving is instant; nothing is lost. `flush()` consumes only its own buffer — a live session's on-disk tail is never inferred abandoned, only spooled entries are.

**JRN-2 bash-wide daemon** (`cmds/journal.py`): `xlii journal install` adds one marked block to `~/.bashrc`; a fork-free DEBUG trap appends `ts⇥cwd⇥cmd` to a feed only when `XLII_JOURNAL=1`; `xlii journal serve` tails and batch-summarizes into each opted-in project. `xlii journal key` provisions a dedicated key so the journal's LLM spend is isolated on the xAI console for auditing (falls back to the primary key when none is provisioned).

The journalist can also *ride flush to draft wiki pages* when `--wiki-on` is set (create-only, born unverified) — see [[xliiwiki]].

## Status badge

The status-line / input-chrome journal badge (D8) is **always visible**: `jrnl●` while recording, `jrnl○` when idle — presence-only, no pending count (PR #179 dropped the count). Rendered by `status.journal()` and consumed by both the plain REPL status and the TUI input chrome. See [[status-and-chrome]].

## startup-task notes

`/project startup` bind / clear / mute, and `apply_startup_task` fire / recorded
skip, write one coarse Project Shadow line via `observe_turn` (who, task, confirm
vs auto, skip reason). Same gate as gitpain: only while the journal is recording;
unavailable or off is a no-op and **must not** fail the bind or the boot. See
[[tasks-and-pipes]].

## gitpain integration

`/gitpain commit journal` drafts a commit subject from the rolling summary + recent entries + active goal (`git.py`). After a commit, `_maybe_journal_commit` writes a post-commit entry `committed <hash> — <subject>` — but **only after a verified commit**: the run reported success AND HEAD actually moved. `git_cmd` treats exit 1 (e.g. nothing staged) as success, so the HEAD-moved check is load-bearing, not belt-and-braces — a failed commit never journals the previous hash as new. See [[gitpain]].

## Turn receipts & claim gates

A receipt is the structured, always-produced artifact recording evidence vs claims, built once per turn at the spine's single finalize point (`conversation.complete_turn_effects`) — so TUI, inline, and loop turns are covered by construction (`turn_receipt.py`). It never blocks a turn (v1 policy: warn + receipt, don't deadlock).

**Evidence → class** (a list, not an exclusive enum): `edit` (dirty_paths present), `verify` (pytest/unittest/ruff/mypy/tox/npm test/cargo test ran), `commit` (git commit/push observed). **Claims are tiered** from the final prose: edit / verify / publish. **Gates** flip `gate=unsubstantiated` + a warning when: an edit claim has no writes; a verify claim has no check command; a verify claim's *last* check exited nonzero; or a publish claim has no commit. Evidence classifies — the model does not get to self-declare a soft class to skip a gate.

On edit turns a `git diff --stat HEAD` (capped 2000 chars) attaches automatically; a commit records its short hash (from git's `[branch hash]` bracket, then a push range's NEW hash, then a `rev-parse` fallback — never a bare first-hex token).

**Recording** (`.xlii/receipts.jsonl` + `session.last_turn_receipt`): the ledger writes in **every** mode. Loudness follows `claim_gates`: `warn` (default) prints only the verify/publish lanes P0 doesn't already cover; `strict` prints the edit lane too; `off` silences warnings but still writes the ledger. The P0 edit-claim gate lives separately in `agent_stats._detect_unsupported_claim` (so nothing yells twice). See [[trust-and-gates]].

**Phasing**: P0 (read-only tools no longer satisfy edit claims) + P1 (receipt struct + diff-stat lane) + P2 (`claim_gates: warn|strict|off`, PR #165) + Wave-1 exit binding (bash results paired to calls via `tool_call_id`; the `--- exit N ---` trailer records exit codes; PR #197).

**Exit-spoof fix (PR #213)**: the `--- exit N ---` trailer regex is anchored to the tail (`\Z`), so an earlier echoed/quoted `--- exit 0 ---` inside command output can no longer masquerade as the real trailing exit and read a red run green. The same fix records the NEW commit hash from a push range, never the old one.

`plan_source` audit field: on a post-approval turn, one-shot-records whether `plan-last.md` was fed from the reconciled plan file or the legacy chat snapshot; omitted on ordinary turns. See [[plan-surface]].
