# /edit

Open a **known artifact** in `$EDITOR` — your way to author the things xlii owns
without leaving the session. `/edit` is a dispatcher: the target is a facet
(`--id`, `--file`, `--doc`, `--plugin`), not a separate command. Exactly one
target per call.

Think of it as the human-authoring seam, distinct from its neighbors:

- the agent's `write_file` / `edit_file` tools *patch* files (model-driven);
- `/browse` *picks* a path (orient + select); `/edit` *opens* it;
- `!!nvim <path>` is the full-screen handover; `/edit` is the discoverable slash.

## Usage

```
/edit                       # current persona's prompt (in a chat session)
/edit --id <name> [--new]   # a persona prompt
/edit --file <path> [--new] # a project file (jailed to the project root)
/edit --doc <name> [--new]  # a reference doc
/edit --plugin <id> [--new] # a plugin's markdown
```

`--new` means "create it": for `--id`/`--doc`/`--plugin` it is strict (refuses an
existing artifact so you can't clobber one by accident); for `--file` it
pre-creates the file (and any parent dirs) before opening. Without `--new`, an
existing artifact is opened; a missing one is created from a template (`--id`,
`--doc`, `--plugin`) or handed straight to `$EDITOR` as a new buffer (`--file`).

- `--id <name>` — works in both the `code` and `chat` REPLs. Editing the persona
  you're currently using prints a reload hint; editing another persona prints how
  to start it (`/chat --id <name>`).
- `--file <path>` — resolves relative to the project root (or the live shell cwd
  when it's inside the root) and is **jailed**: a path that escapes the root is
  refused. Ignored paths (`.gitignore`/`.xliiignore`) may still be edited, with a
  note. Needs a project, so it's a `code`-session target.
- `--doc <name>` — a reference doc; after editing, if it's attached, `/undoc
  <name>` then `/doc <name>` refreshes the inlined copy.
- `--plugin <id>` — a plugin's markdown; restart the session to reload it.

## Examples

Create a persona from a `code` session and start chatting as it:

```
/edit --id reviewer
/chat --id reviewer
```

Pick a changed file with browse, then open it in `$EDITOR`:

```
/browse --changed
/edit --file xlii/repl_cmds/browse.py
```

## Gotchas

- One target per call — combining `--id` and `--file` isn't supported; the dispatcher
  prints usage.
- `--file` is project-jailed by design (the same jail `/browse` uses). To open an
  arbitrary file outside the project, use `!!nvim <path>` or your shell.
- Editing the current persona's prompt doesn't hot-reload yet — restart or
  `/chat --id <name>` to pick up the change.
- `/edit` authors xlii's own artifacts; to stage an arbitrary OS file for the model
  use `/upload` / `/locker` instead.

See also: `/browse` (pick a path first), `/doc` (attach reference docs),
`/chat` (start a persona). Exact flags: `/describe edit`. Deep dive:
`/howto personas-loadouts`.
