# xlii — REFERENCE (extended)

The deep/advanced companion to the [GUIDE](GUIDE.md) (what xlii is + the
command map) and [HOWTO](HOWTO.md) (the task-by-task walkthrough). This is the
**extended reference**: every command and agent tool, the full configuration
schema, the environment-variable catalog, the architecture, the security model,
hooks, plugin authoring, and troubleshooting — drawn from the source in
`xlii/cli.py`, `xlii/commands.py`, `xlii/tools.py`, `xlii/agent.py`, and friends.

The three command surfaces documented here:

- **CLI commands** — top-level `xlii <subcommand>` (your shell). This is what `xlii help` lists.
- **REPL slash commands** — typed inside `xlii code` / `xlii chat` (`/` or `!`). This is what `/help` lists.
- **Agent tools** — the function-calling commands the AI itself invokes (`read_file`, `bash`, `dispatch_subagent`, …).

> The subcommand and slash *tables* are generated from the live code into the
> [GUIDE](GUIDE.md#command-reference) and CI-checked. The prose below is
> hand-maintained context; if it ever disagrees with the generated tables or with
> `xlii help` / `/help`, the code wins. Project-specific extensions live under
> `.xlii/` (see Extensibility).

---

## 1. Top-Level CLI Commands

Invoked as `xlii <command> [args]`. See `xlii help` or `xlii <cmd> --help` for flags.

### Project Lifecycle
| Command | Description | Example |
|---------|-------------|---------|
| `xlii init [NAME]` | Initialize a project (creates `.xlii/`, uploads to Collection). `--local`, `--snapshot`, `--yes`, `--no-sync`, `--force`. | `xlii init my-app`<br>`xlii init --local --snapshot` |
| `xlii new NAME` | Create a new directory + `init` it. `--local`, `--kind`. | `xlii new my-new-project`<br>`xlii new notes --local --kind collection` |
| `xlii code [TARGET]` | Enter the project code REPL (full tools, write access, plan mode, RAG). TARGET = registry name, path, or omitted (cwd). `--rail` starts on the coding rail; `--tui` the full-screen UI. **Launch gate:** a same-project nested launch is refused — override with `--launch`/`-y` or `--force`. **Ephemeral:** `--preview` (no `.xlii`/snapshot/sync) or `--init` (local-only `.xlii`, no Collection). | `xlii code`<br>`xlii code my-app --yolo`<br>`xlii code --preview` |
| `xlii scratch [NAME]` | Ephemeral local-only scratch dir under `~/.xlii/scratch/`, then enter chat. | `xlii scratch quick-fix` |
| `xlii sync [PATH]` | Force sync dirty files to the Collection. | `xlii sync . --dry-run` |
| `xlii status [PATH]` | Show config, project state, collection, keys, etc. | `xlii status` |
| `xlii projects [FILTER]` | List registered projects (current marked in REPL). | `xlii projects my` |

### Chat / Personas
| Command | Description | Example |
|---------|-------------|---------|
| `xlii chat [NAME]` | Start persona-based chat REPL (persistent memory via its own Collection). | `xlii chat bob`<br>`xlii chat --new alice` |
| `xlii chat --new NAME` | Create new persona (opens `$EDITOR` on `~/.config/xlii/personas/<name>.md`). | `xlii chat --new researcher` |
| `xlii chat --list` | List all personas. | `xlii chat --list` |
| `xlii chat --edit NAME` | Edit a persona's system prompt. | `xlii chat --edit bob` |
| `xlii chat --delete NAME` | Delete persona + its state (with confirmation). | `xlii chat --delete old-one --yes` |

### Knowledge & Plugins (CLI surface)
| Command | Description | Example |
|---------|-------------|---------|
| `xlii doc --new NAME` | Create a reference doc (opens `$EDITOR`). | `xlii doc --new coding-conventions` |
| `xlii doc --list` | List all reference docs. | `xlii doc --list` |
| `xlii doc --edit NAME` | Edit a doc. | `xlii doc --edit coding-conventions` |
| `xlii doc --delete NAME` | Delete a doc. | `xlii doc --delete old-rules --yes` |
| `xlii plugin --new ID` | Create a plugin (markdown API descriptor; opens `$EDITOR`). | `xlii plugin --new openweather` |
| `xlii plugin --list` | List installed plugins. | `xlii plugin --list` |
| `xlii plugin --show ID` | Print full plugin markdown. | `xlii plugin --show openweather` |
| `xlii plugin --edit ID` | Edit a plugin. | `xlii plugin --edit bsky` |
| `xlii plugin --delete ID` | Delete a plugin from catalog. | `xlii plugin --delete unused` |
| `xlii plugin --install-stock` | Install bundled stock plugins. | `xlii plugin --install-stock --force` |

### Setup & Credentials
| Command | Description | Example |
|---------|-------------|---------|
| `xlii setup` | One-shot first-time: config + team discovery + primary + 8 worker keys + model auto-detect. | `xlii setup --workers 4` |
| `xlii config` | Write default `~/.config/xlii/config.json` (if missing). | `xlii config` |
| `xlii bootstrap` | Lower-level key provisioning / revocation. | `xlii bootstrap --count 8 --prefix worker` |
| `xlii keys list` | Show chat keys + expiration. | `xlii keys list` |
| `xlii keys rotate [--label LABEL]` | Rotate secret (key_id stays same). | `xlii keys rotate` |
| `xlii keys expire --days N [--label LABEL]` | Update expiration. | `xlii keys expire --days 180` |
| `xlii keys revoke [--prefix LABEL] [--yes]` | Delete keys server-side + locally. | `xlii keys revoke --prefix worker --yes` |
| `xlii models list` | List accessible models. | `xlii models list` |
| `xlii models recommended` | Heuristic best picks. | `xlii models recommended` |
| `xlii models set --orchestrator NAME [--worker NAME]` | Pin models persistently. | `xlii models set --orchestrator grok-4-1-fast-non-reasoning` |

### Maintenance & Other
| Command | Description | Example |
|---------|-------------|---------|
| `xlii gc [--dry-run] [--yes]` | Garbage-collect orphan Collections. | `xlii gc --dry-run` |
| `xlii doctor [--online]` | Health check + suggested fixes. | `xlii doctor --online` |
| `xlii export DEST` | Serialize personas/docs/plugins/registry (no secrets). | `xlii export ~/backups/xlii-2025` |
| `xlii import SRC [--force]` | Restore from an export. | `xlii import ~/backups/xlii-2025` |
| `xlii auth set <plugin-id> <ENV_VAR>` | Store secret in encrypted vault (prompts, no echo). | `xlii auth set openweather API_KEY` |
| `xlii auth list` | List vault entries (no values). | `xlii auth list` |
| `xlii auth clear <plugin-id> [ENV_VAR]` | Remove credential(s). | `xlii auth clear openweather` |
| `xlii mcp deep-contexts` | Start MCP server exposing DeepContexts/workspaces (for Grok Build). | `xlii mcp deep-contexts` (stdio for MCP clients) |
| `xlii jid house \| add \| ls \| show` | Mint house XMPP JIDs (Prosody). You create addresses; in-band signup stays off. | `xlii jid add --role face --node acer` |
| `xlii node setup <name>` | Stamp a limb: mint daemon + Face JIDs, roster `--remote`, print the Face recipe. | `xlii node setup acer --remote acer` |
| `xlii job post \| ls \| show \| result \| watch` | Named advisory farm jobs. Throne posts; a node that offers the name (`explore`) claims, runs read-only, returns text. No guest bash, no clone. File board `~/.xlii/jobs/` (MUC later). Overlay (Tailscale) optional. | `xlii job post --job explore --task "…" && xlii job watch --once` |
| `xlii help` | Grouped CLI reference (this surface). | `xlii help` |
| `xlii find QUERY` | Find one registered project by exact name or unique substring (`--open` launches it). | `xlii find my-app --open` |
| `xlii project rm [NAME]` | Tear a project down: delete its Collection(s) + registry entry + local `.xlii/` — **never your source files**. `--keep-local` drops only the cloud side; `--local-only` the inverse; `--dry-run` previews. | `xlii project rm old-app --dry-run` |

### The virtual filesystem (address space)

Every readable/writable thing xlii knows — local files, conversation turns, remote hosts — is reachable through one address space, so the same seven verbs work across roots. An **address** is a path (`./README.md`), a `<root>://` URL (`conv://.` for the current conversation), or a bare name resolved by the active provider. See `/panel files` / `/ftp` for the interactive side.

| Command | Description | Example |
|---------|-------------|---------|
| `xlii ls ADDRESS` | List the contents of an address (browse any root). | `xlii ls .`<br>`xlii ls conv://.` |
| `xlii cat ADDRESS` | Print the bytes of a leaf (read a file/turn). | `xlii cat ./README.md` |
| `xlii stat ADDRESS` | Show an address's kind + size. | `xlii stat conv://./turn.md` |
| `xlii cp SRC DST [--force]` | Copy any readable address to any writable one (cross-root). | `xlii cp conv://./turn.md ./backup.md` |
| `xlii mv SRC DST [--force]` | Move/rename a file address (files only, for now). | `xlii mv ./a.md ./b.md` |
| `xlii rm ADDRESS [--yes] [--recursive]` | Remove a leaf, or an empty dir (`-r` for a non-empty tree). | `xlii rm ./scratch.md --yes` |
| `xlii mkdir ADDRESS` | Create a directory at an address (parents as needed). | `xlii mkdir ./notes/drafts` |

### Remote hosts — FTP / FTPS / SFTP

Named connections live in the encrypted vault (secrets never touch disk in the clear); once added they mount into the address space and power `xlii remote publish` / the `/remote` slash (`/ftp` = hidden legacy alias). See the [remote hosts help topic](help/topics/bridges.md).

| Command | Description | Example |
|---------|-------------|---------|
| `xlii remote add NAME` | Add/replace a connection (`--host --port --user --protocol {ftp,ftps,sftp} --key-path --insecure`; secret prompted). | `xlii remote add box --host h --user me --protocol sftp` |
| `xlii remote list` | List configured connections (no secrets shown). | `xlii remote list` |
| `xlii remote test NAME` | Smoke check: connect and list the login home. | `xlii remote test box` |
| `xlii remote publish LOCAL DEST` | Mirror a local dir into a remote docroot (skips size-unchanged files). | `xlii remote publish ./site box:/var/www` |
| `xlii remote rm NAME [--yes]` | Remove a connection and its vault secret. | `xlii remote rm box --yes` |

### Semantic memory — wiki & journal

The project **wiki** is durable, human-curated semantic memory with a verified/unverified trust split; the **journal** (Project Shadow) is the passive observer that records how the project was actually worked. Both have a REPL side (`/wiki`, `/journal`); ask about the recorded history with `/mojo` (iXaac, its memory fused with the journal + wiki — the old `/askjo` is a hidden alias).

| Command | Description | Example |
|---------|-------------|---------|
| `xlii wiki list` | List the project's wiki pages with trust markers. | `xlii wiki list` |
| `xlii wiki show NAME` | Print a wiki page with its sources. | `xlii wiki show architecture` |
| `xlii wiki new NAME` | Create a page from the template (born unverified). | `xlii wiki new architecture` |
| `xlii wiki verify NAME` | Manually promote a page to verified (the AI distill/verify pass is `/wiki verify` in the REPL). | `xlii wiki verify architecture` |
| `xlii wiki rm NAME [--yes]` | Delete a wiki page. | `xlii wiki rm stale-page --yes` |
| `xlii journal key` | Provision an API key used **only** by the journal (isolates its LLM spend for cost auditing). | `xlii journal key --expire-days 90` |
| `xlii journal install` | Opt in to bash-wide capture (adds one marked block to `~/.bashrc`). | `xlii journal install` |
| `xlii journal serve` | Run the summarizer daemon (detached, idempotent; `--once`, `--stop`). | `xlii journal serve` |
| `xlii journal uninstall` | Remove the capture block from `~/.bashrc` and stop the daemon. | `xlii journal uninstall` |

### Account & artifacts

| Command | Description | Example |
|---------|-------------|---------|
| `xlii account [WHAT]` | xAI account hub (read-only): `status` · `keys` · `usage` · `billing`. `--days N` windows usage/billing. | `xlii account usage --days 30` |
| `xlii artifact image [PROMPT]` | Generate an image via xAI Imagine into `.xlii/artifacts/` (`--save`, `--last`, `--redo`, `--inline`, `--model`). The REPL twins are `/imagine` and `/image`. | `xlii artifact image "a red bike" --save ./bike.png` |

**Aliases / notes**: Many subcommands have `--help`. `xlii` (no args) shows usage. Admin commands (keys, bootstrap, models set, gc) are CLI-only. **Every command and every flag** — including ones not called out above — is rendered exhaustively in the generated reference immediately below.

---

## 1a. Full flag reference (generated)

> Generated from the argparse tree by `python -m xlii.docgen` — **do not hand-edit**
> between the GENERATED markers. Every leaf command and every flag, with its
> help string, straight from the parser (the source of truth). The compact
> overview table lives in [GUIDE](GUIDE.md#command-reference).

<!-- BEGIN GENERATED: cli-reference -->
#### `xlii init`

- **`xlii init [name]`** — Initialize an xlii project. Positional NAME labels the collection.
    - `--path <path>` — Project directory (default: cwd)
    - `--collection-id <collection_id>` — Reuse an existing collection instead of creating one
    - `--id <persona>` — Bind this project to a persona: code sessions inherit its memory + loadout (rebinds in place if the project already exists).
    - `--no-sync` — Skip the initial sync
    - `--yes` — Skip the sensitive/large-directory upload confirmation prompt.
    - `--force` — Reinitialize even if project exists
    - `--local` — Local-only mode: no Collection, no upload, no sync. search_project disabled.
    - `--snapshot` — Cache a paths+sizes index at .xlii/index.txt for fast structural search.
    - `--kind {code,collection}` — Folder kind: code (lab / repo) or collection (real-folder pile). Missing kind on an existing tree stays code; named scratch is a collection.

#### `xlii new`

- **`xlii new <name>`** — Create a new project directory and initialize it.
    - `--path <path>` — Parent directory (default: cwd)
    - `--local` — Local-only mode: no Collection, no upload, no sync.
    - `--kind {code,collection}` — Folder kind: code (lab / repo) or collection (real-folder pile).

#### `xlii projects`

- **`xlii projects [filter] [query]`** — List all registered xlii projects (filter by substring).
    - `--open` — With `find`, launch the matched project
    - `--yolo` — With `--open`, auto-approve bash commands
    - `--no-sync` — With `--open`, skip sync for this session

#### `xlii find`

- **`xlii find <query>`** — Find one registered project by exact name or unique substring.
    - `--open` — Launch the matched project
    - `--yolo` — With `--open`, auto-approve bash commands
    - `--no-sync` — With `--open`, skip sync for this session

#### `xlii sync`

- **`xlii sync [path]`** — Push local changes to the project's collection.
    - `--dry-run`

#### `xlii status`

- **`xlii status [path]`** — Show config + project state.

#### `xlii gc`

- **`xlii gc`** — Find xAI Collections with no live project behind them — tracked-dead (still in the registry but the project was deleted on disk) or untracked-cloud (an xli*/xlii* collection with no registry entry) — and delete them so you stop paying for orphaned storage. Destructive: preview with --dry-run, delete all with --yes, or answer the interactive all / dead-path-only prompt.
    - `--dry-run` — Show what would be deleted, take no action
    - `--yes` — Delete all orphans without prompting

#### `xlii project`

- **`xlii project rm [name]`** — Remove a project: delete its Collection(s) + registry entry + local .xlii/ (NEVER your source files). Sweeps orphan journal Collections too.
    - `--yes` — Skip the confirmation prompt
    - `--dry-run` — List exactly what would be deleted; take no action
    - `--keep-local` — Delete the Collection(s) + registry entry only; keep the local .xlii/ tree
    - `--local-only` — Remove local .xlii/ + registry entry only; leave the cloud Collection(s)

#### `xlii scratch`

- **`xlii scratch [name]`** — Scratch mode: an ephemeral, unbound, never-sync session (bare = from home; `here` = local .xlii in the cwd; NAME = ~/.xlii/scratch/NAME).
    - `--no-chat` — Create the scratch (named/here) without entering the session
    - `--tui` — Launch the experimental full-screen Textual UI (needs `pip install 'xlii[tui]'`)
    - `--tauri` — Launch the desktop face (xlii-desktop) over this scratch — 7am home window (three-faces Q5); bare form uses ~/.xlii/scratch/home
    - `--resume` — With --tauri: if a face is already running, continue that session
    - `--replace` — With --tauri: if a face is already running, stop it and start new
    - `--yolo` — Auto-approve bash in the session
    - `--force` — Re-init even if a scratch with this name exists

#### `xlii ask`

- **`xlii ask <prompt>`** — Run a single agent turn and print the reply (headless; for scripts + the XMPP daemon).
    - `--attach <path>` — Attach a file for the agent to SEE this turn (image → vision, PDF/text → inlined; repeatable). The media-in path the XMPP mouth uses to hand iXaac a photo or document.
    - `--workspace <name>` — Project name (registry) or path to run in (default: cwd / most-recent)
    - `--persona <name>` — Run the turn AS this persona over its own memory (the mojo read: recalls the persona's long-term memory; overrides --workspace/--session)
    - `--no-accrue` — (with --persona) don't write this turn into the persona's memory (default: the turn accrues so texting it is a continuing conversation)
    - `--no-sync` — (with --persona, on the center) accrue the turn locally but DON'T drain it to the shared remote Collection — keep it private/local
    - `--outbox <dir>` — Grant the turn a delivery channel: the send_file tool queues files into DIR and the caller delivers them after the turn (the XMPP mouth uploads them encrypted into the owner's chat). Also unlocks generate_image on the persona surface — a mouth that can deliver media may make media.
    - `--yolo` — Auto-approve bash without prompting (for trusted non-interactive use)
    - `--session <id>` — Persist this conversation under the project's .xlii/ keyed by a caller-chosen ID — repeated calls with the same ID share context (the multi-turn primitive for the daemon and scripts)
    - `--new-session` — Reset the --session conversation before running this turn

#### `xlii loop`

- **`xlii loop [goal]`** — Autonomous build→test→fix loop (headless; walk away to green).
    - `--status` — Show active loop state
    - `--resume` — Resume a persisted loop
    - `--workspace <name>` — Project name (registry) or path (default: cwd / most-recent)
    - `--judge <names>` — Comma-separated judge profiles (default: tests)
    - `--max <max_cycles>` — Maximum build→test cycles (default: 5)
    - `--test <cmd>` — Shell test command (default: pytest -q)
    - `--budget <budget>` — Stop if judge spend exceeds this USD cap (L2+)
    - `--from-plan` — Use goal from .xlii/plan-last.md (after /plan + /execute)
    - `--drain-inbox` — Run every .xlii/inbox/*.md task in order, archiving each to inbox/done/
    - `--commit {never,each,final}` — Git commit on test pass (each) or loop done (final); implied each when --push is set
    - `--push {never,each,final}` — Git push before CI judge (must pair with --commit each|final)
    - `--read-budget <read_budget>` — Max file excerpts per judge READ_REQUEST (default: 3)
    - `--swarm <n>` — Writer-workers per build phase (default: 1; capped by /swarm ceiling)
    - `--merge {auto,llm}` — Merge strategy for overlapping writers: auto=git-only fail-closed, llm=merge-agent
    - `--merge-judge <profile>` — Cross-vendor judge for LLM merge resolutions (default: loop_defaults.merge_judge)
    - `--yolo` — Auto-approve bash without prompting

#### `xlii code`

- **`xlii code [target]`** — Project-scoped code agent REPL. Pass a project NAME (registry lookup) or PATH; default cwd.
    - `--yolo` — Auto-approve every bash command regardless of intent (no confirmation prompts)
    - `--rail` — Start in Coding Rail mode: gate each turn through Requirements → Architecture → Edge Cases → Pseudocode → Implementation → Self-Review (toggle in-session with /rail).
    - `--discovery` — Start in discovery mode: read-only discussion/research — the agent reads & explains but won't change code (toggle in-session with /discovery).
    - `--ops` — Start in ops mode: OS diagnostics & workflow — platform-correct shell probes, read-only first (toggle in-session with /ops).
    - `--no-sync` — Skip startup and end-of-turn syncing to the Collection this session
    - `--no-startup` — Skip the per-project startup-task ritual this launch
    - `--tui` — Launch the experimental full-screen Textual UI (needs `pip install 'xlii[tui]'`)
    - `--tauri` — Launch the desktop face (the xlii-desktop Tauri app) over this project — iXaac-first chat, [$] flips to code
    - `--replace` — With --tauri: if a face is already running, stop it and start a new one
    - `--keep-session` — Start an episode at launch AND make it sticky for this project: every turn snapshots the full live history, and each later launch offers to restart where you left (same as /session on; /session off clears it)
    - `--resume <id>` — Resume a stored episode (omit ID for the most recent): full history + conversation id restored
    - `--force` — Proceed even when launched from within another xlii session for this project
    - `--preview` — Open the REPL without initializing: no .xlii, no snapshot, no sync (ephemeral for a non-project; skips startup sync for an existing one)
    - `--init` — Initialize a local-only .xlii here, then launch (no snapshot/Collection)
    - `--launch` — Skip the launch gate and open an existing project normally

#### `xlii chat`

- **`xlii chat [name]`** — Persona-based conversational agent with persistent memory (each persona has its own Collection).
    - `--new <name>` — Create a new persona; opens $EDITOR on its prompt file
    - `--list` — List all personas and exit
    - `--edit <name>` — Open an existing persona's prompt in $EDITOR
    - `--delete <name>` — Delete a persona (prompt + state dir)
    - `--yolo` — Auto-approve bash commands
    - `--tui` — Launch the experimental full-screen Textual UI (needs `pip install 'xlii[tui]'`)
    - `--yes` — Skip confirmation prompts (used with --delete)
    - `--force` — Proceed even when launched from within another xlii session for this persona

#### `xlii make`

- **`xlii make <name> <description>`** — S4 instant-apps: one gesture from a description to a live URL. iXaac builds a self-contained static app, then it is published to the apps box so it serves at https://<name>.<domain>.
    - `--domain <domain>` — Apps domain (default: xlii-code.com)
    - `--conn <conn>` — Remote connection to the apps box (xlii remote add; default: appbox)
    - `--root <root>` — Remote apps root under the login home (default: srv/apps)
    - `--dir <apps_dir>` — Local dir that holds app folders (default: ~/serve-sandbox)
    - `--no-publish` — Build locally only; don't publish (prints the local path)
    - `--no-yolo` — Prompt before bash during the build (default: auto-approve — this is a hands-off one-gesture build in a throwaway dir)

#### `xlii config`

- **`xlii config`** — Create a config template at ~/.config/xlii/config.json, then edit it to paste your management_api_key and add chat API keys to the keys[] list. Safe to re-run: it writes the template only when no config exists — it never clobbers real keys — and otherwise just repairs the file's permissions. `xlii setup` does this and also provisions worker keys.

#### `xlii shell-init`

- **`xlii shell-init [shell]`** — Print a shell wrapper so `cd` inside `xlii code` follows you out on exit (opt-in; add `eval "$(xlii shell-init)"` to your rc file).

#### `xlii setup`

- **`xlii setup`** — One-shot first-time setup: writes config, checks env mgmt key, provisions primary + workers.
    - `--workers <workers>` — Number of worker keys to create (default: 8)
    - `--expire-days <expire_days>` — Key expiration in days (default: 180; 0 = no expiry)
    - `--force` — Re-run bootstrap even if pool already populated
    - `--journal <journal>` — Mojo nickname for this body (limb: the throne's /name). Default: $XLII_MOJO_NAME, else mojo. Daemon cannot /name.

#### `xlii bootstrap`

- **`xlii bootstrap`** — Provision worker API keys via the management API (lower-level than `setup`).
    - `--count <count>` — How many worker keys to create (default: 8)
    - `--prefix <prefix>` — Label prefix for created keys (default: 'worker')
    - `--expire-days <expire_days>` — Key expiration in days (default: 180; 0 = no expiry)
    - `--force` — Add new keys even if matching prefix already exists
    - `--revoke` — Revoke (delete) all keys with matching prefix
    - `--yes` — Skip confirmation when revoking

#### `xlii models`

- **`xlii models list`** — List models the team has access to.
- **`xlii models recommended`** — Show heuristic best-of-class picks.
- **`xlii models set`** — Pin orchestrator, worker, chat, and/or help model(s).
    - `--orchestrator <orchestrator>` — Model id for the main code agent
    - `--worker <worker>` — Model id for dispatched workers
    - `--chat <chat>` — Model id for persona / conversational chat
    - `--help-model <help_model>` — Model id for /howto (help role)
- **`xlii models profile list`** — Show built-in and custom profiles.
- **`xlii models profile set <name>`** — Apply a profile to orchestrator, worker, chat, and help (persisted).

#### `xlii keys`

- **`xlii keys list`** — List local chat keys with their server-side expiration.
- **`xlii keys rotate`** — Rotate the secret of one or all keys (same key_id, new value).
    - `--label <label>` — Rotate only this label (otherwise: all)
- **`xlii keys expire`** — Update expireTime on existing key(s).
    - `--days <days>` — Days from now (0 = remove expiry)
    - `--label <label>` — Apply to a single label (otherwise: all)
- **`xlii keys revoke`** — Delete keys by label prefix (server-side + local).
    - `--prefix <prefix>` — Label prefix to revoke (default: worker)
    - `--yes` — Skip confirmation
- **`xlii keys prune`** — Delete orphaned xlii-provisioned keys not in this machine's pool.
    - `--name <name>` — Only keys whose xAI name matches this glob (e.g. '*test*'). Overrides the xlii-only default.
    - `--any-name` — Consider keys of ANY name, not just xlii-provisioned (use with care — reaches other tools' keys).
    - `--older-than <days>` — Only keys created more than DAYS ago.
    - `--include-active` — Also consider your live pool keys (dangerous).
    - `--dry-run` — Show what would be deleted, then stop.
    - `--yes` — Skip the confirmation prompt.
- **`xlii keys migrate`** — Move plaintext chat-key secrets from config.json into the encrypted vault (local, no network).
    - `--dry-run` — Show what would move, then stop.
    - `--no-backup` — Skip the timestamped config.json backup.

#### `xlii auth`

- **`xlii auth set <plugin_id> <env_var>`** — Store a credential: xlii auth set <plugin-id> <ENV_VAR> (value prompted, not echoed).
- **`xlii auth list`** — List plugins + env var names in the vault (never values).
- **`xlii auth clear <plugin_id> [env_var]`** — Remove a credential or a plugin's whole entry.

#### `xlii account`

- **`xlii account [what]`** — xAI account: status, keys, usage, billing (read-only).
    - `--days <days>` — for usage: rolling window in days (default: month to date)

#### `xlii artifact`

- **`xlii artifact image [prompt]`** — Generate an image via xAI Imagine.
    - `--path <path>` — Project directory (default: cwd)
    - `--redo` — Regenerate with the last prompt
    - `--edit-prompt <edit_prompt>` — New prompt (iteration; uses last artifact as ref)
    - `--ref <path>` — Reference image for edits (repeatable, max 3)
    - `--save <save>` — Copy last artifact to a project path
    - `--last` — Show last artifact metadata
    - `--no-preview` — Skip inline terminal preview
    - `--inline` — Force inline preview
    - `--model <model>` — Imagine model id
    - `--yolo` — Skip paid-action confirmation
- **`xlii artifact video [prompt] [request_id]`** — Kick off an async video job, or `status <id>` to poll one. Async by default — pass --wait N to poll up to N seconds now.
    - `--path <path>` — Project directory (default: cwd)
    - `--model <model>` — Imagine video model id
    - `--wait <secs>` — Poll up to SECS seconds before returning (default 0 = async)
- **`xlii artifact edit [prompt]`** — Up to 3 reference image paths plus a prompt produce a new artifact (paid; confirms unless --yolo).
    - `--ref <path>` — A reference image path (repeatable, up to 3)
    - `--path <path>` — Project directory (default: cwd)
    - `--model <model>` — Imagine model id
    - `--yolo` — Skip paid-action confirmation
- **`xlii artifact pdf [source]`** — Resolve a source alias (last, verify, peer, loop, plan) or file path to markdown, render to PDF with the first available engine, and print the saved path. Use --open to launch the OS viewer.
    - `--path <path>` — Project directory (default: cwd)
    - `--out <out>` — Output filename under .xlii/artifacts/
    - `--engine {auto,weasyprint,pandoc,wkhtmltopdf,fpdf2}` — PDF engine (default: auto-detect)
    - `--open` — Open the PDF in the OS viewer

#### `xlii plugin`

- **`xlii plugin`** — Manage plugins (markdown API descriptors used via /lib + /get).
    - `--new <id>` — Create a new plugin from template; opens $EDITOR
    - `--list` — List all installed plugins
    - `--show <id>` — Print a plugin's full markdown
    - `--edit <id>` — Edit a plugin in $EDITOR
    - `--delete <id>` — Delete a plugin
    - `--yes` — Skip confirmation for --delete
    - `--lint` — Validate installed + stock plugin frontmatter and manifests
    - `--install-stock` — Install the bundled stock plugins (skips ones you've edited)
    - `--force` — With --install-stock: overwrite existing stock plugins

#### `xlii doc`

- **`xlii doc`** — Manage reference docs (markdown files attached via /doc in any REPL).
    - `--new <name>` — Create a new doc; opens $EDITOR
    - `--list` — List all docs
    - `--edit <name>` — Open an existing doc in $EDITOR
    - `--delete <name>` — Delete a doc
    - `--yes` — Skip confirmation prompt for --delete

#### `xlii export`

- **`xlii export <dest>`** — Serialize everything that makes an install yours — personas (prompts + memory turns), docs, plugins, and the project registry — to a plain directory tree you own. Secrets are excluded: config.json API keys and the credential vault never leave the machine. The destination must be empty or new. Round-trips with `xlii import`; use it to back up your curation or move it to another machine. Example: xlii export ~/xlii-backup

#### `xlii import`

- **`xlii import <src>`** — Restore an `xlii export` tree onto this machine: personas, memory turns, docs, and plugins. Existing files are kept per-file unless --force overwrites them. Secrets are not part of an export, so re-add API keys with `xlii config` or `xlii setup` afterwards. Example: xlii import ~/xlii-backup
    - `--force` — Overwrite existing personas/docs/plugins

#### `xlii email`

- **`xlii email accounts add <name>`** — Add an account (password prompted → vault).
    - `--imap-host <imap_host>` — IMAP hostname
    - `--imap-port <imap_port>`
    - `--smtp-host <smtp_host>` — SMTP hostname
    - `--smtp-port <smtp_port>`
    - `--user <user>` — Login email address
    - `--default` — Mark as default account
- **`xlii email accounts list`** — List configured accounts (no secrets).
- **`xlii email list`** — List recent messages from INBOX.
    - `--account <account>` — Account name
    - `--unread` — Unread only
    - `--limit <limit>`
- **`xlii email search <query>`** — Search messages (IMAP TEXT).
    - `--account <account>`
    - `--unread`
    - `--limit <limit>`
- **`xlii email read <message_id>`** — Fetch and display a message by id.
    - `--account <account>`
- **`xlii email send`** — Send a message (typed 'send' confirm unless --yolo).
    - `--account <account>`
    - `--to <to>` — Recipient address
    - `--subject <subject>` — Subject line
    - `--body <body>` — Plain-text body
    - `--html <html>` — Optional HTML alternative body
    - `--yolo` — Skip send confirmation

#### `xlii ls`

- **`xlii ls <address>`** — List the contents of an address (browse the VFS). e.g. `xlii ls .` or `xlii ls conv://.`

#### `xlii cat`

- **`xlii cat <address>`** — Print the bytes of an address (read a VFS leaf). e.g. `xlii cat ./README.md`

#### `xlii cp`

- **`xlii cp <src> <dst>`** — Copy any readable address to any writable one (cross-root). e.g. `xlii cp conv://./turn.md ./backup.md`
    - `--force` — Overwrite the destination if it exists

#### `xlii mv`

- **`xlii mv <src> <dst>`** — Move/rename a file address (read+write+delete). Files only for now.
    - `--force` — Overwrite the destination if it exists

#### `xlii rm`

- **`xlii rm <address>`** — Remove an address (a leaf, or an empty dir; -r for non-empty).
    - `--yes` — Confirm the deletion
    - `--recursive` — Recursively delete a non-empty directory

#### `xlii mkdir`

- **`xlii mkdir <address>`** — Create a directory at an address (parents as needed).

#### `xlii stat`

- **`xlii stat <address>`** — Show an address's kind/size. e.g. `xlii stat conv://./turn.md`

#### `xlii map`

- **`xlii map [path]`** — Repo map: file tree + Python class/function signatures — orientation, offline, deterministic (same bytes as /map and the map tool).
    - `--depth <depth>` — Tree depth cap (1 = top level only; default full)
    - `--detail {files,symbols}` — 'files' = tree only; 'symbols' (default) adds class/function signatures

#### `xlii remote`

- **`xlii remote add <name>`** — Add/replace a named connection (secret prompted, stored in the vault).
    - `--host <host>` — Remote hostname or IP (webdav may give --base-url instead)
    - `--port <port>` — Port (per-protocol default: 21/22/443/445)
    - `--user <user>` — Login user (empty → anonymous FTP)
    - `--protocol {ftp,ftps,sftp,webdav,smb}` — Wire protocol for this connection (decides its scheme: ftp/sftp/dav/smb)
    - `--key-path <key_path>` — SSH private key file (sftp; secret becomes its passphrase)
    - `--insecure` — Skip TLS certificate verification (ftps/webdav; webdav: also allow plain http)
    - `--base-url <base_url>` — WebDAV base URL, may carry a path (https://cloud.example.com/remote.php/dav/files/me — replaces --host)
    - `--auth {basic,digest,bearer}` — WebDAV auth mode (default basic; bearer sends the vault secret as a token)
    - `--share <share>` — Share name (smb only; required for that wire)
    - `--domain <domain>` — Domain/workgroup (smb only; optional)
- **`xlii remote list`** — List configured connections (no secrets shown).
- **`xlii remote rm <name>`** — Remove a connection and its vault secret.
    - `--yes` — Confirm the removal
- **`xlii remote test <name>`** — Smoke check: connect and list the login home.
- **`xlii remote publish <local> <dest>`** — Mirror a local dir into a remote docroot (skips size-unchanged files; docroot is relative to the connection's login root; a leading / is stripped, never honored).
    - `--delete` — Prune remote files/dirs not present locally (clean redeploy)
- **`xlii remote unpublish <dest>`** — Recursively delete a published remote docroot (docroot is relative to the connection's login root; a leading / is stripped, never honored).
    - `--yes` — Confirm the recursive remote delete

#### `xlii role`

- **`xlii role list`** — List available roles
- **`xlii role show <name>`** — Show a role's loadout + identity

#### `xlii serve-inbox`

- **`xlii serve-inbox`** — Run a localhost webhook that turns each POST body into a markdown goal in .xlii/inbox/. It only enqueues — nothing runs until you drain the queue with `xlii loop --drain-inbox` — so an external trigger (CI, a phone shortcut, another service) can stage work without ever executing code directly. Bound to 127.0.0.1 by default. A token is required (generated if omitted); pass --insecure-no-token only for a trusted loopback experiment.
    - `--host <host>` — Bind host (default: 127.0.0.1 — localhost only)
    - `--expose` — Allow a non-loopback --host (specific tailnet/LAN address). Wildcard binds are always refused.
    - `--port <port>` — Bind port (default: 8765)
    - `--token <token>` — Require this shared secret in the X-XLII-Token header (auto-generated if omitted)
    - `--insecure-no-token` — Allow unauthenticated POSTs (loopback only; still refuse wildcards)
    - `--workspace <name>` — Project name (registry) or path (default: cwd / most-recent)

#### `xlii serve`

- **`xlii serve`** — Serve the existing Textual TUI over HTTP: a browser tab becomes the terminal (xterm.js over a WebSocket), one session per tab, running `xlii code --tui` for the current directory. SECURITY: there is NO AUTH — anyone who can reach the port has a shell on this machine (unless --public, which code-gates the entry). The default bind is 127.0.0.1; to use it from another device, bind your tailnet interface address. Never bind 0.0.0.0. With --public, bind stays loopback and Caddy owns TLS.
    - `--host <host>` — Bind host (default: 127.0.0.1 — localhost only; 'tailnet' binds this machine's Tailscale IPv4; a specific address still works. Non-loopback requires --expose. Ignored/refused under --public which always binds loopback)
    - `--expose` — Allow a non-loopback --host (specific tailnet/LAN address). Wildcard binds (0.0.0.0 / ::) are always refused.
    - `--port <port>` — Bind port (default: 8042; --face defaults to an ephemeral port instead — pass --port to pin one)
    - `--preview` — Serve a read-only exploration session (`xlii code --preview --tui`) — natural for a shared screen
    - `--public` — Code-gated public entry (serve-public): loopback bind only, pairing codes via grant cookie; put Caddy in front for TLS
    - `--base-url <url>` — (with --public) public base URL for the entry page (or set [serve.public] base_url); required in --public mode
    - `--ws` — WebSocket JSON event protocol (W2) instead of textual-serve. Uses ask --session; token auth mandatory.
    - `--face` — The face server (protocol v2): ONE live REPL session over the WebSocket wire + the face page over plain HTTP — the backend of the desktop (Tauri) and browser face. Token auth mandatory.
    - `--handshake` — (with --ws/--face) bind ephemeral port, print one JSON handshake line on stdout, exit when stdin closes (Tauri sidecar contract)
    - `--token <token>` — (with --ws/--face) auth token for WebSocket connections (auto-generated if omitted)
    - `--workspace <path>` — (with --ws/--face) project directory (default: cwd / most-recent)
    - `--yolo` — (with --ws/--face) skip per-intent confirmation gates for agent turns
    - `--force` — (with --face) proceed even when launched from within another xlii session for this project (shared on-disk state — same risk as `xlii code --force`)
    - `--replace` — (with --face) if another face is already running on this machine, stop it and take over (one face per environment)
    - `--view {desk,phone}` — (with --face) desk chrome (default) or phone glass — public webcode spawns phone so daemon xsu/webcode lands the thumb face
- **`xlii serve mint`** — Mint a pairing code for the public entry page. Writes the code to serve-grants.json under the state dir; type it into base_url's form. Requires serve.public.base_url in config.json.
    - `--preview` — Mint a preview-mode code (read-only TUI session).
    - `--email` — Email the magic link to the owner inbox (default email account, or serve.public.owner_email).
    - `--state-dir <path>` — State directory for the grant spool (default: ~/.local/share/xlii; XLII_STATE_DIR overrides).
- **`xlii serve sessions`** — List live public-serve sessions from the session mirror.
    - `--state-dir <path>` — State directory for the session mirror (default: ~/.local/share/xlii; XLII_STATE_DIR overrides).
- **`xlii serve revoke <session_id>`** — Revoke a paired browser session: queue it for the live serve process, which kills it at its next sweep. Pass a session id or 'all'.
    - `--state-dir <path>` — State directory for the session mirror (default: ~/.local/share/xlii; XLII_STATE_DIR overrides).

#### `xlii skin`

- **`xlii skin list`** — List compiled skins and discovered packs.
- **`xlii skin check <path>`** — Lint a pack directory (scope, absolute urls, extension/size).
- **`xlii skin install <path>`** — Copy a local pack directory into ~/.config/xlii/skins/ (no network fetch).

#### `xlii help`

- **`xlii help`** — Show grouped command listing.

#### `xlii doctor`

- **`xlii doctor`** — Check install + project health and print fixes.
    - `--online` — Also test Collection reachability
    - `--migrate-legacy` — Migrate legacy paths (.xli/ → .xlii/, global loadouts, .xliignore)
    - `--dry-run` — With --migrate-legacy, print actions without applying

#### `xlii mcp`

- **`xlii mcp deep-contexts`** — Expose DeepContexts + live workspaces via MCP (for Grok Build attachment). Requires the [mcp] extra.

#### `xlii daemon`

- **`xlii daemon`** — Run the XMPP/OMEMO command daemon (experimental; requires the [daemon] extra).
    - `--config <path>` — Path to daemon.toml (default: ~/.config/xlii/daemon.toml).
- **`xlii daemon trust <jid> <fingerprint>`** — Pin a sender device's OMEMO fingerprint as trusted (for blind_trust=false).
    - `--config <path>` — Path to daemon.toml (default: ~/.config/xlii/daemon.toml).
    - `--distrust` — Distrust (un-pin) the device instead of trusting it.
- **`xlii daemon pair`** — Mint a one-time QR pairing window (pins a device; never flips blind_trust).
    - `--invite <jid>` — Also append this JID to allowed_jids on grant.
    - `--ttl <secs>`
    - `--no-wait`
    - `--config <path>`
- **`xlii daemon totp`** — Generate a TOTP secret for the daemon's elevation gate (add to your authenticator, export XLII_DAEMON_TOTP_SECRET).
- **`xlii daemon panic-phrases`** — Set the three panic-mail subject phrases (vault; not git).
    - `--set <p1 p2 p3>` — Three owner-invented subject phrases.
- **`xlii daemon panic-check`** — Fetch unseen panic mail now (same as daemon/Face wake).

#### `xlii pair`

- **`xlii pair`** — Mint a one-time QR pairing window (pins an OMEMO device; never flips blind_trust).
    - `--rail {daemon,notify,face}` — Which OMEMO rail owns the window (default: daemon).
    - `--invite <jid>` — Also append this JID to allowed_jids on grant (opt-in enrollment).
    - `--ttl <secs>` — Window lifetime in seconds (default 600).
    - `--no-wait` — Mint and print, then exit — leave the window open.
    - `--config <path>` — Path to daemon.toml (default: ~/.config/xlii/daemon.toml).

#### `xlii fabric`

- **`xlii fabric pull`** — Pull a node's accrued persona turns to the throne and archive them to the shared Collection (run on the throne — needs the management key).
    - `--node <name>` — Pull just this node (default: every configured node)
    - `--persona <name>` — Persona whose memory to pull (default: the node's configured persona, else 'mojo')
    - `--dry-run` — Report what would be pulled without writing or archiving
- **`xlii fabric nodes`** — List the configured fabric nodes.
- **`xlii fabric add-node <name>`** — Register a node: a name + an existing remote-fs connection (xlii remote add).
    - `--remote <conn>` — Name of a configured sftp connection (xlii remote add) that reaches this node
    - `--chat-state <path>` — Remote path to the node's chat-state dir (default: .xlii/chat)
    - `--persona <name>` — Default persona to pull from this node (default: mojo)
- **`xlii fabric rm-node <name>`** — Remove a node from the roster.
- **`xlii fabric sync-projects`** — Pull every node's project registry onto this throne, then push the shared catalog back. Throne Home never travels.
    - `--dry-run` — Read nodes; do not write pointers or registries
- **`xlii fabric new <node> <name>`** — Mint a Collection-first project attributed to a fabric node. No box is the file home until someone adopts.
    - `--kind {code,collection}` — Folder kind stamp on the pointer (default: collection)

#### `xlii jid`

- **`xlii jid house`** — Set or show the XMPP house (domain + optional admin remote that can sudo prosodyctl).
    - `--domain <domain>` — e.g. home.xlii-remote.com
    - `--admin-remote <admin_remote>` — xlii remote name that SSH-execs on the Prosody host (empty = print the command)
- **`xlii jid ls`** — List ledgered house JIDs.
- **`xlii jid add [localpart]`** — Mint a JID: register on the house Prosody when an admin remote is set, else print the command.
    - `--role {me,throne,daemon,node,face}` — me | throne | daemon | node | face
    - `--node <node>` — limb name (required for --role face)
    - `--domain <domain>` — override house domain
    - `--password <password>` — set this password (default: generate). Never put this in a task file.
    - `--adopt` — Record an existing account; do not call Prosody
- **`xlii jid show <localpart>`** — Show one ledgered JID.
    - `--reveal` — Print the vault password (once, on this terminal)

#### `xlii job`

- **`xlii job post`** — Post an advisory job ad (throne).
    - `--job <job>` — Named job (v0: explore)
    - `--task <task>` — What the node should answer
    - `--context <context>` — Pasted snippets, a file path, or - for stdin (brief workplace)
    - `--accept <accept>` — What 'done' looks like
    - `--project <project>` — local-project name (must exist in the node's jobs.projects)
    - `--rev <rev>` — Optional git rev hint
    - `--budget <budget>` — Soft USD cap
    - `--max-iters <max_iters>`
    - `--kind <kind>` — advisory (house) or market (cross-throne; refuses context / stage / lab)
- **`xlii job ls`** — List open / claimed / done jobs.
- **`xlii job show <id>`** — Print one ticket (and result if done).
- **`xlii job result <id>`** — Print a done job's text.
    - `--json`
- **`xlii job watch`** — Node loop: claim the next eligible ad and run it (explore, no bash).
    - `--once` — One pick or idle, then exit
    - `--poll <poll>` — Seconds between scans
- **`xlii job cancel <id>`** — Withdraw a ticket (cooperative). Running node aborts within one iteration.
- **`xlii job bench <node>`** — Flip a node pickup → observe (cooperative). Daemon stays up.
    - `--until <until>` — Optional until stamp (informational)
- **`xlii job evict <node>`** — Kick / revoke MUC membership (enforced, XEP-0045). Requires owner affiliation.
- **`xlii job accept <id>`** — Accept a quarantined market result (bumps fingerprint rep; never auto-fuses).
    - `--fp <fp>` — OMEMO fingerprint to credit
- **`xlii job invite <fp>`** — Open a deal room and invite a market badge (fingerprint). Terms first.
    - `--venue <venue>` — Venue host or room JID

#### `xlii node`

- **`xlii node setup [name]`** — Mint daemon + Face JIDs for a named limb and roster it when --remote is set.
    - `--remote <remote>` — Existing xlii remote name that reaches this box
    - `--key-path <key_path>` — SSH key path (existing)
    - `--new-key` — Generate a dedicated SSH key (option)
    - `--gig <gig>` — jobs.gig larynx name on the box
    - `--mint-xai` — Mint a capped xAI child (not yet pushed)
    - `--journal <journal>` — Mojo nickname to stamp on the limb (default: this throne's /name).

#### `xlii notify`

- **`xlii notify <message>`** — Send an OMEMO-encrypted notification to your phone (requires the [daemon] extra).
    - `--config <path>` — Path to notify.toml (default: ~/.config/xlii/notify.toml).

#### `xlii journal`

- **`xlii journal key`** — Provision an API key used exclusively by the journal (isolates its LLM spend for cost auditing).
    - `--force` — Provision another journal key even if one already exists
    - `--expire-days <expire_days>` — Key expiry in days (default: 180)
- **`xlii journal install`** — Opt in to bash-wide capture: add one marked source block to ~/.bashrc.
- **`xlii journal uninstall`** — Remove the bash-wide capture block from ~/.bashrc and stop the daemon.
- **`xlii journal serve`** — Run the journal daemon that summarizes bash-wide capture (detached, idempotent).
    - `--once` — Drain the feed once and exit (no daemon loop).
    - `--stop` — Signal a running journal daemon to stop.

#### `xlii wiki`

- **`xlii wiki list`** — List the project's wiki pages with trust markers.
- **`xlii wiki show <name>`** — Print a wiki page (with its sources).
- **`xlii wiki new <name>`** — Create a wiki page from the template (born unverified).
- **`xlii wiki verify <name>`** — Mark a page verified (manual promote; AI pass is /wiki verify in the REPL).
- **`xlii wiki rm <name>`** — Delete a wiki page.
    - `--yes` — Confirm the deletion

#### `xlii sweep`

- **`xlii sweep`** — Inventory xAI Collections, dead registry rows, and expired/orphan chat keys. Default is look-only. Add --empty / --test / --ghosts / --keys to clean. Never deletes a Collection a live project still claims.
    - `--empty` — Delete untracked empty collections
    - `--test` — Delete untracked test/tmp-named collections
    - `--ghosts` — Drop dead registry rows (disk untouched)
    - `--keys` — Prune orphan/disabled server keys not in this pool
    - `--yes` — Apply without prompting

#### `xlii pr`

- **`xlii pr sweep [pr]`** — One poll pass: new PR events → .xlii/inbox files (never drains).
    - `--marker <marker>` — Comment summon string (default: @xlii). Not a GitHub account.
    - `--all-comments` — Treat every conversation/review comment as actionable (not just --marker).
    - `--workspace <path>` — Project directory (default: cwd).
- **`xlii pr watch [pr]`** — Foreground loop over sweep. Drains by default after a pass that queued work.
    - `--marker <marker>` — Comment summon string (default: @xlii). Not a GitHub account.
    - `--all-comments` — Treat every conversation/review comment as actionable (not just --marker).
    - `--workspace <path>` — Project directory (default: cwd).
    - `--interval <secs>` — Poll interval (default 45; floor 30).
    - `--drain` — After a pass that enqueued, run `xlii loop --drain-inbox` as a subprocess (default).
    - `--no-drain` — Queue only — never spawn the drain subprocess.

#### `xlii destroy-all`

- **`xlii destroy-all`** — Human-only deny ladder — dry-run default; keys-and-local wipes this body.
    - `--dry-run` — Level 0 inventory only (the default). Harmless if passed with --keys-and-local.
    - `--keys-and-local` — Level 1: revoke minted ids and wipe this body's local state.
    - `--local-only` — Skip cloud revokes; journal surviving ids.
    - `--aim {all,throne,node}` — Destroy scope (Wave 1: all/throne = this body).
    - `--target <target>` — Node name when --aim=node.
<!-- END GENERATED: cli-reference -->

---

## 2. REPL Slash Commands (`/…` and `!…`)

Available inside `xlii code` and `xlii chat` (some are REPL-specific). Use `/help` inside a session for the live list (generated from the registry in `xlii/commands.py`; handlers register from `xlii/repl_cmds/` at import time).

Core ones (from `SLASH_HELP`, `CHAT_SLASH_HELP`, and live registry):

### Session Control (both REPLs)
- `/help` — Show slash commands for this REPL.
- `/clear` (aliases `/cls`, `/clear-screen`) — Wipe the transcript (pixels only). Talk stays; `/reset` forgets.
- `/exit`, `/quit` — Leave the REPL (special-cased in `repl.py`).
- **Shell-primary input (`xlii code`)** — bare input is a **live shell command** run in a tracked cwd (`cd` moves it); summon the AI with `?<text>`. E.g. `ls -la`, `cd tests && pytest`, `? why did the build fail`. Toggle off with `XLII_SHELL_PRIMARY=0`. See `proposals/done/flipmode.md`.
  - The live cwd is shown in the **terminal title** (kitty/gnome-terminal/etc.) to keep the prompt clean; set `XLII_NO_TITLE=1` to disable that and show the cwd inline in the prompt instead.
- `!<shell command>` — Force a shell command at the **project root** (ignores the live cwd; ungated escape). E.g. `!ls -la`, `!clear`, `!git status`.
- `!!<shell command>` — Same, but **always** inherits the terminal (raw passthrough) — the escape hatch for pagers, `vim`, `less`, full-screen apps. Only differs from `!` when styled capture is on (see below); otherwise both are raw.

#### Styled TUI (presentation layer)
Opt-in unified block grammar — shell runs, agent tool calls, and the answer all render through one renderer with one theme (`proposals/done/tui-layer.md`). Env knobs:
- `XLII_SHELL_STYLE=styled` — turn it on (default `raw` = classic output). `=raw` forces classic.
- `XLII_SHELL_MAXLINES=N` — display budget for captured output (default `40`, head+tail so the verdict survives; `0` = never truncate). Full output always still reaches the model.
- `XLII_NO_TOOLBAR=1` — hide the bottom status bar (mode · cwd · attachments) while keeping the rest of the styled UI.
- `?<text>` — Send to the AI / agent (the only way to talk to the model in shell-primary `code`; a harmless alias in `chat`, where bare input already talks).
- `xlii shell-init` — Print a shell wrapper (opt-in; `eval "$(xlii shell-init)"` in your rc file, or `xlii shell-init fish | source`) so that a `cd` inside `xlii code` follows you out to your real shell on exit. A child process can't move its parent's cwd directly — the wrapper does the `cd` for you. Only moves your shell if you actually navigated away from the project root.
- `/sync` — Force full Collection sync now.
- `/reset` — Forget this chat (working talk only). Journal, wiki, typed lines, and attachments stay.
- `/status` — Project/persona state, collection, pool, modes, attachments (REPL-aware).
- `/cost` — Pricing table + configured models.
- `/models` — Current orchestrator/worker + temps (code REPL).
- `/temp <0.0..2.0>` — One-shot orchestrator temperature override for *next* turn only.
- `/project` — List, find, switch, bind a startup task (`/project startup <task> [--auto]` · `--show` · `--clear` · `--off`), or remove registered projects (code REPL; `/projects` is a hidden alias).
- `/cwd [path]` — Return the live shell to the project root (no arg), or navigate to `<path>`. Works in plan mode too, where a bare `cd` would go to the model. (code REPL)
- `/describe <name>` — Self-document any slash command, agent tool, or plugin from live registries (very powerful for exploration).
- `/inspect` — Dump live REPLState, agent internals, attachments, and registry stats (debug console).
- `/debug` — Debug mode: staged bug hunt (hypothesize → instrument → reproduce → analyze → fix → verify).
- `/attachments` — Show durable attached refs + docs.
- `/clear-attachments` (aliases: `clearatt`, `forget-attachments`) — Remove all attachments.
- `/workspace` (alias `ws`) — Named durable attachment sets (save/load/list/delete current + global export/import).
- `/context` — Manage DeepContexts (save/show/list/delete/sync) for bridging to Grok Build.
- `/commands [reload|errors]` — List/reload project commands; inspect load errors.
- `/tools [show <name>|reload]` — Inspect/reload project-defined *agent* tools.
- `/reload` — Alias for `/commands reload`.
- `/interactive [add <prog> | remove <prog> | list]` — Manage the registry of
  full-screen programs that get a real TTY instead of being captured (defaults +
  your `~/.config/xlii/interactive.txt` additions). See §9c in the HOWTO; `!!cmd`
  is the per-command raw escape hatch.

### Mode / Flow Control (primarily code REPL)
- `/plan` — Enter plan mode (read-only investigation; produces numbered plan + scratchpad at `.xlii/plan-notes.md`). The working plan doubles as the session's todo surface: `panel` opens the item-level view (checkbox items with receipts + the amendments queue; also `/panel plan` · Alt-P), and the TUI carries a one-line strip above the input (`plan 3/7 ▸ next item`) that repaints live as `plan_check`/`plan_amend`/plan writes land. `--with <provider>` hires a configured gigwork brain as the session's planner: plan turns ride that endpoint (xAI server tools stripped by capability; status tag `PLAN·gigwork[<name>]`), the hire ends with the mode, and `/execute` always runs home.
- `/execute [rail]` — Approve plan and execute (with full tools). Add `rail` to run on the coding rail.
- `/cancel` — Exit plan mode without executing (archives scratchpad).
- `/off` — Leave every overlay/mode at once — plan · rail · debug · discovery · ops · howto · image · a foreground harness (cursor/claude/grok-build/codex) — back to the base surface (code/chat/scratch). One command instead of each mode's own `off`. Does not touch the base surface or the trust ladder (`/safe` lowers yolo).
- `/rail [next|back|status|off|start]` — Coding Rail (6-stage gated flow: Requirements → Architecture → Edge Cases → Pseudocode → Implementation → Self-Review). Code REPL only.
- `/yolo` (alias `yolo!`) — Disable bash confirmation gate (use with extreme care).
- `/safe` — Re-enable bash gate.
- `/verify` — Spawn a cold-context verifier on uncommitted work vs the last turn's task (PASS/FAIL with file:line). Gets the brief. Saves to `.xlii/verify-last.md` for `/consult --from-verify`. Code REPL.
- `/peer [--since <ref>]` — Blind peer review of a committed range (default `HEAD~1..HEAD`); the reviewer sees the diff but NOT the original intent. Saves to `.xlii/peer-last.md` for `/consult --from-peer`. Code REPL.
- `/loop <goal> [--from-plan] [--judge NAMES] [--max N] [--test CMD] [--budget USD] [--commit never|each|final] [--read-budget N] [--swarm N] [--merge auto|llm] [--merge-judge PROFILE]` — Autonomous build→test→judge→fix macro-loop, optionally fanning out parallel writer-workers in git worktrees (`--swarm`). `/loop status`, `/loop resume`, `/loop pause`, `/loop cancel` (alias `/loop off`) control a running loop. Code REPL only. See §4 Autonomous loop.
- `/alias <task>` — Promote a saved `/tasks` pipeline into a first-class slash command (task-args P2). Live reference: `/<task>` runs the *current* task, forwarding argv to its declared `[params]` (`/mytask needle scope=src/`). `/alias list` · `/alias rm <name>`. Per-project (persists in `.xlii/aliases.toml`, re-registers at startup); refuses to shadow a builtin. Code + chat REPL.
- `/providers` — Data providers (S2): list the manifests with key state (✓ configured / ✗ names the env var) and today's quota use, `/providers run <name> [k=v…]` (or bare `/providers <name>`) executes one into `.xlii/provider-results/`, `/providers new <name> <url>` scaffolds a BYO manifest into `.xlii/providers/`, `/providers show <name>` dumps one. The runner is `xlii/providers.py`; the `author-provider` stock skill teaches the agent the write-test loop. Code REPL only — it reaches the network and writes into the project, both outside the chat surface's project-blind contract.
- `/workbench [type]` — Pack **home | chat | code** (three-faces law) — not modes, not faces. On the face the pack **is** the slot dropdown + Panel Workbench list. Bare lists the catalog with the active pack marked; `/workbench <type>` persists `.xlii/workbench.json` and refreshes those views. **home** = scratch desk (`switch` door → projects list first). **chat** = companion + research power doors (plugins · kg · canvas · browser · locker · sources · results · …). **code** = lab doors (plan · git · jobs · explorer · skills). Retired peer names `research` / `general` alias to `chat` / `home` on load. `/workbench new <type> <name> [path]` creates a local-only project of that pack. Persona bind is **opt-in** (`--bind-persona`); pack ≠ identity. Override/extend via `.xlii/workbench.toml` (`[[type]]` rows). See `proposals/three-faces.md` · `proposals/typed-workbenches.md`.

### Knowledge Layer (both REPLs; durable attachments survive restarts)
- `/ref [persona]` — Attach another persona's memory to `search_project` (cross-session recall). Bare = list current. `/unref <persona>` to detach.
- `/unref <persona>` — Detach a ref.
- `/doc [name]` — Attach reference doc into system prompt (bare = list). `/undoc <name>` to detach.
- `/loadout [show]` — Inspect the active loadout (plugins, docs, refs, locker, model, approximate context budget). `/loadout save|load|list|delete|export|import|global-list|global-delete` manage saved bundles. Aliases: `/workspace`, `/ws` (bare `/workspace` = list saved loadouts).
- `/locker [add <path> [--once] | on|off|remove|list]` — Stage local files (images, text, PDF) for multimodal turns.
- `/upload [<path> … [--once]]` — Drag-drop popup to fill the locker (headless: pass paths or use `/locker add`).
- `/howto [topic | wiki [question] | fix [symptom] | latest | off]` — Self-guidance mode: ask xlii how to use itself — the tool (talk-primary; attaches bundled guide + live command list). `/howto wiki <q>` retrieves from `xwiki://` — the shipped, read-only self-wiki (version-locked, identical in every project): cites `xwiki://page#section`, opens ranked results in a side panel, folds those sections into the answer. For questions about **this project**, use `/askjo`.
- `/plugin [all | subscribe <id> | unsubscribe <id> | remove <id> | call <plugin>.<action> k=v … | panel]` — Plugins: subscribe, call an action directly, or open the catalog panel (`/lib` is a hidden legacy alias).
- `/get <intent>` — Find + invoke a subscribed plugin via natural language (rewrites prompt to use `plugin_search` + `plugin_get` + `bash`, or `plugin_call` when manifest actions exist).
- `/consult [--last N | --turns | --full] [--from-verify | --from-peer | --from <path>] <question>` — Ask a second, cross-vendor model for an independent opinion; prints under `[consult · <model>]`, never enters history. The simple setup is **`/consult --set-to <gigworker>`**: perma-hire a `gigwork.providers` entry as the default judge — a binding by name (`judges.consult = {"gig": "<name>"}`), so the endpoint/key/model stay defined once in the gigwork registry and any preset (Kimi, DeepSeek, Gemini, a local Ollama, …) can judge. Loop judges accept the same `gig:` key in their profiles. Inline `judges` profiles (provider `anthropic`/`openai`/`xai` + `api_key_env`) remain for hand-rolled setups; the legacy `secondary_ai` block is deprecated. See `/describe consult`.
- `/gigwork <provider> [--kit explore|bash|general] <task>` — Hire a configured **non-xAI** brain (Kimi, DeepSeek, Anthropic, Gemini, HF router, Ollama, …) for ONE read-only worker pass on xlii's own tools. `ls` (providers + key status), `presets` (built-in endpoint catalog), `add <preset> [--as name] [--model m]` / `add --custom <name> <base_url> <env> <model>`, `rm <name>`, `allow <name>` / `deny <name>` (toggle the orchestrator's `dispatch_subagent(gig=…)` allow list), `gaggle <name> <question>` (nested gaggle; same as `/gaggle`), `panel` (the providers + jams panel in the TUI; also `/panel gigwork` · Alt-H). Keys come from the environment (`api_key_env`) — never the file. Alias: `/gig`. See the GUIDE's "Gigwork" section and `/howto gigwork`.
- `/gaggle <name> <question>` — Run a named multi-brain preset (gaggle): every member (home xAI + gig workers, read-only, parallelism capped, `write: false`) answers the same question, then a merge step combines them — `synth_conflicts` (agreements / conflicts / verdict, one home-model call) or `concat_digest` (answers under member headers, no call). Stock: `second-opinion` (home explore + default gig), `debate` (gig first), `scout` (2× gig, digest). `ls` lists stock + `gigwork.jams` config presets; stock `"gig"` slots bind to the first allowlisted (else sole) configured provider. `add <name> <backend[:kit][@model]>… [--merge synth_conflicts|concat_digest] [--cap N]` composes a crew; `rm <name>` removes a configured crew. Alias: `/jam`. The orchestrator may pass `gaggle="second-opinion"` on `dispatch_subagent` when every foreign member is on `gigwork.defaults.allow`. Heavy investigate hops accept the same pin (or `gigwork.defaults.investigate_gaggle` / `investigate_gig` when the call site is unset — not both). Nested: `/gigwork gaggle second-opinion <question>`.
- `/describe modes` — Decision tree for plan vs rail vs loop vs verify/peer/consult.

### Chat-only (persona REPL)
- `/persona <name>` — Switch persona mid-session.
- `/personas` — List personas (current marked).
- `/edit [--id <name> | --file <path> | --doc <name> | --plugin <id>] [--new]` — Open a known artifact in `$EDITOR` (persona prompt / project file / reference doc / plugin). Available in both `code` and `chat`; `--file` is jailed to the project root. Bare `/edit` opens the current persona.
- `/forget` — Wipe current persona's transcript (confirm).

### Surface switching (both REPLs)
- `/code` — Switch back to the code surface in place (its own detached thread).
- `/chat [--id NAME]` — Switch to a chat persona in place; each surface keeps a
  separate conversation thread, so code history never bleeds into a persona's
  memory (or vice-versa). A project's code persona is always project-local.

### Marks — the cross-persona idea library (both REPLs)
- `/mark <name> [--window N]` — Tag the last turn as a named reference point;
  `--window N` captures the adjacent span, not just the single turn.
- `/marks [--all]` — List marks here; `--all` browses the cross-persona library.
- `/recall <mark> | <persona>:<mark> [--window M]` — Attach a marked turn as a
  reference. The `<persona>:<mark>` form reaches **across identities** — pull a turn
  from another persona into the current session.

**Prompt prefix indicators** (visible in REPL banner): `+1r`, `+2d`, `[plan]`, `[yolo]`, workspace names, etc.

**Note on durability**: `/ref`, `/doc`, `/locker`, `/attachments`, `/loadout` changes are persisted in `.xlii/session.json` (and global exports under `~/.config/xlii/loadouts/`).

### Source control & the working tree (code REPL)
- `/gitpain [status|tree] | stage|unstage|discard <path>… | stage-all|unstage-all | commit <msg>|commit journal|commit summary | generate | push|pull|sync | branch [name] | stash <msg>|-m <msg> [-u]|stash journal|stash list|stash pop|apply|drop [n] | sweep` — **Gitpanel** source control: stage, commit (journal-aware AI drafts), push/pull/sync, branch, stash-with-message, sweep. The write side of the `git://` doorway. For full git porcelain, type `git` in the shell.
- `/git …` — Deprecated alias of `/gitpain` (still works).
- `/checkpoint [label]` — Snapshot the working tree now (a manual checkpoint) so `/diff` and `/rewind` have a baseline.
- `/diff [N]` — Diff the working tree against a prior checkpoint (N steps back).
- `/rewind [N]` (alias `/undo`) — Restore the working tree to before the last N write-turn(s).

### Panels, files & theme (styled TUI)
- `/panel [home|skills|docs|bookmarks|images|wiki|tasks|jobs|gigwork|plan|files] [off] [--set left|right]` — Open a content panel beside the transcript (the Commander Panel surface).
- `/home` — Alias of `/panel home` (the home hub launcher). Not a session switch.
- `/file-view <path>` (alias `/fileview`) — Open the file-tab panel on a file's contents.
- `/theme` — Open the theme picker; click a theme to apply it live (trim/chrome only — list filtered to match transcript canvas polarity).
- `/history` — Open the input-line history panel (`.xlii/repl_history`, newest-first); select a row to prefill the command line for review-before-run — never executes on click. Also **Command → Input history…** and the **home://** History row. TUI only.
- `/config` — Open the config panel, four sections of session knobs. **models:** one row per role (orchestrator · worker · chat · help) with the resolved model + price hint — select opens a model picker and persists the choice — plus per-role sampling temperatures (0..2, persisted); profile picker. **session:** budget (cap + spend so far, edits in place — same fields as `/budget`, session-only), no-sync toggle (scratch locks it on), claim gates cycle, keep-session toggle, swarm ceiling, tool/worker iterations. **app:** panel side, editor, **canvas** row cycles transcript paper (`tui_canvas` dark/light), theme picker (trim), doorway-hotkey modifier (rebinds live), foreign-skills import toggle. **identity:** the persona row opens a picker and binds/unbinds the project's persona (same field as `xlii init --id`; lands next session); the xmpp row edits the daemon JID in place (validated bare JID, targeted `daemon.toml` rewrite — the password stays in its env var, untouched); the privacy row checks the xAI account on demand (tier · ZDR eligibility · data-sharing) — a report, never a toggle, because the data-sharing opt-in is irreversible and console-side. TUI only; headless use `xlii models set` + `/budget`.
- `/send <program> <target>` — Open a file or the last output in an external program (target: `last` · `reply` · `shell` · path · address; `--wait` to round-trip an edit back).
- `/xtool ls | <tool-id> [target] [--fix]` — Project-sniffed lint/format catalog: `ls` lists grouped tools with availability; `<tool-id>` seeds `!<argv>` into the command line for review-before-run (never auto-executes). Destructive variants (`--fix` / `--write`) appear in the seeded line. Also **Tools → Shell tools…** in the TUI (grouped by language; missing binaries dimmed). Python defaults to Ruff; JS/TS to Biome; legacy tools live under More….
- `/hygiene scan|strip [path…]` (alias `/sanitize`) — Text hygiene for paste / harness / cross-platform files: **scan** reports newlines, UTF-8 BOM, encoding, and one **credibility** counter (injection-class Unicode only — ZWSP, bidi overrides, exotic spaces; CRLF/BOM are portability notes, not danger). **strip** applies the default pack (LF · drop BOM · strip that junk); rare flags `--newlines lf|crlf|keep` · `--keep-bom`. Ingress auto-scan (take-note only, never auto-strip): harness `capture_output`, inbox enqueue (stderr), inbox drain. Not a lab-watermark eraser and not a language formatter (compose with `/xtool`). See `/describe hygiene`.

### Semantic memory & the journal
- `/wiki [list] | show|new|edit|rm <name> | distill <name> <addr>… [-- intent] | verify <name> [--promote]` — Author the project's semantic-memory wiki, including the AI distill + verify passes. CLI twin: `xlii wiki`.
- `/journal [--code-on | --code-off | --code-auto | --wiki-on | --wiki-off]` — Project Shadow: show status, or toggle the code journal / self-building wiki. CLI twin: `xlii journal`.
- `/askjo [wiki] <question>` — Ask Project Shadow about **this** project, grounded in the journal (episodic) and the project wiki (`.xlii/wiki/`, semantic). `/askjo wiki <q>` is the wiki-forward form: cites `wiki://page#section`, opens the ranked results in a side panel, and folds those pages into the answer; the wiki alone can answer even before the journal records. Code REPL.
- `/nfo [--print] [--show] [--clear] [--full|--brief] [--focus "topic"] [--global|--root]` — Generate an AI project-status `.nfo` that becomes the session's startup splash.

### Remote hosts
- `/remote [list | connect <name> | ls <name>/<path> | publish <local> <scheme>://<name>/<docroot> | add <name> [flags] | rm <name> | close [<name>]]` — Remote hosts (FTP/FTPS/SFTP + growing): one manager for every connection; browse via `ftp:// sftp:// dav:// smb://` addresses. Hidden legacy alias: `/ftp`. CLI twin: `xlii remote`.

### Images
- `/image edit "…" [--ref <path>] | auto|graphics|blocks|path` — Edit an existing image (Focus or `--ref`). View is Canvas. `/imagine` makes a new one.
- `/imagine "prompt" | --redo | --editprompt "…" | --save <path> | --last` — Generate an image via xAI Imagine (local artifact under `.xlii/artifacts/`). CLI twin: `xlii artifact image`.
- `/render <source> [--out NAME] [--engine ENGINE] [--open]` — Render markdown/text (aliases: `last`, `verify`, `peer`, `loop`, `plan`, or a path) to a local PDF under `.xlii/artifacts/` (free, ungated). CLI twin: `xlii artifact pdf`.
- `/mail list|search|read|send` — Inbox triage over IMAP (read ungated; send confirms with typed `send`). CLI twin: `xlii email`.

### Session economy & account
- `/compact [--recent N] [--dry-run]  |  /compact auto on|off` — Summarize older turns and continue with a shorter context window (busts the prompt cache — see `/cost`).
- `/budget [<usd>]  |  /budget --clear` — Set or show the soft session spend cap (`XLII_BUDGET`).
- `/account [status|keys|usage|billing|budget [off]] [--days N]` — xAI account hub (read-only): status · keys · usage · billing · budget. CLI twin: `xlii account`.

### More modes (code REPL)
- `/scratch [off|status]` — Scratch mode: an ephemeral, unbound, never-sync session (the free-traversal daily driver). CLI twin: `xlii scratch`.
- `/ops [on|off|status]` — Ops mode: OS diagnostics & workflow with platform-correct, read-only-first shell probes.
- `/approve <category…>  |  /approve --none` — Pre-authorize bash intent categories — the middle ground between `/safe` and `/yolo`.

### Admin & session meta
- `/project` — List, find, switch, bind a startup task, or remove registered projects (`/projects` is a hidden alias). `/project startup <task>` binds a per-machine boot ritual (confirm, or `--auto` when elevated); `--show` / `--clear` / `--off` inspect, unbind, or mute this session; `xlii code --no-startup` mutes one launch. `rm` is the REPL twin of `xlii project rm`: tear down a project's Collection(s) + registry entry + local `.xlii/` — **never your source files**.
- `/admin [status|unlock|lock|set-key|clear-key] [secret]` — Capability gate: elevate/lock the session and manage the admin key (guards capability-gated commands).
- `/tools-reload` — Reload project-defined *agent* tools (an alias of `/tools reload`).
- `/os [--refresh]` — Show the detected OS/distro profile (injected as `[SYSTEM]` in prompts).
- `/terminal` (alias `/inline`) — Leave the full-screen TUI and return to the inline REPL (the inverse of `/tui`).
- `/help [compose|power|all] [--search <keyword>] [--topics]` — Slash-command listing by tier; `--search` is keyword search across commands and help topics, `--topics` lists the attachable-topic index. The old doors keep working as hidden aliases: `/apropos`/`/search-help` = `--search`, `/manuals` = `--topics`.
- `/sh` — Natural language → a shell command (confirm before it runs); `--explain` and `--transform` post-process the last shell capture (`/explain` and `/shum` are hidden aliases).
- `/btw <note>` — Steer the running agent turn: the note folds into its history at the next tool boundary (the same safe yield point ■ stop uses). In the TUI an agent turn runs as a background `turn` job and the input stays free — shell lines, `/btw`, and `/jobs` all work mid-turn (`XLII_FG_TURNS=1` restores foreground turns). Bare `/btw` shows the active turn and pending steering.
- `/session` CLI twins: `xlii code --keep-session` starts an episode at launch; `xlii code --resume [id]` restores one (omit the id for the most recent). A crashed episode leaves `unclean` residue and earns one dim offer line at the next launch.
- `/session [on|off|list|resume [id]]` — Opt-in episode continuity (code only): `on` snapshots the FULL live history + conversation id to `.xlii/sessions/<id>.json` every turn (a `sess <id>` chip appears in the status bar); `resume` loads it back after a restart — same history, same conversation id, so the prompt cache can stay warm. The default launch stays ceremony-free: project turn-seed only.

### The Rail (Coding Rail) — Fine-Grained Gated Flow

Available only in the code REPL. Started with `/rail` (or `/rail start`). Each turn is forced through 6 explicit stages. The rail is stricter than `/plan`:

Stages (defined in `xlii/rail.py`):

0. **REQUIREMENTS_LOCK** (read-only) — Restate the full task. List explicit + implicit requirements. Ask for clarification on anything ambiguous. *Never* output code or pseudocode.
1. **ARCHITECTURE_PLAN** (read-only) — High-level components, data flow, classes, patterns, and rationale. No code.
2. **EDGE_CASES** (read-only) — Explicit checklist of edge cases, errors, validation, security, performance. Confirm plan handles them. No code.
3. **PSEUDOCODE** (read-only) — Detailed pseudocode or numbered steps for every component. No real code.
4. **IMPLEMENTATION** (writes unlocked) — Full, production-ready code with types, comments, no TODOs.
5. **SELF_REVIEW** (writes unlocked) — Review your own output against the earlier stages. Fix issues.

Control:
- `/rail next` (or `n`) — Advance (after human review). In some cases auto-runs the next stage turn.
- `/rail back` (or `b`, `prev`) — Step back to redo a stage.
- `/rail status` — Show current stage + brief.
- `/rail off` — Exit rail (or `/rail` with no args after it's running may toggle in some contexts).

Implementation notes (from source):
- Stages 0-3 map to the plan-mode read-only tool surface (no `write_file`, `edit_file`, `bash`, `dispatch_subagent`).
- Stage 4+ unlocks full tools.
- The rail injects a stage-specific system directive each turn.
- Can be seeded from a `/plan` via `/execute rail`.
- Complements (does not replace) the coarser `/plan` → `/execute` flow.

Use when you want the model to think visibly and you to gate every phase.

---

## 3. Agent Tools (The AI's Commands)

These are the **function calling tools** the orchestrator and worker agents use (defined in `xlii/tools.py:REGISTRY`, schemas in `tool_schemas()`, `worker_tool_schemas()`, `plan_mode_schemas()`, plus `dispatch_subagent_schema()` from `agent.py`).

They are what the AI "executes" when it decides to act. Workers get a read-only subset.

**Core built-in tools** (always available unless filtered by mode):

### File System & Project Inspection (read-heavy)
- **`read_file`**  
  **Description**: Read a file from the project. Returns line-numbered text. Use `offset`/`limit` for large files.  
  **Params**: `path` (required), `offset` (int, 0-based), `limit` (int).  
  **Example call** (as the AI would output):
  ```
  read_file({"path": "src/main.py", "offset": 10, "limit": 50})
  ```
  **Safety**: Read-only. Available in plan mode + workers.

- **`list_dir`**  
  **Description**: List immediate children of a directory.  
  **Params**: `path` (default ".").
  **Example**: `list_dir({"path": "src"})`

- **`glob`**  
  **Description**: Recursive glob using fnmatch against project-relative POSIX paths.  
  **Params**: `pattern` (required).
  **Example**: `glob({"pattern": "**/*.py"})`

- **`grep`**  
  **Description**: Recursive regex search across project files. Returns `path:line: match`.  
  **Params**: `pattern` (required, Python regex), `glob` (optional fnmatch), `case_insensitive` (bool).
  **Example**:
  ```
  grep({"pattern": "class .*Agent", "glob": "*.py", "case_insensitive": true})
  ```

### Mutation (code REPL only; **not** in plan mode or for workers)
- **`write_file`**  
  **Description**: Create or overwrite a file with content. Marks dirty for end-of-turn sync.  
  **Params**: `path`, `content` (both required).
  **Example**:
  ```
  write_file({"path": "README.md", "content": "# New Title\n\nContent here."})
  ```

- **`edit_file`**  
  **Description**: Replace exact substring. Errors if `old_string` not unique unless `replace_all=true`.  
  **Params**: `path`, `old_string`, `new_string`, `replace_all` (bool).
  **Example** (surgical):
  ```
  edit_file({
    "path": "app.py",
    "old_string": "def old_func():",
    "new_string": "def new_func():",
    "replace_all": false
  })
  ```

### Execution & External
- **`bash`**  
  **Description**: Run a shell command in the project root. **MUST** declare honest `intent`. Used for gating.  
  **Params**:
  - `command` (required)
  - `intent` (required, enum): `"read-only"`, `"modifies-project"`, `"modifies-system"`, `"network"`
  - `timeout` (int, default 60)
  **Critical rule** (from source): "You MUST declare `intent` honestly — lying ... is a serious bug."
  **Examples**:
  - Read-only: `bash({"command": "ls -la src/", "intent": "read-only"})`
  - Modify: `bash({"command": "git add -A && git commit -m 'refactor'", "intent": "modifies-project"})`
  - Network: `bash({"command": "pip install -U foo", "intent": "network"})`
  - Dangerous: `bash({"command": "sudo apt update", "intent": "modifies-system"})`

- **`search_project`**  
  **Description**: Hybrid RAG search across the project's xAI Collection (and attached `/ref` personas). Returns top-k chunks with file names.  
  **Params**: `query` (required), `limit` (default 10), `retrieval_mode` ("hybrid"|"semantic"|"keyword").
  **Example**:
  ```
  search_project({"query": "how authentication is implemented", "limit": 5})
  ```

- **`generate_image`**  
  **Description**: Generate image(s) from a text prompt (xAI Imagine). A **paid action**: prompts y/N via the console unless yolo; refuses cleanly when headless. Saves to `.xlii/artifacts/` and returns the path(s) — never the image payload. Excluded from plan mode, workers, and parallel fan-out by design.  
  **Params**: `prompt` (required), `n` (1–4), `model` (optional override).
  **Example**: `generate_image({"prompt": "a fox reading a terminal", "n": 1})`

- **`create_pdf`**  
  **Description**: Render markdown content or a project source alias/path to a local PDF under `.xlii/artifacts/` (free, local, no network). Returns the saved path — never PDF bytes. Ungated; excluded from plan mode, workers, and parallel fan-out.  
  **Params**: `content` (markdown) **or** `source` (alias/path), optional `title`, optional `out` (filename).
  **Example**: `create_pdf({"content": "# Resume\n\n…"})`

- **`read_email`**  
  **Description**: Fetch one message by id (`account:folder:uid`). Returns parsed headers + plain-text body (256 KiB cap). Never raw MIME or attachment bytes — attachments are name/size only. Safe in plan mode, workers, and parallel fan-out. Hidden from the live palette when no email account is configured (so chat cannot burn iterations retrying a guaranteed fail).  
  **Params**: `id` (required; or `message_id`), optional `account`.  
  **Example**: `read_email({"id": "personal:INBOX:42"})`

- **`search_email`**  
  **Description**: Search the inbox (IMAP TEXT + client fallback). Returns bounded summaries — id, from, subject, date.  
  **Params**: `query` (required), optional `account`, optional `unread_only`.  
  **Example**: `search_email({"query": "invoice due"})`

- **`send_email`**  
  **Description**: Send mail via SMTP. Gated outward action: UI must type `send` to confirm (`--yolo` bypasses). Refuses headless without yolo and refuses workers.  
  **Params**: `to`, `body`, optional `subject`, optional `html`, optional `account`.  
  **Example**: `send_email({"to": "you@example.com", "subject": "Re: …", "body": "…"})`

### xAI docs (DNA read — hosted docs MCP, not a Responses sub-call)
- **`xai_docs`**  
  **Description**: Read the official xAI documentation (docs.x.ai) so the house brain can look up how Grok and the API actually run — models, Responses, chat, Imagine, tools, limits, auth. Prefer this over `web_search` for anything about xAI/Grok/the API. Not xlii's wiki or howto (those are the garage engine; docgen owns them). Kill-switch: `config.xai_docs` (default on). Not an MCP marketplace.  
  **Params**: `action` (`search` | `get` | `list`), `query` (search), `slug` (get, e.g. `developers/models`), optional `max_results` (search, 1–20).  
  **Example**: `xai_docs({"action": "search", "query": "responses api tools"})` then `xai_docs({"action": "get", "slug": "developers/model-capabilities/tool-use"})`

### Server-side xAI Tools (exposed as ordinary tools; fire Responses API sub-calls)
- **`web_search`**  
  **Description**: Live web search via xAI. Prefer for current docs, library versions, errors outside project context. Returns answer text + citations. Prefer `xai_docs` when the question is about xAI/Grok/the API itself.  
  **Params**: `query` (required), `allowed_domains` (array), `excluded_domains` (array).
  **Example**: `web_search({"query": "grok-4 reasoning model context window 2025"})`

- **`x_search`**  
  **Description**: Search X (Twitter) posts. Good for real-time dev chatter, announcements.  
  **Params**: `query`, `allowed_x_handles`, `excluded_x_handles`, `from_date`, `to_date`, `enable_image_understanding`, `enable_video_understanding`.
  **Example**: `x_search({"query": "xai api changes", "allowed_x_handles": ["xai"]})`

- **`code_execute`**  
  **Description**: Execute Python in xAI's sandbox (NumPy/Pandas/Matplotlib/SciPy preinstalled). For verification/prototyping isolated snippets. Use `bash` for project-local verification.  
  **Params**: `task` (description or actual code).
  **Example**:
  ```
  code_execute({"task": "import numpy as np; print(np.array([1,2,3]).mean())"})
  ```

### Plugin System (user-curated capabilities)
- **`plugin_search`**  
  **Description**: Search subscribed plugins by natural-language intent. Returns matches with id/score/risk/desc. Returns `NO_PLUGIN_MATCH` if none. **Do not fabricate** plugin output.
  **Params**: `intent` (required).
  **Example**: `plugin_search({"intent": "current weather in tokyo"})`

- **`plugin_get`**  
  **Description**: Read full markdown of a subscribed plugin (endpoints, auth, curl examples). Use after `plugin_search`.
  **Params**: `name` (plugin id).
  **Example**: `plugin_get({"name": "openweather"})`

- **`plugin_call`**  
  **Description**: Invoke a structured HTTP action from a plugin manifest (`actions:` block). Validates params and performs the request directly — prefer over bash+curl when available. `exec` actions are not supported yet.
  **Params**: `plugin` (id), `action` (manifest action id), `params` (object, optional).
  **Example**: `plugin_call({"plugin": "open-meteo", "action": "geocode", "params": {"name": "London"}})`

  (Legacy plugins without manifests still use `bash` + `curl` after `plugin_get`.)

### Parallelism & Delegation
- **`dispatch_subagent`** (main orchestrator only; **not** for workers)  
  **Description**: Dispatch a read-only worker agent on a focused investigation task. Workers run in parallel. They see ONLY the brief you give them. Use `context` for snippets. Returns worker's final summary (with header showing model/iters/tokens/cost).  
  **Params**: `task` (required, specific), `context` (optional snippets/background), `role` (`explore` default — no shell · `bash` — read_file+bash only · `general` — full read-only palette), `gig` (optional: hire a configured non-xAI provider as the worker's brain — only names in `gigwork.defaults.allow`; reply badge becomes `gigwork[<name>]`, no xAI pool draw, and the gig's schemas have `search_project`/`web_search`/`x_search` stripped by capability).  
  **Example** (as orchestrator):
  ```
  dispatch_subagent({
    "task": "Investigate every Python file under src/ that imports 'auth'. Summarize the authentication patterns used. List files and key functions.",
    "context": "Focus on JWT vs session handling. Project uses FastAPI."
  })
  ```
  **Usage pattern**: Great for "investigate X in parallel and summarize each". Multiple can be called in one batch.

### Safety / Filtering Sets (from source)
- **PLAN_MODE_TOOLS**: `read_file`, `list_dir`, `glob`, `grep`, `search_project`, `web_search`, `xai_docs`, `x_search`, `plugin_search`, `plugin_get` (+ safe project tools). No writes, no `bash`, no `dispatch_subagent`.
- **WORKER_REGISTRY**: Everything except `write_file`, `edit_file`.
- **PARALLEL_SAFE**: Most read-only + `dispatch_subagent`, `code_execute`, etc. (see `xlii/tools.py:615`).
- Project tools can declare `plan_mode_safe`, `worker_safe`, `parallel_safe`.

**Tool result previews**: The REPL shows 1-3 dimmed preview lines after each tool call (file head, match counts, last lines, etc.).

**Hallucination guard**: Yellow warning if model claims work done but called 0 tools.

### Concrete Implementation Notes (from `xlii/tools.py`)

- `read_file`: Always line-numbered output (even for slices). Respects project root (refuses escape). Uses `errors="replace"`. Supports `offset`/`limit` for huge files.
- `write_file` / `edit_file`: Both call `_mark_dirty()` so the file is queued for automatic end-of-turn Collection sync. `edit_file` is strict: aborts if `old_string` is ambiguous and `replace_all=false`.
- `grep` / `glob`: Use a filtered walker (`_iter_searchable_files`) that applies the project's ignore rules (`.gitignore` + `.xliiignore`). Prevents the model from drowning in `node_modules`, `__pycache__`, etc.
- `bash`: The `intent` field is **mandatory** in the schema and is the only thing that drives the yolo/safe confirmation gate in the REPL loop. The agent must pick the strongest applicable category.
- `search_project`: Hybrid RAG over the xAI Collection. When `/ref` personas are attached, their collection IDs are also searched (see `ToolContext.extra_collection_ids`).
- `dispatch_subagent`: Orchestrator-only. Spawns a fresh `WorkerAgent` (read-only tool surface) on a separate chat key from the pool. The worker's entire transcript is summarized back with a cost/iteration header. Context blob is passed verbatim (use for file excerpts).

**Example of a full parallel investigation turn** (what the AI might emit):
```
dispatch_subagent({ "task": "Find every call site of `authenticate_user` and summarize the authz model used in each." })
grep({ "pattern": "def authenticate_user", "glob": "**/*.py" })
search_project({ "query": "authentication middleware", "limit": 8 })
```

---

## 4. Extensibility ("User Composes the System")

### Project Commands (for the *human* to type in REPL)
Place in `.xlii/commands.py` (or `.xlii/commands/*.py`).

Must define `def get_commands() -> list[REPLCommand]`.

They appear under "PROJECT COMMANDS" in `/help`. Can override builtins.

See `examples/project-commands.py` and `xlii/commands.py:load_project_commands`.

Example skeleton:
```python
from xlii.commands import REPLCommand
from typing import Any

def _my_deploy(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    console.print("Deploying...")
    return True

def get_commands():
    return [
        REPLCommand(name="deploy", handler=_my_deploy, description="Deploy this project", category="project"),
    ]
```

Reload with `/commands reload` or `/reload`.

### Project Tools (for the *AI agent* to call)
Place in `.xlii/tools.py` (or `.xlii/tools/*.py`).

Must define `def get_tools() -> list[AgentTool]`.

See `examples/project-tools.py` and `xlii/tools.py:load_project_tools`, `AgentTool` dataclass.

These are advertised in `tool_schemas()` and dispatched via `get_tool_fn`.

Example:
```python
from xlii.tools import AgentTool, ToolContext, ToolResult
from typing import Any

def _my_special_grep(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    # ... use ctx.project, ctx.clients, etc.
    return ToolResult(content="...")

def get_tools():
    return [
        AgentTool(
            name="project_grep",
            handler=_my_special_grep,
            description="Grep with project-specific ignores and highlighting",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            parallel_safe=True,
            plan_mode_safe=True,
        )
    ]
```

Reload with `/tools reload`.

### User Hooks (`.xlii/hooks/<event>/`)

Defined in `xlii/hooks.py`. User-wired observers (the "Emacs move").

- Events: `pre-turn`, `post-turn`, `pre-sync`, `post-tool`, `on-plan-approved`, `on-loop-cycle`, `on-turn-stop`.
- Any executable file placed in `.xlii/hooks/<event>/` (they are run in sorted order).
- Receives JSON on stdin: `{"event": "...", "project_root": "...", "data": {...}}` + `XLII_EVENT` env var.
- stdout is printed (dimmed). Non-zero exit prints a warning **but does not abort** the operation (observers only).
- Hard timeout (~10s); hung hooks are killed.

Example use: `post-tool` to auto-lint changed files, `on-plan-approved` to log or notify, `on-loop-cycle` to observe autonomous loop progress, `pre-sync` to run a quick check.

#### Policy (control) hooks — `on-turn-stop` (opt-in)

Every hook above is an **observer**: it cannot change control flow. The one exception is the `on-turn-stop` event, which can — but only after you explicitly opt in. This is how you wire "keep going until the tests pass" without re-prompting by hand.

- **Disabled by default.** An `on-turn-stop` hook runs as an ordinary observer (stdout printed, return value ignored) unless control is enabled via **either** `project.json` → `{"hooks": {"control": true}}` (persistent) **or** the session toggle `/hook-control on` (which overrides the project default; `/hook-control auto` reverts to it).
- **When enabled**, a control hook may print a single JSON object on stdout:
  `{"followup": "run the tests again and fix any failures", "max_loops": 3}`. The REPL runs `followup` as a synthetic user turn. The first hook that emits a non-empty `followup` wins.
- **Hard cap.** Follow-ups are capped at **5** (`POLICY_HOOK_HARD_CAP`); default budget is **3**. A hook's `max_loops` is clamped to the ceiling — there is no unbounded "keep going" (cloud-style infinite loops are out of scope by design).
- **Scope.** Only the inline `xlii code` REPL, and only when no autonomous `/loop` is driving (the loop owns its own continuation). Each follow-up is a normal turn, so the bash gate, plan/rail/debug modes, and `loop_lock_tests` all still apply.
- **Security.** A control hook can make the agent take actions you did not type. Treat enabling it like `/yolo`: only with hooks you wrote/audited, in a project you trust. The cap and the explicit opt-in are the guardrails; the model is not the boundary.

### Autonomous loop (`/loop`, `xlii loop`)

Macro-loop over full agent turns: build → test → judges → fix → repeat. State in `.xlii/loop-active.json` (resume after interrupt with `/loop resume` / `xlii loop --resume`).

| Flag | Default | Meaning |
|------|---------|---------|
| `--judge` | `tests` | Comma-separated judge profile names |
| `--max` | `5` | Max build→test cycles |
| `--test` | `pytest -q` | Shell oracle command |
| `--budget` | none | Judge spend cap (USD); cross-vendor (L2+) only |
| `--from-plan` | off | Goal from `.xlii/plan-last.md` |
| `--commit` | `never` | `each` (on test pass) or `final` (on done) |
| `--push` | `never` | Push before CI judge; requires `--commit each|final` |
| `--read-budget` | `3` | Max file excerpts per judge `READ_REQUEST` |
| `--swarm` | `1` | Writer-workers per build phase (capped by the `/swarm` ceiling) |
| `--merge` | `auto` | Overlap strategy: `auto` (git-only, fail-closed) or `llm` (merge-agent) |
| `--merge-judge` | config | Cross-vendor judge refereeing `llm` merge resolutions |
| `--workspace` | cwd | Project name (registry) or path (headless `xlii loop`) |
| `--status` / `--resume` | — | Show or resume a persisted loop (headless) |
| `--drain-inbox` | off | Run every `.xlii/inbox/*.md` task in order, archiving each to `inbox/done/` (B2) |

#### Goal inbox (`xlii loop --drain-inbox`)

Automation-lite (cursor-workflows.md B2): queue unattended work as files instead of operating a VM fleet. Defined in `xlii/inbox.py`.

- Drop a markdown task into `.xlii/inbox/<name>.md`. Optional frontmatter: `goal`, `judge` (comma string or YAML list), `max_cycles`, `test`, `budget`, `commit`. With no `goal:` key the markdown body **is** the goal, so a plain task file works.
- Optional seventh key `branch:` (pr-watch): drain **holds** the file without archiving when the tree is on a different branch or is dirty, so a queued PR fix cannot `git add -A` someone else's WIP.
- `xlii loop --drain-inbox` processes files in sorted (filename) order, each as its own headless loop with a fresh agent, then moves the file to `inbox/done/` (same-named re-drops are suffixed, not clobbered). The CLI's `--judge`/`--max`/`--test`/`--budget`/`--commit` are the per-file defaults; a file's frontmatter overrides them.
- Exit code is `0` only if **every** task reached `LOOP_PASS`, so cron can branch on it. A malformed file is reported, archived (so it can't jam the queue), and counted as a failure. A second concurrent drain exits `0` ("already running") under an inbox flock.
- **Cron / automation:** `*/30 * * * * cd /repo && xlii loop --drain-inbox --workspace /repo` drains every 30 min; a webhook or the XMPP fabric just writes a file into `inbox/`. No event bus, no cloud agent. (`xlii loop --resume` likewise survives a crash.)
- **PR producer:** `xlii pr sweep [N]` polls GitHub via `gh` (issue comments, review comments, reviews, checks) and writes one inbox file per actionable event. `xlii pr watch` loops that pass and, by default, spawns `--drain-inbox` after a pass that queued work. Marker default `@xlii`; `--all-comments` opens the floodgate. After a green drain, the `on-loop-cycle` hook writes `.xlii/pr-watch/outbox/<token>.json`; the next sweep (and the tail of `--drain`) flushes it: safe push (never default branch, never dirty tree, remote SHA must equal HEAD), `poll_pr_checks`, then a stamped `fixed in <sha>` reply (`<!-- xlii-pr-watch -->`). Push is watcher-side only — there is no inbox `push:` key.
- **Webhook (B2.1):** `xlii serve-inbox [--port 8765] [--token <secret>] [--workspace <path>]` runs a stdlib HTTP server bound to `127.0.0.1`; a `POST /` writes its body to `inbox/<name>.md` (`X-XLII-Name` header sets the stem, sanitized; `X-XLII-Token` gated if `--token`). It **only enqueues** — nothing runs until you `--drain-inbox`, so the webhook can't trigger execution directly. Localhost-only by default; expose it only behind a reverse proxy with auth.

**Judge profiles** live in `~/.config/xlii/config.json` under `judges`, layered by `kind`:
- `kind: shell` — the `tests` oracle: runs `--test` and blocks on failures (deterministic, free).
- `kind: ci` — built-in `github-ci`: polls required PR checks via `gh pr checks` after optional push.
- `kind: same_vendor` — built-ins `xai-verify` / `xai-peer`: a fresh Grok reads the diff and renders a verdict.
- `kind: cross_vendor` — outside-vendor judges via `secondary_ai`-style transport; they add **honesty guards** (`detect_weakening` alarms if tests were weakened/deleted to pass) and are what `--budget` caps. This is the L2+ tier.
- `kind: external` — Cursor Composer cold judge (`cursor` profile) via `cursor-agent` CLI.

Verdict contract (same as `/verify`): `PASS: …` or `FAIL` with numbered `path:line — finding [tag]` lines.

**Writer swarm** (`--swarm N > 1`): each writer-worker builds in its own git worktree under `~/.xlii/worktrees/` (outside `project_root`, so sync can't see them); results integrate sequentially. `--merge auto` fails closed on overlapping edits; `--merge llm` resolves them with a merge-agent. See `xlii/swarm.py`.

See `xlii/loop.py`, `xlii/loop_judge.py`, `xlii/ci_judge.py`, `xlii/swarm.py`, `proposals/autonomous-loop.md`, `proposals/ci-judge.md`, `proposals/writer-swarm.md`.

See `xlii/hooks.py:HOOK_EVENTS` and `run_hooks()` (called from agent and cli paths). Hooks are a primary extensibility vector for "user composes the system."

### Other Curated Layers
- **Personas**: `~/.config/xlii/personas/<name>.md` + per-persona Collection under `~/.xlii/chat/`.
- **Docs**: `~/.config/xlii/docs/<name>.md` (or per-project?); attached via `/doc`.
- **Plugins**: `~/.config/xlii/plugins/<id>.md` (stock in `xlii/stock_plugins/`). Subscribe per-project via `/plugin`.
- **Roles** (`proposals/roles.md`): named specialist bundles — a persona with a stocked loadout (`skills:`/`docs:`/`plugins:`/`refs:`/`model:` + identity body). Tiers `project (.xlii/roles/) > global (~/.config/xlii/roles/) > stock (bundled xlii/stock_roles/)`. `/role` lists, `/role <name>` activates (**equip** the loadout in code; **become** the persona in chat), `/role off` drops an equip; `/hire` aliases it; `xlii role list|show` headless. Shipped starters: `code-architect`, `test-engineer`, `debugger` (all bundle the stock `grounded-analysis` skill), and `fleet-conductor` (bundles `vectoring` + `grounded-analysis`). A role *references* skills, never redefines them.
- **Vault**: Encrypted credentials via `xlii auth set` (for plugin `env:` requirements).
- **Marks / Workspaces / DeepContexts**: Additional durable cross-session state.

---

## 5. Verification & Best Practices (from codebase)

- **Mandatory verification** (especially in `xlii code`): After writes/edits, use `bash` (with `read-only` intent) to run tests (`pytest`, `ruff`, `mypy`, etc.) **before** claiming success. The agent is instructed to do this.
- **Plan mode first** for non-trivial changes: `/plan` → review → `/execute`.
- **Intent honesty** on every `bash`.
- **Workers are read-only** by design (safety).
- **Source of truth**: Local files on disk. Sync happens automatically on dirty paths at end of turn (or via `/sync`).
- **RAG + attachments**: `search_project` sees current project + `/ref` personas. `/doc` inlines rules into system prompt.
- Use `/describe <thing>` liberally inside a session to explore live state.

---

## 6. Environment variables

| Variable | Effect |
|----------|--------|
| `XAI_MANAGEMENT_API_KEY` | **Required.** The one privileged credential (creates/rotates/revokes chat keys, manages collections). Read from env only — never written to disk. |
| `XAI_API_KEY` | **Not read from the environment.** The chat key comes from the `keys` list in `~/.config/xlii/config.json` (run `xlii config` to create it); `xlii setup` provisions the pool. |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` (or any name) | Credential for a cross-vendor `/consult` / `judges` profile. Read **indirectly**: a `judges` profile's `api_key_env` field names which variable to read, and you export that variable. Not hardcoded — there is no `CONSULT_API_KEY`. |
| `XLII_CONFIG_DIR` | Override `~/.config/xlii` (test seam / alternate profiles). |
| `XLII_SHELL_PRIMARY` | `0`/`false`/`no`/`off` → bare input in `xlii code` talks to the AI instead of running as a shell command. Default on. |
| `XLII_NO_TITLE` | Don't show the live cwd in the terminal title; show it inline in the prompt instead. |
| `XLII_CWD_FILE` | Set by the `xlii shell-init` wrapper; the path xlii writes its final cwd to on exit so your real shell can follow a `cd` out. Internal — the wrapper sets it. |
| `XLII_SHELL_STYLE` | `styled` → the unified block presentation layer (shell/tool/answer blocks, toolbar). Default `raw` (classic output). `raw` forces classic. The `--tui` front-end forces `styled`. |
| `XLII_SHELL_MAXLINES` | Display budget for captured output under `styled` (default `40`, head+tail so the verdict survives; `0` = never truncate). The model always receives the full text. |
| `XLII_NO_TOOLBAR` | Hide the bottom status bar (mode · cwd · attachments) while keeping the rest of the styled UI. |
| `XLII_NO_TRACELOG` | Disable the rolling debug trace log (`~/.config/xlii/logs/xlii.log`). |
| `XLII_VAULT_KEY` | Master key for the credential vault in headless/CI contexts (otherwise the OS keyring, with a file fallback, is used). |
| `XLI_VAULT_KEY` | **Legacy alias** of `XLII_VAULT_KEY` (still accepted). See [LEGACY.md](LEGACY.md). |
| `XLII_EVENT` | Set by xlii when invoking a user hook — the hook reads it to know which event fired. (You don't set this.) |
| `XLII_SESSION`, `XLII_SESSION_PROJECT` | Set by an active interactive session and inherited by child processes, so a nested `xlii code`/`chat` for the same project can be refused before it collides on state. Internal — don't set these by hand. |

Intentional `xli` leftovers (keyring service name, collection/key prefixes, path drains): **[LEGACY.md](LEGACY.md)**.

---

## 7. Configuration & storage layout

### `~/.config/xlii/config.json`

The single global config (`chmod 600` enforced). `management_api_key` is **never**
in this file — it's env-only (`XAI_MANAGEMENT_API_KEY`). `keys[0]` is always the
primary (sync + main agent); workers round-robin through the rest.

```json
{
  "orchestrator_model": "grok-4.20-reasoning",
  "worker_model": "grok-4-1-fast-non-reasoning",
  "orchestrator_temperature": 0.7,
  "worker_temperature": 0.3,
  "team_id": "c13e6a5c-...",
  "keys": [
    {"api_key": "xai-...", "label": "primary-1", "api_key_id": "...", "expire_time": "2026-10-26T00:00:00Z"}
  ],
  "max_tool_iterations": 20,
  "max_worker_iterations": 10,
  "max_parallel_workers": 8,
  "max_file_bytes": 1000000,
  "pricing": {},
  "models_detected_at": "2026-04-29T00:00:00Z",
  "region": null,
  "serve": {"public": {"base_url": "", "code_ttl_s": 300, "session_ttl_s": 28800,
                       "idle_timeout_s": 1800, "max_sessions": 3}},
  "gigwork": {
    "providers": {
      "kimi": {"kind": "openai_compat", "base_url": "https://api.moonshot.ai/v1",
               "api_key_env": "KIMI_API_KEY", "model": "kimi-latest"},
      "haiku": {"kind": "openai_compat", "base_url": "https://api.anthropic.com/v1/",
                "api_key_env": "ANTHROPIC_API_KEY", "model": "claude-haiku-4-5",
                "cache": "auto"},
      "claude-native": {"kind": "anthropic_native",
                        "base_url": "https://api.anthropic.com/v1",
                        "api_key_env": "ANTHROPIC_API_KEY",
                        "model": "claude-sonnet-5",
                        "cache": "auto"}
    },
    "defaults": {"allow": ["kimi"]},
    "jams": {"trio": {"members": [{"backend": "xai"},
                                     {"backend": "kimi", "kit": "bash"},
                                     {"backend": "haiku", "model": "claude-sonnet-5"}],
                         "merge": "synth_conflicts", "max_parallel": 3}}
  }
}
```

`region` pins the API plane (chat, media, collections, model discovery) to a
regional xAI edge — `"us-west-2"` → `us-west-2.api.x.ai`; `null`/absent → the
global `api.x.ai`. Regional edges are independent ingress points, so a pinned
region rides out a flapping global edge and keeps traffic in-region. The
`XAI_REGION` environment variable overrides it per-shell. The management API
host is a separate plane and is never regionalized. `/status` shows the active
host.

`serve.public` is the pairing-code gate for `xlii serve --public` (see the
GUIDE's "Public serve posture"). `gigwork.providers` are named non-xAI worker
brains (`/gigwork add <preset>` writes them; keys are env-only via
`api_key_env` — an inline `api_key` is refused on load); per-provider
`kind` is `openai_compat` (default) or `anthropic_native` (Claude workers /
`/plan --with` only — native `/v1/messages` with real `cache_control` and
`cache_read_input_tokens` in stats; judges stay compat). `temperature` pins a
sampling value and `cache` (`true`|`false`|`"auto"`)
controls anthropic-style `cache_control` marks (`auto` = on for
`api.anthropic.com` and always on for `anthropic_native`). `gigwork.defaults.allow` is the list the
ORCHESTRATOR may hire via `dispatch_subagent(gig=…)` — toggled by `/gigwork
allow|deny <name>`; the human `/gigwork` can hire any configured provider.
`gigwork.jams` holds named crews (`/jam add` writes them; a member
`model` overrides that member's model).

### On-disk layout

| Path | Holds |
|------|-------|
| `~/.config/xlii/personas/<name>.md` | persona system prompts (hand-editable) |
| `~/.config/xlii/docs/<name>.md` | reference docs, inlined into the prompt on `/doc` |
| `~/.config/xlii/plugins/<id>.md` | plugin descriptors (frontmatter: id, name, description, categories, risk, auth_env_vars + markdown body) |
| `~/.config/xlii/projects.json` | global project registry, auto-maintained by `xlii init` |
| `~/.config/xlii/vault.enc` | Fernet-encrypted plugin credentials (`xlii auth …`) |
| `~/.config/xlii/logs/xlii.log` | rolling debug trace log |
| `<project>/.xlii/project.json` | collection id (empty for `--local`), name, `local_only`, `extra_ignores`, `conversation_id` |
| `<project>/.xlii/manifest.json` | `relpath → {sha256, mtime, file_id, last_synced}` for diff-based sync |
| `<project>/.xlii/index.txt` | paths+sizes index (when `--snapshot`) |
| `<project>/.xlii/plugins.txt` | subscribed plugin IDs |
| `<project>/.xlii/session.json` | durable attachments (`/ref` `/doc`) + named workspaces |
| `<project>/.xlii/repl_history` | that project's REPL command history |
| `~/.xlii/chat/<persona>/.xlii/` | per-persona state (personas are projects under the hood) |
| `<project>/.xliiignore` | optional `.gitignore`-syntax extra patterns to skip during sync |

---

## 8. Architecture

### Sync engine
On startup the REPL walks the project (respecting `.gitignore` + `.xliiignore`), diffs against the Collection by sha256, and uploads/updates/removes deltas across `max_parallel_workers` threads with **429 backoff per op** (exponential, max 5 retries). Empty files and binaries are skipped. After every mutating turn, dirty paths flush. **Local disk is the source of truth.** The default ignore list is aggressive (`.git/`, `.xlii/`, `venv/`, `node_modules/`, build outputs, …). *Known limitation:* only the root `.gitignore` is read — for monorepos, use `.xliiignore` at the root.

### Streaming & presentation
Orchestrator completions stream via `stream=True`. By default content renders progressively through a `rich.Live` Markdown widget; tool calls materialize as `→ tool` events with ✓/✗ badges and dim `⎿` previews. With `XLII_SHELL_STYLE=styled` (and in `--tui`), everything routes instead through the unified block grammar (one renderer, one theme; shell, tool, and answer share a look). **Reasoning models** emit `delta.reasoning_content` separately from `delta.content` — reasoning is private thinking, surfaced in a yellow panel only when the model reasoned but produced no answer (so the failure is diagnostic, not silent).

### Multi-key swarm
Tool calls in a batch are classified parallel-safe (reads, greps, `search_project`, server tools, `dispatch_subagent`) or sequential (writes, edits, bash). Parallel-safe calls fan out via a thread pool. Each `dispatch_subagent` worker pulls a chat key round-robin, runs its own loop with **read-only tools only**, and returns a tight summary. Workers cannot write, edit, or dispatch further workers.

### xAI server-side tools
`web_search`, `x_search`, `code_execute` are local function tools that fire one-shot Responses-API sub-calls (xAI moved these off Chat Completions). Sub-call usage is absorbed into the turn's stats.

### Plugin invocation (L1, shipped)
`/get <intent>` → `plugin_search(intent)` (top-5 from the subscribed set) → `plugin_get(name)` (full markdown) → agent composes a `curl` (expanding `${ENV_VAR}`) → `bash(..., intent="network")` → interpret + answer. L2 (templated curl) and L3 (`plugin_call` structured RPC) are designed but deferred.

### Plan mode & the rail
Plan mode restricts the tool list to read-only investigation + injects a preamble; the agent outputs a numbered plan; `/execute` replays it with the full toolset. The rail goes further — six stages with writes locked at the tool layer until the design stages pass (see §The Rail).

### Hallucination guard
After each turn, if the orchestrator's text contains a past-tense action verb (`created`, `wrote`, `tested`, …) but `tool_calls == 0`, a yellow `⚠ model said "X" but called 0 tools` nudge surfaces. False-positives are tolerable — it's a nudge, not a wall.

### Personas, knowledge layer, registry/GC
Each persona is a Collection-backed project at `~/.xlii/chat/<name>/`; recent turns load inline, older ones sync as searchable `turns/<ts>.md`. `/attach doc` rebuilds `_effective_system_prompt()` each turn; `/plugin`+`/get` add `plugin_search`/`plugin_get` to the palette only when something is subscribed. `xlii gc` cross-references the registry against the cloud + filesystem to find orphan Collections.

**Housekeep.** `xlii sweep` (or `/sweep` from either REPL) inventories Collections, registry ghosts, and chat keys. Bare, it only looks; `--empty` / `--test` / `--ghosts` / `--keys` clean, and it never deletes a Collection a live `project.json` still claims.

---

## 9. Security model

- **One privileged credential, env-only.** `XAI_MANAGEMENT_API_KEY` is read from the environment, never stored on disk.
- **Chat keys are scoped, expiring, rotatable.** Each `xlii setup`/`bootstrap` key has an `expireTime` (default 180 days) and named ACLs; revoke via `xlii keys revoke` (server + local).
- **Bash gating.** The agent's `bash` tool requires an honest `intent` (`read-only`, `modifies-project`, `modifies-system`, `network`); riskier intents prompt y/N unless `--yolo`/`/yolo`. Workers may only run `read-only` bash.
- **Plugin risk levels.** Each plugin declares `risk:` (`low`/`medium`/`high`). Plugin calls go through `bash(intent=network)`, so the bash gate prompts by default.
- **File perms.** `config.json` is `chmod 600`; durable state writes go through atomic writers (tmp+fsync+rename) at restrictive modes.
- **Nested-session guard.** A second same-project session is refused before it can collide on state (see §7 / `XLII_SESSION`).
- **Public-release safety.** Don't paste real keys into model inputs (xAI auto-flags accounts); rotate periodically; the 180-day expiry caps blast radius if `config.json` leaks.

---

## 10. Troubleshooting

- **`xlii setup` couldn't auto-detect models** — some accounts don't expose `/v1/models`. Set them: `xlii models set --orchestrator <name> --worker <name>`.
- **Server tools error `'OpenAI' object has no attribute 'responses'`** — `openai` too old: `./venv/bin/pip install -U 'openai>=1.50'`.
- **Sync: "Empty stream received"** — 0-byte files (auto-skipped at the walk step); check whether a file's size changed mid-sync.
- **Sync uploaded files I didn't expect** (e.g. `.next/`) — add the dir to `.xliiignore` at the project root.
- **Yellow `⚠ … called 0 tools`** — the model claimed work without tools (likely a hallucination). Verify before trusting.
- **Reasoning model produces no answer** — it "thought" without emitting. Rephrase, or use a non-reasoning model (`xlii models set --orchestrator grok-4`); the yellow reasoning panel shows where it stuck.
- **`/doc <name>` isn't sticking** — reasoning models can reason past system-prompt rules. Rewrite the doc as firm imperative English, or use a non-reasoning model for persona/style work.
- **`/doc CALLMESIR` → "no such doc"** — that's the *content*, not the *name*. Names are slug-cased; use the first column of `xlii doc --list`.
- **`--tui` won't render / terminal garbled after `/tui`** — make sure the `[tui]` extra is installed and the terminal is a real TTY; report layout issues (the inline REPL is unaffected).
- **`xlii code --tui` refuses to launch** — you're inside another session for the same project (the nested-session guard). Use `/tui`, or `--force`.

---

## 11. Known limitations / future work

- **Plugin authoring wizard** — `xlii plugin --new <id>` opens `$EDITOR` on a template today; an interactive `xlii plugin add` wizard (id/name/risk/URL/auth/params → generated markdown) is designed.
- **Bulk plugin import** — a planned `xlii plugin import <dir>` to seed the catalog from existing connector definitions.
- **Plugin tiers L2 + L3** — only L1 (read-and-bash) ships; templated curl and structured `plugin_call` are deferred until L1 friction is real.
- **Committable default attachment sets** — `/ref`/`/doc` already persist per-project (`.xlii/session.json`); auto-attaching defaults on a fresh clone (`.xlii/refs.txt`/`docs.txt`) is not built.
- **Multi-machine fabric (XMPP/OMEMO)** — behind the `[daemon]` extra. `xlii notify` (send-only) and `xlii daemon` (experimental inbound, tailnet + OMEMO + JID-whitelist gated) both ship; the offline-verifiable parts are unit-tested but the live transport needs your own tailnet. Setup: [HOWTO §13](HOWTO.md); plan: [`proposals/xmpp-fabric.md`](../proposals/xmpp-fabric.md).
- **Hierarchical `.gitignore`** — only the root is read; use `.xliiignore` for monorepos.
- **TUI interrupt** — `xlii code --tui` now has full slash-command parity (routed through the same dispatch as the inline REPL), a `/`-completion popup, end-of-turn sync, and the frame chrome (status strip, pinned question, heartbeat, context meter). What's left: a Ctrl-C interrupt to cancel a running turn, and flipping `XLII_SHELL_STYLE` to styled by default.
- **Auto-snapshot refresh** — `--snapshot` builds the index once; `/sync` rebuilds it (auto-rebuild on stale index is a future win).

### Explicit non-goals (Cursor-parity workflow surfaces)

These are deliberately **not** built — xlii is a composable terminal substrate, not
a re-host of a vendor cloud product (see [`proposals/cursor-workflows.md`](../proposals/cursor-workflows.md)):

| Surface | Decision | Rationale |
|---------|----------|-----------|
| Cloud agent VMs | **Do not build** | Ops burden; contradicts the terminal substrate — delegate via `/cursor` |
| Automations marketplace | **Do not build** | Vendor-curated; xlii has plugins + skills |
| IDE-integrated debug UI | **Do not build** | Terminal-native; a future `/debug` mode (proposal Tier B0) suffices |
| Browser / computer use | **Optional MCP plugin** | User subscribes; not core |
| PR video/screenshot artifacts | **Skip** | Poor fit for terminal UX |
| Secret per-model harness | **Do not pursue** | Conflicts with user-overridable `prompts/*.md` |

---

## Sources (for maintainers)

- CLI surface (argparse tree) + subcommand modules: `xlii/cli.py`, `xlii/cmds/`
- Slash command registry + help generation + project commands: `xlii/commands.py`; built-in slash handlers: `xlii/repl_cmds/`
- Agent tools + schemas + project tools: `xlii/tools.py`
- Tool execution, dispatch_subagent, parallel batching, plan/rail/rail hooks: `xlii/agent.py`
- Server tools (web/x/code_execute wrappers): `xlii/server_tools.py`
- REPL loop, state, attachments, workspaces, context: `xlii/repl.py`, `xlii/context.py`
- Autonomous loop + judges + writer swarm: `xlii/loop.py`, `xlii/loop_judge.py`, `xlii/swarm.py`
- The coding rail; live profile/surface seam; interactive-program registry: `xlii/rail.py`, `xlii/profile.py`, `xlii/interactive.py`
- Examples: `examples/project-commands.py`, `examples/project-tools.py`
- Narrative docs: [`README.md`](../README.md) (storefront), [`GUIDE.md`](GUIDE.md) (command map), [`HOWTO.md`](HOWTO.md) (walkthrough)
- Subcommand/slash tables: generated by `xlii/docgen.py` into `docs/GUIDE.md`; CI-checked by `scripts/check_docs.py`

Update this file when new commands/tools are added to the registry. The generated
README tables stay fresh automatically; the prose here is hand-maintained.

**Run `xlii help` or `/help` inside a REPL for the freshest generated view.**
