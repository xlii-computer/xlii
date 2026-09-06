# /verify

Cold-context check on your uncommitted work, judged against what you actually
asked for this turn. `/verify` spawns a fresh reviewer agent that has never seen
your conversation. It is handed exactly two things: the last turn's task, and a
`git diff HEAD` of everything not yet committed. The question it answers is
narrow on purpose — "did this change do what was asked?" — not "is this good
code?"

Reach for it after the model finishes a change and before you commit. It is the
fastest way to catch the two failures that slip past a self-review: scope creep
(unrelated files got touched) and verification theater (a commit message or
comment claims something the diff doesn't actually prove). Because the reviewer
runs in a clean context, it can't rationalize the change the way the author can.

`/verify` is `code` REPL only. It takes no flags or arguments — it reads the
last user turn and the working tree, and that's the whole input.

```
/verify
```

The reviewer has read-only investigation tools (read_file, grep, glob, bash for
imports/tests), so it will actually open the files the diff sits inside rather
than squinting at hunks. Output is either a one-line `PASS: <summary>` or `FAIL`
followed by a numbered list of specific defects, each tagged file:line. Nothing
it produces goes back into your chat history — the review is printed and saved,
not threaded into the agent's memory.

The report is written to `.xlii/verify-last.md` so you can escalate it. If the
verifier flags something you want a second, outside-vendor opinion on, hand that
finding to `/consult`.

## Examples

Verify a fix right after the model writes it, before committing:

```
?fix the off-by-one in pagination
/verify
```

If it comes back `FAIL`, get an independent read on its findings:

```
/verify
/consult does this off-by-one finding actually hold?
```

## Gotchas

- It only sees **uncommitted** changes. Once you commit, `git diff HEAD` is
  empty and `/verify` has nothing to look at — it prints "no uncommitted
  changes." For committed work, use `/peer` instead (blind review of a range).
- It needs a prior turn. On a fresh session with no AI turn yet, there is no
  task to verify against and it bails with "no prior turn this session."
- It compares against the **last** user prompt only. If you stacked several
  asks into one turn before running it, that whole turn is the brief; if you
  want a tighter scope, verify after each discrete change.
- `PASS` means "did what was asked," not "ship it." It does not vouch for style,
  architecture, or anything outside the stated task.

For the live signature and any flags, run `/describe verify` (and `/describe
peer` / `/describe consult` for its siblings). Deep dive: `/howto review`; to see
where `/verify` sits next to `/peer`, `/loop`, and `/plan`, read `/howto modes`.
