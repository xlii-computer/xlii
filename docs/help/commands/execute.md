# /execute

Approve a pending plan and let the agent carry it out. `/execute` is the second
half of the two-phase plan gate: `/plan` puts the next turn into read-only
investigation that ends in a numbered plan, then you read that plan and decide.
`/execute` is your "yes, go" — it drops out of plan mode and runs the very next
turn with full tool access against the plan you just read.

Reach for it when you ran `/plan` for a risky or multi-step change, the plan
looks right, and you want it done in one execution pass. If the plan is wrong,
don't `/execute` — `/cancel` instead, or just keep talking in plan mode to
refine it. There is nothing to approve unless plan mode is on: outside plan mode
`/execute` is a no-op and tells you so.

## Usage

```
/execute [rail]
```

- bare `/execute` — approve the plan and execute it free-form, using all
  available tools in a single turn.
- `/execute rail` — approve the plan but carry it onto the **Coding Rail**
  instead of a free-for-all run. The plan seeds Stage 0 (Requirements Lock) and
  the agent works it through all six rail stages, confirming each requirement
  against the approved plan rather than re-deriving it. Equivalent to `/rail`
  when a plan is already pending.

## When to add `rail`

Plain `/execute` is the right call for most changes — the plan is the
checkpoint, and you trust the model to land it. Add `rail` when the change is
big or fragile enough that you want stage gates *during* execution too:
requirements → architecture → edge cases → pseudocode → implementation →
self-review, advancing with `/rail next`. Think of it as "I approved the *what*,
now gate the *how*."

## Examples

Plan a refactor, review it, then run it in one pass:

```
/plan
> split the auth module into provider-agnostic and xAI-specific layers
# read the numbered plan it produces...
/execute
```

Approve the same plan but force stage-by-stage discipline:

```
/execute rail
# lands on Stage 0 (Requirements Lock); use /rail next to advance
```

## Gotchas

- **Not in plan mode = nothing happens.** `/execute` only acts when `/plan` is
  active. If you typed it and saw "(not in plan mode — nothing to execute)", run
  `/plan` first.
- **A loop blocks plan mode.** You can't enter `/plan` (and therefore can't
  `/execute`) while a loop is running — `/loop off` first.
- **Rail and plan are mutually exclusive.** `/execute rail` converts the plan
  into a rail run; you're no longer in plain plan mode afterward.
- **`/execute` consumes the next turn immediately** with "execute the plan
  above" — it does not wait for more input. Make sure the plan on screen is the
  one you mean to run.

For the live signature, run `/describe execute` (and `/describe plan` /
`/describe rail` for its siblings). Deep dive: `/howto modes`.
