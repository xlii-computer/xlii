# xlii

```
          .__  .__.__
   ___  __|  | |__|__|      xlii · a personal AI substrate
   \  \/  /  | |  |  |
    >    <|  |_|  |  |       Your files and curation first.
   /__/\_ \____/__|__|       Remote primitives second.
         \/

   make /code · /plan · /rail      agentic edits, gated
   ask  /chat · /consult · /peer   persona · model · peer
   run  /loop · /tasks · /jobs     set it going, walk away
   keep /wiki · /bookmarks · /nfo  curate; /nfo redraws this

   /help  ·  /howto   →  start here
```

## Install

```bash
pip install xlii
# Optional feature sets:
pip install 'xlii[tui]'     # full-screen Textual UI (xlii code --tui)
pip install 'xlii[web]'     # the TUI in a browser tab (xlii serve — NO AUTH on loopback/tailnet; --public = pairing-code gate behind Caddy)
pip install 'xlii[mcp]'     # Grok Build bridge (xlii mcp deep-contexts)
pip install 'xlii[files]'   # PDF + drag-and-drop support for /upload
pip install 'xlii[remote]'  # SFTP for ftp:// remotes
pip install 'xlii[daemon]'  # experimental XMPP multi-machine (slixmpp-omemo)
# From a checkout, TUI/Textual tests need [tui]. [dev] already pulls it:
#   pip install -e '.[dev]'          # pytest + ruff + Textual
#   pip install -e '.[dev,tui]'      # equivalent
```

Requires Python >= 3.11.

## First run

1. Get a management API key from xAI.
2. Export it (never stored on disk):

   ```bash
   export XAI_MANAGEMENT_API_KEY=xai-...
   ```

3. Run setup (creates `~/.config/xlii/config.json` template + provisions chat keys + discovers models):

   ```bash
   xlii setup
   ```

4. **`xlii doctor`** — recovery mantra; fix anything it flags before continuing.

**Two places, one talk seat, two postures.** Home is the building (roam the
machine, never-sync). A **folder you make** is a room — that’s a project. `[M]`
talks to **mojo** (the journal; it walks with you). `[$]` is the lab (`/sh`,
explain, `/ops`, edits) — that memory stays in the room. `xlii chat` sits
with **iXaac** (a costume, not a `/role`), not a third home.

First hour: [docs/GOLDEN-PATH.md](docs/GOLDEN-PATH.md) — doctor → Home → enter a
folder → plan → plugin. Full walkthrough: [docs/HOWTO.md](docs/HOWTO.md).

**Architecture (what’s true now):** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
**Legacy names:** [docs/LEGACY.md](docs/LEGACY.md)

## Quick start

```bash
# Home — the desk. Never-sync. No project required.
xlii scratch              # ephemeral session from $HOME
xlii scratch --tui        # same, full-screen TUI
xlii scratch --tauri      # the desktop face (throne)

# Enter a folder when you mean a real project
xlii init --local         # this directory becomes a room
xlii code                 # lab in that folder (prompts to init if needed)
xlii code . --tui

# Talk-only (iXaac without the desk — not a third home)
xlii chat                 # chat costume (iXaac). Jobs are /role
xlii chat --new ada       # another island (chat/ada), not another Home
```

Inside a session:

- `[M]` / talk — mojo. `[$]` / lab — shell; `?` asks the coder (not mojo).
- On the TUI/REPL, bare input in the lab is the shell (`ls`, `cd`, `pytest`).
- `/help`, `/commands`, `/tools` for discovery.
- `/exit` or `/quit` to leave.

Home stays never-sync. A folder gets a Collection only when you `xlii init`
(without `--local`). Other personas live in `chat/<name>` rooms.

## The REPL model

The primary surface is deliberately a **shell-first REPL**:

| Input prefix | Meaning |
|--------------|---------|
| (none)       | Shell command (passthrough, full TTY for vim, less, etc.) |
| `?text`      | Send to the agent as a task |
| `!cmd`       | One-off shell escape (styled capture when enabled) |
| `!!cmd`      | Raw inherited-TTY shell escape |
| `/<cmd> [args]` | Slash command (see below) |

History, project memory, and attachments persist across restarts for the same project/persona.

## Core concepts

### Projects

