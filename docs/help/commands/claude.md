# /claude

Drive Claude Code from inside your xlii session. `/claude` is a permanent alias
for `/delegate claude`: it spawns the Claude Code ACP harness in the current
project, hands it your task, and streams its read/propose/edit loop back into
the REPL. Works from both the `code` and `chat` REPLs.

Claude *is* its model — there is no `--model` flag (see `/cursor` for a
model-host harness). Use `--plan` or `--ask` for read-only turns.

## Usage

```
/claude [--plan|--ask|--agent] [--context] [--reject] <task>
/claude new <name> [--plan|--ask] [--context]
/claude @<name> <task>           drive (or open) a named session
/claude @<name> --bg <task>      drive it as a background job (keep working)
/claude ls | close <name> | switch <name>
/claude on [<name>] | off        harness-as-a-mode (bare input drives it)
```

- `--context` — hand xlii's live tab manifest to the harness as a prompt preamble
  (no MCP registration; that stays cursor/grok-only).
- Session verbs, foreground mode, and background jobs mirror `/cursor` — swap the
  command name only.

## See also

- `/delegate` — the unified harness surface; `/claude` is its Claude alias.
- `/cursor` — Cursor Composer (model-host; supports `--model`).
- `/grok-build` — Grok Build (same ACP session shape; `--context` also registers
  DeepContext MCP into `.grok/mcp.json`).

Deep dive: `/howto bridges`
