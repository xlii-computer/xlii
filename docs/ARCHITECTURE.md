# xlii architecture — what’s true now

> **Source of truth** for control flow, state ownership, and surfaces.  
> If a lab-only design brief disagrees with this file, **this file wins** until this doc is updated.  
> Dated: 2026-07-12 (Wave 0 truth-up: fast exit / journal defer_flush).

**Start here if new:** [GOLDEN-PATH.md](GOLDEN-PATH.md) · [LEGACY.md](LEGACY.md) · [FIRST-LOOK-REVIEW.md](FIRST-LOOK-REVIEW.md)

---

## 1. What xlii is

A **terminal-native personal AI substrate**: local files are source of truth; the user composes identity, mode, knowledge, and trust. Not an IDE clone — a **workshop with walls** (isolation by default, bridges by choice).

| Surface | Entry | Isolation |
|---------|--------|-----------|
| **code** | `xlii code` | Project-scoped tools + shell-primary REPL |
| **chat** | `xlii chat` | Persona memory; no automatic project dump |
| **scratch** | scratch / no-sync | Ephemeral; does not upload |
| **CLI** | `xlii <cmd>` | Setup, doctor, export, models, keys, … |
| **face** | `xlii serve --face` (Tauri webview / browser / public route) | One live `CodeSession`, token-gated WS; the workbench deck (B1) projects the headless panes over protocol 2 — `/workbench` picks the type |

Switch in-process: `/code` ↔ `/chat` — **detached threads**, not one blended brain.

---

## 2. Control flow

```text
xlii.cli (thin)
  → cmds/*                    # domain CLI
  → sessions/code|chat        # launch REPL
       → repl loop (repl.py + REPLState)
            bare / ! / ? / /
       → Agent.run_turn (agent.py)
            tools → dirty paths
       → end-of-turn / close → Collection sync (if enabled)
       → exit → run_graceful_exit (journal defer_flush; next open catch_up)
```

**TUI** (`xlii code --tui`): same brain (`Agent` + `REPLState`); view is `XliiApp` + mixins under `xlii/tui/`. Entry: `tui_textual.launch` / `run_tui_over_session`.

---

## 3. State ownership (one owner each)

| Owner | Holds |
|-------|--------|
| **SessionState** (`xlii/session_state.py`) | Per-session flags & attachments — nested: `trust`, `attachments`, `meter`, `overlays`, `model_pins`, `compact` + thin residual. Flat facades (`session.yolo`, …) for compat. |
| **Agent** | `session`, history, `active_mode` (ModeController), pool, project, cfg |
| **REPLState** | Surface/persistence (workspaces, shell cwd, persona handle); **delegates** session flags to the same `SessionState` |
| **ProjectConfig** / disk `.xlii/` | Project identity, collection ids, local index, session.json attachments |
| **GlobalConfig** `~/.config/xlii/` | Models, key refs, vault-backed secrets |

**Rule:** no bidirectional sync of the same flag between Agent and REPLState. Agent holds `session`; REPL properties write through.

**Where new session fields go:** see module docstring on `xlii/session_state.py`.

---

## 4. Modes, trust, surface (three axes)

Status chrome and `/status` lead with:

```text
mode: plan|rail|debug|discovery|ops|—
trust: safe|yolo|freeball
surface: code|chat|scratch
```

| Slot | Exclusive? | Notes |
|------|------------|--------|
| **active_mode** | yes | `ModeController` on Agent — tool palette restricted in **code**, not only prompt |
| **trust** | ladder | freeball ⊃ yolo; rails (delete-guard, admin, budget) never drop |
| **surface** | yes | code / chat / scratch |
| **overlays** | multi | howto, image — talk-primary, not a second exclusive mode |

---

## 5. Knowledge & composition

Canonical verb: **`/attach`** (`doc` | `ref` | `bookmark`) / **`/detach`**.  
Aliases forever: `/doc`, `/undoc`, `/recall`, …  

Cost shapes stay distinct (inline every turn vs search-on-demand vs live pointer).  
Plugins: markdown manifests + `plugin_call`; stock under `xlii/stock_*`.

---

## 6. Tools & workers

- Main agent: full project tools (read/write/bash/search/…).
- **`dispatch_subagent`**: workers **read-only by architecture** (role palette); no nested dispatch.
- Intent gate on bash is **code-level** (model is not the security boundary).
- Path jail: project-relative resolution for file tools.

---

## 7. Sync, Collections, journal, exit

| Concern | Behavior |
|---------|----------|
| **Local files** | Truth while editing |
| **Collections** | Optional mirror + RAG (`search_project`); management key env-only |
| **Dirty sync** | End of turn + **blocking** on `agent.close()` / exit |
| **Journal** | Optional diary; exit uses `defer_flush` (raw entries durable; no LLM on the exit path); next session `catch_up` summarizes the spool |
| **Exit** | `run_graceful_exit` (`xlii/exit_sequence.py`) — announced teardown for inline + TUI; journal step stays near-instant |

Local-only / `--preview` / FTS5: first-class for analysis without upload.  
Collections = **memory insurance**, not a token accelerator. See product notes in first-look + Collections discussion.

---

## 8. Help & docs ratchets

| Surface | Behavior |
|---------|----------|
| `/help` | **daily** by default; `compose` · `power` · `all` |
| `xlii help` | CLI subcommands |
| Generated tables | `python -m xlii.docgen` → GUIDE regions |
| Help corpus | `docs/help/**` → `scripts/bundle_help.py` → `xlii/help/` |
| Truth check | `scripts/check_docs.py` |

---

## 9. Naming / legacy

Intentional `xli` leftovers (keyring service, collection prefixes, env alias): **[LEGACY.md](LEGACY.md)**.  
Canonical paths: `~/.config/xlii/`, project `.xlii/`.

---

## 10. Quality track (grades)

Raising first-look B/C grades — plan lives in **local** `.xlii/plans/grades-bc-to-a.md` (gitignored with `.xlii/`). Shipped on branch `groked` / PR:

| Phase | Outcome |
|-------|---------|
| 1 | Progressive help + status axes |
| 2 | LEGACY + vault env + doctor line |
| 3 | GOLDEN-PATH |
| 4 | SessionState nesting |
| 5 | TUI mixins + `addressing.builtins` |
| 6 | This architecture doc + public-tree hygiene |

---

## 11. What’s next (product)

Product next-steps: see [ROADMAP.md](../ROADMAP.md). Historical design proposals are archived out of the public tree.

Not in critical path for “usable public spine”: Tauri, canvas, PDF lab work, XMPP mobile fabric (separate plan when ready).

---

## 12. Map for maintainers

| Area | Location |
|------|----------|
| CLI | `xlii/cli.py`, `xlii/cmds/` |
| REPL / slash | `xlii/repl.py`, `xlii/repl_cmds/`, `xlii/commands.py` |
| Agent / tools | `xlii/agent.py`, `xlii/tools*`, `xlii/session_state.py` |
| TUI | `xlii/tui/app.py` + `app_*_mixin.py`, `tui_textual.py` |
| Addressing / VFS | `xlii/addressing/`, `xlii/addressing/builtins/` |
| Config / vault | `xlii/config.py`, `xlii/vault.py` |
| Sync / search | `xlii/sync*`, storage / FTS |
| Loop / swarm | `xlii/loop.py`, harness under `xlii/harness/` |

*Update this file when ownership or control flow changes — not when a one-off experiment lands in lab.*
