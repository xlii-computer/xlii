# /grok-build

Drive Grok Build from inside your xlii session. `/grok-build` is a permanent alias
for `/delegate grok` (the internal harness key stays `grok`). The `/build` alias
also resolves to this command when unclaimed. Works from both the `code` and `chat`
REPLs.

Grok Build *is* its model — there is no `--model` flag.

## Usage

```
/grok-build [--plan|--ask|--agent] [--context] [--reject] <task>
/build …                         alias for /grok-build (when registered)
/grok-build new <name> [--plan|--ask] [--context]
/grok-build @<name> <task>       drive (or open) a named session
/grok-build @<name> --bg <task>  drive it as a background job (keep working)
/grok-build ls | close <name> | switch <name>
/grok-build on [<name>] | off    harness-as-a-mode (bare input drives it)
```

- `--context` — hand xlii's live tab manifest to the harness **and** register
  xlii's DeepContext MCP into `.grok/mcp.json` (same handoff shape as `/cursor
  --context`).
- Session verbs, foreground mode, and background jobs mirror `/cursor` — swap the
  command name only.

## See also

- `/delegate grok …` — byte-identical to `/grok-build …` for one-shot tasks.
- `/claude` — Claude Code over the same ACP session machinery.
- `/cursor` — Cursor Composer (model-host; supports `--model`).

Deep dive: `/howto bridges`
