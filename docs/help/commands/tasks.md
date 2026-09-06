# /tasks

Pipe an ordered list of steps and thread their output through one another. Each
step is a **shell command**, an **agent prompt**, or a **slash command**, and
each step's text output — the *carry* — flows into the next. If `/loop` is *one
goal churned to green*, `/tasks` is *many steps piped to a result*: "do A, feed
its output to B, then hand both to C." The order is deterministic and
user-authored — the agent is a participant in the pipe, not the orchestrator of
it.

Available in the **code** and **chat** REPLs.

## What and why

The shell already pipes programs (`cmd1 | cmd2`); `/tasks` pipes **programs,
prompts, and slash commands** through one another with the agent in the loop.
Steps are separated by the **fat pipe** `|>` (distinct from shell `|`, so a step
may itself contain a normal shell pipe). A step's type is inferred from its
leading prefix:

| Prefix | Kind | Body is | Carry injection |
| --- | --- | --- | --- |
| `?` | agent | a prompt for the live session | appended under a `--- piped input ---` fence (or via `{{prev}}`) |
| `/` | slash | a full `/<command> …` line | substituted raw at `{{prev}}` |
| _(none)_ | shell | a command line | piped to **stdin**, and substituted **shell-quoted** at `{{prev}}` |

Reference the prior carry explicitly with `{{prev}}` (shell-quoted in shell
steps, raw elsewhere) or `{{prev:raw}}` (always verbatim, word-splitting). Agent
and shell steps that omit the token still receive the carry automatically. The
full carry is always persisted and returned; only the copy injected into the
*next* step is trimmed to a budget (default 20k chars).

## Usage

```
/tasks run '<cmd> |> ?prompt |> /slash' [--dry-run] [--yes] [--keep-going] [--from FILE]
/tasks run <saved-name> [flags]   # a bare word loads .xlii/tasks/<name>.toml
/tasks '<cmd> |> ?prompt'          # bare form — the `run` verb is optional
```

Subcommands for saved pipelines and run state:

```
/tasks list            # saved pipelines in .xlii/tasks/
/tasks show <name>     # render a saved pipeline's plan + last-run state
/tasks new <name> [--from "<description>"]   # scaffold (or agent-draft) a .toml
/tasks new <name> --shape linear|params|verdict|rc|split
/tasks new <name> --clone <stock>            # copy git-triage / tests-guard / …
/tasks edit <name>     # open the .toml in $EDITOR
/tasks resume          # continue the last interrupted pipeline
/tasks status          # where the latest run stands
/tasks cancel          # mark the active run cancelled
```

Flags on `run`:

- `--dry-run` — validate and print the plan; execute nothing.
- `--yes` — skip the per-step confirmation for shell/slash steps.
- `--keep-going` — carry on past a failing step (tagging its stderr onto the carry).
- `--from FILE` — seed the initial carry from a file's contents.

## Examples

Summarize what changed, with the agent reading the diff off the pipe:

```
/tasks run 'git diff --stat |> ?summarize these changes in one line'
```

Preview a three-stage pipe without running it, then hand the result to a slash command:

```
/tasks run 'pytest -q |> ?triage the failures |> /doc add notes {{prev}}' --dry-run
```

Save a pipeline and run it by name (resume picks up where a failure stopped):

```
/tasks new nightly
/tasks edit nightly
/tasks run nightly --keep-going
```

## Startup task

Bind a saved pipeline to fire when **this project** opens on **this machine**. The
binding lives in `~/.config/xlii/startup.json` (never in the repo). Default is
prefill — `/tasks run <name>` is typed for you, never run for you.

```
/project startup nightly          # bind (interactive confirm)
/project startup --show           # current binding + hash/missing
/project startup --clear          # unbind
/project startup --off            # mute for this session
/project startup nightly --auto   # auto-run; needs /admin unlock
```

`xlii code --no-startup` skips the ritual this launch. Switch-time re-evaluates
the same guards; `/chat` / `/howto` detour-return does not re-fire. Bind, clear,
mute, fire, and recorded skips write a journal note when Project Shadow is
recording (fail-soft if the journal is off). The bound task shows a `startup`
badge in the Tasks pane, `tasks://`, and `/tasks list`.

## Task+ — parameters, branching, parallelism

A **saved** pipeline (a `.toml`, not an inline `|>` string) can do more than run
straight through. Add these only when the task needs them; a linear pipe needs
none of it. Give any step you route to or from an `id`. Every feature below ships
as a runnable stock task — read the source with `/tasks show <name>`.

**Parameters** — declare inputs with `[params.<name>]` and reference them as
`{{<name>}}` anywhere in the pipeline. Fields: `required = true`, `default`,
`enum = [...]`, `help`. They bind from the run line positionally or by name
(`/tasks run diff-review main` or `base=main` / `--base=main`); shell steps quote
them safely. `prev` is reserved.

```toml
[params.base]
default = "HEAD~1"
help = "git ref to diff against"

[[step]]
run = "git diff {{base}}"
```

