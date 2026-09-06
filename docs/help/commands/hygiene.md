# /hygiene

Text hygiene for **paste · harness returns · attach · cross-platform files**.

The slash command is `/hygiene` (alias `/sanitize`).

Two verbs, one default pack, one **credibility** counter. Not a language
formatter (use `/xtool` for ruff/biome) and not a claim that it removes lab
AI watermarks.

## Usage

```text
/hygiene scan [path…]
/hygiene strip [path…] [--newlines lf|crlf|keep] [--keep-bom]
/sanitize …                 # alias
```

Bare paths default to **scan**.

## scan (always safe)

Reports:

| Field | Meaning |
| --- | --- |
| **newlines** | LF · CRLF · CR · mixed · none |
| **bom** | UTF-8 BOM present or not |
| **encoding** | utf-8 (preferred) or cp1252 fallback note |
| **credibility** | Count of **injection-class** Unicode only |

Credibility hits (examples): zero-width space, soft hyphen, exotic spaces,
bidi overrides (RLO/LRE/…). **CRLF and BOM do not raise credibility** — those
are portability notes, not “danger.”

When credibility &gt; 0 the command prints a **take note** hint with a strip
suggestion.

## strip (opt-in)

Default pack:

1. Newlines → **LF**
2. Drop **UTF-8 BOM**
3. Strip injection-class controls; map exotic spaces → ASCII space

Flags only for exceptions:

- `--newlines crlf` — Windows export
- `--newlines keep` — leave line endings alone
- `--keep-bom` — preserve a leading UTF-8 BOM if the file had one

Skips binary-suspect files (NUL in the first bytes).

## Why it exists

New models, providers, and harnesses will keep landing in this substrate.
Hygiene is a cheap, deterministic **“hey — look”** / Danger Will Robinson on
untrusted text — not a full firewall.

## Automatic ingress (take-note only)

The substrate already **scans** some untrusted doors — it never auto-strips:

| Door | Behavior when credibility &gt; 0 |
| --- | --- |
| Harness capture (`/cursor`, `/delegate`, … → `capture_output`) | Yellow **take note** on the console; `last_output.meta.credibility`; same note prefixed into history fold |
| Inbox enqueue (`serve-inbox` / `enqueue_inbox`) | One-line note on stderr |
| Inbox drain (`xlii loop --drain-inbox`) | Yellow **take note** before the task runs |

You still run `/hygiene strip` yourself when you want a clean file.

## See also

- `/xtool` — real linters/formatters after text is honest
- Source hygiene CI: `tests/test_source_hygiene.py`
