# Selecting & copying text in the TUI

In the full-screen TUI (`xlii code --tui`, or `/tui` from the inline REPL), the
app owns the mouse — so copying works a little differently from an ordinary
terminal. Everything you need is now **built in**, and every copy reaches your
real system clipboard over **OSC 52**, so it works even over SSH.

## Select with the mouse, copy with Ctrl+C

**Drag** across the transcript to select text, then press **Ctrl+C** to copy it
(⌘C / Super+C also work). A plain **click** somewhere else — or **Esc** — clears
the selection. That's the whole gesture: drag, Ctrl+C.

## One click copies a whole block

You rarely need a precise drag. The everyday things are a single click:

| Click on… | …and it copies |
|---|---|
| a **code fence** | the raw code in that block |
| a **tool / shell / command-output** block | that block's text |
| an **assistant answer** (anywhere on its prose) | the whole answer, as markdown |

Each copy pops a brief "copied" confirmation. Prefer the keyboard? **Tab** to a
block (or click it) to focus it, then press **`c`**. Clicking a code fence inside
an answer copies just that fence; clicking the surrounding prose copies the whole
answer.

Copying only ever writes to the clipboard — it never runs anything. You still
press **Enter** to run a command you've pasted or typed.

## Keyboard copy-mode (mouseless, whole blocks)

No mouse? Press **Ctrl+R** to enter *copy-mode* — a modal, tmux/less-style way to
grab one or more whole blocks with the keyboard alone:

| Key | Does |
|---|---|
| **j** / **k** | move the block cursor down / up |
| **g** / **G** | jump to the first / last block |
| **v** | mark the range anchor (press again to unmark) |
| **y** | yank — copy the selected block(s) to the clipboard, then exit |
| **Esc** / **q** | leave copy-mode without copying |

The cursor block is highlighted; once you press **v**, everything from the anchor
to the cursor is the selection. Without an anchor, **y** copies just the block
under the cursor. Copy-mode selects **whole blocks** (not partial lines) — for a
character-precise range, drag with the mouse (or Shift+drag) as above.

## Why it's built into the app

The TUI turns on **mouse tracking**. Once an app does that, the terminal hands
every mouse event *to the app* — clicks, drags, wheel — so the TUI can make chips
clickable, scroll the transcript, and focus panes. The cost is that the
terminal's own drag-to-select is suppressed while the app owns the mouse, which
is why selection is *implemented in xlii* (drag + Ctrl+C above) rather than
inherited from the terminal.

## The fallback: Shift+drag (terminal-native selection)

On most terminals the built-in copy above is all you need. Two cases still want
the terminal's own selection instead:

- **macOS Terminal and Apple Terminal don't accept OSC 52 clipboard writes**, so
  Ctrl+C-to-copy can't reach the clipboard there. Use Shift+drag (see below).
- You'd simply rather use your terminal's native selection and copy.

Almost every terminal reserves **Shift** as the "ignore the app, let me select"
modifier. Hold Shift and drag to make a native selection over the transcript,
then copy with your terminal's usual shortcut:

| Terminal | Select | Copy |
|---|---|---|
| kitty | **Shift**+drag | Ctrl+Shift+C (or auto-copy on release) |
| GNOME Terminal / VTE, Konsole | **Shift**+drag | Ctrl+Shift+C |
| xterm | **Shift**+drag | middle-click to paste (PRIMARY) |
| Alacritty, Windows Terminal, WezTerm | **Shift**+drag | Ctrl+Shift+C |
| iTerm2 (macOS) | hold **⌥ Option** and drag | ⌘C |
| Apple Terminal (macOS) | hold **⌥ Option** and drag | ⌘C |

Running inside **tmux** with `mouse on`? tmux grabs the mouse before the terminal
does, so hold **Shift** to bypass tmux too (or `set -g mouse off` for the window).

## If nothing copies

- **Over SSH and nothing lands?** Your terminal may not honor OSC 52 clipboard
  writes. Enable it (kitty: `clipboard_control write-clipboard`; others have an
  equivalent), or use the Shift+drag fallback.
- Some terminals bind native selection to a *different* modifier — check the
  terminal's docs for "select while mouse reporting" or "bypass application
  mouse".
- A terminal multiplexer (tmux/screen) in the middle can intercept first; bypass
  or disable its mouse mode.
- As a last resort, drop back to the inline REPL (leave the TUI), where the
  terminal owns the mouse and ordinary drag-select works everywhere.
