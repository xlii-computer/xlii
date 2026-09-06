---
sources: file://xlii/panes/plan.py, file://xlii/panes/plan_items.py, file://xlii/tui/plan_strip.py, file://proposals/plan-surface.md, file://docs/help/commands/plan.md, file://xlii/prompts/plan-preamble.md, file://xlii/agent.py#L210-234, file://xlii/agent.py#L715-724, file://xlii/repl_cmds/mode.py#L619-658, file://xlii/tui/app_menu_mixin.py#L346-366, file://xlii/tui/app_turn_mixin.py#L556-558, file://xlii/plan_ops.py, file://xlii/chat_backend.py
verified: false
---
# plan-surface

Plan is a first-class surface in xlii, not a scratch note. The working plan file
`.xlii/plans/current.md` IS the session's todo list — structured `{#id}` checkboxes,
receipts on check, a queued-amendments channel — addressable over `plan://` and
rendered as live TUI chrome. Three layers stack: **plan mode** (the read-only planning
turn), **plan-write-domain** (the file substrate + `plan://` + amend), and the **plan
surface** (the strip and item panes, T1), with **`/plan --with`** hiring a foreign
planner (T2).

## Plan mode — no fake actions

`/plan` toggles the code REPL into plan mode: the next turn investigates read-only and
hands back a numbered plan instead of editing (`docs/help/commands/plan.md`). It is a
`PlanController` set as `Agent.active_mode`; `plan_mode` is *derived*
(`isinstance(active_mode, PlanController)`), not a stored flag — active_mode is the sole
truth. The controller reports `read_only_tool_palette`, threaded into the tool builder as
`plan_mode=`, which strips the palette to reads/search **plus** `write_file`/`edit_file`
scoped to `.xlii/plans/` only — no bash, no `dispatch_subagent`; any write outside plans/
is refused (`prompts/plan-preamble.md`). Because repo writes are physically blocked, the
model must speak in future tense — "steps describing exactly what changes you would make"
— and can never truthfully claim it edited the repo; a claim of file creation in plan mode
is a hallucination to verify on `/execute`. "Planning IS writing": the turn's real side
effect is reconciling `current.md` in place as one coherent document. `/plan` is
one-shot-per-turn and mutually exclusive with `/rail` and `/loop`; `/execute` approves
(repo writes unlock, plans/ goes read-only), `/cancel` drops it. See [[command-surface]].

## plan-write-domain — the substrate (PRs #182–#195)

The plan file is a real data structure, not prose. `xlii/plan_ops.py` parses
`- [ ] {#short-id} …` checkboxes into `PlanItem`s with three states — `[ ]` open (☐),
`[x]` receipted (☑), `[x?]` checked-without-receipt (☑?) — plus a `## Amendments` queue
of `- [?]` lines. After approval the implementer may only *mark items done*
(`check_plan_item`, receipts attached), never rewrite them; to contest, it queues an
amendment (`amend_plan`) that the planner resolves on the next plan turn
(fold-with-provenance or reject). `plan://` is the VFS scheme (`PlanProvider`):
`plan://<name>` is one plan, `plan://<name>#<id>` one checkbox or amendment. See
[[addressing-and-vfs]], [[journal-and-receipts]].

## T1 — the todo surface (PR #291)

Two panes plus one strip, all pure projections of `(address, selection)` that **mutate
nothing** — every action PREFILLs a `/plan …` command into the input for
review-before-run (the house contract):

- **`PlanPane`** (`xlii/panes/plan.py`) — the `plan://` list: one row per plan file with
  `checked/total`, age, and a `✎N` pending-amendment badge; the working `current` sorts
  first.
- **`PlanItemsPane`** (`xlii/panes/plan_items.py`) — one plan's items: ☐/☑/☑? rows
  showing receipts, then the `[?]` amendment queue; actions are state-shaped (open→check,
  `[x?]`→add receipt, any id→contest via amend), the raw file one action away.
- **The plan strip** (`xlii/tui/plan_strip.py`) — one always-current line above the
  input, `plan 3/7 ▸ <next item>  ✎N`, replacing the old below-input checkmark ticker; a
  click opens the item pane. Visible only when the plan has items and is unfinished (or
  plan mode holds); the `7/7 ✓` line shows only inside plan mode.

See [[panes-and-dock]].

## T2 — `/plan --with <provider>` (PR #292)

`/plan --with <gig>` hires a configured gigwork brain as **this plan session's planner**
(`repl_cmds/mode.py`). The backend lives on `PlanController.chat_backend`;
`_stream_orchestrator_iteration` swaps its model/endpoint in for exactly these
orchestrator turns, with the xAI server tools stripped by capability — the gig
investigates with grep/glob/read. It is safe-by-construction: the read-only palette plus
writes code-gated to `.xlii/plans/` mean the one room where a foreign brain drives the
MAIN loop has a blast radius of one reviewable markdown file. `/execute` replaces the
controller, so **execution is home-plane by construction**; a gig-planned plan is
indistinguishable downstream (same checkboxes, receipts, `/execute`). See
[[gigwork-and-foreign-brains]], [[trust-and-gates]].

**The gate experiment (2026-07-19):** haiku, hired through the real gigwork worker path,
drafted this very T2 plan from the in-repo proposal — 20 iterations, 61s, ~$0.60, naming
the exact seams with correct line numbers (one invented symbol of 14 items). The
hypothesis ("a foreign brain can be better at planning") held. **Secondary finding:** the
Anthropic OpenAI-compat layer reported ZERO cached tokens for the `cache_control` marks —
multi-iteration long-context planning is exactly where caching matters, so this motivated
the native `AnthropicNativeBackend` (PR #297; scope D18: workers + `/plan --with` only).
See [[gigwork-and-foreign-brains]].

## Refresh model

Plan surfaces are recomputed from `plan_ops` on three paths (`plan_strip.py`,
`app_menu_mixin.py`, `app_turn_mixin.py`): the kernel **plan listener**
(`set_plan_listener`, fired by `plan_check`/`plan_amend`/plan writes, marshalled to the
main thread), the **end of every turn** (mode flips and `/plan save|continue` don't ride
the listener), and **app mount**. A visible `plan://` dock pane re-opens its own address
on refresh so a check/amend reflects at once. The kernel never imports the TUI — the
listener is a callback seam, same shape as the jobs and conversation listeners.
