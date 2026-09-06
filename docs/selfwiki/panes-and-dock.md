---
sources: file://xlii/panes/__init__.py, file://xlii/panes/dock.py, file://xlii/tui/dock_surface.py, file://xlii/tui/app.py#L506-567
verified: false
---
# panes-and-dock

The pane layer (kernel-rebuild Vector #2) turns a VFS address into something you look at and act on. It sits above [[addressing-and-vfs]] and below the surface, and is the seam that kills the "TUI reimplements the REPL / no single owner of a turn" desync class. See [[kernel-architecture]] for the four-noun spine this composes into.

## The pane contract

`xlii/panes/__init__.py` defines the `Pane` protocol — the intersection of four ports:

    address           → the mounted Address (property)
    mount(addr,select)→ bind to a VFS address (optionally restore selection)
    render()          → project to a Rendered tree (sink port)
    selection()       → expose current Selection (turn-context input)
    actions()         → selection-actions offered (command port)
    handle(key)       → local nav only, never the kernel

The load-bearing rule: **a pane instance is a pure projection of `(address, selection)`.** Destroy and rebuild from those two values → byte-identical. A pane caches no tree; re-projection *is* the refresh, so a surface never holds state the kernel must agree on. The module is headless — testable with no terminal.

`Selection` is intentionally tiny: the focused `Node` (or `None`). Its `.address` alone reconstructs the whole pane. `render()` returns a frozen `Rendered` (`title`, `rows`, `empty`, `media`); `RenderedRow` carries `text/address/kind` plus surface hints (`selected`, `accent` for a live rider dot, `tone` for git-status colour). `RenderedMedia` is a non-text descriptor (an image) the pane never rasterizes — the surface draws it and falls back to `caption`.

## Actions and Outcomes

An `Action` is `name + label + outcome`. A pane **returns** outcomes as data; it never executes kernel effects. `Outcome` is `kind + address + text + claim`, where `text` carries an `ENQUEUE_TURN` prompt or a `PREFILL` command line, and `claim` carries an `InputClaim` for `CLAIM_INPUT` only.

The bounded outcome set (`xlii/panes/__init__.py`):

- `NAVIGATE` — re-mount this same slot onto `address`
- `RETARGET_SLOT` — open `address` into the other slot
- `ENQUEUE_TURN` — feed the selection to the kernel as a turn (AI context-grab)
- `PREFILL` — seed `text` into the command line, review-before-run, no execution
- `CLAIM_INPUT` — morph the one input line for a single ask (the minibuffer rule)
- `ATTACH` / `DETACH` — ride/unride `address` on the session
- `SHOW_MEDIA` — render an image in the primary surface (Pane 1 / REPL)
- `SPAWN_JOB` — background work through the JobRegistry
- `MUTATE_VFS` — a VFS write/delete over the selection

## The sinks

Five sink protocols name who executes a non-layout outcome — each a duck-typed port the Dock routes to: `TurnSink.submit`, `SessionSink.attach/detach`, `MediaSink.show`, `InputSink.prefill`, `JobSink.spawn`. `ClaimSink.claim` is an **optional second verb on the input sink** (routing duck-types on `claim`), returning `True` when the ask was granted, `False` to refuse; a refusing sink invokes no callbacks — the Dock owns refusal notification via `on_cancel`.

## The Dock

`xlii/panes/dock.py` is the kernel-side layout: named slots, a focus, a pane-type registry, and the outcome executor — headless, never importing the surface. The registry maps an `accepts(node)` predicate to a factory; `open_address` stats once and mounts the first type that accepts (`home`, `transcript`, `skills`, `locker`, `artifacts`, `bookmarks`, `wiki`, `tasks`, `git`, `gigwork`, `plan`, `plan-items`, `explorer`, `image`, `view` — first match wins, specific before generic). New pane types register without touching the Dock.

`dispatch` executes the two layout outcomes itself (`NAVIGATE` re-mounts in place — but crosses to a fresh pane *type* when the scheme changes, so `home://` → `git://` picks by kind rather than asking a HomePane to re-draw; `RETARGET_SLOT` opens the other slot). Every sink-routed outcome raises `NotImplementedError` until its sink is installed, so the seam is explicit, never silently swallowed. Note the module docstring is stale on one point: it lists `ENQUEUE_TURN` as kernel-raised, but `dispatch` routes it to the turn sink — only `MUTATE_VFS` falls through to the final raise.

## The surface

`DockSurface` (`xlii/tui/dock_surface.py`) is the Textual front-end as a **projection of a Dock**: it holds no view-state, and every `repaint()` re-reads `pane.render()`, so the screen can't drift from the kernel. `KEY_MAP` and `slot_renderable` are pure (no Textual). `Tab` cycles slot focus (a surface concern); mapped keys go to the focused pane's `handle`; an Enter that `handle` declines runs the pane's primary action through `Dock.dispatch`. Single-letter keys `a/v/d` (attach/view/detach) and `s/u/c/x/r/y/b/h` (GitPane verbs — see [[gitpain]]) are the keyboard peers of the data-driven footer buttons.

On mount, `DockSurface.on_mount` wires the five app-side sinks: `AppTurnSink` (feeds the app's one `_submit_prompt` pipeline — never re-runs `run_turn`, no double-write; conversation lifecycle stays owned by the kernel spine's `drive_turn`), `AppSessionSink`, `AppMediaSink` (draws into the REPL, not the pane), `AppInputSink`, `AppJobSink`.

## House rules (durable law)

- **Panes and menus PREFILL; they never auto-execute.** A saved task seeds `/tasks run <name>`, a bookmark seeds `/ref <mark>` — dropped into the input un-executed, cursor at the end. Menus do, don't type.
- **CLAIM_INPUT morphs the one input line — no new ModalScreen classes.** The minibuffer rule: a panel item that needs input relabels the single input line for one ask, the same ask-shape any body answers its own way (the TUI morphs its line; a fabric body replies over chat — see [[serve-fabric]]).
- The line is **single-tenant**: Esc always releases it (restoring prompt and stashed draft); a second claim while one is active is **refused, never queued**; and a `PREFILL` mid-claim releases the ask first (via `on_cancel`) rather than silently becoming its answer.

As of 2026-07-20 (#301), `AppInputSink.__init__` class-binds `ClaimPrefillBridge` (from `xlii/tui/task_builder`) and `GitpainPrefillBridge` (from `xlii/panes/git`) so pane-side bridges with no sink handle of their own route through whichever sink the TUI mounted last (idempotent across Dock remounts). `XliiApp.on_mount` (`xlii/tui/app.py`) drains `state.pending_input` into the input line once at boot — editable, never auto-submitted — mirroring the bare REPL's prompt-default for startup-task capture. Related: [[command-surface]], [[tasks-and-pipes]], [[status-and-chrome]].
