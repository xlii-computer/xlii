# Modes & gates

Modes change *how the next turn runs* — what the model is allowed to do, how
many tools it gets, and who reviews the result. Pick a mode by the shape of the
task, not by habit. The decision tree below mirrors `/describe modes`; that
command prints the **live** tree, so when in doubt run it.

## Decision tree

```
What are you trying to do?

  Risky / structured code change
    ├─ heavy, want hard gates per phase   → /rail        (6 stages, writes locked until impl)
    └─ lighter two-phase gate             → /plan → /execute   (or /cancel to drop)

  Walk away until it builds & passes      → /loop         (autonomous build→test→fix)
                                          → `xlii loop`    (same, headless; shell command)

  Review work that already exists
    ├─ against the task you just gave it   → /verify       (cold-context: task + diff)
    └─ blind, no author intent             → /peer         (committed range, no context)

  Second opinion from another vendor       → /consult      (one-shot, never enters history)

  Drive another harness's live session     → /cursor on    (or /delegate <harness> on; bare input → that harness)

  Many writers at once, in worktrees       → /swarm        (concurrency ceiling; see /describe loop)

  Tune the agent loop
    ├─ tool budget per turn                → /iterations    (session-wide)
    └─ creativity, just this turn          → /temp          (one-shot)

  Bash confirmation gate
    ├─ stop asking before shell            → /yolo
    └─ ask again                           → /safe

  Bail out of plan mode                    → /cancel
```

Run `/describe modes` for the authoritative, always-current version of this tree,
and `/describe <cmd>` for the exact flags of any command below.

## /plan

Enter read-only investigation mode. The next turn explores the codebase with
read tools only — no `write_file`, `edit_file`, or `bash` — and ends in a
numbered plan. Nothing changes on disk yet. From there you `/execute` to run the
plan, `/rail` (or `/execute rail`) to run the approved plan through the rail
stages one by one, or `/cancel` to drop it. Plan mode and the rail are mutually
exclusive; starting one clears the other. Plan mode is blocked while a `/loop`
is active.

The plan IS the session's todo list, and the TUI shows it that way: a one-line
strip above the input (`plan 3/7 ▸ next item`, live as checks land) and an
item-level panel — `/plan panel`, `/panel plan`, Alt-P, or a click on the
strip. Panel rows are the plan's `{#id}` checkboxes (☐ open · ☑ receipted ·
☑? checked without a receipt) plus the pending-amendments queue; actions seed
`/plan check` / `/plan amend` into the input for review, and the raw file
stays one action away.

`/plan --with <provider>` hires a configured gigwork brain as THIS plan
session's planner: plan-mode turns route through that endpoint (its model, its
key), with the xAI server tools stripped by capability — it investigates with
grep, glob, and file reads. Providers with `"kind": "anthropic_native"` refuse
streaming plan turns until a native stream adapter ships — use `openai_compat`
for `/plan --with` on Claude until then. The hire is scoped to the session: `/execute` and
`/cancel` end it, and execution always runs on the home plane. A gig-planned
plan is indistinguishable downstream — same checkboxes, same receipts, same
`/execute`.

## /execute

Approve the pending plan and run it. `/execute` alone unlocks all tools and tells
the model to carry out the plan above. `/execute rail` instead seeds the Coding
Rail from the approved plan and starts it at stage 0, so each phase still gets a
gate. If you're not in plan mode there is nothing to execute and it no-ops.

## /rail

The Coding Rail — stage-gated coding through six phases: Requirements Lock →
Architecture & Plan → Edge Cases → Pseudocode → Implementation → Self-Review.
Stages 0–3 are **read-only**: the agent loop physically restricts tools to the
investigation set, so the model cannot write files or run shell while it is still
thinking. Writes unlock only at stage 4. You are the gate between every stage.
Type your task to begin stage 0, then `/rail next` to advance, `/rail back` to
redo, `/rail status` to reprint the current stage, and `/rail off` to exit. A
pending `/plan` plus a bare `/rail` carries the approved plan onto the rail. See
`/howto rail` for the full walkthrough.

## /loop

