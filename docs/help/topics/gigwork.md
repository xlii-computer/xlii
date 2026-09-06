# Gigwork & gaggles

Hire a non-xAI API brain for one bounded worker pass on xlii's tools. A
**gaggle** is a named pack of those hires: who + how many + merge policy +
budget cap. Home plane stays xAI. Keys live in the environment
(`api_key_env`) — never in the config file.

`/howto gigwork` and `/howto gigwork gaggle` attach this page.

## One brain — `/gigwork`

```text
/gigwork add kimi            # once: write the provider block; then export KIMI_API_KEY
/gigwork ls                  # configured providers + whether the key env is set
/gigwork kimi review this race for missed awaits
/gigwork allow kimi          # the orchestrator may now dispatch_subagent(gig="kimi")
```

Default kit is `explore` (search/read, no shell). `--kit bash` adds shell;
`--kit general` is the full read-only palette. Gigs cannot write files or
dispatch nested workers. xAI server tools (`search_project`, `web_search`,
`x_search`) are stripped from a gig's schema.

`defaults.allow` is the orchestrator's permit. Empty = only you, via the
slash command, can hire. Alias: `/gig`.

## A pack — `/gaggle` (also `/jam`, `/gigwork gaggle`)

A gaggle is not swarm, not fleet, not a crew UI. Caps are mandatory.
`write: false` is locked.

```text
/gaggle ls
/gaggle second-opinion is this migration safe to run twice?
/gigwork gaggle second-opinion is this migration safe to run twice?
```

Stock `second-opinion`: home explore + one gig, merge `synth_conflicts`
(agreements, conflicts, verdict). The stock `"gig"` slot binds to the first
allowlisted — else the sole — configured provider, so the recipe works the
moment one `/gigwork add` exists.

Other stock presets: `debate` (gig speaks first, same merge), `scout` (two
gig passes, `concat_digest`, no synthesis call). Compose your own with
`/gaggle add <name> <backend[:kit][@model]>… [--merge …] [--cap N]`.

The orchestrator may pass `gaggle="second-opinion"` on `dispatch_subagent`
only when every foreign member is on `gigwork.defaults.allow`. Investigate
sub-queries on a heavy deep-search hop accept `gaggle=` / `gig=` on
`request_deep_search`, and fall through to `gigwork.defaults.investigate_gaggle`
or `investigate_gig` when the call site is unset (not both). Hire is
allowlist-gated; a failed hire does not fall back to home.
Do not pass `gig=` and `gaggle=` together.

## Not this

| Surface | What it is |
| --- | --- |
| `/delegate` / `/cursor` | External **CLI** products. Their tools, their login. |
| `/consult` | One-shot HTTP opinion. No tool loop. |
| `/swarm` | Parallel **writers** in worktrees. |

## See also

- `/describe gigwork` · `/describe gaggle` · `/describe jam`
- `/howto review` — `/consult` as a one-shot second brain
- GUIDE "Gigwork" section
