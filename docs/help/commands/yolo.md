# /yolo

`/yolo` drops the bash **confirmation gate** for the session: `modifies-system`
and `network` commands stop prompting and just run. `/safe` puts the gate back.
It's the top of the trust ladder that starts at `/safe` and steps up through
`/approve`:

- `/safe` — every gated command prompts (the default).
- `/approve <categories>` — pre-authorize named intent categories (e.g.
  `/approve network`), leaving the rest gated.
- `/yolo` — the whole bash gate off.
- `/yolo --freeball` — one rung higher: the **trusted-run tier** (below).

## --freeball: the trusted-run tier

`/yolo --freeball` is a *composite* above yolo — a strict superset. On top of the
bash gate being off, it also **auto-approves spend confirmations** (image
generation and friends) and **skips the remaining per-action prompts**, so a run
you've already proven flows end to end without a single y/N. It exists for the
ergonomics of *proven* tasks: exploration earns the careful path, habit earns the
cheap one.

Two forms, one flag — there is no separate command and no hidden alias:

```
/yolo --freeball
   → FREEBALL ON — stays on until /safe (or the session ends)

/yolo --freeball rebuild the index and run the smoke tests
   → runs THAT turn gates-down, then the tier restores itself automatically
```

The **one-shot** (`/yolo --freeball <task>`) is the form you'll reach for most:
it opens the gates for exactly one turn and closes them the moment the turn ends —
even if the turn is interrupted or errors — so you never leave a session sitting
wide open. The session **toggle** (no task) is for a burst of proven work; stand
it down with `/safe`.

## Rails that never drop

Freeball drops **friction**, never **safety**. These stay standing under it —
they are data-loss and security rails, not confirmations:

- the **sync delete-guard** — a sync that would delete more than a handful of
  remote docs still stops and asks;
- **capability gates** — `/admin` elevation is still required for privileged
  actions; freeball is not a shortcut around it;
- the **debug-marker cleanup contract** — instrumentation still must be
  greppable;
- the **`/budget` cap** — the soft spend warning still fires. Under freeball,
  `/budget` is the one brake left, so it's the one worth setting;
- the **`/tasks` untrusted-carry gate** — freeball auto-approves ordinary
  per-step confirmations in a pipeline, but a shell/slash step fed by an
  **agent** step's output (untrusted) still stops and asks — that's a
  prompt-injection rail, not friction.

## Gotchas

- **Loud on purpose.** The status strip shows a filled red `FREEBALL` pill while
  the tier is live; `/status` spells it out and names the rails that still hold.
  If you can't see it, it's off.
- **Never persisted.** Freeball is session-scoped — a fresh session *always*
  starts safe. (Plain yolo persists as before; reopening a session lands you at
  most in plain yolo, never the freeball composite.)
- **`/safe` is the master off switch.** It clears yolo, freeball, and any pending
  one-shot in one move.
- **The one-shot runs `<task>` as a normal agent turn** — checkpoints, journal,
  and cost accounting all apply; only the prompts are skipped.
- Prefer the one-shot to the toggle. Gates that re-arm themselves are safer than
  gates you have to remember to close.

See also: `/safe` to stand down, `/approve` for the middle rung, and `/budget`
for the brake that stays.

Deep dive: `/howto modes`
