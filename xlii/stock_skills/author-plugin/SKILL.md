---
name: author-plugin
description: Author a markdown plugin (API descriptor + optional actions: manifest). Scaffold with /plugin new, fill the real endpoint, subscribe, call. No Chrome store, no MCP subprocess.
metadata:
  primitive: "/plugin new (repo: xlii/plugin_scaffold.py)"
  use-before: "adding any external HTTP API the agent or /plugin call should hit"
---

# Skill: author-plugin

A plugin is a **markdown file** at `~/.config/xlii/plugins/<id>.md`. Nothing
is spawned. Structured `actions:` let `/plugin call` hit HTTP with zero
tokens; without them the model reads the doc and drives `bash`.

## Method

1. **Read the API docs.** URL, method, params, auth (header vs query vs
   none), response shape. Do not invent hosts.
2. **Scaffold, no editor:**
   `/plugin new <id> --effect read-only --trust subscription --auth none --output interpret --subscribe`
   Auth ring: `none` · `header` · `query`. Effect: `read-only` unless it
   writes; `local-system` / `destructive` need a reason. Trust
   `always-confirm` for anything that posts or spends.
3. Replace the stub `ping` action: real `url`, `params` (required /
   default / const), `headers` if needed. Env vars are **names**
   (`${FOO_KEY}`); `xlii auth set <id> FOO_KEY=…` holds the secret.
4. `/plugin call <id>.ping …` against the live API. Iterate on the
   actual error. Switch `output: schema` only after you know the JSON.
5. Done = a call returns a real body. Tell the user the subscribe line
   if they skipped `--subscribe`.

## Guardrails

- Never write API keys into the markdown.
- One plugin, several `actions:` — don't make a plugin per endpoint
  unless the APIs are unrelated.
- Face: Plugins → New (or Panels → Plugin maker) cycles the badges and
  seeds `/plugin new`. The pane writes nothing until they send.
- This is **not** a provider (`/providers new`) and **not** an MCP
  server. Don't scaffold those here.