_Stock: `diff-review` (default), `grep-explain` (required), `bump-note` (enum)._
_System: `new-folder` (required name, kind enum) — Project → New project folder runs it._

**Class** — optional top-level `class = "system"` on a saved pipeline. System
tasks are product/ops recipes (create a folder and claim it, later remotes…).
`/tasks list` and the Tasks pane badge them `system`. Clone one with
`/tasks new mine --clone new-folder` and edit. A class does not change gates or
how the pipe runs.

Pin a task to chrome with `/bind` — a menu row and/or an F-key that only
starts `/tasks run`. Same idea as `/alias`, different mouth.

**Branch by exit code** (`on_success` / `on_failure`) — on a shell step, jump to
another step by id based on its exit status. This is a **one-sided forward
skip**: after the jump the runner keeps going in file order, so it cleanly says
"on failure *also* run X," but it is **not** a mutually-exclusive either/or — for
that, use a verdict.

```toml
[[step]]
id = "tests"
run = "python -m pytest -q"
on_failure = "triage"
```

_Stock: `tests-guard`._

**Branch by agent verdict** (`[[edge]]` + a `TASK+` trailer) — the clean
either/or. An `ask` step ends its reply with a FINAL line that is exactly
`TASK+ {"branch": "<label>"}`; `[[edge]]` tables route on it, and after a routed
arm runs the pipeline **stops**. Cover every label you ask for with an edge — an
unmatched or missing verdict **fails the run closed** (it will not silently fall
through, even under `--keep-going`).

```toml
[[step]]
id = "classify"
ask = """…decide, then a FINAL line exactly one of:
TASK+ {"branch": "clean"}
TASK+ {"branch": "dirty"}"""

[[edge]]
from = "classify"
when = { branch = "clean" }
to = "clean"

[[edge]]
from = "classify"
when = { branch = "dirty" }
to = "dirty"
```

_Stock: `git-triage`._

**Parallel fan-out** (`split` / `join`) — a step with a `split` list runs those
branch ids **concurrently** (thread pool capped by `/swarm`), then continues at
`join`. `policy` decides success: `all` (default) · `any` · `first_ok`. Branch
ids must be their own **shell** steps and reachable only through the split. The
join step sees every branch's output as `{{prev}}` (with `### arm <id> (<status>)`
headers) and each individually as `{{arm.<id>}}`.

```toml
[[step]]
id = "fan"
split = ["lint", "types"]
join = "report"
policy = "all"

[[step]]
id = "lint"
run = "ruff check ."

[[step]]
id = "types"
run = "mypy ."

[[step]]
id = "report"
ask = "Summarize the lint and type results above."
```

Preview any branching pipeline's routes without running it — `/tasks show <name>`
and `--dry-run` print a `paths (N):` map (`a ─ok→ b`, `─branch=x→`,
`split{a‖b‖c}`, `↻` loop, `⊘` stop). Draft one from a description with
`/tasks new <name> --from "…"`, pick a filled Task+ skeleton with
`--shape verdict` (params / rc / split), or copy a stock task with
`--clone git-triage`. On the face, **Tasks → New** (or Panel Workbench → Task maker)
opens a closed HTML form in the other slot and seeds `/tasks new` — it does
not write until you send. Or promote a saved task to its own command with `/alias <name>`.

## Tasks pane — compose without writing

Open **Tasks** from the Panel menu (or `Alt-T`). **New** opens the Task maker
(`taskmake://`) in the other slot (listing | form): pick a shape (linear /
params / verdict / rc / split) or clone a stock task, then seed `/tasks new`.
The form writes nothing. F10 Task Builder is still the linear step composer
on the TUI.

## Startup task — capture default and auto

Per-project startup bindings live on this machine only (`~/.config/xlii/startup.json`).
When a binding exists, opening a code session **prefills** `/tasks run <name>` for review.
A binding marked auto runs only when the live session trust tier is at least the tier
recorded in the binding, and always prints the exact `/tasks run …` line before running.
Create, show, and clear the binding with `/project startup` (`--show` / `--clear` /
`--off`; `--auto` needs `/admin unlock`). `xlii code --no-startup` mutes one launch.

## Gotchas

- A carry that has passed through an **agent** step is treated as untrusted: the
  next shell/slash step prompts before running even under `--yes` (unless the
  project opts out via `confirm_shell`).
- In the **chat** REPL, shell steps confirm per-step by default; `/tasks` warns
  you and `--yes` skips the prompt.
- Saved pipelines, `resume`, `status`, and `cancel` need a project (an `.xlii/`
  dir); inline pipes run anywhere a session is live.
- Each saved `[[step]]` needs **exactly one** of `run` / `ask` / `slash` — except
  a `split` step, which carries no body of its own (it fans out to its branches).

Related: `/describe loop` (churn-to-green, the autonomy sibling), `/describe rail`
(gated single-task stages), `/describe cursor` (the "vendor ships primitives,
user composes the system" thesis this command expresses).
