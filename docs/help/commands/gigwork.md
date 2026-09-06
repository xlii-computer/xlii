# /gigwork

Hire a configured **non-xAI** brain for one read-only worker pass on xlii's
own tools. Home plane stays xAI. Alias: `/gig`.

## Usage

```
/gigwork <provider> [--kit explore|bash|general] <task>
/gigwork ls
/gigwork presets
/gigwork add <preset> [--as name] [--model m]
/gigwork add --custom <name> <base_url> <api_key_env> <model>
/gigwork rm <name>
/gigwork allow <name> | deny <name>
/gigwork gaggle <name> <question> | ls | add … | rm <name>
/gigwork panel
```

Default kit is `explore` (no shell). Keys come from the environment
(`api_key_env`) — an inline key in the file is refused.

`allow` / `deny` toggle `gigwork.defaults.allow` — what the orchestrator
may pass as `dispatch_subagent(gig=…)`. Nested `/gigwork gaggle` is the
same as `/gaggle`.

## When

A second brain on xlii's tools. For a pack of brains, `/gaggle
second-opinion`. For a one-shot with no tools, `/consult`.
