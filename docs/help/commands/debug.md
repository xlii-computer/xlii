# /debug

`/debug` puts a turn into a staged bug hunt: hypothesize → instrument → reproduce
→ analyze → fix → verify. Each phase narrows the tool surface so the model gathers
runtime evidence before it edits, and the exit is gated on cleaning up after
itself. It is the runtime-evidence sibling of `/plan` (read-only design) and
`/rail` (staged design→implementation); reach for it when a bug is
non-deterministic and you need logs, not just reasoning.

The phases and their tool gates:

```
0  Hypothesize   read-only        — form falsifiable hypotheses from the code
1  Instrument    edit_file only   — add log lines (each MUST carry # xlii-debug)
2  Reproduce     bash only        — run the repro, capture the instrumented output
3  Analyze       read-only        — read the evidence; confirm or refute
4  Fix           full writes      — the real fix
5  Verify        read-only + gate — re-run; /debug exit is blocked until clean
```

The teeth are at the tool layer, not the prompt: in Instrument the model is
offered only `edit_file`, and any added line missing the `# xlii-debug` marker is
**refused**, so every trace is greppable. `/debug exit` runs a repo scan for that
marker and stays blocked until zero remain — you cannot leave instrumentation
behind.

## Usage

```
/debug           start the hunt at phase 0 (Hypothesize)
/debug next      this phase looked good — advance and run the next one
/debug back      redo the previous phase
/debug status    reprint the current phase and its tool gate
/debug exit      leave debug mode (blocked until all # xlii-debug markers are gone)
```

`/debug next` carries the **same bug** forward and runs that phase immediately —
you do not retype it. To refine within a phase instead of advancing, type a normal
message; you stay put. (`next`/`n`, `back`/`b`, `status`/`?`, `exit`/`off` are
accepted aliases — `/describe debug` has the authoritative list.)

## Examples

Chase a flaky test through the full hunt:

```
/debug
test_checkout intermittently double-charges
# read the hypotheses, then:
/debug next        # → Instrument: add `log(...)  # xlii-debug` around the charge path
/debug next        # → Reproduce: pytest -k checkout in a loop
/debug next        # → Analyze: the evidence pins a race on the retry
/debug next        # → Fix: make the retry idempotent
/debug next        # → Verify: re-run, then remove the markers
/debug exit        # allowed only once the tree is marker-clean
```

## Gotchas

- **Plan, rail, and debug are mutually exclusive.** Entering `/plan` or `/rail`
  while debug is on turns debug off, and vice versa.
- **A live loop blocks it.** If `/loop` is running, `/debug` refuses until you
  `/loop off` first.
- **Instrument refuses unmarked edits and all `write_file`.** Add log lines into
  existing files with a `# xlii-debug` comment on every added line; new files wait
  for the Fix phase.
- **You cannot exit dirty.** `/debug exit` lists any remaining `# xlii-debug`
  lines and stays in debug mode until you remove them (or fold them into the fix).
- It is per-session code-surface state — switching to `/chat` and back does not
  carry the debug session.
- Not to be confused with `/inspect`, which dumps live REPL/agent internals.

Deep dive: `/howto debug-flaky`
