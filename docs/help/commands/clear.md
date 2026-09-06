# /clear

Wipe the **glass**, not the talk. `/clear` (aliases `/cls`, `/clear-screen`)
empties the transcript the way Xlii → Clear transcript does: pixels gone, memory
stays. Reach for it when the feed is noisy and you want a blank page without
forgetting the conversation.

A real terminal's `clear` / `cls` does the same in the inline REPL. On Face and
the TUI that shell command cannot reset the widget — it only prints escape codes
into a captured buffer — so this slash is the one that actually blanks the
screen.

`/reset` is the other verb: it forgets this chat. Use that when you want a new
talk, not a tidy screen.

## Usage

```
/clear
/cls
/clear-screen
```

Takes no flags or arguments. Available in both the `code` and `chat` REPLs,
including Face `[M]` (Mojo) — chat-safe, pixels only.

## Behaviors

- **Pixels only.** Working talk, journal, wiki, typed input history, and
  attachments stay. The next turn still knows what you were saying.
- **Same as the menu.** Xlii → Clear transcript sends `/cls`.
- **Survives `/replay`.** Captured shell/harness output lives on session state,
  not the visible feed — `/replay` still reprints the last capture after a
  clear.
- **Not `/reset`.** `/reset` forgets the working talk (`agent.history` back to
  the system prompt) and drops this stream's parked tape. `/clear` does neither.

## Examples

Tidy the glass mid-talk:

```
/cls
```

Forget the talk instead:

```
/reset
```

Related: `/reset` (forget this chat), `/replay` (reprint last capture),
`/history` (typed lines — clear those on the History panel).
