# /replay

Re-print the last thing that produced output — verbatim, and for free. `/replay`
re-spits the most recently captured output exactly as it was shown the first
time: no model call, no tokens, and it can't fail. Reach for it when you scrolled
past something, ran a screen `/clear`, and now want that output back without
re-running the command or asking the agent to recall it.

The captured output lives on the session, not in the visible transcript, so it
*outlives a screen `clear`* — clearing your terminal does not lose it. `/replay`
just reads that cached string back and prints it again, byte-for-byte. There is
no AI summary involved; this is a faithful copy, not a recap.

## What gets captured

One buffer holds "the last thing that produced output," typed by source:

- `shell` — the stdout/stderr of the last shell command you ran.
- `harness` — the last external-harness exchange (e.g. `/cursor`, an ACP turn).
- `answer` — a captured agent answer or `/consult --capture` reply.

Each new captured output replaces the previous one — there is only ever one
buffer, so `/replay` always shows the *most recent* capture.

## Usage

```
/replay
```

Alias: `/last`. Takes no flags or arguments. Available in both the `code` and
`chat` REPLs.

## Behaviors

- **Token-free.** No provider call is made; it re-prints a string already in
  memory. Nothing is added to your conversation history.
- **Survives `/clear`.** The buffer is on session state, not the on-screen
  transcript, so a cleared screen still replays.
- **Verbatim.** The text is printed with markup and re-highlighting off, so it
  is a faithful copy — Rich markup in the captured text is never interpreted.
- **A header marks the replay.** Output is prefixed with a dim
  `↻ replay · <label>` line (e.g. `↻ replay · $ git status`).
- **Nothing yet?** If nothing has been captured this session, `/replay` says so
  and points you to run a shell command, `/cursor`, or `/consult` first.

## Examples

Scrolled past a long shell output, then cleared the screen — get it back:

```
$ pytest -q
/clear
/replay
```

Re-show the last `/cursor` exchange without re-running it:

```
/cursor refactor the auth middleware
/replay
```

Related: `/sh --explain` and `/sh --transform` read the same shell capture (explain /
summarize it instead of re-printing); `/cursor` and `/consult` are common
sources of a captured harness or answer buffer.
