# xlii — HOWTO (the complete walkthrough)

A start-to-finish walkthrough: from a fresh machine to a personal AI substrate
you've made your own — projects, persona "custom agents", a plugin library, the
coding rail, the optional TUI, the Grok Build bridge, and the (in-development)
multi-machine fabric.

This is the **guided** doc, one of four:

- **[README](../README.md)** — the one-screen pitch + 60-second start. Read it first.
- **[GUIDE](GUIDE.md)** — what xlii is, install, first-time setup, the command map, the design thesis.
- **HOWTO** (this file) — the task-by-task walkthrough.
- **[REFERENCE](REFERENCE.md)** — architecture, full config schema, every `XLII_*` env var, the agent tool catalog, security, hooks, troubleshooting.

**Two helps, two moments.** Outside a session, `xlii help` lists the CLI
subcommands (how to set up & manage xlii). Inside a `code`/`chat` session, `/help`
lists that REPL's slash commands (how to drive the session). Run `xlii <command>
--help` for any one subcommand's full flags. (See [GUIDE §two helps](GUIDE.md#the-two-ways-to-ask-for-help).)

> Conventions: shell commands assume the `xlii` binary is on your `PATH`. If you
> didn't symlink it, prefix with `./venv/bin/` (e.g. `./venv/bin/xlii status`).

---

## The first hour (start here)

If you only have an hour, **do not read this file end-to-end**. Follow the linear
spine:

**→ [GOLDEN-PATH.md](GOLDEN-PATH.md)** — install → doctor → code → plan → plugin →
chat → switch → export.

Recovery mantra for the whole product: **`xlii doctor`**. In-session guide:
`/howto` or `/howto first-session`. Daily slash kit: `/help` (then
`/help compose` · `/help power` · `/help all`).

Everything below is the encyclopedic walkthrough once the first hour sticks.

---

## 0. What you're setting up

xlii gives you two REPLs and a curated layer underneath them:

- **`xlii code`** — a project-scoped coding agent (read/write files, run tests,
  parallel read-only workers, plan mode + the coding rail).
- **`xlii chat`** — persona-based conversation with durable, searchable memory.
  Each persona is a *portable custom agent*: instructions + memory + a tool/doc
  loadout + a pinned model.

Underneath: a user-written **plugin** library, reference **docs**, cross-persona
**refs**, an encrypted credential **vault**, and a bridge that exposes your
curated workspaces to **Grok Build** over MCP.

The thesis: **vendor provides primitives; you compose the system.**

---

## 1. Prerequisites

- **Python 3.11+**, Linux or macOS.
- An **xAI account** with a **management API key** (created in the xAI console
  under Team Settings) and at least one team you have admin access to.
- *(Optional, for the multi-machine fabric — §12)* **Tailscale** on each machine
  and an XMPP server. Skip until you want it.

You do **not** create chat API keys by hand — xlii provisions and rotates them
for you from the one management key.

### The management key — the one privileged credential

The management key can create, rotate, and revoke other API keys and manage your
collections. It is the only secret that matters, and xlii **never writes it to
disk**. Export it in your shell (add to your shell rc):

```bash
export XAI_MANAGEMENT_API_KEY=xai-...your-management-key...
```

Everything else (the chat keys xlii mints) lives in `~/.config/xlii/config.json`
at mode `0600`.

---

## 2. Install

```bash
git clone <your-repo-url> xlii
cd xlii
python3 -m venv venv
./venv/bin/pip install -e .
```

Put it on your `PATH` (optional but convenient):

```bash
sudo ln -s "$(pwd)/venv/bin/xlii" /usr/local/bin/xlii
```

Optional extras (install only if you want that feature):

```bash
pip install -e ".[mcp]"   # the Grok Build / DeepContexts MCP bridge (§11)
pip install -e ".[dev]"   # pytest + ruff + Textual ([dev] pulls [tui])
```

TUI / Textual tests need `[tui]`. `[dev]` already pulls that extra, so
`pip install -e ".[dev]"` (or equivalently `".[dev,tui]"`) is the install for
the suite including those tests.

Confirm the install is healthy at any time:

```bash
xlii doctor          # checks install + project health, prints fixes
xlii doctor --online # also tests Collection reachability
```

---

## 3. First-time setup

One command provisions everything:

```bash
xlii setup
```

It writes `~/.config/xlii/config.json` (chmod 600), auto-discovers your `team_id`,
creates **1 primary + 8 worker** chat keys via the Management API (tune with
`--workers N`), sets a 180-day expiration on each, and auto-detects the best
orchestrator and worker models. It is idempotent — safe to re-run; add `--force`
to re-provision.

Verify (always end setup with doctor — not only `status`):

```bash
xlii doctor          # install health + fixes
xlii status          # keys, team_id, models
```

You should see doctor green (or actionable fixes), management key found,
`team_id` cached, chat keys in the pool, models configured.

### (Optional) pricing for cost tracking

Add a `pricing` map to `~/.config/xlii/config.json` with USD-per-million-token
rates from your xAI dashboard:

```json
"pricing": {
  "grok-4.20-reasoning":         {"input_per_million": 5.00, "output_per_million": 15.00},
  "grok-4-1-fast-non-reasoning": {"input_per_million": 0.10, "output_per_million": 0.40}
}
```

Without it, token counts still show; cost is simply omitted (xlii never fabricates
a price). Inspect coverage anytime with `/cost` in a REPL.

---

## 4. Your first project (`xlii code`)

Inside any directory you want the agent to work in:

```bash
xlii init                 # name defaults to the directory basename
xlii init my-app          # explicit collection label
```

`init` uploads the project to an xAI Collection for RAG. A guard warns (and,
non-interactively, aborts) before bulk-uploading a home/system/oversized tree —
`--yes` skips it once you're sure. Don't want anything uploaded? Use local mode
(§10).

Open the coding REPL:

```bash
xlii code            # current directory
xlii code my-app     # a registered project by name, or a path
```

**The launch gate.** To protect on-disk state, xlii won't open a second `code`
session for a project that already has one running (it would race syncs) — it
refuses with a hint. Override deliberately with `--launch` (`-y`) or `--force`.
For quick, non-project work there are two ephemeral modes:

```bash
xlii code --preview        # open a REPL with NO .xlii, no snapshot, no sync (throwaway)
xlii code --init           # initialize a local-only .xlii here, then launch (no Collection)
```

Inside, just talk to it. Type **`/help`** for the **daily** command kit (one
screen). More surface on demand: `/help compose` · `/help power` · `/help all`.

Daily starters:

- `/plan` → read-only investigation → a numbered plan → `/execute` to run it.
- `/attach doc <name>` → durable knowledge (aliases: `/doc`; also `ref` / `bookmark`).
- `/status` → **mode · trust · surface** first, then project/attachments detail.
- `/yolo` / `/safe` → trust ladder (bash confirmation gate).
- `!<shell>` or bare shell in code → local command (no model turn when shell-primary).
- `/help power` → rail, loop, swarm, admin, and the rest when you need them.

Files on disk are the source of truth; changed files mirror to the Collection at
the end of every turn (when sync is enabled).

---

## 5. Your first persona (`xlii chat`)

A persona is a named personality with its own durable memory.

```bash
xlii chat --new ada      # creates the prompt, opens $EDITOR
xlii chat                # most-recently-used (or 'default')
xlii chat ada            # a specific persona
xlii chat --list         # list personas
```

Each persona is a Collection-backed project under the hood: recent turns load
inline as history, older turns stay RAG-searchable forever via `search_project`.
Switch mid-session with `/persona <name>`; `/edit` opens the prompt; `/forget`
wipes its transcript (with confirmation).

---

## 6. Personas as portable custom agents (the loadout)

This is the part that makes a persona more than a prompt. The top of a persona's
`.md` can carry a **frontmatter loadout** — tools, knowledge, memory links, and a
pinned model — that's applied on every chat. Open it with `/edit`:

```
---
plugins: [open-meteo, hackernews]   # subscribe these plugins (invoke via /get)
docs: [my-conventions]              # auto-attach these reference docs
refs: [research-notes]              # pull in another persona's memory
model: grok-4                       # pin this persona's orchestrator model
temperature: 0.4                    # pin its temperature
---
You are Ada, a meticulous research assistant who...
```

On `xlii chat`, the loadout reconciles into the session: the plugins get
subscribed, the docs attached, the refs pulled in, and the model/temperature
pinned — no manual `/plugin`/`/doc`/`/ref`. It's **additive** (anything you attach by
hand still sticks). `/loadout` shows declared vs. active. `xlii chat --new`
scaffolds the block (commented out) so you can see the shape.

Because the loadout lives in the file, `xlii export` / `xlii import` carries the
**whole agent** — instructions, accumulating memory, and loadout — to another
machine or another person. That's the part a vendor-hosted custom assistant can't
be: yours, portable, and it remembers.

> Model tip: reasoning models follow persona/style rules less reliably than
> non-reasoning ones. Pin `model: grok-4` for direction-following personas; save
> reasoning models for research/code personas.

---

## 6b. Switching surfaces, workspaces & marks

You don't have to leave a session to change what you're doing.

- **`/code` ↔ `/chat`** — switch between the coding surface and a chat persona
  **in place**, without restarting. Each surface keeps its **own detached thread**
  (your code conversation isn't dragged into the persona's memory, or vice-versa),
  and a project's code persona is always project-local. `/chat --id NAME` jumps
  straight to a specific persona.
- **`/workspace` (alias `/ws`)** — a workspace is a named, durable set of
  attachments (the refs/docs you've attached, plus the model/temperature). Save
  the loadout you're using now, switch tasks, and `/workspace load` it back later:
  `/workspace save api-work`, `/workspace list`, `/workspace load api-work`.
- **`/mark` + `/recall`** — tag a turn worth keeping with `/mark <name>` (add
  `--window N` to capture the surrounding span). `/marks` lists them; `/marks --all`
  browses the **cross-persona** library. `/recall <mark>` re-attaches that turn as
  a reference; `/recall <persona>:<mark>` reaches *across identities* — pull a great
  answer your research persona gave into your current coding session.

These work in both REPLs (marks/recall/workspaces are shared); `/loadout` shows
what's currently declared vs. active.

---

## 7. The knowledge layer — docs, refs, plugins

The canonical session verb is **`/attach`** (Fold A). Cost shapes stay distinct:

| Command | Cost shape |
|---------|------------|
| `/attach doc <name>` | Inlined into the system prompt every turn |
| `/attach ref <mark>` | Live pointer to a saved turn (alias: `bookmark`) |
| `/detach <name>` | Remove any of the above |

Aliases keep muscle memory: `/doc` → attach doc, `/undoc` → detach, `/recall` /
legacy `/ref` for inlining a marked turn.

### Reference docs (`/attach doc` · alias `/doc`)

Static rules/specs/conventions inlined into the system prompt.

```bash
xlii doc --new my-conventions   # opens $EDITOR
xlii doc --list
```

Then in a REPL: `/attach doc my-conventions` (or `/doc my-conventions`),
`/detach my-conventions`. Attachments are durable — they survive REPL restarts
(stored in `<project>/.xlii/session.json`).

### Refs (`/attach ref` · recall family)

Keep a marked turn as a live pointer: `/mark spark` then `/attach ref spark` /
`/detach spark` — previewable on demand, zero per-turn cost. (Attaching a whole
persona's memory is banned — personas are sealed islands; cross-persona search
is pending fabric work.)

### The media inbox (`/media`)

Files other mouths sent your persona — a phone screenshot texted to iXaac over
XMPP is persisted on the node (`~/.xlii/chat/<persona>/media/`, with a sidecar
recording caption · sender · time), carried home by `xlii fabric pull`, and
surfaced in any session:

```
/media              # newest first: name · size · age · sender · caption
/media attach 2     # drop one into the Tray — the NEXT turn's agent sees it
```

`media://` is the matching address scheme. Distinct stores, on purpose:
`artifacts://` is what xlii MADE, the Tray (`/locker`) is what THIS session
staged, `media://` is what ARRIVED.

