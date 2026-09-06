# Sessions & projects

A project is a directory xlii knows about: a `.xlii/` state dir plus (usually) a
remote xAI Collection that backs RAG search. This topic covers the project
lifecycle and what survives a restart. For the authoritative, always-current
flags on any command, run `/describe <cmd>`.

## Create or register a project

```bash
xlii init                 # init the current dir (name = dir basename)
xlii init my-app          # explicit label
xlii init --local         # local-only: no Collection, no upload, sync is a no-op
xlii new my-app           # mkdir my-app/ then init it
xlii new notes --local --kind collection
```

On the face or TUI, **Project → New project folder…** (or **New collection…**)
asks for a name and runs the stock system task `new-folder`: `xlii new --local`
then `/project switch`. Examine it with `/tasks show new-folder`. **Adopt
folder…** / **Adopt collection…** stay for trees that already exist.

`xlii init` writes `.xlii/project.json`, provisions a Collection (unless
`--local`), and records the project in the global registry. Re-running on an
already-initialized dir is a no-op unless you pass `--force`; bind a chat persona
with `--id <persona>`. `xlii new` creates the directory first, then inits it.
`--local` and `--kind` work on `new` the same as on `init`.

`--id` is optional. Unset (or `default` / `mojo`) means the mobile journal —
same seat, two words; unnamed spelling is `mojo`. A named bind (`--id research`)
is a *different island* for talk in that folder. See `/howto personas-loadouts`.
The lab (`$`) still works the tree; the bind only names who `[M]` talks to.

For local-only vs. synced trade-offs and snapshot indexing, run `/describe init`.

## List what's registered

```bash
xlii projects             # every registered project, alive/dead marker
xlii projects api         # substring filter (matches name or path)
```

Inside a `code` session, `/project` prints the same registry list. A red marker
means the recorded path no longer holds a `project.json` (moved or deleted).

## Open a session

```bash
xlii code                 # open the code REPL in the current project
xlii code my-app          # by registered name or path
```

See `/howto first-session` for code-vs-chat surfaces, input routing, and the
ephemeral `--preview` / `--init` launch options.

A per-project **startup task** (`/project startup <task>`) prefills `/tasks run
<name>` when that project opens on this machine. Bindings live in
`~/.config/xlii/startup.json` — they never travel with a clone. Mute one launch
with `xlii code --no-startup`, or the rest of the session with
`/project startup --off`. Bind/clear/mute/fire write a journal note when the
project journal is recording.

## Sync to the Collection

Files upload to the Collection so `search_project` (RAG) can find them. Sync runs
automatically after each turn; force it manually anytime:

```bash
xlii sync                 # push local changes now
xlii sync --dry-run       # show the upload/update/delete plan, change nothing
```

In-session, `/sync` does the same push for the current project. On a local-only
project both are no-ops (nothing to upload). A sync that would delete remote docs
prompts first from the CLI; run `/describe sync` for the current behavior.

## Check state

```bash
xlii status               # config + keys + active project, from the shell
```

Inside a session, `/status` is project-focused: it shows the project name, root,
mode (full/synced vs. local-only), collection or cached index, conversation id,
key-pool size, the second-opinion judge line, and live mode flags (plan mode,
rail stage, durable attachments, current workspace). Run it whenever you're
unsure what's attached or which mode you're in.

## Move around the shell

The `code` REPL tracks a live shell cwd; a bare `cd` moves it. On `[M]`
(ask-primary) a short `cd` / `ls` / `pwd` still moves that same desk, and
`!cd` sticks — `/sh` uses it. To jump back:

```text
/cwd            # return the live shell to the project root
/cwd <path>     # navigate elsewhere (warns if you leave the project)
```

`/cwd` works even in plan mode, where a bare line would otherwise go to the model.
It's code-REPL only.

## Forget this chat

```text
/reset          # forget this chat; journal, wiki, typed lines, attachments stay
/clear          # wipe the glass only — talk stays (`/cls`, `/clear-screen`)
```

`/reset` forgets the working talk (`agent.history` back to the system prompt)
and drops this stream's parked tape (`.xlii/turns` / the persona store) so a
reload or switch-back does not resurrect it. If a rail is active, it restarts
at stage 0 (the rail stays enabled if you opted in). It does **not** touch
journal, wiki, typed input lines, or attached refs/docs/locker files. Typed
lines live on the History panel — clear them there. Use `/clear-attachments`
for locker riders. Marks in the forgotten turns go with the tape.

