# Config, models & keys

How xlii is configured, which model runs in each role, and where your
credentials live. For the authoritative, always-current flags on any command,
run `/describe <cmd>` — this topic covers the operator workflow, not every flag.

## Where config lives

One global file: `~/.config/xlii/config.json` (mode 600, written atomically).
It holds models, temperatures, the chat-key pool, retrieval mode, iteration
ceilings, judge profiles, and optional pricing. There is no separate models
file — everything is in this one JSON.

Per-project state lives in `.xlii/` inside each project (Collection id, name,
conversation id, ignores). That's project identity, not credentials.

Bootstrap or repair the global file:

```bash
xlii config        # write the template if missing; idempotent perms-fix (600) if it exists
xlii doctor        # validate config, warn on typos and legacy/insecure layouts
```

Override the config directory for tests/sandboxes with `XLII_CONFIG_DIR`.

## Model roles

xlii dispatches four roles, each with its own model:

| Role | Used by | Default |
| --- | --- | --- |
| `orchestrator` | the main code agent | `grok-build-0.1` |
| `worker` | dispatched subagents (the swarm) | `grok-build-0.1` |
| `chat` | persona / conversational chat | `grok-4.3` |
| `help` | `/howto` self-guide turns | `grok-build-0.1` |

The code surface builds with the cheaper, code-tuned build model. Persona chat
defaults to a general chat-class model. **Help is a separate cheap slot** so
`/howto` does not borrow the chat (or flagship) model. Worker falls back to
orchestrator → legacy `model` when unset; `chat` and `help` have their own
defaults.

### See and set models

```bash
xlii models list           # models the team has access to
xlii models recommended    # heuristic best-of-class picks
xlii models set --orchestrator <name> --worker <name> --chat <name>
```

`xlii setup` auto-detects orchestrator/worker on first provisioning; use
`xlii models set` to override any role, or `xlii models profile set vision` for
a named preset triple. In a running session, `/models` shows all three roles plus
the active slot; `/model --profile vision` applies a preset live.

## Temperatures

The orchestrator runs warmer (creative planning, tool strategy); workers run
colder (precise, repeatable execution).

| Setting | Config key | Default |
| --- | --- | --- |
| orchestrator | `orchestrator_temperature` | 0.7 |
| worker | `worker_temperature` | 0.3 |
| chat | `chat_temperature` | 0.7 |

Persistent change: edit those keys in `config.json`. A deliberate `0.0`
(deterministic) is preserved, not coerced.

One-shot override for the next turn only:

```
/temp 1.2        # applies to the next turn, then reverts to config
/temp 0.4 --chat # chat/help turns only (orchestrator code turns unchanged)
```

Valid range is `0.0..2.0`. `/temp` with no argument shows the current value.

### Per-role worker models

Optional map in `config.json` — `worker_models` — routes dispatched subagents
by role (`explore`, `bash`, `general`, or custom keys). Unmapped roles fall
back to the global `worker_model`.

```json
"worker_models": {
  "explore": "grok-build-0.1",
  "verify": "grok-4.20-reasoning"
}
```

Loop judge profiles can set `model_role: chat` (or `orchestrator` / `worker`)
to pick which config slot a same-vendor reviewer uses.

## Cost & pricing

`/cost` prints the pricing table and shows which models have rates configured.
Pricing is optional: without it, token counts still appear but dollar
estimates are omitted.

To enable estimates, add a `pricing` map to `config.json` with USD-per-million
rates from your xAI dashboard:

```json
"pricing": {
  "grok-build-0.1": {"input_per_million": 1.0, "output_per_million": 2.0}
}
```

## Keys: the chat-key pool

Day-to-day work uses **chat keys** in `config.json` under `keys[]`. The first
entry is the primary (sync + main agent); workers round-robin through the rest.
`xlii setup` provisions a primary plus N workers (default 8) and saves them
here. Manage them with:

```bash
xlii keys list                       # local keys + server-side expiration
xlii keys rotate [--label <name>]    # new secret, same key id
xlii keys expire --days N [--label <name>]   # update expiry (0 = none)
xlii keys revoke [--prefix worker]   # delete by label prefix (server + local)
xlii keys prune                      # delete orphaned xlii-provisioned keys
```

Several of these take more flags (`--yes`, `--dry-run`, `--older-than`, …) —
run `/describe keys` or `xlii keys <sub> --help` for the current set.

### Encrypting plaintext keys

Newly provisioned keys can live in an encrypted vault rather than as plaintext
in `config.json`. Migrate existing plaintext secrets into the vault:

```bash
xlii keys migrate            # move keys[] secrets into the encrypted vault
xlii keys migrate --dry-run  # show what would move, then stop
```

`xlii doctor` nudges you when plaintext keys remain on disk.

## The management key (privileged, env-only)

The **management API key** is the one privileged credential — it creates and
revokes chat keys and manages Collections. xlii never writes it to disk. Set it
in your shell:

```bash
export XAI_MANAGEMENT_API_KEY=xai-...
```

The env value always wins; xlii ignores any management key found in a legacy
config file and `xlii doctor` flags it for removal. Chat keys are bootstrapped
from this key (`xlii setup`, `xlii bootstrap`) and are independently revocable.

## Plugin credentials: `xlii auth`

Plugin secrets (API tokens a subscribed plugin needs) live in the encrypted
vault, keyed by plugin id and env-var name — never in plaintext config:

```bash
xlii auth set <plugin-id> <ENV_VAR>   # value is prompted, not echoed
xlii auth list                        # plugins + var names (never values)
xlii auth clear <plugin-id> [<ENV_VAR>]
```

See `/howto plugins` for how plugins consume these.

## Related

- `/howto first-session` — code vs chat surfaces, input routing
- `/howto loop-swarm` — worker swarm and the iteration/parallelism ceilings
- `/howto bridges` — second-opinion judges (`/consult`) and cross-vendor models
- `/howto troubleshoot` — when `xlii doctor` flags something
- `/describe <cmd>` — authoritative, live flags for any command above
