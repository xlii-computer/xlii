# Getting help in xlii

xlii has three layers of self-documentation. They're complementary — learn when
to reach for each.

## `/help` — the menu

`/help` lists every slash command available in your current REPL (code or chat),
grouped by category, generated live from the command registry. There are no
hand-written help strings to drift: if a command exists, it's in `/help`.

Use it to **see what's available**.

## `/describe <name>` — the deep-dive (always current)

`/describe` is xlii's introspection command — the `C-h f` of the tool. Give it a
slash command, an agent tool, or a plugin name and it prints, **straight from the
live registry of this exact build**:

- what it is, its category, aliases, and which REPLs it works in,
- its usage signature and flags,
- for tools: parameter names, types, and which are required,
- for plugins: actions, risk level, and required auth env vars.

On top of those code-truth facts, `/describe` appends an **expansive description
pulled from the GitHub help corpus** (always up to date, etag-cached so it's fast
and works offline after the first fetch), plus a **see-also graph** of related
commands and a pointer to the `/howto` topic that covers it in depth.

```
/describe loop        # the autonomous loop: facts + prose + see-also
/describe rail        # the six-stage coding rail
/describe cursor      # drive Cursor's Composer agent over ACP
/describe modes       # special: the plan/rail/loop/verify decision tree
```

Because the facts come from code and the prose comes from GitHub, `/describe` is
**never stale** — prefer it over any flag list you might half-remember. When in
doubt about *any* command, `/describe` it.

## `/howto [topic]` — task-oriented guides

`/howto` enters a conversational mode and attaches a task guide to the system
prompt so you can just ask questions in plain language (no `?` needed).

- `/howto` — the operator guide + topic index + your live command list.
- `/howto <topic>` — a focused shard (e.g. `/howto modes`, `/howto knowledge`).
- `/howto wiki [question]` — ask **xlii's built-in self-wiki** (`xwiki://` — the
  shipped, read-only self-docs, version-locked to your build; same answer in every
  project). Prints openable `xwiki://page#section` citations, opens the ranked
  results in a side panel, and folds just those sections into the answer —
  retrieval, not the whole guide. Bare `/howto wiki` browses the shipped pages.
- `/howto latest [topic]` — force the newest copy from GitHub.
- `/howto off` — leave the mode.

`/howto` answers about **xlii itself** (the tool). Questions about **this
project** — how it works or how it's been worked — go to `/mojo`, which answers as
mojo with its own memory FUSED with this project's journal (episodic) *and* wiki
(`.xlii/wiki/`, semantic):

- `/mojo <question>` — mojo answers in one shot, grounded in both, and you stay
  right where you were (no mode switch). The journal's `--wiki-on` auto-distill is
  what fills the wiki that gets folded in.
- `/askjo <question>` — a hidden alias of `/mojo`, kept for muscle memory.

Use `/howto` to **understand xlii**, `/mojo` to **ask what this project knows**,
`/describe` to **nail a single command**, and `/help` to **see the menu**.

## From your shell

`xlii help` prints the full CLI reference, and `xlii doctor` diagnoses a broken
setup. See `/howto install` and `/howto troubleshoot`.