### Plugins (`/plugin` + `/get`)

A plugin is a markdown file describing an API. Write one in a minute; subscribe to
it per project/persona; invoke by natural-language intent.

```bash
xlii plugin --new openweather   # create from template; opens $EDITOR
xlii plugin --list
xlii plugin --install-stock     # install the bundled stock plugins
xlii plugin --lint              # validate frontmatter + manifests
```

In a REPL:

```
/plugin                    # plugins subscribed in this project
/plugin all                # the whole catalog
/plugin subscribe openweather
/get the weather in Berlin # finds + invokes a matching subscribed plugin
```

If a plugin needs a credential, store it in the encrypted vault rather than a
plain env var:

```bash
xlii auth set openweather OPENWEATHER_API_KEY   # value is prompted, never echoed
xlii auth list                                  # plugins + var names (never values)
xlii auth clear openweather
```

The vault is a Fernet-encrypted file at `~/.config/xlii/vault.enc`, with the
master key in your OS keyring (passphrase fallback; `XLII_VAULT_KEY` for
headless/CI).

---

## 8. Plan mode

The lightest discipline: investigate read-only, propose a plan, then execute.

```
› /plan
[plan] › refactor the auth module to use the new token format
... agent reads files, greps, returns a numbered plan ...
[plan] › /execute        # now it has write tools and carries the plan out
```

