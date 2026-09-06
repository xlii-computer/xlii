# /loop

Hand xlii a goal and walk away. `/loop` runs an autonomous build→test→fix
macro-loop: it edits, runs your test command, reads the failures, patches, and
repeats until the oracle says green (or it hits a ceiling). Reach for it when the
target is *verifiable* — a failing test suite, a flaky module, a migration with
clear pass/fail — and you'd rather check back on a result than babysit each turn.

It is the "walk away until green" leg of the modes tree. Use `/rail` or
`/plan` + `/execute` when the change is risky and you want stage gates; use
`/loop` when the spec is mostly settled and the work is grind. The `xai-verify`
judge is the same cold-context reviewer `/verify` runs — see `/describe verify`.

## Usage

```
/loop <goal> [--from-plan] [--judge tests,xai-verify,github-ci] [--max N] [--test CMD] [--budget USD] [--commit never|each|final] [--push never|each|final] [--read-budget N]
```

Lifecycle subcommands once a loop exists:

```
/loop status    # where it is: cycle, phase, judges
/loop pause     # stop after the current step
/loop resume    # pick the loop back up
/loop cancel    # tear it down (alias: /loop off)
```

Key flags and the judgment behind them:

- `--judge` — the oracle stack. `tests` is a shell gate (your test command must
  pass); `xai-verify` adds a cold-context LLM reviewer on top; `github-ci` polls
  required PR checks via the `gh` CLI after push. Default is `tests`. If you stack
  only LLM judges with no shell judge, `/loop` warns you — there is then no
  objective test gate.
- `--max N` — cycle ceiling (default 5). This is your runaway brake; raise it for
  stubborn goals, but a loop that needs 20 cycles usually needs a human.
- `--test CMD` — override the test command (default `pytest -q`, or whatever the
  project/config sets). This is what the `tests` judge actually runs.
- `--budget USD` — hard spend cap; the loop stops when projected cost exceeds it.
- `--commit never|each|final` — `never` (default) leaves the tree dirty for you
  to inspect; `each` commits per green cycle; `final` commits once at the end.
- `--push never|each|final` — push before CI poll. **Must** pair with `--commit each|final`
  (commit defaults to `each` when push is set). Without `--push`, CI polls whatever is
  already on the remote branch.
- `--read-budget N` — how many files the loop may pull into a verdict bundle per
  cycle (default 3). Raise it for wide changes where context is thin.
- `--from-plan` — seed the goal from the plan you just built in plan mode instead
  of typing it out. You can still prepend extra text before the plan.

## Examples

Walk away on a tight, test-gated fix:

```
/loop fix the failing auth_token tests --max 8 --commit each
```

Carry a plan straight into a loop, with an LLM reviewer behind the test gate:

```
/plan
/loop --from-plan --judge tests,xai-verify --budget 2.50
```

Ship through remote CI (requires `gh auth login` and an open PR):

```
/loop "ship feature X" --judge tests,xai-verify,github-ci --commit each --push each
```

## Gotchas

- `/loop` will not start while `/rail` or plan mode is active — run `/rail off`
  or `/cancel` first. Starting a loop otherwise also clears those modes.
- One loop at a time per project; a second `/loop <goal>` is refused while one is
  active. Use `/loop status` or `/loop cancel`.
- With `--commit never` (the default) nothing is committed — review the dirty
  tree and commit yourself, or pass `--commit each|final`.
- No shell judge means no real test gate. Keep `tests` in the stack unless you
  truly want LLM-only verification.
- `github-ci` needs the GitHub CLI (`gh auth login`), an open PR for the current
  branch, and network access. It is skipped in writer-swarm `judge_panel` gates.

Siblings: `/describe rail` and `/describe verify` for the modes around it.
Deep dive: `/howto loop-swarm` (autonomous loops + writer swarm) and
`/howto review` (the verifier judges).
