---
name: author-task
description: Author a saved /tasks pipeline — pick a Task+ shape (linear, params, verdict, rc, split) or clone a stock task, write .xlii/tasks/<name>.toml, show the plan, never auto-run.
metadata:
  primitive: "/tasks new --shape|--clone|--from (repo: xlii/task_shapes.py)"
  use-before: "saving any multi-step recipe the user will rerun"
---

# Skill: author-task

A *task* is a user-authored pipe, not an agent plan. You write the `.toml`.
The runner is deterministic. Never `/tasks run` a draft you just wrote unless
the user asked to run it.

## Pick a shape first

| Need | Flag | What it is |
| --- | --- | --- |
| A then B | `--shape linear` | carry flows down |
| Caller input | `--shape params` | `[params.x]`, `{{x}}` |
| Clean either/or | `--shape verdict` | `TASK+ {"branch":…}` + `[[edge]]` |
| On fail also do X | `--shape rc` | `on_failure` — **not** exclusive |
| Run A‖B then join | `--shape split` | shell arms only |

Stock starting points: `/tasks new NAME --clone git-triage` (verdict),
`--clone tests-guard` (rc), `--clone diff-review` (params),
`--clone map-and-ask`.

## Method

1. Ask the user the **decision**, not the TOML: linear / input / either-or /
   on-fail / parallel.
2. Scaffold: `/tasks new <slug> --shape <that>`. Extras the maker seeds:
   `--param base`, `--branches yes,no`, `--arms a,b --policy all`.
3. `/tasks show <slug>` — if the plan is wrong, `/tasks edit <slug>` or
   rewrite the file. Do not invent `[[edge]]` on an inline `|>` pipe
   (inline is linear-only).
4. A verdict `ask` must end with a FINAL line that is **exactly**
   `TASK+ {"branch": "<label>"}` and every label needs an `[[edge]]`.
5. Done = the file loads (`/tasks show` prints `paths`). Tell them
   `/tasks run <slug>` — do not run it.

## Guardrails

- `prev` is reserved. Params bind fail-closed.
- Split arms are **shell** steps, reached only through the split.
- Face: Tasks → New (or Panels → Task maker) cycles the same knobs and
  seeds this command. The pane writes nothing until they send.
