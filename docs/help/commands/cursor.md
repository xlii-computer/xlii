# /cursor

Drive an external coding harness from inside your xlii session. `/cursor` is a
permanent alias for `/delegate cursor`: it makes xlii the ACP *client*, spawns
`cursor-agent acp` in the current project, hands Composer your task, and streams
its read/propose/diff/apply loop back into the REPL. By default Composer runs in
agent mode and **may edit files on disk** — the work happens in your working
tree, not in a sandbox.

This is the control direction of xlii's bridge story: the MCP/DeepContext side
(`/context`) exposes your context for an outside agent to *pull*; `/cursor`
instead lets you *steer* a different vendor's coding agent without leaving the
REPL. Reach for it when you want a second engine's hands on the same repo —
Composer to grind out an edit while xlii stays your console — or a read-only
second read on a tricky area before you commit to an approach. Works from both
the `code` and `chat` REPLs.

`/cursor` is one of four harnesses behind the same surface. Use `/delegate
<harness> …` (or that harness's alias) to reach `claude`, `codex`, or `grok`
instead — same flags, same sessions. The one thing that makes **cursor** special
is `--model`: cursor is a *model-host*, so it can run any model it exposes.
`claude`, `codex`, and `grok` **are** the model — the harness is its own engine,
so `--model` is inert there (see "Harnesses" below).

## Usage

```
/cursor [--plan|--ask|--agent] [--model <name>] [--context] [--reject] <task>
/cursor new <name> [--plan|--ask] [--model <m>] [--context]
/cursor @<name> <task>           drive (or open) a named session
/cursor @<name> --bg <task>      drive it as a background job (keep working)
/cursor ls | close <name> | switch <name>
/cursor on [<name>] | off        harness-as-a-mode (bare input drives it)
/cursor swarm <plan.md> [--vectors a1,c] [--go] [--keep]
```

- No flag — **agent mode** (default). Composer plans, edits, and applies. It can
  change files, so treat it like running a coworker's branch on your tree.
- `--plan` — read-only. Composer proposes a plan and touches nothing.
- `--ask` — read-only Q&A over the project; ask about the code, get no edits.
- `--agent` — the explicit form of the default (useful to override a session's
  saved mode for one turn).
- `--model <name>` — pin any Cursor model. The default is `composer-2.5`; only
  pass this when you specifically want a different one. **cursor-only** — the
  other harnesses ignore it because they *are* their own model.
- `--context` — hand xlii's live state to the harness (see "Context handoff").
- `--reject` — auto-deny the harness's edit permission prompts (a hard read-only
  fence even in agent mode).

A bare `/cursor` with no task just prints usage. Run `/describe cursor` for the
full flag set straight from the live registry.

## Harnesses

`/delegate <harness>` (and the `/cursor` alias) fronts four engines. The split
that matters is **model-host vs. self-model**:

- **cursor** — a model *host*. `--model` selects any model it exposes; default
  `composer-2.5`. This is the only harness where the model is yours to pick, and
  the only one whose stat bar shows `· <model>`. Full ACP session.
- **claude** — *is* its model (default `claude-sonnet-4-6`). Full ACP session.
- **grok** — *is* its model (default `grok-build-0.1`). Full ACP session, and —
  like cursor — can be handed xlii's DeepContext MCP via `--context`.
- **codex** — *is* its model (default `gpt-5.3-codex`). Runs as a **headless
  one-shot** (no live ACP session), so it threads task-by-task rather than
  keeping context across turns.

## Sessions and harness-as-a-mode

One-shot is the floor; the real power is keeping a harness *alive*.

**Named sessions.** `/cursor new build` opens a persistent ACP session called
`build` (its mode/model/context are remembered). `/cursor @build <task>` runs a
turn on it — every later turn reuses the same live session, so the harness keeps
its own context across turns. `/cursor ls` lists sessions with their state
(`live` / `idle` / `headless`), `/cursor switch <name>` re-points the active one,
and `/cursor close <name>` tears one down. Names are global across harnesses, so
`/delegate grok @build` and `/cursor @build` address different sessions only if
they were opened under different names.

