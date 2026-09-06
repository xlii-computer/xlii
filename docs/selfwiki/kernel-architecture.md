---
sources: file://proposals/kernel-rebuild.md, file://docs/ARCHITECTURE.md#L44-56, file://pyproject.toml#L111-238, file://xlii/conversation.py#L135-620, file://xlii/agent.py#L193-249, file://xlii/wiki.py#L1-28, file://.xlii/wiki/kernel-and-session.md, file://xlii/addressing/builtins
verified: false
---
# kernel-architecture

The windowpane core from the kernel-rebuild north-star: one session **kernel** behind
everything, **panes** as the only surface, a **VFS** as the only address space, an
event/transport seam for inbound work, and a **wiki** as semantic memory. The build is a
strangler migration onto that core — the load-bearing seams are shipped and enforced;
full convergence (TUI transcript folded into a Dock-backed pane) is still in progress.

## The one insight

xlii's three most expensive recurring bug classes — the TUI reimplementing REPL input,
the `REPLState`-flag desync, the `cmds/*`/`tui_textual` sprawl — were **the same bug**:
no single owner of a turn. The core had been left with two parallel implementations (REPL
and TUI) while seams were cut everywhere else. The fix: make REPL, TUI, and future
daemon/mobile *clients* of one session kernel. "Built-ins as client #1 of a public seam,"
finally applied to the kernel itself (proposals/kernel-rebuild.md).

## The nouns and the irreducible kernel

Four nouns: the **VFS** (what you can address — the namespace), **panes** (surfaces; hold
view-state only), **xlii** (the actor — session + turn + agent, ambient behind every
pane), and the **event/transport** seam (inbound work: mail, xmpp, cron, jobs). Panes
can't think; the VFS can't act. The kernel owns only: turn lifecycle, session state,
dispatch/registry (things register *into* it; it never imports them), and four ports —
**provider** (xAI decoupling), **storage/VFS** (the locker keystone), **surface** (panes),
**sink** (rendering).

## The load-bearing fence: faces import kernel, never the reverse

Enforced as an import-linter `forbidden` contract (pyproject.toml, godzilla-mothra V0a).
Faces = `xlii.tui`, `xlii.panes`, `xlii.tui_textual`; kernel = every other `xlii` module,
and no kernel module may import a face. CI runs `lint-imports`. `scripts/check_contracts.py`
keeps `source_modules` in sync with the filesystem (catching PEP 420 namespace escapes),
so a new kernel module cannot dodge the contract. A frozen baseline of ~two dozen
`ignore_imports` edges remains — never wildcarded, additions sanctioned case-by-case,
burned down over time
(`unmatched_ignore_imports_alerting = error` keeps it honest). This fence is what keeps a
GUI/web surface from reimplementing kernel behavior.

## One turn owner

`drive_turn` (`xlii/conversation.py`) is the single spine every surface calls: loop-session
sync, metering, pre/post hooks, attachments, checkpoints, live `Conversation` lifecycle,
one persistence policy, end-of-turn sync, journal, continuation. Surfaces supply only
`render(result, prompt)` and `on_error`. `Conversation` owns completed turns plus in-flight
state; persistence delegates to the turn store exactly once (no double-writes). The
executor/sink seam — `KernelTurnExecutor` + a `TurnSink` protocol (`KernelTurnSink` is the
in-tree impl, not yet fully wired) — encapsulates the per-turn hooks so pane `ENQUEUE_TURN`,
inline REPL, and TUI main input all feed one pipeline. This is the structural death of the
"TUI-reimplements-REPL-input" class.

## One mode truth

`Agent.active_mode` (an `Optional[ModeController]`: Plan/Discovery/Ops controllers) is the
single source of mode. There is no global mode flag living in two places. `REPLState` holds
only surface/persistence state (workspaces, shell cwd, persona handle) and **delegates**
session flags to the same `SessionState` the Agent holds; the Agent is authoritative
(docs/ARCHITECTURE.md §3). The kernel-rebuild target generalizes this — "modes become
panes": focus + selection *is* the mode, so there is nothing to desync.

## Addressing, panes, wiki

- **VFS / addressing** — the uniform `scheme://target` scheme (scheme == provider,
  centralized bare-token resolution) shipped on `kernel-rebuild`; the open-question #1 is
  closed. Providers live under `xlii/addressing/builtins/` (file, project, conv, config,
  persona, git, locker, wiki, jobs, tasks, home, plan, docs, map, mark, gigwork, remote,
  xlii-root, …). The kernel is itself a mountable root (`xlii://`). See [[addressing-and-vfs]].
- **Panes** — the only surface; a pane instance is a pure projection of `(VFS address,
  selection)` and is reconstructible. Anything another surface must agree on lives in the
  kernel, not the pane. Dock = the turn-input slot. See [[panes-and-dock]].
- **Wiki** — the semantic tier of the memory hierarchy (`conv://` episodic · `xlii://`
  working · `wiki://` semantic): distilled, linked, durable pages under `.xlii/wiki/`.
  Two anti-confabulation guards are data in `xlii/wiki.py`: provenance (`sources:` addresses)
  and verify-before-trust (born `verified: false`, promoted only after a skeptical pass —
  write → refute → promote). See [[xliiwiki]].

## Deployment

Same kernel, three topologies (config, not fork): local (`xlii code`), single-tenant
personal cloud on a VPS (the always-on home the daemon/event-seam/Conductor need), and
multi-tenant SaaS (deferred behind a tenancy + hard-isolation layer). The capability/risk
gate is the security boundary once the kernel acts on untrusted inbound events. See
[[trust-and-gates]] for the fence work.
