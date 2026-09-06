# Autonomous loop & writer swarm

Walk away to green: `/loop` runs a full build → test → judges → fix cycle on its
own until everything passes, gives up honestly, or hits a cap. `/swarm` sets the
ceiling on how many worker agents run at once, which lets a loop fan its build out
across parallel writers in isolated git worktrees.

For the authoritative, always-current flags on any command below, run
`/describe loop` or `/describe swarm`. This page is the operator's overview.

## `/loop` — the autonomous cycle

```
/loop <goal> [--from-plan] [--judge tests,xai-verify] [--max N] [--test CMD]
             [--budget USD] [--commit never|each|final] [--read-budget N]
```

Each cycle does the same thing:

1. **build** — the agent works on the goal until it believes tests pass.
2. **test** — the shell oracle (`--test`, default `pytest -q`) runs. Non-zero
   exit routes the failure tail back as a fix prompt for the next cycle.
3. **verify** — once tests are green, any LLM judges in the stack run against a
   cold diff bundle. A FAIL routes back with the findings.
4. **done / route back** — all judges PASS → loop is `done`. Otherwise the cycle
   increments and the agent gets a targeted fix prompt.

The loop stops on its own when: all judges pass (`done`), it hits `--max` cycles
(`capped`), the same failure signature repeats with no progress (`failed`), or a
budget/setup problem makes further work pointless.

`/loop` takes over from `/rail` and `/plan` — start one of those first and the
loop will refuse until you `/rail off` or `/cancel`. Only one loop is active per
project at a time.

### Driving an active loop

```
/loop status     # cycle, phase, judges, verdicts, cost, budget
/loop pause      # stop after the current turn
/loop resume     # continue a paused/interrupted loop
/loop cancel     # abandon it (alias: /loop off)
```

### Seeding from a plan

`/loop --from-plan` reads the last approved plan as the goal — run `/plan` then
`/execute` first so there is a plan to consume. Pass extra text alongside
`--from-plan` to prepend your own framing to the plan body.

### Flags worth knowing

- `--judge a,b,c` — the judge stack (comma-separated). See **Judges & gates**.
- `--max N` — cycle cap before the loop gives up (`capped`).
- `--test CMD` — the shell oracle command; this is the machine gate.
- `--budget USD` — ceiling on cumulative judge spend; hitting it caps the loop.
- `--commit never|each|final` — `each` commits after every green cycle, `final`
  commits once at the end, `never` (default) leaves committing to you.
- `--read-budget N` — files an LLM judge may pull in per `READ_REQUEST`
  follow-up before it must return a verdict.

When the stack has no shell judge, `/loop` warns you: the LLM judges then run
without a machine test gate. Run `/describe loop` for the current flag set.

## Judges & gates

A judge is one gate in the verdict panel. Built-in profiles:

- **tests** — the shell oracle. Exit code 0 = PASS. This is the cheap machine
  gate and is the default stack (`--judge tests`).
- **xai-verify** — same-vendor cold verifier: re-reads the task + diff. Cheap,
  correlated with the builder.
- **xai-peer** — same-vendor blind peer: judges the diff with no author intent,
  to catch intent mismatch.
- **cursor** — cross-agent cold verifier via the Cursor Composer CLI
  (read-only). Needs `cursor-agent` on PATH; missing/timeout degrades to a
  failing "unavailable" verdict rather than crashing the loop.

Stack them: `/loop "harden the parser" --judge tests,xai-verify`. Cross-vendor
and custom shell judges can be defined in config — `/describe loop` and
`/howto config-models` cover declaring your own profiles.

**Test lock & collusion guard.** When a cross-vendor judge is in the stack, the
loop locks your test files: the builder is told not to weaken them, the tool
layer enforces it, and a post-cycle check fails the loop with a collusion alarm
(written to `.xlii/loop-collusion-alarm.md`) if tests were silently gutted to
pass. This is what makes "walk away" safe — the agent can't make tests pass by
deleting them.

