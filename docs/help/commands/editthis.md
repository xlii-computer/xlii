# /editthis

Open a file **inside xlii** — read it, live-edit it, and discuss it without ever
shelling out. `/editthis` is the in-xlii counterpart to `/edit`: where `/edit`
hands a path to `$EDITOR`, `/editthis` opens A2's preview/edit surface, `cat`s the
file into a focused view, and lets you flip to a `TextArea`, type, and `ctrl+s` to
save — no external editor. It is the editable face of the same surface the tabs
read from (read ↔ write).

It is TUI-native: under `xlii code`'s Textual TUI it pushes the live surface; off
the TUI (inline REPL, headless, tests) there is no surface host, so it falls back
to printing the file and handing it to `$EDITOR`.

## Usage

```
/editthis <file>                 # read · live-edit · discuss an existing/new file
/editthis --draft "<desc>" [<f>] # agent drafts the seed → you live-edit → save
/editthis                        # (no path) edit the file the file-tab panel is showing
```

With `--draft`, the agent writes the *starting* content from your description; it
seeds the editor and is **never auto-saved** — you review, edit, then `ctrl+s` to
keep it. Pass a file to say where the save goes; omit it and the draft lands in a
`.xlii/drafts/` scratch file.

With **no path and no `--draft`**, `/editthis` edits whatever the two-pane file-tab
panel is currently showing — the file in view. Point the panel at a file (click its
tab on the input bar, or open it directly with the file-view command), then run a
bare `/editthis` to read · live-edit · discuss it without retyping the path. When
the panel is on the tree, the gallery, or closed — or you are off the TUI — there is
no target, so `/editthis` just prints its usage.

- **Project-jailed** — the file path resolves against the project root (or the
  live shell cwd inside it) and is refused if it escapes, the same jail `/edit
  --file` and `/browse` use. So it's a `code`-session command; without a project
  it tells you to run it in `xlii code`.
- **Four-mode surface** — `ctrl+e` edit · `ctrl+r` read · `ctrl+s` save · `ctrl+d`
  discuss · `esc` back (then close). Discuss runs one AI turn scoped to the open
  file and drops the reply inline — it answers, it does not rewrite; edits stay
  yours to make.
- **Markdown-aware** — a `.md`/`.markdown` file renders the read view as Markdown;
  anything else reads as plain text.
- **Knows what you're opening** — the surface title shows the target's git/sync
  state: **tracked** (committed project file), **untracked** (on disk, not in git),
  or **new file** (you're creating it), plus a `scratch (no-sync)` tag in scratch
  mode. Opening a *new* file asks for confirmation first — so a slip like `/editthis
  tell me about this` (which parses `tell` as the path) can't silently litter a
  stray file, which matters most in scratch where nothing's tracked or synced.
- **Safe save** — writes are atomic and preserve an existing file's permission
  bits; a brand-new file is tagged `(new file)` on save.

## Examples

Live-edit a project file in the TUI, then save with `ctrl+s`:

```
/editthis xlii/repl_cmds/browse.py
```

Have the agent draft a new doc, review it in the editor, and save:

```
/editthis --draft "a short README for the locker module" docs/locker.md
```

Point the file-tab panel at a file, then edit the file in view with a bare
`/editthis` — no path to retype:

```
/file-view xlii/repl_cmds/browse.py
/editthis
```

## Gotchas

- The four-mode surface is TUI-only. In the inline REPL `/editthis` `cat`s the
  file and opens `$EDITOR` instead (and `--draft` seeds the file first, so the
  `$EDITOR` save is your review gate).
- Needs a project — a path is jailed to the project root; to open an arbitrary OS
  file use `!!nvim <path>` or your shell.

See also: `/edit` (the `$EDITOR` sibling), `/browse` (pick a path first),
`/file-tab` (the two-pane panel — point it at a file, then bare `/editthis` edits
it). Exact flags: `/describe edithere`.
