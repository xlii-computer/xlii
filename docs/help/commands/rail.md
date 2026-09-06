# /rail

`/rail` puts a coding turn on a stage-gated track: requirements → architecture →
edge cases → pseudocode → implementation → self-review. The agent cannot skip to
code. Stages 0–3 are read-only — the model gets only the investigation tool set
(no `write_file`, `edit_file`, or `bash`), so it physically cannot touch the tree
while it is still thinking. Writes unlock at stage 4. You are the gate: review
each stage's output, then advance with `/rail next`.

Reach for it when a wrong turn *early* is expensive — a misread requirement or a
missed edge case that you would otherwise discover only after the diff is
written. For a change that is already clear, `/plan` then `/execute` gives you one
checkpoint instead of six and is the lighter tool. The rail is a code-surface
mode; start it inside `xlii code`.

## Usage

```
/rail            start (or restart) the rail at stage 0
/rail next       this stage looked good — advance and run the next one
/rail back       redo the previous stage
/rail status     reprint the current stage and its instructions
/rail off        exit the rail
```

`/rail next` carries the **same task** forward and runs that stage immediately —
you do not retype the request. To refine within a stage instead of advancing,
just type a normal message; you stay put. (`next`/`n`, `back`/`b`, `status`/`?`,
`off`/`stop` are accepted aliases — `/describe rail` has the authoritative list.)

## Examples

Drive a risky change through the full rail:

```
/rail
Add idempotent retry to the webhook publisher
# read the Requirements Lock, then:
/rail next        # → Architecture & Plan (still read-only)
/rail next        # → Edge Cases
/rail back        # not thorough enough — redo Edge Cases
/rail next        # → Pseudocode, then /rail next unlocks Implementation
/rail off         # done
```

Carry an approved plan straight onto the rail:

```
/plan
/execute rail     # approve the plan and run it through all 6 stages, seeded
```

## Gotchas

- **Plan mode and the rail are mutually exclusive.** Entering `/plan` while the
  rail is on turns the rail off; seeding the rail from a plan clears plan mode.
- **A live loop blocks it.** If `/loop` is running, `/rail` refuses until you
  `/loop off` first.
- The first `/rail` only *arms* the rail and prints stage-0 instructions — you
  still type your task to run stage 0.
- `/rail next` at the final stage prints `rail complete` rather than advancing;
  `/rail back` at stage 0 is a no-op.
- It is per-session code-surface state — switching to `/chat` and back does not
  carry the rail.

Deep dive: `/howto rail`
