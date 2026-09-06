# /file-tab

Dock the **file-tab panel** beside the transcript in the full-screen TUI. The TUI
is **two panes** — one is the AI transcript, one is the file-tab panel — and you
choose which side is which. There is never a third column.

The panel is **binary**: it shows the file **tree**, **or** one **file's contents**
(file-view), **or** the image **gallery** — exactly one at a time. `/file-tab`
returns it to the tree; that is the verb for "show me the tree again".

## Usage

```
/file-tab [on|off] [--image] [--set left|right]
```

- bare `/file-tab` — open the panel on the **tree**, or return it to the tree if it
  is already showing a file or the gallery. (Run it again with the tree already
  showing and it toggles the panel closed.) The `files` tab on the input bar does
  the same "back to the tree" jump.
- `on` / `off` — explicitly dock or undock the panel.
- `--image` — swap the panel to the **gallery** face: a grid of every image rider,
  inline thumbnails via the shipped chafa/sixel renderer.
- `--set left|right` — order the two panes (which side the panel sits on). The
  choice persists for the session.

## Two clicks, two jobs

The crux of the two-pane model is that the tree and the input bar do **different**
things — keeping them separate is the whole point:

- **Click a file in the tree → a rider.** It attaches for the next turn (the same
  `state.attach_file` producer the locker uses) and shows up as a chip on the input
  bar and a tab on the input cap. **The panel does not change.**
- **Click that rider's tab on the input bar → swap the panel.** Only a tab-click
  (or the file-view command) points the panel at that file's contents, rendered by
  kind.

To skip the click dance and point the panel at a file directly, use its sibling:

```
/file-view <path>     # open the panel straight to a file
/editthis             # then edit whatever the panel is showing
```

## Examples

Dock the panel on the left, browse the tree, then click a file to ride it on the
next turn:

```
/file-tab --set left
/file-tab
```

Glance at every image you have staged, then go back to the tree:

```
/file-tab --image
/file-tab
```

## Gotchas

- `/file-tab` only does something in the full-screen TUI — it docks a pane beside
  the transcript. In the inline REPL there is no transcript to dock beside, so it
  nudges you to `xlii code --tui` (or `/tui`).
- A file-click attaches; it does **not** open the file in the panel. That is by
  design — riders ride the turn, the panel views. Use the tab-click or the
  file-view command to swap the panel.
- The panel is a view *over* the session: the file tree attaches into the same
  locker that `/locker` and `/upload` feed, and the gallery is just the locker's
  images. It adds producers and a viewer, not a second data model.

See also: `/locker` (inspect what is staged), `/upload` (desktop file picker),
`/editthis` (edit the file in view). Exact flags: `/describe file-tab`. Deep dive:
`/howto knowledge`.
