# Plugins

A plugin is a **markdown file** describing an external API or service. It is not
a subprocess MCP server — nothing is spawned. Frontmatter declares `actions:`;
`plugin_call` runs them. A file without actions is not a plugin. Plugins live
at `~/.config/xlii/plugins/<id>.md`.

Each file has YAML frontmatter (id, name, description, categories, effect/trust,
`auth_env_vars`, `actions`) plus a short prose body. Required params are form
fields by default; `secret:` never reaches the agent; `store:` writes the
vault.

## Mandatory subscription

The agent only sees plugins **subscribed in the current project**. The global
catalog can grow without bloating any one project's context. Subscriptions are a
per-project file: `<project>/.xlii/plugins.txt`, one id per line.

Manage subscriptions from any REPL with `/plugin` (the old `/lib` still works as a
hidden alias):

```
/plugin                      # subscribed plugins for this project
/plugin all                  # browse the full installed catalog
/plugin subscribe <id>       # add to this project
/plugin unsubscribe <id>     # remove from this project
/plugin remove <id>          # delete the plugin file itself
```

Each row carries the plugin's **effect · trust** badges, so the risk a
subscribe carries is visible next to it. If a plugin isn't subscribed,
`plugin_search` / `plugin_get` / `plugin_call` will not return or invoke it —
they fail with a "not subscribed" message telling you to `/plugin subscribe <id>`
first.

## Authoring on the face

**Plugins → New** (or Panel Workbench → Plugin maker) cycles effect / trust / auth /
output and seeds `/plugin new <id> …` — send to write the stub. No `$EDITOR`.
Then replace `ping`'s URL and params. Skill `author-plugin` is the agent
write-test loop.

## The catalog panel (TUI)

`/plugin panel` docks the installed catalog in a side panel: subscribed plugins
are marked `●`, each row shows its effect/trust badges, and a click toggles the
subscription into `plugins.txt`. High-risk plugins (destructive, local-system, or
always-confirm) require an elevated session (`/admin unlock`) to subscribe — the
click surface never becomes the easiest way past that gate.

## Calling an action directly

When you already know the action, skip the model entirely and call it:

```
/plugin call <plugin>.<action> [key=value …]
```

For example `/plugin call open-meteo.geocode name=London`. Params validate
against the manifest, the HTTP call runs directly (vault auth injected), and the
result prints to you — **zero tokens, no model in the loop**. Values are literal
strings; a value that opens with `{` or `[` is parsed as JSON, so a nested-object
param rides through (`message={"text":"hi"}`).

Because `/plugin call` is a slash command, plugins are also `/tasks` pipe
primitives for free: a recipe of `/plugin call` steps is a zero-token automation.

## Output modes — `raw`, `schema`, `interpret`

An action declares what happens to its response with an `output:` field (default
`interpret`, so nothing changes until you opt in). It's **per action**, not per
plugin:

- `raw` — the body prints straight to you; the model receives at most a one-line
  receipt. A response that never enters model context can't prompt-inject the
  model, so raw is a real security property, not just a speed-up.
- `schema` — the body is rendered deterministically through the action's
  `output_renderer` (a `table`, `message_list`, `text_template`, or
  `paste_command`). No model synthesis. If the raw API body doesn't already match
  the renderer's fields, an ordered `output_transforms:` list reshapes it first —
  a small closed set of named ops (`rename`, `lift`, `zip`, `flatten_map`, `cast`,
  `concat`, `first_of`, `scale`, `code_map`, `derive`), plus `body_format: rss`
  for non-JSON feeds. See `xlii/plugin_transforms.py`.
- `interpret` — today's behaviour: the body returns to the model for synthesis,
  right where judgement is genuinely needed.

Most **stock** plugins ship with `schema` on their read-only actions (weather,
finance, news, search render as clean tables/text with zero model tokens); the
few whose output genuinely needs a model — a summary to write, a value to
compute that no fixed transform covers — stay `interpret` on purpose.

## Invoking a plugin by intent

You don't need to know which plugin handles a job. Describe the intent:

```
/get <intent>             # e.g. /get current weather in Berlin
```

