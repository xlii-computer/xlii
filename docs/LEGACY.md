# Legacy & naming contract (`xli` → `xlii`)

> **Status:** living contract (grades plan Phase 2).  
> **Rule:** intentional compat stays forever until a *versioned* migration ships;  
> user-facing **lies** (wrong paths, wrong product name) get fixed immediately.

The product name is **xlii**. Some identifiers still say `xli` because changing
them would strand vaults, cloud Collections, or API keys. This document is the
single place that lists those exceptions.

If something is named `xli` and is **not** listed here, treat it as a bug.

---

## Keep forever (compat)

Do **not** rename these without a deliberate migration tool and release note.

| Kind | Value | Why |
|------|--------|-----|
| OS keyring service | `xli` (`KEYRING_SERVICE` in `xlii/vault.py`) | Changing strands existing vault master keys in Secret Service / Keychain / Credential Locker |
| Keyring username | `vault-master` | Paired with the service name |
| Vault env alias | `XLI_VAULT_KEY` | Permanent alias of `XLII_VAULT_KEY` — headless/CI scripts |
| Collection name prefixes | `xli/` **and** `xlii/` | Cloud Collections created under the old prefix; `xlii gc` matches both |
| Journal / key name prefixes | `xli-<label>` **and** `xlii-<label>` | Provisioned API key display names; rotate/list match both |
| Project dir drain | `.xli/` → `.xlii/` | Auto-migrated on open; drain only, never dual-write forever |
| Ignore file drain | `.xliignore` → `.xliiignore` | Doctor warns; migrate renames |
| Global loadouts drain | `~/.xli/workspaces/` → `~/.config/xlii/loadouts/` | One-way copy on read |
| DeepContexts drain | `~/.xli/contexts/` → `~/.config/xlii/contexts/` | One-way copy on read |

### Canonical paths (always prefer these in docs and UX)

| Role | Path |
|------|------|
| Global config dir | `~/.config/xlii/` |
| Config file | `~/.config/xlii/config.json` |
| Vault ciphertext | `~/.config/xlii/vault.enc` |
| Vault key file fallback | `~/.config/xlii/.vault-key` |
| Project state | `<repo>/.xlii/` |
| Project ignore | `.xliiignore` |

There is **no** supported `~/.config/xli/` tree. If you still have one from a
pre-rename experiment, move contents into `~/.config/xlii/` manually (or ask
`xlii doctor --migrate-legacy` for project-scoped migrates).

### Env vars

| Canonical | Legacy alias | Notes |
|-----------|--------------|--------|
| `XLII_VAULT_KEY` | `XLI_VAULT_KEY` | First hit wins; both accepted |
| Other `XLII_*` | — | Prefer `XLII_*` for new scripts |

Management key remains **`XAI_MANAGEMENT_API_KEY`** (vendor env, not product rename).

---

## Fix immediately (not compat)

These are **bugs** when they appear in user-visible text or active docs:

- Product CLI name written as `xli` (correct: `xlii`)
- Paths `~/.config/xli/…` (correct: `~/.config/xlii/…`)
- Log tags `[xli]` on new code (prefer `[xlii]`)
- Telling users to create **`.xliignore`** as the normal ignore file (correct: **`.xliiignore`**)
- Docstrings that claim vault files live under `~/.config/xli/` while code uses `GLOBAL_CONFIG_DIR` (`xlii`)

---

## Operator checks

```bash
xlii doctor
# expect a line like:
#   ✓ legacy aliases OK — vault env $XLII_VAULT_KEY (alias $XLI_VAULT_KEY); …

xlii doctor --migrate-legacy --dry-run   # preview .xli/ → .xlii/, ignore rename, …
```

Full narrative: [HOWTO.md](HOWTO.md) setup + maintenance; first-look note on
rename leftovers: [FIRST-LOOK-REVIEW.md](FIRST-LOOK-REVIEW.md).

---

## Related code

| Area | Module |
|------|--------|
| Vault + keyring + env | `xlii/vault.py` |
| Global paths | `xlii/config.py` (`GLOBAL_CONFIG_DIR`) |
| Project `.xli/` migrate | `xlii/config.py`, `xlii/legacy_migrate.py` |
| Key name prefixes | `xlii/bootstrap.py` (`provisioned_key_names`) |
| Collection GC prefixes | `xlii/cmds/project/gc.py` |
| Loadout / context drains | `xlii/loadout_paths.py`, `xlii/context.py` |
| Doctor | `xlii/cmds/diag.py` |

---

*When you add a new permanent alias, document it here in the same PR.*
