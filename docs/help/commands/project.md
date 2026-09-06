# /project

List, find, switch, or remove registered projects, or bind a **startup task**
for this project on this machine.

Available in the **code** REPL (`/projects` is a hidden alias). `startup` is a
reserved first token (subcommand-wins); a project actually named "startup"
stays reachable via `/project find startup`.

## Usage

```
/project                              # registry list (optional filter)
/project find <name>                  # match without switching
/project switch <name>                # switch the live code session
/project startup <task>               # bind (interactive confirm)
/project startup <task> --auto        # auto-run; needs /admin unlock
/project startup --show               # current binding + hash/missing
/project startup --clear              # unbind
/project startup --off                # mute for this session
/project rm [name|.] [--dry-run] [--yes] [--keep-local|--local-only]
```

`rm` is the REPL twin of `xlii project rm`: tear down a project's Collection(s)
+ registry entry + local `.xlii/` — **never your source files**.

## Startup task

Bind a saved `/tasks` pipeline to fire when **this project** opens on **this
machine**. The binding lives in `~/.config/xlii/startup.json` (never in the
repo, never in `project.json` or the task TOML). Default is prefill —
`/tasks run <name>` is typed for you, never run for you.

`--auto` is elevation-gated (`/admin unlock`). Auto still prints the exact
`/tasks run` line first, and downgrades to prefill if the pipeline hash drifted
or the live trust tier is below the binding's recorded tier.

`xlii code --no-startup` skips the ritual this launch. Switch-time re-evaluates
the same guards; `/chat` / `/howto` detour-return does not re-fire.

When the project journal is recording, bind / clear / mute / fire / skip write
a concise note (who, task, confirm vs auto). If the journal is off or
unavailable, the action still succeeds.

Related: `/describe tasks`, `/howto sessions-projects`.
