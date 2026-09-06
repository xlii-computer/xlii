# /upload

Stage files in the locker through a **desktop file picker**. `/upload` opens a tiny
native dialog (a tkinter subprocess, so it never fights the REPL's input or the
full-screen TUI); you pick image/text/PDF files, hit **Done**, and the chosen paths
are folded into the same locker `/locker` manages — riding your next turn to the
model.

It is the OS-wide complement to the in-project pickers: where `/browse` and the
`/file-tab` panel tree stay inside the project root, `/upload` reaches anywhere on
disk — a screenshot on the Desktop, a PDF in Downloads — without you typing the
path.

## Usage

```
/upload [<path> … [--once]]
```

- bare `/upload` — open the desktop picker (needs a GUI). Pick one or more files,
  optionally remove a mistake, then **Done**.
- `/upload <path> …` — skip the dialog and stage paths inline (handy over SSH or in
  a script). Accepts several paths at once.
- `--once` — share the file on the next turn only, then auto-hold (same semantics as
  `/locker add --once`).

## One consumer, many producers

`/upload` is **superseded but not removed**. In the full-screen TUI the `/file-tab`
panel tree is the default in-TUI attach path — clicking a file there stages it
through the *same* `attach_file` producer, with no subprocess. `/upload` stays on as
desktop sugar for the OS-wide case (a file that lives outside the project). Both,
plus `/locker add`, feed the one locker; `/locker` is where you inspect and gate
what they staged.

## Examples

Stage a screenshot from the Desktop for the next turn only, then ask about it:

```
/upload ~/Desktop/error-dialog.png --once
?what is this dialog telling me?
```

Open the picker, choose a few files, and review what landed:

```
/upload
/locker
```

## Gotchas

- **Drag-and-drop needs the optional `tkinterdnd2` dependency.** Without it the
  popup quietly falls back to click-to-pick ("Add files…"). That fallback is
  expected, not a bug — and since the `/file-tab` panel tree supersedes the popup as
  the default in-TUI path, it is intentionally low priority.
- The popup needs a display. Headless (no `DISPLAY`/`WAYLAND_DISPLAY`, and not
  macOS/Windows) `/upload` prints a note and points you at `/locker add <path>` or
  inline `/upload <path>` instead.
- The picker needs system tkinter (`python3-tk`); if it can't launch, `/upload`
  falls back the same way. Inline `/upload <path>` always works.
- `/upload` is OS-wide on purpose — it is **not** jailed to the project. For
  in-repo files use `/browse` or the `/file-tab` panel tree.

See also: `/locker` (inspect and gate what is staged), `/file-tab` (in-TUI
panel-tree staging), `/browse` (project-jailed picker), `/attachments` (everything
attached this session). Exact flags: `/describe upload`. Deep dive: `/howto
knowledge`.