- Any directory can be an xlii project: `xlii init` or `xlii code --init` (or the launch gate).
- State lives in `.xlii/` (project.json, turns/, repl_history, session.json, manifest, wiki/, journal/, inbox/, etc.). Local disk is source of truth.
- `xlii sync` (or auto) mirrors files + metadata into an xAI Collection. `search_project` gives the agent hybrid RAG.
- `--local` / local-only projects skip Collections entirely (great for private trees).
- `.xliiignore` (pathspec) and extra ignores in project.json control what gets indexed.

### Sessions & profiles

- **Home** is not a project. Lab on the desk is `/sh` · explain · `/ops` (the
  machine is the job). It is not a second journal.
- **A folder**: lab memory and RAG live in that room’s `.xlii/turns`.
- **mojo**: talk memory (`~/.xlii/chat/mojo/`). Walks into folders; ambient
  there is that folder’s wiki/journal, not a new persona.
- Other personas are other rooms (`chat/<name>`), not other Homes.
- Episodes: `xlii code --keep-session` snapshots the full lab conversation
  in a folder. `/session off` clears it.

### Knowledge you own (plain files)

Everything that makes the AI yours is editable text:

- **Personas** — `~/.xlii/personas/<name>.md` (frontmatter + system prompt). `/persona`, `/chat`.
- **Docs** — reference material attached with `/doc` or via loadouts. Managed with `xlii doc`.
- **Plugins** — declarative HTTP API descriptors (markdown). Invoke via `/get` or agent tool `plugin_get`. `xlii plugin`, stock plugins included.
- **Roles** — packaged behavior (e.g. `code-architect`). `xlii role`.
- **Skills** — reusable methods (often using parallel subagents). E.g. `grounded-analysis`.
- **Project commands** — add `/<name>` commands by dropping `.xlii/commands.py` or `.xlii/commands/`.
- **Custom agent tools** — `.xlii/tools.py` exporting via `get_tools()`.

### Agent & tools

The orchestrator agent (Grok by default) loops with tool use until it stops. Core built-in tools include:

- File: `read_file`, `write_file`, `edit_file`
- Navigation: `list_dir`, `glob`, `grep`
- Execution: `bash` (requires honest `intent`: read-only / modifies-project / modifies-system / network)
- Search: `search_project` (RAG), `web_search`, `x_search`
- Plugins: `plugin_search`, `plugin_get`, `plugin_call`
- Subagents: `dispatch_subagent` (parallel workers with restricted tools; used by roles/skills/loops)
- Others: `code_execute`, etc.

Tool gating, auto-approve, yolo mode, and per-intent confirmation protect the user.

### Safety & workflow surfaces

- **Rail** (`--rail`, `/rail`): 6-stage gated flow (Requirements → Architecture → Edge Cases → Pseudocode → Implementation → Self-Review). Writes locked until late stages.
- **Plan mode** (`/plan`): investigation only; produces numbered plan. `/execute` to act.
- **Discovery** (`--discovery`): read-only research & explanation.
- **Ops** (`--ops`): OS diagnostics with platform-correct probes.
- **Loop** (`xlii loop`, `/loop`): autonomous build→test→fix until judge green. Multiple judges (shell tests, LLM, CI), optional parallel writers in git worktrees, collusion detection, merge strategies.
- **Verify / peer / consult**: cold-context verification, blind review, cross-vendor second opinion.
- Workers are strictly read-only.

### Other surfaces