`/get` searches your subscribed plugins and picks the best match. When the
matched action is `raw`/`schema`, `/get` takes a **fast path**: one cheap
structured call fills `{action, params}`, then xlii executes it deterministically
— the model translates at the front and is then gone, never orchestrating a tool
loop. Otherwise it falls back to letting the model read the doc and drive the
call.

## The agent tools

The model has three plugin tools (reference them by bare name):

- `plugin_search` — rank subscribed plugins against an intent string. Returns top
  matches with id, score, effect/trust, and a one-line description. A
  `NO_PLUGIN_MATCH` marker means nothing fit (the model reports that rather
  than fabricating).
- `plugin_get` — read the full markdown of one subscribed plugin after a search
  identifies a candidate.
- `plugin_call` — invoke an action. Missing form/secret fields open a closed
  HTML form; secrets never enter chat. Honour the action's output mode:
  `raw`/`schema` print to the user and return only a receipt to the model;
  `interpret` returns the body. A file without `actions:` is not a plugin.

For the exact tool schemas, run `/describe plugin_search`, `/describe plugin_get`,
or `/describe plugin_call`.

## Effect, trust & risk

Manifest plugins carry two badges:

| field | values |
| --- | --- |
| `effect` | `read-only` · `external-write` · `local-system` · `destructive` |
| `trust` | `subscription` (implicit once subscribed) · `always-confirm` (per call) |

A plugin is **high-risk** — and needs `/admin unlock` to subscribe from the panel
or auto-attach via a role — when its effect is local-system/destructive or its
trust is always-confirm. The parser **fails closed**: a plugin with missing,
unparseable, or invalid frontmatter, or with no `actions:`, is treated as
high-risk. Malformed never means trusted.

## Credentials

Never put secrets in the plugin markdown or in chat. A `secret:` param opens a
closed form; `store:` writes through `Vault.set`. The runner injects
`auth_env_vars` into the request. TTY leftover: `xlii auth set` still prompts
with getpass if you are already at a shell.

```
/plugin call <plugin>.set          # form (or TTY) — preferred
xlii auth list                     # plugins + var names (never values)
xlii auth clear <plugin-id> [ENV]  # drop one var or the whole entry
```

## Managing plugins (CLI)

```bash
xlii plugin --list                 # all installed plugins
xlii plugin --show <id>            # print full markdown
/plugin new <id>                   # stub manifest in the face (no $EDITOR)
xlii plugin --new <id>             # create from template, opens $EDITOR
xlii plugin --edit <id>            # edit in $EDITOR
xlii plugin --delete <id>          # remove (--yes to skip confirm)
xlii plugin --lint                 # validate frontmatter + manifests
xlii plugin --install-stock        # install the bundled stock pack
```

`--install-stock` skips plugins you've already edited; add `--force` to overwrite.
For the authoritative, current flag list run `/describe` or
`xlii plugin --help`.

## Inspecting a plugin live

```
/describe <plugin-id>     # metadata + risk + actions from the live registry
```

Use `/describe` for anything you're unsure of — it reads the running registries,
so it's always correct for your build.

## A typical flow

1. `/plugin new weather` (or `--install-stock`).
2. `/plugin subscribe weather`.
3. If it needs a key, run `weather.set` — the form stores it.
4. `/get weather in Tokyo` — search, resolve, call.

## Stock: Appwrite (self-hosted)

One markdown plugin against **your** instance — not Appwrite Cloud's MCP,
not a Python SDK. Install the stock pack, subscribe, store the origin +
server key (and an optional user JWT for whoami):

```
xlii plugin --install-stock
/plugin subscribe appwrite
/plugin call appwrite.set
/plugin call appwrite.health
/plugin call appwrite.databases
/plugin call appwrite.collections databaseId=main
/plugin call appwrite.users
/plugin call appwrite.create_bucket name=files
```

`account` needs `APPWRITE_JWT` (GET /account is a user route). A server
API key lists people via `users`. File upload is not a plugin action —
`plugin_call` has no multipart body. Attach the stock skill:

```
/skill appwrite
```

## See also

- `/howto knowledge` — refs, docs, locker, and the rest of the context surface.
- `/howto bridges` — Cursor, Grok Build, and other external-tool integrations.
- `/howto config-models` — models, keys, and project config.
- `/describe <cmd>` — live, per-command detail for anything above.
