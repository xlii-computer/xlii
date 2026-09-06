# Workflow Recipes

Use these recipes when you know the shape of the work but want the right xlii
surface for each phase. Cursor vocabulary maps cleanly onto xlii, but the center
of gravity is different: xlii keeps workflow state in commands, local files,
personas, docs, plugins, and independent judges.

## Cursor Vocabulary In xlii

| Cursor idea | xlii surface | Use it when |
| --- | --- | --- |
| Ask | normal chat or howto mode | You want explanation without changing files. |
| Plan | `/plan` then `/execute` | You want read-only investigation before writes. |
| Agent | `xlii code` or a normal code turn | You want the agent to edit, run, and verify. |
| Debug | `/plan`, targeted shell repro, then `/execute` | You need runtime evidence before a fix. |
| Background agent | `xlii loop` or `/loop` | You want unattended build-test-fix cycles. |
| Best-of-N writers | `/swarm` and loop writer swarm | You want isolated parallel attempts. |
| Review | `/verify`, `/peer`, `/consult` | You want cold or cross-vendor scrutiny. |

## Recipe: Investigate Before Editing

```text
/plan
? inspect the failing path and produce a numbered implementation plan
/execute
```

Use this when the task touches unfamiliar code. `/plan` keeps write tools locked
while the agent reads, searches, and proposes the work. `/execute` turns the
approved plan into the next implementation prompt.

## Recipe: Ship A Feature

```text
/plan
? design the smallest implementation for <feature>
/execute
? implement it, add focused tests, and run the relevant test command
/verify
```

Use `/verify` before you hand work off. It asks a cold reviewer to inspect the
local changes against the task, which catches the kind of regression a warm
implementation context can miss.

## Recipe: Refactor With Guardrails

```text
/rail
? refactor <area> while preserving behavior
/rail next
/rail next
/rail next
/execute
```

The rail is stricter than plan mode. It forces requirements, architecture, edge
cases, pseudocode, implementation, and self-review stages, with writes locked
until the design stages are complete.

## Recipe: Unattended Green

```text
/loop start "make the current task pass tests" --test "pytest -q"
```

Use `/loop` when the stop condition is objective. The loop keeps cycling through
build, test, judge, and fix until the test oracle passes or the configured stop
rule fires. For headless automation, use `xlii loop` with the same goal and test
oracle.

## Recipe: Parallel Writers

```text
/swarm 3
/loop start "implement <feature>" --test "pytest -q" --swarm 3
```

Use this when there are several plausible implementations and merge risk is
manageable. Writer workers run in isolated git worktrees, then xlii merges and
judges the result instead of letting parallel attempts trample one checkout.

## Recipe: Flaky Bug Debugging

```text
/plan
? identify hypotheses, the smallest repro command, and temporary instrumentation
/execute
? add the instrumentation, run the repro several times, analyze the evidence, fix, and remove the instrumentation
/verify
```

A dedicated debug mode is planned, but today the safe pattern is explicit:
hypothesize in `/plan`, reproduce with shell commands, keep instrumentation
temporary, and verify that cleanup happened before you stop.

## Recipe: Second Opinion (gaggle)

```text
/gigwork add kimi            # once: configure a foreign brain (then export KIMI_API_KEY)
/gaggle ls                   # stock presets: second-opinion, debate, scout
/gaggle second-opinion is the sweep loop racy under concurrent pairs?
/gigwork gaggle second-opinion is the sweep loop racy under concurrent pairs?
```

`second-opinion` runs two read-only workers on the same question — one on the
home xAI plane, one on your default gig provider — then a synthesis pass lists
their agreements, their conflicts (who said what), and a verdict. Caps are
mandatory; `write: false`. Use it when one model's answer is the thing you're
unsure about. `scout` fans two cheap gig passes and returns a plain digest
(no synthesis call); `debate` is `second-opinion` with the gig speaking first.
`/jam` is the same command. Compose your own with `/gaggle add` — members are
`backend[:kit][@model]` tokens — or hand-edit `gigwork.jams` in config.json.
The orchestrator may pass `gaggle="second-opinion"` on `dispatch_subagent`
when the gig is on `gigwork.defaults.allow`. One-brain version: `/gigwork
<provider> <task>` (`--kit bash` for a read+shell palette). Cross-vendor
one-shot without tools: `/consult`. Full page: `/howto gigwork`.

## Recipe: Cursor Delegation

```text
/cursor                 # hand a build task to Composer/Cursor as the engine
/cursor --plan          # ask Cursor for a plan first (read-only)
/cursor --ask           # one-shot question, no edits
/cursor --context ...   # attach DeepContext (xlii project context) to the call
/verify                 # cold-judge the result locally
```

Use `/cursor` when Composer or Cursor-specific context is the right engine for a
large multi-file edit. Keep xlii as the orchestrator of record: run local tests,
then use `/verify`, `/peer`, or `/consult` for independent review.

### When to use which

| Situation | Reach for |
|-----------|-----------|
| Routine edit / investigation in this repo | the **native agent** (`/plan` → `/execute`, or just ask) |
| Big multi-file change where Composer's harness shines | **`/cursor`** (delegate the build) |
| Long unattended build→test→fix on a goal | **`/loop`** with a judge profile |
| Loop build phase you want Cursor to drive, xlii to judge | **`/loop "..." --judge tests,cursor`** |
| You only want a plan or a question, no edits | **`/cursor --plan`** / **`/cursor --ask`** |

The split is deliberate: **delegate the cloud/engine work, own the verification.**
xlii's cold judges (`/verify`, `/peer`, `/consult`) and test oracles stay native
no matter which engine wrote the code — that's the trust boundary you keep.

## Lint and format (local tools)

Use **Tools → Shell tools…** in the TUI (or `/xtool ls` / `/xtool <tool-id>`) to seed
`!ruff …`, `!biome …`, and other linters/formatters on the focused file or project.
Every pick prefills the command line — nothing runs until you press Enter. Missing
binaries are dimmed; `/xtool ls` marks them `missing`. Destructive flags (`--fix`,
`--write`) always appear in the seeded line.

```text
/xtool ls
/xtool ruff-check path/to/file.py
/xtool ruff-check --fix
```

## Choosing Quickly

- Need design before edits: `/plan`.
- Need a staged design gate: `/rail`.
- Need repeated test-fix cycles: `/loop`.
- Need several isolated attempts: `/swarm` plus `/loop`.
- Need another set of eyes: `/verify`, `/peer`, or `/consult`.
- Need Cursor itself: `/cursor`, then bring the result back through xlii review.