Sibling one-shot reviewers (no loop): `/verify` runs a cold verifier on your
uncommitted work vs the last task, and `/peer` does a blind peer review of a
committed range. See `/howto review`.

## Writer swarm — parallel builders

The swarm runs **several writer agents at once**, each in its own throwaway git
worktree under `~/.xlii/worktrees`, then integrates their branches sequentially
into one tree before anything touches your working copy.

`/swarm` controls the live ceiling on concurrent workers:

```
/swarm          # show the current ceiling
/swarm 4        # set it to 4 for this session
/swarm 4 --save # persist it as the project default
```

The swarm itself is launched as a parallel-build mode of `/loop` (configured
swarm size > 1) — see `/describe loop` and `/describe swarm` for how to turn it
on and for the merge options. The flow is:

1. **fan out** — N writers get the goal (with a slice hint to prefer disjoint
   files) and build in parallel worktrees.
2. **integrate** — branches merge sequentially, fewest-files-touched first.
   Clean merges land; conflicts either abort (auto mode) or are resolved by a
   merge agent and checked by a merge judge (LLM merge mode).
3. **pre-land gate** — the *same* judge panel that gates a serial loop runs
   against the whole integration tree. Nothing un-green ever lands.
4. **fix or land** — if the gate fails, a fix-writer patches the integration
   tree in place and the gate re-runs (walk-away-to-green, entirely pre-land).
   On success the integration branch fast-forwards your branch and the
   worktrees/branches are cleaned up. A non-converged run keeps the integration
   branch for inspection and does **not** land.

Because integration is verify-then-land, a working tree that isn't clean is
refused — commit or stash before a swarm lands.

## `xlii loop` — headless

Run a loop to completion without the REPL:

```
xlii loop
```

It drives the same controller, prints cycle/judge progress, and exits with a
result of `LOOP_PASS`, `LOOP_CAP`, or `LOOP_FAIL` — usable as a CI / scripting
gate. Run `xlii loop --help` for its current options, or `/describe loop` for the
shared flag semantics.

## `/tasks` — the horizontal pipe

`/loop` and `/swarm` both chase *one* goal; `/tasks` is the orthogonal primitive:
it pipes *many* ordered steps through one another. Where the loop churns a single
goal to green and the swarm parallelizes writers, `/tasks` runs a deterministic,
user-authored sequence — "do A, feed its output to B, then hand both to C."

Steps are separated by the **fat pipe** `|>` (distinct from a shell `|`, so a
step can still contain its own shell pipe), and each step's text output — the
**carry** — threads into the next. A step's kind is inferred from its prefix:

- _(no prefix)_ — a **shell** command (carry arrives on stdin, or at `{{prev}}`).
- `?` — an **agent** prompt for the live session (carry appended, or `{{prev}}`).
- `/` — a **slash** command line (carry substituted raw at `{{prev}}`).

```
/tasks 'git diff --stat |> ?summarize these changes in one line'
/tasks run 'pytest -q |> ?triage the failures |> /doc add notes {{prev}}' --dry-run
/tasks new nightly      # scaffold a saved .toml pipeline; run it by name later
```

Agent and shell steps that omit `{{prev}}` still receive the carry automatically;
`--dry-run` previews the plan, `--keep-going` carries on past a failing step, and
saved pipelines `resume` from where a failure stopped. A carry that has passed
through an agent step is treated as untrusted, so the next shell/slash step
prompts even under `--yes`. Available in the **code** and **chat** REPLs. See
`/describe tasks` for the full surface.

## See also

- `/howto rail` — stage-gated coding when you want structure, not autonomy.
- `/howto modes` — the full decision tree (rail vs plan vs loop vs review).
- `/howto review` — `/verify`, `/peer`, and `/consult` one-shot reviewers.
- `/howto config-models` — declaring custom judges and loop defaults.
- `/describe loop` · `/describe swarm` · `/describe tasks` · `/describe verify` — live command detail.
