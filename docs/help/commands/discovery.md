# /discovery

Enter **discovery mode** — a read-only "just talk about the code" gate. The agent
can read, grep, and search the project to answer you, but it **cannot change
anything**: no `write_file`, no `edit_file`, no `bash`, no subagents. It's the
durable form of typing "don't change any code" at the start of a session.

Alias: `/research`. Available in the **code** REPL.

## When to reach for it

Reach for `/discovery` when you want to **understand or discuss**, not act:

- **Explore an unfamiliar codebase** — "how does the mode controller work?",
  "where does sync happen?" — without the agent jumping to edits.
- **Talk through a design** before committing to it, weighing trade-offs in prose
  rather than getting a numbered plan you have to approve.
- **Review and reason** about code, bugs, or architecture in a back-and-forth,
  knowing nothing will be touched.

It's lighter than `/plan`: plan mode is also read-only, but it pushes toward a
numbered implementation plan and an `/execute` step. Discovery has no deliverable
and no execute — it's a conversation. When you're ready to act, leave discovery
(`/discovery off`) or enter `/plan`.

## Usage

```
/discovery [on|off|status]
```

- bare `/discovery` — toggle the mode on (or off if already on).
- `/discovery off` — leave discovery; writes are unlocked again.
- `/discovery status` — show whether it's currently active.

While active, bare input goes to the agent as a message (like `/plan`), so you can
just type questions. Mode-switch commands (`/plan`, `/rail`, `/debug`) still
dispatch, so you can move straight from discussing into acting.

## Start a session in it

```
xlii code --discovery     # or --disc
```

Boots straight into discovery so you never have to ask the agent to keep its
hands off the code. Toggle it off in-session with `/discovery` once you want to
make changes. `--discovery` and `--rail` are mutually exclusive (one mode slot);
`--rail` wins if both are passed.

## Examples

```
/discovery
# now: "walk me through how loop.py decides to stop" → the agent reads & explains
/discovery off
# back to normal — edits allowed again
```
