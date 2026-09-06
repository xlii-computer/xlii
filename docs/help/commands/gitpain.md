# /gitpain

**Gitpanel** is xlii's worktree cockpit — not a second porcelain git. It makes painful,
forgettable git ergonomics simple (especially **stash with a real message** and **commit
drafts that see journal context**) while full git stays bare ``git`` in the shell.

``/git`` remains a deprecated alias; prefer ``/gitpain`` and the **Gitpanel** (Alt-G /
Panel menu → Git).

## Usage

```
/gitpain [status|tree]
/gitpain stage|unstage|discard <path>…
/gitpain stage-all|unstage-all
/gitpain commit <message>
/gitpain commit summary          # AI draft from diff only
/gitpain commit journal          # AI draft from diff + project journal (code REPL)
/gitpain generate                # alias of commit summary (draft only)
/gitpain push|pull|sync
/gitpain branch [name]
/gitpain stash <message> [-u]    # positional message; -m <message> also works
/gitpain stash journal           # draft stash message from journal/turns
/gitpain stash list|pop|apply|drop [n]
/gitpain sweep                   # list merged branches/worktrees → seed cleanup cmds
```

Bare ``/gitpain`` (or ``tree``) prints the Gitpanel tree summary on the transcript.
Open the panel from the TUI (**Panel Workbench → Git** or Alt-G) for the sectioned view:
**Staged · Changes · Untracked · Stashes**.

## Stash with a message (the killer habit)

Bare git already supports ``git stash push -m "…"`` — almost nobody uses it consistently.
Gitpain **requires a message** on the primary path — positional or ``-m``:

```
/gitpain stash pause: auth race investigation
/gitpain stash -m "explicit -m works too"
/gitpain stash journal           # draft the message, then review on the command line
```

The Gitpanel **Stash…** action claims the input line for your one-line comment
(review-before-run). **Stash incl. untracked…** adds ``-u``. Listing stashes is standard
``git stash list`` — gitpain makes **create + browse** humane in the panel.

## Commit drafts

```
/gitpain commit summary          # model reads staged (or unstaged) diff
/gitpain commit journal          # diff + rolling journal summary + optional loop goal
```

Nothing auto-commits — the draft lands on the command line for review. After a successful
commit with the code journal recording, a line like ``committed abc1234 — subject`` may be
observed in the project journal.

## Sweep

After a merge round, ``/gitpain sweep`` lists merged local branches, stale worktrees on
those branches, and already-merged remote branches — then **seeds the exact cleanup
commands** on the command line (``git branch -d …``, ``git worktree remove …``, etc.) for
review-before-run.

## Full git porcelain

For rebase, reflog, submodules, or any flag salad — type ``git`` in the shell. Gitpain
is the xlii-native control protocol for the ``git://`` doorway, not a Magit replacement.

## See also

- ``/browse`` — repo orientation (skeleton, tree, changed files)
- ``/checkpoint``, ``/diff``, ``/rewind`` — working-tree snapshots
- Panel **Gitpanel** (``git://``) — review-before-run footer actions; a file row also
  offers the project's first available check tool from the ``/xtool`` catalog (seeded
  as a ``!`` line, never executed)