`/cancel` drops plan mode without executing.

Approved plans are saved to `.xlii/plan-last.md` when you `/execute` (or `/execute rail`).
Use that file as the goal for the autonomous loop:

```
[plan] › /execute
› /loop --from-plan --judge tests,xai-verify --max 5
```

---

## 8b. Autonomous loop (walk away to green)

The macro-loop runs **full agent turns** in a cycle: build → test → judges → fix →
repeat until tests and judges pass or a stop rule fires. It sits *over* the normal
tool loop; you start it once and walk away.

```
› /loop "add retry to fetcher" --judge tests,anthropic --max 5 --budget 1.50
› /loop --from-plan --judge tests,xai-verify
› /loop status
› /loop resume
› /loop cancel
```

Headless (same flags):

```bash
xlii loop "fix the tests" --judge tests --max 5
xlii loop --from-plan --judge tests,anthropic --commit final
```

**Judges** are named profiles in `~/.config/xlii/config.json`, layered from cheap
to thorough — pass any combination to `--judge`:

- `tests` — the default **shell oracle** (`kind: shell`): runs your test command
  (`--test`, default `pytest -q`) and blocks on failures. Free, fast, deterministic.
- `xai-verify` / `xai-peer` — **same-vendor** cold reviewers (`kind: same_vendor`):
  a fresh Grok reads the diff/commit range and renders a PASS/FAIL verdict.
- cross-vendor profiles (e.g. `anthropic`) — **outside eyes** (`kind: cross_vendor`)
  via your `secondary_ai`/`judges` config. These add an **honesty guard**: if the
  diff weakened or deleted tests to pass, the loop alarms instead of declaring
  victory. Cross-vendor (and `--budget`) judging is the L2+ tier.

**Stop rules:** `max_cycles` (`--max`), judge `--budget` (USD cap, L2+), repeat
failure signature, the test-weakening alarm (cross-vendor), `/loop cancel`.

**Commits:** `--commit each` commits when tests pass each cycle; `--commit final`
commits once when the loop completes (default: no commits).

**Read budget:** judges may emit a `READ_REQUEST:` block; the loop fetches line
ranges once per judge per cycle (`--read-budget 3` default).

**Parallel writers (the swarm).** `--swarm N` runs N writer-workers per build
phase, each in its **own git worktree** outside the project (so they can't see or
clobber each other), then integrates them sequentially:

```bash
xlii loop "port the module to async" --swarm 3 --merge auto
xlii loop --from-plan --swarm 2 --merge llm --merge-judge anthropic
```

`--merge auto` is git-only and **fails closed** on overlapping edits; `--merge llm`
brings in a merge-agent (refereed by `--merge-judge`, a cross-vendor profile) to
resolve conflicts. The per-loop `--swarm N` is capped by the live `/swarm` ceiling
(your global concurrent-worker limit).

Persisted state: `.xlii/loop-active.json` — resume after interrupt with `/loop resume`.

---

## 9. The coding rail

Stricter than plan mode: it gates a task through six stages and locks writes **at
the tool layer** until the design stages are done — the agent physically cannot
jump to editing files before the plan is locked.

| Stage | Name | Tools |
|---|---|---|
| 0 | Requirements Lock | read-only |
| 1 | Architecture & Plan | read-only |
| 2 | Edge Cases | read-only |
| 3 | Pseudocode | read-only |
| 4 | Implementation | writes unlocked |
| 5 | Self-Review | writes unlocked |

```bash
xlii code --rail        # start on the rail
```
```
› /rail                 # or toggle mid-session
› add rate-limiting to the API gateway
[RAIL 0/5 Requirements Lock] (read-only) ...
› /rail next            # advance a stage  (/rail back to step back)
› /rail status          # show the current stage + directive
› /rail off             # leave the rail
```

A pending `/plan` can be promoted onto the rail with `/execute rail`.

---

## 9b. Styled output & the full-screen TUI

By default `xlii code` prints plainly. Opt into the **styled presentation layer**
and every action — your shell commands, the agent's tool calls, and its answer —
renders as one consistent block grammar (a dim header rule, a `⎿` gutter, a small
green/red status accent):

```bash
export XLII_SHELL_STYLE=styled    # this shell only; unset (or =raw) to revert
xlii code
```