Autonomous build→test→fix: hand it a goal and walk away. The loop iterates,
running your test command and judges, until it reaches green or hits a budget.
Flags cover the test command, max iterations, dollar budget, commit policy, and
which judges run — run `/describe loop` for the authoritative set, or use
`xlii loop` for the headless equivalent from your shell. Loop is mutually
exclusive with `/plan` and `/rail` (turn the loop off first). See
`/howto loop-swarm`.

## /swarm

Show or set the live ceiling on concurrent worker agents — the cap on how many
writers run in parallel (typically in worktrees, driven by `/loop`). Bare
`/swarm` reports the current ceiling; pass a number to change it for the session,
and `--save` to persist. The mechanics live with the loop, so read
`/describe loop` and `/howto loop-swarm` for how swarming actually executes.

## Harness modes — `/cursor on`, `/delegate <harness> on`

Making an external harness the *foreground mode* flips who bare input talks to.
`/cursor on` — and for the other harnesses `/delegate claude on`, `/delegate codex on`,
`/delegate grok on` — puts that harness in front: from then on, a plain line drives
the harness's **live session** instead of xlii's own agent. `/cursor` is the permanent
alias for `/delegate cursor`. The escapes still work — `?` asks xlii, `!`
runs shell, `/` runs a command — so you only hand over the plain typing. `/cursor off`
(or `/delegate <harness> off`) leaves the mode; the underlying session stays alive, so
you can flip back in with `on`.

The harness *is* the model in most cases — `claude` ("claude code"), `codex`,
and `grok` ("grok build") each speak as themselves. Cursor is the exception: it's
a **host** you run a chosen model through, so it's the one harness where `/model`
selects which model the session uses and the stat bar appends `· <model>`. The
others wear their own name with no model axis. See `/howto bridges` for the
session lifecycle (`/cursor new`, `@name`, `ls`) underneath the mode.

## /verify

A one-shot, cold-context check on your **uncommitted** work against the task from
the last turn. A fresh verifier sees the task and the diff with no prior context,
so it catches drift in-session model rationalized away. Use it right after a
change, before you commit. See `/howto review`.

## /peer

Blind peer review of a **committed** range — the reviewer gets the diff with no
author intent and no conversation history, the way a stranger would read your PR.
Scope it with a since-ref (run `/describe peer` for the exact flag). Use `/verify`
for "did this do the task"; use `/peer` for "is this good code on its own terms."

## /consult

Ask a second, cross-vendor model for an independent opinion. The reply never
enters your conversation history, so it's a true outside check rather than an
echo of your own context. You can scope how much of the session it sees (recent
turns, the current turn, or the full thread) and append a question — run
`/describe consult` for the exact selectors. The simple setup is
`/consult --set-to <gigworker>` — perma-hire a `gigwork.providers` entry as
the default judge (a binding by name: the endpoint/key/model stay defined
once in the gigwork registry, and loop judges accept the same `gig:` key).
Inline judge profiles in `~/.config/xlii/config.json` remain for hand-rolled
setups; `/describe consult` prints the live provider list.

## /yolo and /safe

`/yolo` disables the bash confirmation gate so shell commands run without a
prompt — fast, but the model can run anything. `/safe` re-enables the gate. Use
`/yolo` only in a throwaway or sandboxed checkout, and flip back to `/safe` the
moment you're done. `/status` shows the current gate state.

## /iterations and /temp

`/iterations <1..100>` sets the max tool iterations per turn for the rest of the
session — raise it for deep multi-file work, lower it to keep turns tight. Bare
`/iterations` shows the current value. `/temp <0.0..2.0>` overrides the sampling
temperature for the **next turn only**, then reverts; reach for it when you want
one more-creative (or more-deterministic) shot without changing your defaults.

## /cancel

Exit plan mode without executing anything. The plan is discarded and the next
turn behaves normally. Outside plan mode it no-ops.

## See also

- `/howto rail` — the six stages in depth
- `/howto loop-swarm` — autonomous loops and parallel writers
- `/howto review` — `/verify`, `/peer`, and `/consult` compared
- `/howto config-models` — orchestrator/worker models and temperatures
- `/describe <cmd>` — live, authoritative flags for any command above
