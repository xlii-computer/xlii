# /locker

Stage local files — images, text, PDFs — so they ride along with your next turn
to the model. The locker is the explicit, inspectable seam between your disk and
the conversation: nothing is sent until you stage it, and everything staged is
visible at a glance with `/locker`.

Reach for it when the answer depends on a file the model can't already see: a
screenshot to read, a log to diagnose, a spec PDF to summarize, or a snippet of
text you'd rather not paste inline. Files persist for the session (and survive a
restart), so a reference image can sit in the locker across many turns without
re-adding it.

The locker is **one consumer with many producers**: `/locker add` (a path),
`/upload` (the desktop popup), and — in the full-screen TUI — the `/file-tab` panel
tree all feed the *same* store through the *same* `attach_file` contract. `/locker`
is where you inspect and gate whatever they staged.

## Usage

```
/locker [add <path> [--once] | on <name> | off <name> | remove <name> | list]
```

Bare `/locker` (or `/locker list`) shows the current locker: a green ● means the
file is shared on every turn, a hollow ○ means it's held back. Each row prints
its kind and size, and a `live:` line estimates the cost of what's currently
shared.

- `add <path>` — stage a file (enabled by default). Re-adding a known path just
  re-enables it and refreshes its flags.
- `--once` — share on the next turn only, then auto-hold. Good for a one-off
  screenshot you don't want re-sent on every subsequent turn.
- `on <name>` / `off <name>` — flip a staged file between shared and held without
  removing it. Use this to keep a heavy file around but quiet between turns.
- `remove <name>` — drop the entry entirely.

## Examples

Stage a screenshot for the next turn only, then ask about it:

```
/locker add ~/shots/error-dialog.png --once
?what is this dialog telling me, and how do I clear it?
```

Keep a spec around but mute it while you work, re-enabling on demand:

```
/locker add ./design/spec.pdf
/locker off spec.pdf      # held — not sent, but still staged
/locker on spec.pdf       # back in the turn when you need it
```

## Gotchas

- Only images, text, and PDFs are actually embedded. Other types are staged as a
  named reference, not sent as content — `/locker add` warns you when this
  happens.
- Staging an image is pointless if the active model can't see — `add` and `list`
  warn when an image is enabled but the orchestrator model lacks vision.
- Held (○) files cost nothing; shared (●) files count against the turn. Watch the
  `live:` estimate before sending large attachments.
- `--once` is per-entry and resets on re-add — adding the same path again without
  `--once` makes it persistent.
- `/upload` is the GUI front door to the same locker (drag-drop popup, or inline
  paths); headless, it falls back to `/locker add`. Use `/attachments` to see
  locker files alongside attached refs and docs.
- In the full-screen TUI, clicking a file in the `/file-tab` panel tree stages it
  here through the identical `attach_file` producer — no subprocess, no copy. The
  panel is the default in-TUI attach path; `/upload` stays as optional desktop
  sugar. (The panel's gallery face and file-view are just *views* of what is
  staged; they never change this data model.)

See also: `/upload` (desktop popup staging), `/file-tab` (in-TUI panel-tree
staging), `/doc` (inline reference docs into the system prompt). Exact flags:
`/describe locker`. Deep dive: `/howto knowledge`.
