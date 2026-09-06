# /file-view

Open the **file-tab panel** straight to a file. The panel is the one viewer in the
two-pane TUI, and it is *binary*: it shows the file tree, **or** one file's
contents, **or** the image gallery — never more than one at a time, never a third
column. Normally you reach a file in two clicks (click it in the tree to attach it
as a *rider* for the next turn, then click its tab on the input bar to *swap* the
panel to it). file-view is the one-step path that points the panel at a file
directly, skipping the file-click → tab-click dance.

There is no separate preview widget — the panel *is* the viewer, and it renders by
**kind**: text and code render as file contents, an image renders inline
(chafa/sixel, the same renderer the locker uses), a directory shows the tree, and
the gallery face shows a grid of every image rider. "Image mode" isn't a mode; it's
file-view on a PNG.

## Usage

```
/file-view <path>          # point the panel at one file, rendered by kind
```

The path is **project-jailed** — it resolves against the project root (or the live
shell cwd inside it) and is refused if it escapes, the same jail `/editthis`,
`/edit`, and `/browse` use. So it is a `code`-session command; without a project it
tells you to run it in `xlii code`.

Once a file is in view:

- `/editthis` with no arguments edits **that** file — read · live-edit · discuss.
- bare `/file-tab` (and the `files` tab on the input bar) return the panel to the
  tree.
- `/file-tab --image` swaps the panel to the gallery face (all image riders).

## Examples

Point the panel at a file, then edit the file in view without retyping its path:

```
/file-view xlii/repl_cmds/locker.py
/editthis
```

Return the panel to the tree when you are done:

```
/file-tab
```

## Gotchas

- The panel lives in the full-screen TUI. Off the TUI (the inline REPL, headless)
  there is no panel to point, so the command prints a note — run `xlii code --tui`
  (or `/tui`) first.
- A **file-click in the tree does not** swap the panel — it attaches the file as a
  rider for the next turn. Only a tab-click (or this command) swaps the panel. The
  two surfaces are deliberately separate: riders ride the turn, the panel views.
- The path is jailed to the project root; to stage an arbitrary OS file for the
  model, attach it with `/upload` or `/locker add` instead.

See also: `/file-tab` (return to the tree · order the panes · gallery), `/editthis`
(edit the file in view), `/locker` (what is staged to ride the turn). Deep dive:
`/howto knowledge`.
