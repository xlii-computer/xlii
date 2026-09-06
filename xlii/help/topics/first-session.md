# First session — Home, then a folder

> **Full first-hour spine:** [GOLDEN-PATH.md](../../GOLDEN-PATH.md)  
> **Recovery:** `xlii doctor` · `/howto` · `/describe <name>`

## Mental model

**Home** is the building — roam the machine, poke the OS, never-sync. A
**folder you make** is a room (`xlii init` / `xlii code`). **mojo** is who
you talk to (`[M]`); the journal walks with you. **`[$]`** is the lab (`/sh`,
explain, `/ops`, edits). Lab memory stays in that room. Home is not a project.

```text
Home (desk) ──► enter a folder (project)
            ──► stay Home
```

`[M]` / `/mojo` is the journal. `xlii chat` sits with **iXaac** (a chat
costume — not a `/role`), not a third home. Other personas are other rooms
(`chat/<name>`). Jobs are `/role`.

> Tool-output spill under a project's `.xlii/scratch/tool-output/` is **not**
> Home. Home sessions are `xlii scratch` / the face / `~/.xlii/scratch/home`.

## Two places

| Place | Command | Purpose |
| --- | --- | --- |
| Home | `xlii scratch` · face | Roam / OS / never-sync |
| A folder | `xlii code` · `xlii init` | Lab in that room: files, shell, plan/rail/loop |

`[M]` / `[$]` are postures **inside** a place, not extra homes. One-shot:
`xlii ask "..."`.

## Start at Home (recommended first open)

```bash
xlii scratch              # ephemeral from $HOME — nothing under ~
xlii scratch --tui        # same, full-screen TUI
xlii scratch here         # local-only .xlii in the cwd (refuse if already synced)
xlii scratch notes        # named pad under ~/.xlii/scratch/notes/
```

Home rides the lab runner with never-sync forced — `/sh`, explain, `/ops`
when the machine is the job. When you want a real project, **make one**
(face **Project → New project folder…** runs the `new-folder` system task —
create, init, switch) or **adopt** an existing tree, or **switch** into a
folder (`xlii code <name>`, projects panel, `/project switch`). Packs are
`/workbench home|chat|code` (slot views, not modes); see `/describe workbench`.

## Start a coding session

In any project directory:

```bash
xlii init --local --snapshot   # recommended for first hour (no Collection)
xlii init                      # full: Collection + .xlii (name = dir basename)
xlii code                      # open REPL here
xlii code my-app               # registered project by name or path
```

**Launch gate:** xlii refuses a second `xlii code` for the same project (race on
sync). Override with `--launch` / `--force`. Ephemeral options:

```bash
xlii code --preview    # no .xlii, no sync (throwaway)
xlii code --init       # local-only .xlii, no Collection
```

**Keep your session:** `xlii code --keep-session` snapshots the full conversation
after every turn — and sticks: each later launch of that project offers to restart
where you left (`restart there? [Y/n]`). `/session off` clears it. Details:
`/howto sessions-projects`.

## Input routing in `xlii code` / scratch

Default (**shell-primary**): a bare line runs as a shell command in the tracked cwd
(`cd` moves it).

| Input | Effect |
| --- | --- |
| bare line | shell command (code REPL) |
| `?<text>` | ask the AI |
| `!<cmd>` | shell at project root |
| `!!cmd` | raw TTY program (vim, less, …) |
| `/<command>` | slash command — `/help` daily kit; `/help all` for everything |

Toggle shell-primary: `XLII_SHELL_PRIMARY=0`.

## Input routing in `xlii chat`

Bare text talks to the persona. Slash commands work the same (`/help` for list).

## Modes worth knowing early

- `/plan` → read-only investigation → numbered plan → `/execute` or `/cancel`.
- `/howto [topic]` → this guide mode (bare input asks how to use xlii).
- `/status` → **mode · trust · surface**, then project/attachments.
- `/attach doc|ref|bookmark` / `/detach` → durable knowledge (aliases `/doc`, …).
- `/yolo` / `/safe` → trust ladder (bash confirmation gate).
- `/sync` → push local files to Collection now (also auto-runs after each turn when sync is on).

## Start a persona

```bash
xlii chat --new ada    # opens $EDITOR on a template
xlii chat ada
```

Inside chat: bare `/persona` lists (current marked); `/persona <name>` switches;
`/edit`, `/reset`; attach knowledge with `/attach …`.

## When stuck

Run `xlii doctor`, then `/howto troubleshoot`, or `/describe <command>` for live
introspection of a slash command or tool.
