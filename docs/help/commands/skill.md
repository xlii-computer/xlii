# /skill

List your **skills** — user-authored procedural workflows — and attach one to the
session. A skill is a repeatable recipe ("how I ship a PR here", "how I fix a
flaky test") you write once and pull in on demand, instead of re-explaining the
steps every session.

A skill is `<name>/SKILL.md` with YAML frontmatter (`name`, `description`) and a
markdown body of steps, commands, and constraints. xlii looks in two places:

- `.xlii/skills/<name>/SKILL.md` — project skills (committed with the repo)
- `<config>/skills/<name>/SKILL.md` — global skills (yours, everywhere)

A project skill overrides a global one of the same name. The layout follows the
Agent Skills open standard, so a `SKILL.md` is portable between xlii and other
agents (e.g. Cursor) — copy them across.

## Usage

```
/skill                # list available skills (● = attached)
/skill <name>         # attach a skill's steps to the session
/skill off <name>     # detach it
```

The agent always sees an **index** of available skills (names + descriptions) in
its preamble, so it knows what exists. Attaching with `/skill <name>` inlines that
skill's full body into the next turn — the same channel `/doc` uses — so the steps
are dynamic context you pull in only when relevant.

## Example

```
# .xlii/skills/ship/SKILL.md
---
name: ship
description: Ship a feature on a branch with the project's gates
---
1. /plan the change, then /execute
2. run `pytest -q` and `ruff check`
3. /verify the diff, then commit and open a PR
```

```
/skill ship
?walk me through shipping the auth fix using the ship skill
```

## Gotchas

- Skills are read-only recipes — attaching one doesn't run anything; it gives the
  agent the steps. Pair with `/plan`, `/loop`, etc. to act on them.
- Edit a skill in `$EDITOR` like any project file; re-attach (`/skill <name>`) to
  pick up changes mid-session.
- `/status` shows how many skills are available and how many are attached.

See also: `/doc` (attach reference docs), `/howto` (xlii's own built-in guides),
`/status` (session state). Exact flags: `/describe skill`.
