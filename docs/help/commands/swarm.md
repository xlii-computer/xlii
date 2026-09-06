# /swarm

Show or set the live ceiling on concurrent worker agents — the swarm. This is the
single dial that caps how wide xlii is allowed to fan out when the orchestrator
spins up subagents during a turn. It is a **ceiling, not a target**: the
orchestrator still decides how many workers to actually launch for the task in
front of it. `/swarm` only sets the upper bound it can't cross.

Available in the **code** REPL.

## When to reach for it

Reach for `/swarm` when a turn is doing heavy parallel work and you want to widen
or narrow the fan-out without restarting or hand-editing config:

- **Throttle down** when you're sharing a machine, hitting rate limits, or want
  calmer, more sequential behavior you can follow turn by turn.
- **Open it up** before a big parallel job (a wide `/loop` build, a bulk edit
  across many files) so the orchestrator has room to spread the work.
- **Check headroom** by running it bare — it reports the current ceiling plus how
  many worker keys are actually in your pool.

The change is read fresh at dispatch time, so it lands on the **very next turn** —
no relaunch needed.

## Usage

```
/swarm [n] [--save]
```

- bare `/swarm` — print the current ceiling and your worker-key headroom.
- `/swarm <n>` — set the ceiling to `n` concurrent workers (`n` must be `>= 1`).
- `--save` — persist the new ceiling to `config.json`. Without it the change is
  **session-sticky**: it reverts to the configured value on next launch.

## Examples

Check where you stand, then widen for a parallel build:

```
/swarm
swarm ceiling: 4 concurrent worker(s)  (8 worker key(s) in pool)
/swarm 8
swarm ceiling = 8 (this session; --save to persist)
```

Pin a permanent default for this project's config:

```
/swarm 6 --save
swarm ceiling = 6 (saved to config.json)
```

## Gotchas

- **Ceiling vs. target.** Raising it doesn't force more parallelism; the
  orchestrator may still run fewer workers if the task is small.
- **Key headroom matters.** Your pool reserves one key for the orchestrator; the
  rest are worker keys. Set the ceiling above your worker-key count and workers
  start **reusing keys and sharing the rate budget** — usually slower, not
  faster. Provision more keys with `xlii bootstrap` first.
- **Also bounds sync uploads.** The same ceiling limits the end-of-turn sync
  upload fan-out, so a very low value can slow large pushes to the Collection.
- **Forgets on relaunch** unless you pass `--save`.
- **It's what unlocks the writer swarm.** Set above `1`, this ceiling is what
  lets a `/loop` build fan out into parallel writer-workers, each in its own git
  worktree, before integrating sequentially. The loop launches them; `/swarm`
  sets the upper bound. See `/describe loop` and `/howto loop-swarm` for the
  merge and pre-land mechanics.

Deep dive: `/howto loop-swarm`
