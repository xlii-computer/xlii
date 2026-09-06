# /plugin

Manage and invoke plugins — the markdown API descriptors xlii uses to reach
external services. `/plugin` is the canonical verb (the shell already says
`xlii plugin`); the older `/lib` still works as a hidden alias.

A plugin is a subscription, not a spawned process. Subscribing adds a plugin to
this project's `.xlii/plugins.txt` so the agent (and you) can reach it; the global
catalog stays out of context until you opt a plugin in.

## Usage

```
/plugin                              # plugins subscribed in this project
/plugin all                          # the whole installed catalog (● = subscribed)
/plugin new <id>                     # stub manifest (no $EDITOR); --subscribe
/plugin show <id>                    # print the written markdown
/plugin subscribe <id>               # add to this project
/plugin unsubscribe <id>             # remove from this project
/plugin remove <id>                  # delete the plugin file itself
/plugin call <plugin>.<action> k=v   # invoke one action directly (no model)
/plugin panel                        # dock the click-to-subscribe catalog (TUI)
```

Every listing row shows the plugin's **effect · trust** badges, and a high-risk
plugin is flagged `⚠`. Effect is one of `read-only` / `external-write` /
`local-system` / `destructive`; trust is `subscription` or `always-confirm`.

## Calling an action directly

`/plugin call` is the deterministic rung — params validate against the manifest,
the HTTP call runs directly with vault auth injected, and the result prints to
you with **zero model involvement**. Missing form/secret fields open a closed
HTML form on the face (TTY prompts in the REPL). Secrets never seed the input
bar:

```
/plugin call open-meteo.geocode name=London
/plugin call bluesky_chat.send_message convoId=abc message={"text":"hi"}
```

Values are literal strings; a value opening with `{` or `[` is parsed as JSON so
nested-object params ride through. A `destructive` / `always-confirm` action
prompts for confirmation first — the same gate the agent tool honours.

Output follows the action's declared `output:` mode: `raw` prints the body,
`schema` renders it deterministically through the manifest's `output_renderer`,
and `interpret` (the default) shows the body verbatim on the direct rung.

## The panel

`/plugin panel` docks the installed catalog; click a row to subscribe or
unsubscribe. Subscribing a **high-risk** plugin requires an elevated session
(`/admin unlock`) — the one-click surface must never be the easiest way past the
gate that governs auto-attach.

## Managing plugin files (shell)

The plugin files themselves are authored from the shell:

```
xlii plugin --new <id>      # create from a template ($EDITOR)
xlii plugin --list          # installed plugins
xlii plugin --lint          # validate frontmatter + manifests
xlii plugin --install-stock # install the bundled stock pack
```

## See also

- `/get <intent>` — invoke a subscribed plugin by natural-language intent (takes a
  deterministic fast path when the matched action is `raw`/`schema`).
- `/howto plugins` — the full plugin topic: subscription, output modes, security.
- `/describe plugin_call` — the live agent-tool schema.