Now a `git status` you type, a `pytest` the agent runs via `bash`, and the
answer all share one look. A couple of knobs (full list in
[REFERENCE](REFERENCE.md#environment-variables)):

- `XLII_SHELL_MAXLINES=N` — cap captured output (default 40, head+tail so the
  verdict survives; `0` = never truncate). The model always still sees the full text.
- `XLII_NO_TOOLBAR=1` — hide the bottom status bar.
- `!!cmd` — raw passthrough (inherits the terminal) for `vim`, `less`, pagers —
  the escape hatch styled capture can't drive.

### The full-screen TUI

For a spatial, app-like view, launch the optional [Textual](https://textual.textualize.io/)
front-end (install the extra once: `pip install -e ".[tui]"`):

```bash
xlii code --tui
```

You get a **framed** transcript of the same blocks:

- a **status strip** under the header — `MODE · cwd · refs/docs`, the same
  mode·cwd·attachments the inline toolbar shows; it recolors as you `/plan`,
  `/yolo`, or `/rail`;
- your **last question pinned** above the transcript, so it stays in view as a
  long answer scrolls past;
- a **work heartbeat** (a spinner + elapsed, e.g. `working 12s`) above the input
  while a turn runs, so the screen never goes dead;
- a **context meter** in the header — `used / cap · N% cached` (e.g. `102K / 1M`)
  — showing how full the model's context window is.

Type to run a shell command, `?` to ask the agent, and **`/` for any slash
command**: the moment you type `/`, a quick-reference popup lists the matches —
Up/Down to pick, Tab to complete, Enter to run, Esc to dismiss. Slash commands
have full parity with the inline `code` REPL (`/help`, `/plan`, `/ref`,
`/status`, …). `/clear` wipes the transcript; `/exit` (or Ctrl-D) leaves.

### `/tui` and the nested-session guard

To enter the TUI from a session you're **already** in, type `/tui` — it opens the
full-screen view **in the same process** with your live agent and state, then
drops back to the prompt when you exit. Prefer this over launching a second
`xlii code --tui` from inside a running session: two sessions for the same
project share on-disk state and race syncs, so xlii **refuses** a same-project
nested launch (pass `--force` to override). `/tui` is the safe way.

### The TUI in a browser tab (`xlii serve`)

**Security first, because it IS the feature's first fact: `xlii serve` has no
authentication.** Anyone who can reach the port gets the REPL, and the REPL
passes bare input to the project shell — **the port is a shell on the host**.
The default bind is `127.0.0.1`. To use it from another device, bind a
**tailnet interface address** (the same posture §13 documents for Prosody) —
never `0.0.0.0`, and never a public interface. A loud banner restates this on
every start.

With that said: because the TUI is Textual, the whole surface — transcript,
panes, dock, f-keys, task builder — can be served to a browser with zero UI
rework via [textual-serve](https://github.com/Textualize/textual-serve).
Install the extra and serve the current project:

```bash
pip install -e ".[web]"     # or: pip install 'xlii[web]'
cd ~/projects/myproj
xlii serve                  # http://127.0.0.1:8042/
```

A worked tailnet example (iPad on the couch → desktop):

```bash
# on the desktop, in the project directory — bind this machine's tailnet IPv4
xlii serve --host tailnet --expose
# on the iPad: open http://throne:8042/ in Safari/Chrome (MagicDNS name)
```

Flags: `--host` (default `127.0.0.1`), `--port` (default `8042`), `--preview`
(serves `xlii code --preview --tui` — a read-only exploration session, natural
for a shared screen).

Known limits, plainly: **one app subprocess per browser tab** — each tab is its
own session, so don't open two tabs on the same project (same reason as the
nested-session guard above: shared on-disk state races). The host machine runs
everything; the keyboard-first UX is unchanged (it *is* the TUI, in xterm.js).
Sessions don't survive the server stopping. For a richer, non-terminal web
workbench, see `proposals/browser-ui.md` — this command is deliberately the
zero-rework slice.

### The face (`xlii serve --face` · `xlii code --tauri`)

The chat-first surface: lands on your persona (`[M]` — same memory the phone
writes, fused with this project's journal + wiki). Slash in `[M]` is the
chat-safe kit (`/help`, `/reset`, `/cls`, `/tier`, … — same list as `xlii
chat`). `/plan` from talk is a gateway: it flips to `[$]` and enters plan
there — talk never self-starts a planner (research desks are not code
projects). Bare `/plan` is cold; `/plan --from-mojo` rolls recent talk
into the planner. `[$]` is the full code REPL — every slash, shell lines, modes,
with gated actions surfaced as approve/deny buttons instead of terminal
prompts. `/cls` (aliases `/clear`, `/clear-screen`) wipes the transcript;
`/reset` forgets the talk. Theme, F-keys, bold type, and chat tier stick in
`config.json` across launches. Face skins are `dark` / `light` / `slate` /
`mojo`, or a pack id (`pack:y2k` bronze cbuttons, `pack:classic` Winamp 2
visor). Drop a folder in
`~/.config/xlii/skins/<name>/` (or `xlii skin install <dir>` after `xlii skin
check`) and it appears in the picker; the selfwiki page `skin-packs` is the
authoring loop. One live session per process; reconnecting resumes it.

```bash
xlii serve --face            # prints http://127.0.0.1:<port>/?token=…
xlii code --tauri            # the same page in a native window (desktop/)
```

Local runs are loopback + token only — no pairing gate. On the public body the
SAME page serves at `/face/` behind the pairing wall (`serve --public`) and is
the granted landing after login. Set `[serve.public] face_default = false` to
land on the xterm.js TUI instead.
Uploads drag-drop into the session (the Tray in `[$]`, the next ask in `[M]`);
generated files render inline. Wire contract: `docs/ws-event-protocol.md`.

---

## 9c. Full-screen programs (vim, less, htop, …)

Shell-primary input runs your bare commands as captured subprocesses — great for
`ls`, `git status`, `pytest`, but wrong for anything that wants to *own* the
terminal. A captured `vim` or `htop` would freeze. xlii handles this with a
**curated registry** of interactive programs: when you run one, it's handed a real
TTY (inherited stdin/stdout) instead of being captured, runs full-screen, and
hands control back when you quit.

Common tools are recognized out of the box — `vim`/`nvim`, `less`/`more`, `nano`,
`htop`/`top`, `tmux`, `mc`, `ranger`, `fzf`, `lazygit`, and bare REPLs (`python`,
`node`, `irb` — matched only when launched with no script argument). Wrappers like
`sudo`, `env`, and `nice` are peeled to inspect the real program inside.

Manage the list with `/interactive`:

```
/interactive list              # show built-in defaults + your additions
/interactive add k9s           # always hand k9s a real terminal
/interactive remove k9s        # drop it again
```

User additions persist in `~/.config/xlii/interactive.txt` (one program basename
per line). For a one-off — or a program you don't want to register — prefix with
**`!!`** to force raw passthrough for that single command:

```
!!vim notes.md                 # always inherits the terminal, no registry needed
```

(The plain `!cmd` form runs at the project root and is captured under
`XLII_SHELL_STYLE=styled`; `!!cmd` is the always-raw escape hatch.)

---

## 9d. Gitpanel — stash with a message, journal-aware commits

The **Gitpanel** (Panel menu → Git, or Alt-G) is xlii's worktree cockpit — not a second
porcelain git. Footer actions **seed `/gitpain …` on the command line** (review-before-run);
nothing mutates the repo until you press Enter. For full git (rebase, reflog, every flag),
type `git` in the shell.

**Stash with a comment** — the habit gitpain enforces:

```
/gitpain stash pause: auth race investigation
/gitpain stash -m "explicit -m works too"
/gitpain stash journal              # draft the message from journal + diff
```

The panel **Stash…** action claims the input line for your one-line message (required).
**Stash incl. untracked…** adds `-u`. Stashes appear in a **Stashes** section (`stash@{n}`
+ your message); select one for pop/apply/drop prefills.

**Commit drafts** that can see session context (code REPL + project journal only):

```
/gitpain commit summary             # AI from staged/unstaged diff
/gitpain commit journal             # diff + journal summary + optional loop goal
```

After review, Enter runs the commit. With the code journal recording, a post-commit line
may appear: `committed <hash> — <subject>`.

**Sweep** after a merge round — one verb instead of a manual ritual:

```
/gitpain sweep
```

Lists merged local branches, stale worktrees, and merged remotes, then seeds the exact
cleanup commands on the command line for review.

`/git` still works as a deprecated alias; prefer `/gitpain` and the Gitpanel.

---

## 10. Local-only mode + snapshots (the "no upload" path)

For directories whose contents you don't want uploaded — NAS, media libraries,
PDFs, archives, anything binary or private:

```bash
xlii init --local              # no Collection, no upload, no sync
xlii init --local --snapshot   # also cache a paths+sizes index for fast structural search
```

`search_project` then runs against a local FTS index instead of the cloud
Collection. For a one-off in a throwaway dir, `xlii scratch` spins up an ephemeral
local-only project under `~/.xlii/scratch/` and drops you into chat.

---

## 11. The Grok Build bridge (DeepContexts over MCP)

A **DeepContext** is a portable snapshot of an xlii workspace (attachments + any
custom project tools) that an external agent — notably **Grok Build** — can attach
as long-term memory and capabilities, modify, and sync back.

In a REPL, curate one with `/context save <name>` (`/context list` to see them).
Then expose your contexts over an MCP server so Grok Build can attach them:

```bash
pip install -e ".[mcp]"     # one-time: the MCP extra
xlii mcp deep-contexts      # stdio MCP server (point Grok Build at it)
```

It surfaces resources (`xlii://contexts`, `xlii://contexts/{name}`) and tools
(`xlii_list_contexts`, `xlii_load_context`, `xlii_get_live_context`,
`xlii_sync_context` for write-back, and `xlii_call_project_tool`).

---

## 12. Headless: `xlii ask` for scripts and automation

Run a single agent turn and capture just the reply — no REPL:

```bash
xlii ask "summarize today's changes in this repo"
xlii ask --workspace my-app "what does the auth module expose?"
xlii ask --yolo "run the tests and tell me what failed"   # auto-approve bash for trusted, non-interactive use
```

Only the final reply goes to **stdout** (the live UI streams to stderr), so it
pipes cleanly. It's stateless by default — nothing is persisted or synced. This
is also the agent-fallback the multi-machine daemon (§13) calls.

**Multi-turn scripting (`--session`).** Pass a caller-chosen id to make
consecutive calls one continuing conversation — the last turns seed the next
turn's context, and each new turn is written back under the project's
`.xlii/ask-sessions/`:

```bash
xlii ask --session standup "what changed in this repo since yesterday?"
xlii ask --session standup "and which of those touched the auth module?"   # remembers
xlii ask --session standup --new-session "fresh topic: release notes"      # resets first
```

Ids are free-form (pick anything stable: a cron job name, a hook name). The
daemon (§13) derives one per sender — `xmpp:<your-jid>[:<workspace>]` — so a
phone conversation keeps its thread per workspace, and different whitelisted
JIDs never share context. Session state is bounded (last ~20 turns seed the
context) and lives per-project; it never syncs anywhere on its own.

---

## 13. The multi-machine fabric (XMPP/OMEMO) — prerequisites & status

> **Status: experimental — trusted tailnets only.** Both halves ship today behind
> the optional `[daemon]` extra (`pip install "xlii[daemon]"`): the `xlii ask`
> one-shot and the always-on inbound **daemon** (`xlii daemon`). It's hardened but
> still experimental, so run it only on a private network, never a public
> interface. Hardening that has landed — atomic OMEMO state (a crash can't corrupt
> the crypto ledger),
> reconnect-on-drop (a wifi blip no longer kills it), and **device trust is
> secure-by-default**: pair a phone with `xlii pair` (scan the QR, send the
> one-time code). `xlii daemon trust <jid> <fingerprint>` remains the manual
> fallback. It runs **arbitrary commands
> from inbound messages**, so run it only on a trusted tailnet, never a public
> interface — and `xlii daemon` prints that warning every time it starts.

**Fork in the road.** Phone on your tailnet? Use the **tailnet door** — five
minutes, no Prosody. Off-tailnet, or you want message-layer E2EE independent of
the network? The XMPP path below is unchanged. `xlii notify` (send-only) is
untouched either way.

### Phone on your tailnet (recommended)

The Face sitting is the gate. The tailnet door is WhoIs identity on a live HTTP
POST — there is no mailbox, so a message planted while Face is down cannot fire
later (the car-bomb class XMPP has to police).

```bash
# both machines: follow tailscale.com/download, then:
tailscale up
```

On the desk:

```toml
# ~/.config/xlii/face.toml
[tailnet]
allowed_devices = ["phone"]   # MagicDNS name or `tailscale status` hostname
```

```bash
xlii serve --face --host tailnet --expose
# or leave Face on loopback (xlii code --tauri): /remote-control open
# brings the tailnet /remote-turn listener up, and lock/drop/idle tear it down
```

From the phone (same tailnet), with the sitting open:

```bash
curl -sS -X POST http://throne:PORT/remote-turn \
  -H 'content-type: application/json' \
  -d '{"text":"what is this project?"}'
```

No `?token=` on this door. HTTPS on the MagicDNS name: `tailscale serve`. Bind
`--host tailnet` still requires `--expose` (conscious choice). `100.x` literals
are a last resort — prefer the name `throne` (or whatever `tailscale status`
prints).

`/remote-control open --for phone` binds the sitting to that one node.
`/remote-control open --glass --for phone` serves the desk's own HTML face
on that listener (WhoIs on every request). The phone spends a webcode into
a grant cookie; drop tears the port down. Bare `open` stays door-only
(`POST /remote-turn`).

**Phone ladder** — one brain, three rungs. Pick by where the phone is:

| Rung | When | What you get | Gates |
|------|------|----------------|-------|
| XMPP DM | anywhere | text in, text out | daemon whitelist + OMEMO |
| Tailnet glass | on the tailnet | the desk's own face (thumb posture) | sitting `--glass`, WhoIs `--for`, webcode grant |
| Public thin page | off-net browser | chat + wizards on the VM | `serve --public` pairing — still [mobile-face.md](../proposals/mobile-face.md) |

Glass in under five minutes on an already-paired device: DM `webcode` (or
mint from the desk) → open the MagicDNS URL the Face printed → enter the
code → talk. Thumb **mark** drops a bookmark the desk can `/attach bookmark`;
thumb **lock** hangs up the sitting.

### Off-tailnet / message-layer E2EE (XMPP, as before)

The hard part is the infrastructure, and you can set it up now — it's independent
of xlii. The goal: your phone (at work) sends an encrypted DM that your desktop
(at home) acts on, over your private tailnet.

**a) Tailscale** on both machines, so they share a private network without opening
any public ports:

```bash
# follow tailscale.com/download for your OS, then:
tailscale up
```

**b) An XMPP server (Prosody)** on the desktop, bound to the **tailnet** interface
only (never the public one). Roughly:

```bash
sudo apt install prosody        # or your distro's package
# in /etc/prosody/prosody.cfg.lua: set interfaces to the tailnet IP from (a),
# enable mod_omemo_all_access / PEP, and create two accounts:
sudo prosodyctl adduser you@desktop.tailnet      # your phone logs in as this
sudo prosodyctl adduser daemon@desktop.tailnet   # the daemon's own identity
```

Two **separate JIDs** matter: the daemon and your phone are different OMEMO
identities and must not share crypto state.

**c) An OMEMO client on your phone** — [Conversations](https://conversations.im)
(Android) is the reference. Add the `you@desktop.tailnet` account (server =
desktop's tailnet IP), verify OMEMO keys, and you can message the daemon account
once it's running.

With (a)–(c) in place you have everything the fabric needs.

### Send-only notifications today (`xlii notify`)

The lowest-risk half ships now: **outbound, no inbound channel.** With the
`[daemon]` extra installed and a `~/.config/xlii/notify.toml` (a `[notify]`
section with your sender `jid`, the `recipient` JID = your phone, and a
`password_env`), push an OMEMO-encrypted message to your phone:

```bash
xlii notify "build finished on the desktop"
```

Wire it into a hook so a long `xlii code` turn buzzes your phone when it's done —
copy the example and make it executable:

```bash
cp examples/hooks/post-turn/notify-on-work.py .xlii/hooks/post-turn/
chmod +x .xlii/hooks/post-turn/notify-on-work.py
```

(The inbound command **daemon** that *acts* on messages from your phone is the
experimental, still-hardening piece below — `xlii notify` carries no such risk.)

### Panic mail (no keyboard)

Email is **not** chat. It is a deny door: kill this daemon, or destroy this
install, when you cannot type. Silent drop if any gate misses (no bounce).

```toml
# daemon.toml
[panic]
from = ["you@your-mail.example"]
```

```text
xlii daemon panic-phrases --set "phrase one" "phrase two" "phrase three"
```

Subject must be one of those three. Body `kill` stops the daemon (and masks
systemd restart). Body `destroy throne` / `destroy all` asks for round 2
(TOTP on a fob that is not the stolen phone). Face and `xlii daemon` check
unseen mail **on wake**. A laptop that never runs xlii again is full-disk
encryption's job — we do not unsay GitHub.

### Running the command daemon (`xlii daemon`)

> **This runs arbitrary commands from inbound messages.** Only ever run it on a
> trusted tailnet, never a public interface. `xlii daemon` prints that warning on
> every start. Treat the whitelist + OMEMO trust as the security boundary.

**1. Write `~/.config/xlii/daemon.toml`.** Only `[daemon].jid` and
`[whitelist].allowed_jids` are required; everything else has secure defaults:

```toml
[daemon]
jid = "node1@desktop.tailnet"         # this body's own account from §13(b). Give
                                      # EACH machine its own JID (throne@…, node1@…)
                                      # so your phone's roster shows which body it is
node_name = "node1"                   # this body's fabric name (throne, node1, …):
                                      # set as XMPP presence status + answered by
                                      # /whoami, and the provenance tag for `fabric pull`
password_env = "XMPP_DAEMON_PASSWORD" # env var holding its password (default)
# state_file / verbs_dir / audit_log default under ~/.config/xlii + ~/.local/share
progress_after_s = 0                  # >0: ONE "still working…" message if a
                                      # dispatch runs longer than N seconds
                                      # (a typing indicator shows regardless)

[whitelist]
allowed_jids = ["you@desktop.tailnet"]  # only these JIDs are answered; all else
                                        # is silently dropped (empty ⇒ refuse to start)

[rate_limit]                  # defaults shown; per-JID sliding window + lockout
max_per_minute = 10
lockout_threshold = 5
lockout_duration_s = 300

[agent_fallback]
enabled = true                # unknown command → one-shot `xlii ask`
default_workspace = ""        # "" = most-recently-active project; or a project alias
```

**2. Export the password** (never put it in the file):

```bash
export XMPP_DAEMON_PASSWORD='…'   # the password you set with prosodyctl adduser
```

**3. Pair your phone's OMEMO device (one time).** Start the daemon once so it
mints its own identity, then on the desk:

```bash
xlii daemon                 # leave it running
xlii pair                   # QR + grouped code; Ctrl-C cancels the window
```

Scan the QR with Conversations (that adds the daemon JID and pre-verifies the
desk's fingerprint). Send the grouped code as your first OMEMO message. The
desk pins that phone device `TRUSTED`, the window closes, and you never restart
or edit the config. `--invite you@new.account` also appends that JID to
`allowed_jids` on grant. `xlii daemon trust <jid> <fingerprint>` is the manual
fallback if you already have a fingerprint in hand.

### Naming each body & pulling its memory to the throne (`xlii fabric`)

You are one identity; your **laptop is the throne** (it holds the management key —
the center) and every other machine is just another **body** of the same persona.
Give each body a distinct JID and a `node_name` (above) so texting `throne@…` vs
`node1@…` from your phone shows *which* body is answering — and send `/whoami` to
any of them to hear it (`[node] node1 · persona: ixaac · node1@…`).

A keyless body accrues its persona turns locally (it has no management key, so its
memory never leaves the box). To bring that memory home, the throne **pulls** it and
archives it to the shared Collection — after which every surface recalls it. Point
the throne at a body via an existing remote-fs connection (`xlii remote add`, §
sftp) and register it:

```bash
xlii remote add node1 --protocol sftp --host node1.tailnet --user xlii   # once
xlii fabric add-node node1 --remote node1 --persona mojo                 # roster it
xlii fabric nodes                                                        # list
xlii fabric pull --node node1        # drain node1's turns → throne → Collection
xlii fabric pull                     # all nodes; add --dry-run to preview
xlii fabric new node1 lab-app        # folder on that node; Files here
xlii fabric sync-projects            # one project menu on every Face
```

`fabric new` makes the folder **on that node** and points Files here at it
(`sftp://…`). No xAI Collection. Work it remote, same as the VM driving an
app box. On this desk, **Sync to cloud** / `/sync` is opt-in searchable
memory — only if this box has the management key.
`sync-projects` merges node registries into one Projects panel (a section
per node). Home never travels.
Face: Project → **New on node1…** / **Sync fabric projects…**.
REPL: `/project remote <node> <name>` · `/project sync-fabric`.

Pulls are **idempotent** (a turn already pulled is skipped) and provenance is kept
in each pulled turn's filename (`…-node1-…`). Opening chat or talking to mojo on
the throne also pulls when the interval has elapsed (`fabric_pull_interval_s`,
default 15 minutes; `0` = off). `xlii fabric pull` is the force-now and resets
that clock. Recalled turns carry the body: inline history is tagged `[via node1]`,
search hits say `(via node1)`. Run `pull` on the throne — a body without the
management key ingests the turns locally but can't archive them. *(This is the
SFTP transport; an XMPP-native pull over the OMEMO link is the planned durable
path — see the fabric proposal.)*

**4. Run it** (foreground; wrap in systemd/tmux for always-on):

```bash
xlii daemon                       # uses ~/.config/xlii/daemon.toml
xlii daemon --config /path/to/other.toml
```

**5. Drive it from your phone.** Send an OMEMO-encrypted DM to
`daemon@desktop.tailnet` from your whitelisted account:

- `kill` — the one built-in verb; the daemon replies "shutting down" and exits
  cleanly. (Note `kill` only works as the *first word* and *unprefixed* — `[proj]
  kill` is treated as a prompt, so the shutdown switch can't be smuggled behind a
  workspace prefix.)
- **A custom verb** — drop an executable script at
  `~/.config/xlii/verbs/<name>.sh`; messaging `<name> arg1 arg2` runs it and the
  script's stdout becomes the encrypted reply. E.g. a `status.sh` that echoes
  `uptime` lets you text "status" and get the box's load back.
- **Anything else** falls through to the agent: "grep me the auth module" runs
  `xlii ask` in the default workspace and texts you the answer. Prefix with
  `[alias]` to target a specific project: `[isaac2] where's the rate limiter?`
  The fallback is a **continuing conversation per sender and workspace** (X1):
  the daemon derives a session id `xmpp:<your-jid>[:<workspace>]`, so a
  follow-up like "and where is it called?" has the context of your last
  messages. Different whitelisted JIDs never share a conversation. Long answers
  arrive **chunked** as ` (1/3) (2/3) …` messages instead of being cut off.
- **A photo or file** — send an image or PDF (with or without a caption) and the
  daemon fetches it (decrypting the OMEMO `aesgcm://` link), then hands it to the
  agent turn so iXaac actually *sees* it: "what's wrong with this part?" over a
  photo, "summarize this" over a PDF. Images need a vision-capable model; the file
  is fetched to a temp path, shown to the turn, then deleted. (This is the
  headless `xlii ask --attach <path>` seam, which scripts can use too.)
- **A voice note** — hold the mic and talk. The audio file rides the same
  encrypted path as a photo, is transcribed via the xAI speech-to-text API
  (OGG/Opus/M4A straight from the phone — no transcoding), and the transcript
  *becomes your message*: speaking to iXaac is the same as texting it, and the
  transcript (not a placeholder) is what accrues to its memory. A voice note
  that fails to transcribe stays attached and the turn still runs.
- **Files come back too** (media-out). Every agent turn gets a delivery
  channel: the daemon grants the turn an *outbox* (`xlii ask --outbox`), the
  agent's `send_file` tool queues files into it, and after the turn the daemon
  encrypts each one, uploads it to the server's file share (the same 443 path
  your photos ride in on), and sends the `aesgcm://` link — your client renders
  it inline. So *"make me a sign that says SHOP OPEN, send it as an image"*
  works end-to-end: the persona surface may generate images exactly when it has
  a mouth to deliver them, and the render lands in your chat. A file that fails
  to deliver is reported as a message; the text reply always still arrives.

**6. (Optional) Lock destructive commands behind a second factor.** OMEMO +
device pinning is strong, but "encrypted ≠ fully trusted" — a *borrowed unlocked
phone* or a hijacked session could still fire a destructive verb. So the
daemon can require a **TOTP** (the 6-digit code from an authenticator app) before
it runs `webcode` (which grants a shell on the terminal), `kill`, or phone
`/remote-control`. The code comes from a **separate** app, so a compromised
chat device alone can't destroy anything. Face `/remote-control open` skips
`/xsu` — you're at the glass. Same 5-minute idle either way.

```bash
xlii daemon totp                       # prints a base32 secret + an otpauth:// URI
# scan the URI into Google Authenticator / Aegis / 1Password, then:
export XLII_DAEMON_TOTP_SECRET='THE-BASE32-SECRET'   # env-only; add to a 0600 env file
xlii daemon                            # restart — the gate is now armed
```

Once armed, elevate from your phone with the **hidden** command — it is
deliberately **never listed in any help or reply**, documented only here, so a
nuisance bot poking the daemon never learns it exists:

```
/xsu 748291        → "[daemon] elevated."   (idle 5 min; inbound from you refreshes)
/webcode           → now mints the code     (unelevated → "elevation required")
/remote-control    → opens sitting ($ lab)  (Face-open sitting skips /xsu)
```

The elevation is spent on one destructive act (`kill` / `webcode` /
phone-minted `/remote-control`), or lapses after 5 minutes idle. Fumble it
and the daemon protects itself: a 2nd bad code in a minute is a grace ("one more
attempt"), a **3rd locks the whole daemon** — it refuses everything until you
`ssh` in and `systemctl restart xlii-daemon` (the lock is in-memory, so the
restart is the recovery). With no secret set, the gate is off and `kill`/`webcode`
keep their existing admin-tier gate — nothing changes until you opt in.

Every inbound message — accepted, rejected, rate-limited, or dispatched — is
appended to `~/.local/share/xlii/daemon-audit.log` (own-only perms; plaintext
bodies land here, so treat it as sensitive).

**The headline loop, end to end:** phone (at work) texts `[myproj] add a retry to
the fetch helper` → desktop (at home) runs the agent in that project → texts back
the diff summary. Same fabric, `kill` to stop it.

---

## 13b. Semantic memory, the address space & publishing

Three surfaces that grew up after the early walkthrough — each has a CLI twin
(for scripts) and a slash-command twin (inside a session).

**The project wiki** is durable, human-curated semantic memory with a
verified/unverified trust split — the opposite of the auto-synced RAG Collection.

```bash
xlii wiki new architecture      # a page, born unverified
xlii wiki list                  # pages + trust markers
xlii wiki verify architecture   # promote to verified (manual)
```

Inside a session, `/wiki distill <name> <addr>…` drafts a page from sources and
`/wiki verify <name> --promote` runs the AI verification pass.

**The journal** (Project Shadow) is a silent background recorder — it passively
captures how the project was actually worked, so you can ask about it later:

```bash
xlii journal install            # opt in to bash-wide capture (one ~/.bashrc block)
xlii journal serve              # run the summarizer daemon (detached)
```

Then `/journal` shows status and toggles the code/wiki journals, and
`/mojo "why did we switch to SQLite?"` answers from what actually happened —
Mojo replies with its own memory fused with this project's journal + wiki. (The
old `/askjo` still works as a hidden alias.)

**The address space** unifies local files, conversation turns, and remote hosts
under one set of verbs, so the same commands work across roots:

```bash
xlii ls .                       # browse the local root
xlii ls conv://.                # browse the current conversation
xlii cat conv://./turn.md       # read a turn
xlii cp conv://./turn.md ./keep.md   # copy across roots
```

The same seven verbs work across every root: `xlii ls` · `xlii cat` · `xlii stat`
to read, `xlii cp` · `xlii mv` · `xlii mkdir` to write, and `xlii rm` (guarded by
`--yes`) to remove.

**Publishing to a remote host** (FTP/FTPS/SFTP) rides the same space. Add a
connection once (its secret goes to the encrypted vault), then mirror a directory
into a docroot:

```bash
xlii ftp add box --host example.com --user me --protocol sftp
xlii ftp test box               # connect + list the login home
xlii ftp publish ./site box:/var/www/html   # skips size-unchanged files
```

Inside a session the twin is `/ftp` (`/ftp connect box`, `/ftp publish …`). See
the [REFERENCE](REFERENCE.md#the-virtual-filesystem-address-space) for every
address form and flag.

---

## 13c. Instant apps — build & publish in one gesture (`xlii make`)

If you have an apps host set up (a keyless box serving static sites, reached over
an `xlii remote` connection named `appbox`), `xlii make` turns a description into
a **live URL** in one step: it scaffolds a throwaway local project, has iXaac
build a self-contained static app into it, then publishes it so it serves at
`https://<name>.<domain>`.

```bash
xlii make calc "a tip calculator with a dark theme"
#   scaffolds ~/serve-sandbox/calc  (a local-only project)
#   iXaac builds a self-contained index.html into it (build output on stderr)
#   publishes it → sftp://appbox/srv/apps/calc.xlii-code.com/
#   prints:  https://calc.xlii-code.com     ← only the URL goes to stdout
```

The app name is a DNS label — it's the subdomain **and** the folder. Rerun `make`
with the same name to rebuild and redeploy (stale files are pruned). Useful flags:
`--no-publish` (build locally only, prints the path), `--domain` / `--conn` /
`--root` (override the apps host defaults), `--dir` (where local app folders
live), `--no-yolo` (approve bash during the build instead of the hands-off
default). Because stdout is just the URL, `make` scripts cleanly — a phone-side
`make.sh` daemon verb can wrap it and text you back the link.

The build follows the `app-operator` house rules (dependency-free HTML/CSS/JS,
responsive, dark) and runs on the inference key alone — no management key needed,
since a static build touches no Collection.

---

## 14. Maintenance

```bash
xlii keys list                 # chat keys + days-to-expiration
xlii keys rotate               # new secret, same key id
xlii keys revoke --prefix worker --yes
xlii models list               # models the team can access
xlii models recommended        # heuristic best-of-class picks
xlii models set --orchestrator grok-4 --worker grok-4-fast
xlii projects                  # every registered project
xlii gc --dry-run              # find orphan Collections (deleted projects); drop --dry-run to delete
xlii export ./backup           # personas, docs, plugins, registry (secrets excluded)
xlii import ./backup           # restore on another machine
xlii doctor                    # health check + fixes
```

`xlii config` writes a fresh config template if you ever need one.

---

## 15. Uninstall / reset

```bash
rm -rf ~/.config/xlii/         # config, vault, personas, docs, plugins
rm -rf ~/.xlii/                # chat state, scratch projects, transcripts
rm -rf .xlii/                  # per-project state (run in each project dir)
xlii bootstrap --revoke --prefix worker --yes   # revoke worker keys server-side
xlii bootstrap --revoke --prefix primary --yes
# delete orphaned Collections via the xAI dashboard, or `xlii gc`
```

---

For the full command/flag reference see the [GUIDE](GUIDE.md); for architecture
notes see [REFERENCE](REFERENCE.md). For the phased plan see
[`ROADMAP.md`](../ROADMAP.md). Historical design proposals are archived out of
the public tree.
