# Troubleshooting

Start with **`xlii doctor`** (add `--online` to probe Collections). It prints
actionable fixes for config, keys, and project health.

## Setup & models

| Symptom | Fix |
| --- | --- |
| `xlii setup` couldn't auto-detect models | Some accounts hide `/v1/models`. Run `xlii models set --orchestrator <name> --worker <name>`. |
| Management key errors | Export `XAI_MANAGEMENT_API_KEY` in your shell; xlii never stores it on disk. |
| Legacy path warnings | Run `xlii doctor --migrate-legacy` (use `--dry-run` first). |

## Dependencies

| Symptom | Fix |
| --- | --- |
| `'OpenAI' object has no attribute 'responses'` | Upgrade: `./venv/bin/pip install -U 'openai>=1.50'`. |

## Sync & Collections

| Symptom | Fix |
| --- | --- |
| Sync: "Empty stream received" | 0-byte files are skipped; check if a file changed size mid-sync. |
| Unexpected uploads (e.g. `.next/`) | Add the directory to `.xliiignore` at the project root. |
| Local-only project needs search | Use `/sync` to rebuild the local BM25 index. |

## Model behavior

| Symptom | Fix |
| --- | --- |
| Yellow `⚠ … called 0 tools` | Model claimed work without tools — verify on disk before trusting. |
| Reasoning model produces no answer | Rephrase, or `xlii models set --orchestrator grok-4`; check the reasoning panel. |
| `/doc <name>` isn't sticking | Rewrite the doc as firm imperative English, or use a non-reasoning model for style work. |
| `/doc CALLMESIR` → "no such doc" | `CALLMESIR` is content, not the name. Names are slug-cased — see `xlii doc --list`. |

## TUI & sessions

| Symptom | Fix |
| --- | --- |
| `--tui` won't render / garbled terminal | Install `[tui]` extra; need a real TTY. Inline REPL unaffected. |
| `xlii code --tui` refuses to launch | Nested-session guard — use `/tui` from inline REPL, or `--force`. |

## In-session help

- `/howto troubleshoot` — re-attach this guide.
- `/describe <name>` — live docs for a slash command, tool, or plugin.
- `/help` — full slash-command listing for this REPL and build.

If the problem isn't listed, say what you tried and what error you saw — point
the operator to GitHub issues or `docs/REFERENCE.md` §10 for the maintained list.