- **Wiki** (`xlii wiki`, `/wiki`): per-project semantic memory pages (list/show/new/verify/rm). Sources tracked.
- **Journal** (`/journal`): project shadow + per-persona journals. Can archive to dedicated Collections.
- **Tasks** (`/tasks`): pipeline workflow.
- **Jobs** (`/jobs`): background job surface.
- **VFS** (`xlii ls/cat/...`, `conv://...` addresses): uniform addressing for conversations, turns, etc.
- **Remote FS** (`xlii ftp`, `ftp://` provider in tools).
- **Artifacts** (`xlii artifact image`): xAI Imagine generation.
- **MCP** (`xlii mcp deep-contexts`): expose project/workspace to external tools (Grok Build TUI integration).
- **Inbox** (`xlii serve-inbox`): webhook → `.xlii/inbox/` for external triggers.
- **Web** (`xlii serve`): the full TUI in a browser tab via textual-serve — one session per tab; NO AUTH on localhost/tailnet (the port is a shell). `xlii serve --public` keeps the bind on loopback and puts a pairing-code gate + Caddy TLS in front — see [GUIDE · Public serve posture](docs/GUIDE.md#public-serve-posture-xlii-serve---public).
- **Export/Import**: move your entire curated world (personas + memory + docs + plugins) between machines without secrets.
- **TUI**: optional full-screen experience with transcript, panes (explorer, git, tasks, wiki, image, dock, etc.), input chrome, themes, image preview (sixel/kitty/iterm).

## CLI commands (selected)

Top-level verbs (many also have subcommands):

**Project lifecycle**
- `init`, `new`, `projects`, `find`, `sync`, `status`, `gc`, `project rm`
- `scratch` (ephemeral scratch space)

**Sessions**
- `code [target] [--tui] [--rail] [--discovery] [--ops] [--preview] [--init]`
- `chat [name] [--tui] [--new] [--list] [--edit]`
- `ask <prompt>` (one-shot headless)
- `loop <goal>` (autonomous; `--status`, `--resume`, judges, swarm, etc.)

**Knowledge & content**
- `plugin`, `doc`, `export`, `import`
- `role`
- `wiki`

**VFS & remote**
- `ls`, `cat`, `cp`, `mv`, `rm`, `mkdir`, `stat` (operate on `conv://`, `file://`, project paths, etc.)
- `ftp add|list|rm|test|publish` (FTP/FTPS/SFTP remotes)

**Other**
- `account`, `artifact image`
- `setup`, `config`, `bootstrap`, `keys`, `models`, `auth`
- `journal`, `notify`, `daemon`, `mcp`, `serve-inbox`
- `help`, `doctor`

Inside the REPL there are dozens more: `/attach`, `/browse`, `/checkpoint`, `/compact`, `/consult`, `/delegate`, `/edithere`, `/file-tab`, `/git`, `/howto`, `/imagine`, `/jobs`, `/loadout`, `/locker`, `/plan`, `/nfo`, `/replay`, `/verify`, `/scratch`, `/send`, `/skill`, `/code`, `/tasks`, `/theme`, and many more. Use `/commands` and `/apropos` to explore.

## Extensibility highlights

- **Project commands**: `.xlii/commands.py` (or `.xlii/commands/*.py`) → user `/<name>` slash commands.
- **Agent tools**: `.xlii/tools.py` exporting `get_tools()` → new tools the agent can call (parallel to builtins).
- Skills, roles, personas, and plugins are all versionable markdown + YAML frontmatter.
- Plugins require no running servers for typical HTTP cases.
- Hooks system, MCP servers, and full `xlii.*` Python API for advanced use.
- Built-in harnesses (`xlii/harness/`) for running the same task across agents (Grok Build, Claude, Cursor, etc.) for comparison.
- Ships with stock plugins, roles, a grounded-analysis skill, and mojo (the mobile journal). The CLI/substrate is xlii.

See `examples/`, `stock_*/*`, and the `.xlii/` dir created on init. The in-session `/commands reload` and docgen surfaces make iteration fast.

## Configuration & environment

- Global: `~/.config/xlii/config.json` (or `XLII_CONFIG_DIR`)
- Per-project: `.xlii/project.json`, turns/, session state, repl_history, manifest, etc.
- Management key is **env-only** for safety.
- Many `XLII_*` overrides (see `xlii doctor` or reference material in `docs/`).
- Prompt cache uses stable per-project conversation IDs.

## Status

Alpha (as of 2026). End-to-end functional against real xAI accounts. Architecture largely stable; surfaces and details continue to evolve. Extensive test suite lives in `tests/`. Historical design proposals are archived out of the public tree.

`xlii --version`, `xlii help`, `xlii doctor`, and in-session `/help` are your friends.

Want to help after cloning the public tree? Email [join@xlii.computer](mailto:join@xlii.computer) (interest only — not a help desk).

BTC donations (optional): [`bc1qvkc756tdgqsnwsxn8cmrkhk7hz5l622mnhmchr`](https://mempool.space/address/bc1qvkc756tdgqsnwsxn8cmrkhk7hz5l622mnhmchr)

## License

See LICENSE.

---

Built as a complement to (and bridge for) Grok Build and similar surfaces. Local files + your curation first; remote primitives second.