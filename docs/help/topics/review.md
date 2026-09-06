# Review & verification

Three tiers of review, from tightest to most independent. Each spawns a fresh
reviewer with **no stake in the code** — nothing they say enters your chat
history, so you can run them repeatedly without polluting context.

| Command | Sees | Scope | Independence |
| --- | --- | --- | --- |
| `/verify` | your task + uncommitted diff | `git diff HEAD` | cold context, same vendor |
| `/peer` | commit log + diff only | a committed range | blind — no author intent |
| `/consult` | what you choose to send | a question you ask | different vendor |

Run `/describe <cmd>` for the authoritative, always-current flags on any of these.

## `/verify` — did this do what was asked?

A cold-context verifier on **uncommitted** work. It gets your last turn's task
plus `git diff HEAD`, then judges whether the change actually did what you asked.
It checks scope creep, correctness bugs, unverified claims, and security
regressions — not style.

```
/verify
```

Output is `PASS: <summary>` or `FAIL` with a numbered, file:line finding list.

Preconditions:
- There must be a prior turn this session (it reads your last task).
- There must be uncommitted changes vs `HEAD` (otherwise it points you at
  `/peer`).

Use `/verify` as the last step of a turn before you commit — it is the
one-shot counterpart to the review stage of `/rail` and the judge inside
`/loop`. See `/howto rail` and `/howto loop-swarm`.

## `/peer` — blind review of committed work

A brutal peer reviewer that has **never seen your conversation**. It gets only
the commit log, the diff, and read-only access to the codebase — no stated
intent. It must reconstruct what the change is for from the artifact alone; if
it can't, that incoherence is itself a finding.

```
/peer                 # reviews HEAD~1..HEAD
/peer --since <ref>   # reviews <ref>..HEAD
```

It tags findings as `[correctness]`, `[coherence]` (commit message vs. actual
diff), or `[consistency]` (conflicts with patterns elsewhere). Output is the
same `PASS` / `FAIL` shape as `/verify`.

When to reach for `/peer` instead of `/verify`:
- The work is already **committed** (`/verify` only sees uncommitted diffs).
- You want a reviewer who is *not* anchored by your own framing of the task —
  the absence of intent is the point.

On the very first commit there is no `HEAD~1`; `/peer` will tell you to pass
`--since <ref>`, commit an empty baseline, or use `/verify` for uncommitted
work instead. Run `/describe peer` for the full flag list.

## `/consult` — an outside vendor's opinion

`/consult` asks a **second, independently-configured AI provider** a question
and prints the reply under a `[consult · <model>]` header. It is never a tool
and never appended to history — a one-shot opinion from a different vendor,
where the value is independence from your primary (xAI).

```
/consult <question>                 # sends the question only, no history
/consult --turns <question>         # plus the last exchange
/consult --last N <question>        # plus the last N exchanges
/consult --full <question>          # plus the whole conversation
```

It also works inside `xlii chat`, not just `xlii code`.

### Feeding a review into a consult

`/verify` and `/peer` save their last report so a consult can pull it in as
context for a cross-vendor tiebreak:

```
/verify
/consult --from-verify Do you agree with the verifier's findings?
/consult --from-peer Is finding 3 a real bug or a false positive?
/consult --from <path> <question>   # pull any file as context
```

The three `--from*` sources are mutually exclusive — one artifact per consult.
If no report exists yet, `/consult` tells you to run `/verify` or `/peer` first.

### Configuring the judge (cross_vendor)

`/consult` reads its provider from `~/.config/xlii/config.json`. Add a
`judges` profile of kind `cross_vendor`, then export the env var it names:

```json
{
  "judges": {
    "anthropic": {
      "kind": "cross_vendor",
      "provider": "anthropic",
      "model": "claude-sonnet-4-6",
      "api_key_env": "ANTHROPIC_API_KEY"
    }
  },
  "consult": { "default_judge": "anthropic" }
}
```

```bash
export ANTHROPIC_API_KEY=...
```

If you omit `consult.default_judge`, a judge named `consult` is used. Supported
providers are `anthropic`, `openai`, and `xai` (pick a vendor *different* from
your primary so the opinion is genuinely independent). The legacy
`secondary_ai` block is still honored but deprecated.

Check whether it's wired up: `/status` prints a `/consult:` line showing the
provider, model, and env var — or that it's not configured yet. For the
authoritative config detail run `/describe consult`.

## Choosing a tier

- Mid-task, before committing → `/verify` (it knows what you were trying to do).
- After committing, want an unbiased read → `/peer` (it doesn't).
- Want a different vendor to break a tie or sanity-check → `/consult`,
  optionally `--from-verify` / `--from-peer` to hand it the report.

## See also

- `/howto rail` — the stage-gated coding flow whose final gate is a review.
- `/howto loop-swarm` — autonomous build→test→fix with verifier judges.
- `/howto config-models` — where models and the `judges` profile live.
- `/howto troubleshoot` — when a reviewer crashes or can't resolve a ref.
- `/howto gigwork` — hire a foreign brain (`/gigwork`) or a gaggle (`/gaggle second-opinion`).
- `/describe modes` — the live decision tree across all review modes.
