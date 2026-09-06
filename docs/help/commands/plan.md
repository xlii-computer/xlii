# /plan

`/plan` flips the code REPL into **plan mode**: the next turn investigates the
codebase read-only, then hands back a numbered plan instead of editing anything.
You stay in the driver's seat — nothing is written, run, or committed until you
approve.

Reach for it when a change is risky, touches code you don't fully understand
yet, or spans several files and you want to see the blast radius before any edit
lands. If you already know exactly what to type, skip it — plan mode is overhead.
For a heavier, stage-gated treatment of the same problem, use `/rail` instead.

Talk never self-starts plan mode. A research desk is not a code project, and
plan writes ``.xlii/plans/`` after a read-only investigation — that tool was
built for the lab. On Face, `/plan` from `[M]` is a **gateway**: it flips to
`[$]` and then enters plan there. Bare `/plan` is a **cold** start — the lab
does not see the Mojo tape. `/plan --from-mojo` (aliases `--from-talk`,
`--add-context`) rolls the last few talk turns into the planner as task
context, opt-in. The inline chat REPL has no lab flip — `/code`, then `/plan`.

## How it works

`/plan` arms the next turn. Flags on entry only:

```
/plan
/plan --from-mojo
/plan --with kimi --from-mojo
```

```
/plan
how do we add a --dry-run flag to the loop runner?
```

After a Mojo riff, carry the talk:

```
/plan --from-mojo
lets do this
```

The model answers using only read-only tools (file reads, search, scoped
subagents) and returns a numbered plan. Then choose one:

- `/execute` — approve and run the plan now, free-form, with all tools.
- `/execute rail` — approve and carry the same plan through the 6-stage Coding
  Rail (`req → arch → edge → pseudo → impl → review`); equivalent to typing
  `/rail` while a plan is pending.
- `/cancel` — drop the plan and leave plan mode without executing.

```
/plan
add input validation to the config loader and cover it with a test
   → returns a numbered plan
/execute
   → "plan approved — executing"
```

## Gotchas

- Plan mode is **one-shot per turn** in spirit: type your task right after
  `/plan`. A bare line in plan mode goes to the model (not the shell), so you
  can't accidentally run a command — but you also can't shell out mid-plan.
- It's **mutually exclusive** with the rail and the loop. `/plan` while a rail
  is active turns the rail off; `/plan` is refused while a loop is running
  (`/loop off` first). Likewise `/loop` and `/rail` will refuse or take over if
  a plan is pending.
- `/execute` and `/cancel` only do something *while in plan mode*. Outside it
  they no-op with a quiet note — there's nothing to approve.
- The numbered plan is just text in the thread. Approving with `/execute` does
  not re-derive it; it instructs the model to carry out the plan above, so make
  sure the plan you see is the one you want before approving.
- For unattended work, hand the plan to the loop instead of executing inline:
  `/loop <goal> --from-plan` runs to green without you babysitting each turn.
- `/status` shows the live `plan mode` flag if you're unsure whether it's armed.

See also: `/describe modes` for the live decision tree across `/rail`, `/loop`,
`/execute`, and friends.

Deep dive: `/howto modes`
