---
sources: file://xlii/repl_cmds/git.py, file://xlii/panes/git.py, file://xlii/git_status.py, file://docs/help/commands/gitpain.md
verified: false
---
# gitpain

xlii's git cockpit — the review-before-run write side of the `git://` doorway (PR #294, stages GP0–GP2 + sweep). Not a second porcelain: full git stays bare `git` in the shell; gitpain smooths the painful ergonomics (stash-with-a-message, journal-aware commit drafts, merged-branch sweep). Three parts: the `/gitpain` command (write), the Gitpanel `GitPane` (projection over `git://`), and `git_status` (read side — forge-agnostic, local `git` binary only, no network REST calls, non-git trees degrade to empty).

## Command surface
`/gitpain` is primary; `/git` is a deprecated-but-working alias (same handler, `legacy=True`). Subcommands: `status`/`tree`, `stage`/`unstage`/`discard` (+ `stage-all`/`unstage-all`), `commit`, `generate`, `push`/`pull`/`sync`, `branch`, `stash`, `sweep`. Bare `/gitpain` (or `status`/`tree`) prints the Gitpanel summary to the transcript. `sync` = pull then push. `branch <name>` switches to an existing branch or creates-and-switches (`switch -c`) a new one; bare lists. Rebase/reflog/submodules are out of scope — type `git` in the shell. See [[command-surface]].

## Review-before-run (D1)
Nothing mutates the repo until the user presses Enter. Pane actions and AI drafts PREFILL a `/gitpain …` line onto the command line (`state.pending_input`); the command line doubles as the commit-message box. Every mutator this release is a PREFILL — that is the whole release's posture. See [[trust-and-gates]].

## Stash requires a message (D2)
On the `/gitpain` path a stash push must carry a message — positional (`/gitpain stash pause: auth race`) or `-m` (`-m` wins when both given); `-u`/`--include-untracked` is explicit. D2 = git's portable `-m` store only, no xlii-side stash metadata. The legacy `/git` path keeps the message optional. `stash journal` drafts a label from journal context; `list`/`pop`/`apply`/`drop [n]` manage existing stashes — on the primary path a bare `pop`/`apply`/`drop` PREFILLs `… 0` for review rather than acting.

## Commit drafts, never auto-commit
`commit summary` (alias `generate`) drafts a single-line Conventional-Commits subject (≤72 chars) from the staged diff (falls back to the unstaged diff); `commit journal` adds the rolling journal summary plus the optional loop goal. Drafts land on the command line for review — nothing auto-commits. The post-commit journal line `committed <short-sha> — <subject>` is written ONLY after a *verified* commit: `git_cmd` counts exit 1 as success (e.g. nothing staged), so the handler checks HEAD actually moved before journaling. The journal is code-REPL-only; `commit journal` outside it says so. See [[journal-and-receipts]].

## Gitpanel (the pane)
`GitPane` projects `(address, selection)` over `git://` into four sections — **Staged · Changes · Untracked · Stashes**. File rows show a status letter + path, coloured by a status→tone palette (added·green, modified·yellow, removed·red, renamed·cyan, untracked·green). Staged files address `git://staged/<path>`, others `git://diff/<path>`; selecting a file retargets the working slot to its diff. Stash rows carry NO `git://` address (the VFS resolves no stash path, and an unresolvable address breaks copy/export flows) — their patch view is a PREFILLed `!git stash show -p`, and the **Stash…** action CLAIM_INPUTs the required message through a prefill bridge. Actions are data-driven footer buttons; a successful `/gitpain` run re-mounts the pane. Outside a repo the view is empty, not an error. See [[panes-and-dock]], [[addressing-and-vfs]].

## Sweep
`/gitpain sweep` reports what is already merged into the repo's DEFAULT branch (never HEAD) — local branches, the stale worktrees those branches sit in, and merged remote branches — then PREFILLs the exact cleanup as an `&&` chain. Order is load-bearing: `git worktree remove` FIRST (git refuses `branch -d` on a branch checked out in a worktree), then `git branch -d`, then `git push --delete`; all names shell-quoted. The default branch resolves via `origin/HEAD` → `origin/main`|`master` → local `main`|`master` → HEAD as a last resort; measuring "merged" against HEAD on a topic branch would flag every ancestor (the fleet base, sibling branches) as sweepable. `main`/`master`/`develop`/current are always kept.

## D19 — file-row check tool
Each file row offers one Tools button seeding the project's first available check tool from the `/xtool` catalog (fingerprint-ordered, `entry_available`, non-fix) as a `!<argv>` line — review-before-run. Destructive fix variants stay in `/xtool` where their flag is visible. See [[xtool]].

## Decisions & scope
D1–D4 are locked for this release: all mutations PREFILL (D1), the stash message is git `-m` only (D2), and the commit/stash journal is code-REPL-only (D3/D4). In-tree only D2 and D19 carry explicit labels; D1/D3/D4 are the brief's numbering for behaviours the code enforces. Deferred: stages GP3/GP4, and `git://stash/…` addressing (stash rows stay address-less by design).