`/clear` (aliases `/cls`, `/clear-screen`) is the screen wipe: same as
Xlii → Clear transcript. A terminal `clear` does not reset Face or the TUI
widget. Memory stays; `/reset` is the forget.

## What persists across restarts

Chrome prefs in `~/.config/xlii/config.json` (Face + TUI, every project):

- **Theme / skin** — TUI `tui_theme`, Face `face_skin`.
- **F-keys strip** — `face_fkeys` (Options → F-keys).
- **Bold type** — `face_bold` (Face Options).
- **Chat tier** — `chat_tier` (`/tier`, Options cycle, config pane).

Stored in `.xlii/`, restored when you reopen the project:

- **Durable attachments** — attached refs, docs, and locker files (`session.json`,
  per named workspace; see `/howto personas-loadouts`).
- **Session flags** — yolo state and any one-shot temperature override.
- **Project identity** — name, Collection id, stable conversation id, persona
  binding (`project.json`).
- **The registry entry** — so `xlii projects` and `xlii code <name>` keep finding it.

By default, conversation **history** gets a lighter treatment: your recent turns
are re-seeded as context at the next launch (memory, not a full replay), so
"where did we leave off?" works — but the full turn-by-turn history is not
restored. Session-only tunables like `/iterations` and `/swarm` reset to the
config defaults next launch unless you save them — check `/describe swarm` for
`--save`. To carry a specific exchange between sessions, `/mark` a turn and
`/recall` it later (`/howto knowledge`).

## Keep the whole session — episodes

For long, crashy, or parallel runs, opt into an **episode**: the FULL live
history (user/assistant/tool trail) plus the conversation id are snapshotted to
`.xlii/sessions/<id>.json` after every turn, and a resume restores them exactly
(same conversation id, so the prompt cache can stay warm).

From the shell — **say it once, it sticks**:

```bash
xlii code --keep-session     # start persistent AND make it sticky for this project
xlii code                    # every later launch asks: "restart there? [Y/n]"
xlii code --resume           # skip the question: restore the most recent episode
xlii code --resume 5a7b      # …or a specific one by id
```

Once a project is flagged, each bare launch offers to restart where you left —
one typed question showing what's waiting (turns, how long ago, and whether the
KV/prompt-cache prefix is likely still warm). Enter resumes; `n` starts a fresh
episode with the preference intact. Non-interactive launches (pipes, scripts)
resume silently instead of prompting — nothing ever hangs.

Mid-session, same thing:

```text
/session on            # start snapshotting from here (sticky, like --keep-session)
/session list          # stored episodes for this project
/session resume [id]   # restore one into the live session (keeps persisting)
/session off           # stop AND clear the sticky preference
```

A session that ends normally is marked clean. A **crashed** episode leaves
`unclean` residue instead — the next launch offers it back with one dim line
(`/session resume <id> to pick it up`); no boot wizard, ever. Episodes work the
same on both surfaces (inline REPL and `--tui`) and resume across them.

## Related

- `/describe session` — live detail on `/session` and the episode store
- `/howto first-session` — code vs chat, input routing, launch gates
- `/howto knowledge` — refs, docs, marks, locker, RAG search
- `/howto personas-loadouts` — workspaces and saved attachment bundles
- `/howto config-models` — models, temperatures, pricing
- `/howto troubleshoot` — when `init`/`sync`/`status` misbehave
- `/describe <cmd>` — live, authoritative detail on any command above

## Startup task (per-machine)

A project can carry a per-machine startup binding — a saved task that fires when a
code session opens. Bindings live in `~/.config/xlii/startup.json` (never in the
repo). Capture is the default: the exact `/tasks run <task>` line is prefilled for
review, and nothing runs until you submit it. A binding marked auto runs only when
the live session trust tier is at least the tier recorded in the binding, always
prints the exact run line before running, and downgrades to capture when the
pipeline file changed since binding. Bind with `/project startup <task>`
(interactive confirm); `--show` / `--clear` / `--off` inspect, unbind, or mute;
`--auto` needs `/admin unlock`. `/describe project` has the live flag list.
