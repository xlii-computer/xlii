# The Coding Rail

The Coding Rail (`/rail`) walks a risky change through six gated stages. The
agent cannot jump straight to code: stages 0–3 are read-only thinking, and file
writes only unlock at stage 4. **You are the gate** — you review each stage's
output and advance with `/rail next`.

Use it for changes where a wrong turn early (misread requirement, missed edge
case) is expensive to undo. For lighter work, `/plan` then `/execute` is enough
(see below).

The rail is a code-surface mode. Start it inside `xlii code`.

## The six stages

| # | Stage | Writes | What it gates |
| - | --- | --- | --- |
| 0 | Requirements Lock | locked | Restate the task; list every explicit and implicit requirement; surface ambiguities before anything else. |
| 1 | Architecture & Plan | locked | High-level components, data flow, key functions/classes, chosen patterns and why. No code. |
| 2 | Edge Cases | locked | Every edge case, error path, input validation, security and performance concern — and confirmation the plan covers them. |
| 3 | Pseudocode | locked | Detailed pseudocode or numbered steps for each component. Still no real code. |
| 4 | Implementation | **unlocked** | The complete, production-ready code. No TODOs. |
| 5 | Self-Review | **unlocked** | The agent reviews its own work against the requirements, fixes gaps, then prints `REVIEW PASSED – READY`. |

The lock is real, not advisory: in stages 0–3 the agent loop hands the model
only the read-only investigation tool set (no `write_file`, `edit_file`, or
`bash`), so it physically cannot touch the tree while it is still thinking.

## Moving through the rail

```
/rail              start (or restart) the rail at stage 0
                   then type your task to run stage 0
/rail next         review looked good — advance to the next stage
/rail back         redo the previous stage
/rail status       reprint the current stage and its instructions
/rail off          exit the rail entirely
```

A typical pass:

1. `/rail` — turns the rail on and prints the stage-0 instructions.
2. Type your task. The agent produces the Requirements Lock (read-only).
3. Read it. If it captured the task, `/rail next`; if not, refine by typing a
   normal message (you stay in the same stage), or `/rail back`.
4. Repeat through stages 1–3. Each `/rail next` carries the **same task**
   forward and runs that stage immediately — you do not retype the task.
5. At stage 4 writes unlock and the agent implements. Stage 5 self-reviews.
6. `/rail off` when done, or `/rail` again to run a new task on the rail.

`/rail next` from the final stage prints `rail complete` rather than advancing.
`/rail back` from stage 0 is a no-op.

Run `/describe rail` for the authoritative, always-current subcommand list and
behavior. The accepted aliases (`next`/`n`, `back`/`b`, `status`/`?`,
`off`/`stop`) come straight from the live registry.

## Rail vs. /plan then /execute

Both start with read-only investigation; they differ in granularity.

| | `/plan` → `/execute` | `/rail` |
| --- | --- | --- |
| Gates | one (approve the plan, then execute) | six (one per stage) |
| Read-only phase | a single investigation pass | four staged passes (req, arch, edge, pseudo) |
| Best for | a clear change you want planned once | a risky change where each step deserves a checkpoint |
| Advance | `/execute` | `/rail next` per stage |

Pick `/plan` then `/execute` when one checkpoint is enough. Reach for `/rail`
when you want to lock requirements and edge cases *before* any architecture or
code is committed to.

## Seeding the rail from a plan

You do not have to choose up front. From plan mode you can carry an approved
plan straight onto the rail:

```
/plan                  investigate read-only, produce a numbered plan
/execute rail          approve the plan and run it through all 6 stages
```

`/rail` while a plan is pending does the same thing. Either way the rail starts
at stage 0 **seeded from the plan**: the early stages confirm and refine each
point against the agreed plan instead of re-deriving requirements from scratch.

See `/describe execute` for the exact `/execute [rail]` form.

## Constraints and interactions

- **Rail and plan mode are mutually exclusive.** Entering `/plan` while the rail
  is on turns the rail off, and seeding the rail from a plan clears plan mode.
- **A live loop blocks the rail.** If `/loop` is running, `/rail` refuses with a
  prompt to `/loop off` first.
- The rail is per-session state on the code surface; switching to `/chat` and
  back does not carry it.
- Only the *current* stage's directive is injected each turn, so earlier "never
  write code" rules never leak into the implementation stage.

## See also

- `/howto modes` — the full decision tree for plan, rail, loop, verify, peer.
- `/howto loop-swarm` — walk-away build→test→fix and parallel writers.
- `/howto review` — `/verify` and `/peer` for checking finished work.
- `/howto first-session` — code vs. chat surfaces and input routing.
- `/describe modes` — the live, one-line version of the mode picker.
