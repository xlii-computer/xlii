# /peer

Blind peer review of a committed range. `/peer` spawns a fresh reviewer agent
that has never seen your conversation, your prompts, or the design discussion
that produced the change. It gets exactly three things: the commit log for the
range, the full diff, and the list of files touched — then read-only access to
the rest of the codebase. Crucially it does **not** get the original task. It
must reconstruct intent from the artifact alone, the way a stranger opening your
PR would.

Reach for it when work is already committed and you want a cold read on whether
it holds up on its own. This is the complement to `/verify`: `/verify` answers
"did this do what I asked?" against uncommitted changes; `/peer` answers "does
this stand up without me explaining it?" against a committed range. If the
reviewer can't even tell what a change is for from the commits and the code,
that incoherence is itself a finding — which is often the most useful thing it
surfaces.

`/peer` is `code` REPL only. With no flags it reviews `HEAD~1..HEAD` — your last
commit. Use `--since <ref>` to widen the range; the reviewer then sees
`<ref>..HEAD`. The ref is anything git can resolve: a branch, a tag, a SHA, or a
relative like `HEAD~5`.

```
/peer [--since <ref>]
```

The reviewer has read-only tools (read_file, grep, glob, search_project, and
read-only bash like `git show`, `git log`, `git blame`). It is told to actually
open the files the diff sits inside and cross-check against existing patterns,
not just squint at hunks. Output is either a one-line `PASS: <summary>` or
`FAIL` followed by a numbered list, each item tagged `[correctness]`,
`[coherence]`, or `[consistency]` at file:line. Nothing it produces re-enters
your chat history — the review is printed and saved to `.xlii/peer-last.md`, not
threaded into the agent's memory.

## Examples

Review just the last commit you landed:

```
/peer
```

Review a whole feature branch against where it forked from, then escalate the
findings to an outside-vendor model:

```
/peer --since main
/consult --from-peer is the [coherence] finding on the commit message real?
```

## Gotchas

- It only reviews **committed** work. If you haven't committed yet, the range is
  empty — use `/verify` for the working tree instead.
- On a brand-new repo with a single commit there is no `HEAD~1`, so bare `/peer`
  bails. The remedy it prints: pass `--since <ref>`, or seed a baseline with
  `git commit --allow-empty`, or use `/verify` for uncommitted work.
- The blindness is the feature, not a limitation. Don't reach for `/peer` to
  confirm the model did what you asked this turn — that's `/verify`'s job, and
  it has the brief. `/peer` is for catching what survives without the brief.
- `--since` takes exactly one ref. An unresolvable ref fails loudly with the git
  error; check the spelling rather than assuming the range is empty.
- `PASS` means the change is coherent and internally consistent, not that it
  matches some unstated requirement the reviewer never saw.

Deep dive: `/howto review`
