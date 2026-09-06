---
sources: file://xlii/tasks.py, file://xlii/repl_cmds/tasks.py, file://docs/help/commands/tasks.md, file://xlii/session_boot.py#L556-766, file://xlii/tui/task_builder.py, file://xlii/stock_tasks/pre-pr.toml, file://xlii/stock_tasks/notes-from-diff.toml, file://xlii/stock_tasks/map-and-ask.toml
verified: false
---
# tasks-and-pipes

`/tasks` is the horizontal pipe primitive: an ordered list of steps where each step's
text output — the **carry** — flows into the next. It is the deterministic,
user-authored complement to `/loop`'s churn-to-green ("do A, feed B, hand both to C").
The engine is `xlii/tasks.py`; the REPL surface is `xlii/repl_cmds/tasks.py`. Available
in the code and chat REPLs. See also [[command-surface]].

## Grammar and carry

Steps are separated by the **fat pipe** `|>` (distinct from shell `|`, so a step may
contain a normal pipe). A step's kind is inferred from its leading prefix: `?` → agent
prompt, `/` → slash command, anything else → shell. Reference the prior carry with
`{{prev}}` (shell-quoted in shell steps via `shlex.quote`, raw elsewhere) or
`{{prev:raw}}` (verbatim, word-splitting). Agent and shell steps that omit the token
still receive the carry (agent: appended under a `--- piped input ---` fence; shell:
piped to stdin). The full carry is always persisted/returned; only the copy injected
into the *next* step is trimmed (`DEFAULT_CARRY_MAX_CHARS = 20_000`, head+tail elision).

## Tasks are functions

Saved pipelines are TOML files under `.xlii/tasks/<name>.toml`; each `[[step]]` has
**exactly one** of `run`/`ask`/`slash` (except a split step, which has no body).
`[params.<name>]` tables declare inputs (`required`, `default`, `enum`, `help`) bound
from the run line positionally or by name (`name value` / `name=v` / `--name=v`);
`{{prev}}` is the implicit arg #0 and `prev` is a reserved param name. `bind_task_args`
fails **closed** at invocation (unknown flag, extra positional, missing required, enum
miss) — before any step runs. Run state persists to `.xlii/tasks/.runs/*.json` (a
`TaskRun`); `/tasks resume` continues the last interrupted run from its cursor, `status`
and `cancel` read/mutate it. Bundled read-only **stock** tasks live in
`xlii/stock_tasks/`; a project task of the same name wins over stock.
Optional top-level `class = "system"` marks product/ops recipes (`new-folder`);
`/tasks list` badges them. Class does not change gates.

## Inline stays linear; branching is TOML-only

An inline `|>` pipe is **strictly sequential** — `parse_inline` emits linear steps and
inline branching is refused. A pipe with no Task+ feature runs byte-identically to the
pre-Task+ runner (contract pinned as data in `TASKPLUS_NON_GOALS`). Branching, params,
and fan-out are authored only in saved TOML.

- **Shell-rc branch** (`on_success`/`on_failure` on a shell step) is a one-sided
  forward skip — after the jump the runner continues in file order, so it expresses "on
  failure *also* do X", not a clean either/or. _Stock: `tests-guard`._
- **Agent verdict branch** (`[[edge]]` + a final `TASK+ {"branch":"<label>"}` trailer
  line) is the clean either/or; after a routed arm runs, the pipe **stops**. A missing
  or unmatched verdict **fails the run closed** even under `--keep-going`. _Stock:
  `git-triage`._
- A back-edge that revisits an already-run step fails closed — there is no bounded-retry
  primitive here (that stays `/loop`).

## Split/join (T+P3, PR #295)

A `[[step]]` carrying `split = ["a","b"]`, `join = "<id>"`, and `policy` runs the named
branch ids **concurrently** (thread pool capped by `/swarm` / `max_parallel_workers`),
then continues at `join`. Policies: `all` (default) · `any` · `first_ok` — `first_ok`
cancels/kills the remaining branches once one succeeds and carries only the winner.
Branch ids must be their own **shell** steps, reached only via their split (validated at
parse time; also refused: nesting a split inside a split, and a branch declaring
`on_success`/`on_failure`). The join sees each branch as `{{arm.<id>}}`, and the merged
carry is a digest under `### arm <id> (<status>)` headers. A split is gated as one shell
action. `--dry-run` / `/tasks show` print a route map via `enumerate_paths`
(`split{a‖b‖c}·policy`, `─branch=x→`, `↻` loop, `⊘` stop).

## Security gates

A carry that passed through an **agent** step is treated as untrusted: the next
shell/slash step prompts before running even under `--yes`, unless the project sets
`confirm_shell = false`. `/yolo --freeball` auto-`yes`es the per-step friction gate but
leaves the untrusted-carry rail intact. `--background`/`--bg` (Vector J) runs the same
pipeline as a non-blocking job (`xlii.jobs`); a detached job's gates fail closed at the
step (resume in foreground to approve). See [[trust-and-gates]].

## Builders and startup-task

Two compose surfaces (the automation ladder is *action → recipe → routine → Conductor*;
the Conductor is Fleet — see [[panes-and-dock]]). The **F10 Task
Builder** (`TaskBuilderPane`, address `tasks://builder`) is the linear composer: steps are
rows, footer actions run/run-bg/save/draft ride the Dock's PREFILL and CLAIM_INPUT
seams. Face has no CLAIM_INPUT, so **Tasks → New** opens `taskmake://` as a
closed HTML form in the other slot (listing | form): pick a Task+ shape
(linear / params / verdict / rc / split) or clone a stock task, then PREFILL
`/tasks new <name> --shape|--clone|--from`. The form writes nothing. `/tasks new --from "<desc>"` has the agent draft the TOML
(opened for review, never auto-runs). `/alias <name>` promotes a saved task to
its own command.

**startup-task** binds a per-project task fired once at code-session open
(`apply_startup_task`) and again on `/project switch` (not on `/chat`/`/howto`
detour-return). Bindings live per-machine in `~/.config/xlii/startup.json`
(never travel with a clone; never in the repo / `project.json` / task toml).
Default mode is **capture** (prefills `/tasks run <name>` for review). Bind with
`/project startup <task>` (interactive confirm, fail-closed in agent/background
contexts). `/project startup --show` / `--clear` / `--off` (session mute);
`xlii code --no-startup` mutes this launch. An `auto` binding (`--auto`,
admin-elevated) runs only when the session trust tier ≥ the recorded tier
(D17) and always prints the exact `/tasks run` line first, downgrading to capture if the
pipeline file hash drifted. Bind / clear / mute / fire / recorded skip write a
Project Shadow note (who, task, confirm vs auto) when the journal is recording
— fail-soft if it is off. The bound task shows a `startup` badge in TasksPane,
`tasks://`, and `/tasks list`.

_Stock examples: `pre-pr` (read-only pre-PR checklist), `notes-from-diff` (param `base`),
`map-and-ask` (params `path`, `question`), `diff-review`, `grep-explain`, `bump-note`._