**Run it in the background.** A `@name` turn normally *blocks* the REPL until the
harness finishes (up to its timeout). Add **`--bg`** — `/cursor @build --bg <task>`
— to run that turn as a session-owned **background job** instead: it dispatches to
the job pool, the prompt comes straight back so you keep working, the reply prints
as one block when it lands, and `/jobs` (plus the profile-bar segment) tracks it.
Best for long runs; a quick ask is fine in the foreground.

**Harness as a mode (foreground).** `/cursor on` flips cursor into the
*foreground mode*: bare input now drives the live session directly — no `/cursor`
prefix per turn. The escapes still work: `?<text>` summons xlii's own AI, `!<cmd>`
runs a shell command, and `/<command>` reaches xlii's meta-commands. `/cursor
off` leaves the mode (the session stays alive). The input frame and status bar
wear the harness's label (`cursor`, `claude code`, `grok build`) while a mode is
foreground, and only a model-host mode shows `· <model>`.

## Context handoff

`--context` forwards xlii's live tab manifest — the equipped role, attached
docs/refs, and the recent conversation — to the harness as a prompt preamble, so
it starts *where xlii is*. This works for **any** harness (it's just text). For
**cursor** and **grok** it *also* registers xlii's DeepContext as an MCP server
(`.cursor/mcp.json` / `.grok/mcp.json`) so the harness can pull live context on
its own; for cursor you may need `cursor-agent mcp enable xlii-deep-contexts` to
approve it. On a persistent `--context` session the manifest seeds only the first
turn (later turns ride the live session).

## Fleet: swarm a build plan

`/cursor swarm <plan.md>` turns a parallel-build doc into an executable fleet. It
parses the plan's `## Vector <name> — <title>` headers into vectors and, with
`--go`, spawns **one harness session per vector, each in its own git worktree** so
they can't collide, then collects a summary. Without `--go` it's a dry run that
just previews the vectors. `--vectors a1,c` filters to a subset; `--keep` leaves
the worktrees behind (`git worktree list`) instead of pruning them. Swap the
fleet's engine with the harness you front it through — `/delegate grok swarm …`
runs the same plan on grok.

## Examples

Read-only first, so you see the shape before anything is written:

```
/cursor --plan migrate the config loader from JSON to TOML
```

Then let Composer actually do it on a pinned model:

```
/cursor --model composer-2.5 apply that migration and update the tests
```

Keep a session and live in it:

```
/cursor new refactor --context
/cursor on refactor
… pull the duplicated retry logic into one helper
? what did that change touch
/cursor off
```

## Gotchas

- **It edits your real files.** Agent mode (the default) writes to the working
  tree. Run `--plan`, `--ask`, or `--reject` when you only want a read; commit or
  stash first so the harness's changes are easy to isolate and review. After a
  run, `/cursor` lists the files it touched (uncommitted) — diff them before you
  trust them.
- **`--model` is cursor-only.** The other harnesses *are* their model, so passing
  `--model` to `claude`/`codex`/`grok` does nothing. Pick the engine by choosing
  the harness, not the model.
- **The harness must be installed and logged in.** `/cursor` shells out to
  `cursor-agent` (and `/delegate <harness>` to `claude` / `codex` / `grok`). If
  it isn't on PATH or you're signed out, you'll see "unavailable" — fix it with
  that harness's login (e.g. `cursor-agent login`).
- **You need a project root.** Delegation, sessions, and swarms all refuse to run
  in a session with no project root (e.g. a throwaway `xlii code --preview`); open
  it inside a real project so the harness has a tree to work in.
- **It's a separate agent, not your turn.** The harness's output streams to the
  console; `/cursor` and `/delegate` capture the exchange into xlii's history so
  `/replay` and `?what did we do?` can reach it, but the harness keeps its own
  context — carry anything you want xlii to *act* on into your own next prompt.

## See also

- `/delegate` — the same surface for `claude`, `codex`, and `grok`; `/cursor` is
  its cursor alias.
- `/context` — the *pull* side of the same bridge: expose xlii's references and
  tools as a DeepContext for an outside agent.
- `/consult` — a one-shot cross-vendor opinion when you want a second read, not a
  second pair of hands.

Deep dive: `/howto bridges`
