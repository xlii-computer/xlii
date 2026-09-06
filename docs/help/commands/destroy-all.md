# /destroy-all

`/destroy-all` is the **human-only deny ladder** for this install — dry-run by
default, ugly on purpose. `/destroy` is the scoped twin (`/destroy throne`,
`/destroy node <name>`). The CLI is `xlii destroy-all` (also dry-run default;
`--keys-and-local` is the live wipe). It revokes keys and Collections **this body minted**
(see `minted.json`) and erases local state this body holds. It is **not**
forensics, not account-nuclear, and not reachable from tasks, loops, or agents.

Wave 1 covers **level 0** (inventory) and **level 1** (keys + local wipe) for
**this body only** — no child fan-out, no mail button, no partition shred.

## Trust ladder (levels 0–1)

```
/destroy-all                              # level 0 — plan only, change nothing
/admin unlock                             # elevate for capability-gated verbs
/destroy-all keys-and-local               # level 1 — fresh admin + typed phrase
/factory-reset                            # alias for level 1 on this body
/destroy throne                           # level 1 — same body scope (Wave 1)
/destroy node <name>                      # level 1 — only if the node is on paper
```

Level 0 needs **no** admin secret and **no** typed phrase. Level 1 re-prompts
**both** inside the handler — session elevation and forged flags are not proof.

The typed phrase is **`DESTROY THIS INSTALL`** (exact). Never a bare `y`.

## Rails that never drop

- **`human_only` dispatch** — tasks, capturing consoles, and background jobs are
  refused before the handler runs.
- **Fresh admin proof** — level ≥1 verifies the vault admin secret again at
  invocation; `context['elevated']` is ignored.
- **Typed phrase** — level ≥1 requires the exact string above.
- **Manifest-bound remote revoke** — only ids recorded in `minted.json`, grouped
  by minting team. Prefix-only server matches are **reported, not deleted**.
  Never `_bootstrap_revoke`.
- **Remote before local** — any cloud failure **halts** before the local tree
  is wiped; the journal records what still needs revoking.
- **Journal outside blast radius** — append-only log at
  `~/.local/share/xlii/destroy-journal.jsonl` (override `XLII_DESTROY_JOURNAL`).
  Vault, admin secret, and journal are removed **last**.
- **Management key honesty** — `$XAI_MANAGEMENT_API_KEY` cannot revoke itself;
  success paths disclose it as an **unrevoked residual** with manual steps.
- **Vault wipe verified or disclosed** — keyring delete is re-read; if the master
  entry survives, the command does not claim the vault is gone.

## `--local-only`

Skips all cloud operations and writes **surviving key/collection ids** into the
durable journal. It never prints an unqualified "destroyed" — local-only is a
partial teardown with honest leftovers.

## Discovery

Registered with `category="danger"` and omitted from `/help` daily, compose, and
power tiers. It appears under **`/help all`** only — field kit, not casual palette.

## Gotchas

- **Not forensic.** Unlocked RAM, coercion, lab recovery, and flash wear exist
  whether or not this verb runs.
- **Must be alive (or resume from journal).** Mid-wipe crashes leave the
  external journal as the resume manifest.
- **Multi-team management keys** refuse level-1 remote revoke without an explicit
  team scope flag (Wave 1 has none — stop and fix scope manually).
- **Sibling bodies on a shared team** stay safe because deletes are by manifest id,
  not name prefix.

A **gated panic ingest** (allowlist + DKIM/SPF + phrase + round 2) may fire this
same handler. It is not `xlii ask` and not a task step. Forged mail without the
factors does nothing.

See also: `/yolo` (opposite family — friction down, not deny), `/admin`, and
`docs/HOWTO.md` §15 for the manual ladder this automates.
