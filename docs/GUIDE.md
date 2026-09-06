# xlii (42) — Guide & command reference

**xlii** — the personal AI substrate (internal package + CLI `xlii`, user-facing **XLII**).

A terminal-native, user-curated AI environment built on xAI **Grok + Collections**. Two REPLs (project coding + persona chat), a knowledge layer you compose yourself (personas, reference docs, plugins), a self-provisioning multi-key swarm, and an optional full-screen TUI — all in one CLI you live in.

The thesis, in one sentence:

> **The vendor provides primitives; the user composes the system.**

> **Status: alpha.** Works end-to-end against a real xAI account. The architecture is settled; specific features are still in flux. Expect rough edges.

This is the full guide: what xlii is, install + first-time setup, the command map, and the design thesis. For the short pitch, start at the [README](../README.md).

### The docs

| Doc | For |
|-----|-----|
| **[README](../README.md)** | The one-screen pitch + 60-second start. The storefront — start there. |
| **Guide** (this file) | What xlii is, install + first-time setup, the command map, the design thesis. The full tour. |
| **[OVERVIEW.md](OVERVIEW.md)** | A **shareable breakdown** — what xlii is, market positioning, who it's for, honest assessment. Good for onboarding collaborators or explaining the project cold. |
| **[HOWTO.md](HOWTO.md)** | The start-to-finish **walkthrough** — fresh machine → working substrate: first project, your first persona, plugins, the rail, the TUI, the Grok Build bridge. |
| **[REFERENCE.md](REFERENCE.md)** | The **extended reference** — architecture, the full config schema, every `XLII_*` env var, the agent tool catalog, the security model, hooks, plugin authoring, troubleshooting. |

The roadmap lives in [`ROADMAP.md`](../ROADMAP.md). Historical design proposals are archived out of the public tree.

---

## Why this exists

There's a category gap in AI tooling:

- **Claude Code, Codex, Cursor** — vendor-curated coding agents. The tool palette is what the vendor ships, extended via MCP servers (heavyweight, professionally maintained, not yours).
- **ChatGPT, Claude.ai, the Grok app** — vendor-hosted SaaS chatbots. Memory exists but is vendor-controlled. No file ops, no multi-machine, no curation.
- **Frameworks (LangChain, …)** — developer toolkits to *build* one product. Not a shell you live in.
- **Self-hosted RAG apps (Khoj, MemGPT)** — memory-focused, usually one machine, one purpose.

xlii sits in the unoccupied gap: **a substrate that rewards investment.** Like Emacs, but for AI tooling. You write a few personas, a few reference docs, a few plugins for APIs you actually use, and the system becomes uniquely yours. Power users will love it; casual users will bounce — and that's fine.

---

## Headline capabilities

- **Two complementary REPLs.** `xlii code` for project work (read/write files, run tests, parallel workers, mandatory verification). `xlii chat` for persona-based conversation with persistent memory. Switch between them **in place** mid-session (`/code` ↔ `/chat`) — each keeps its own detached thread. `/mojo` is the front door to the **mobile journal** (that one traveling persona; unnamed it is called mojo). xlii is the substrate. Same entity you reach by texting the node over XMPP. Over the fabric, the daemon runs its turns as that persona too (`[agent_fallback] persona`), so one journal answers from every surface. `/name` is just the spelling.
- **A knowledge layer you compose** — four slash commands working in concert:
  - `/ref <persona>` — attach another persona's memory to the current session (cross-session recall)
  - `/doc <name>` — attach a reference doc into the system prompt (rules, conventions, specs)
  - `/get <intent>` — find and invoke a subscribed plugin matching a natural-language intent
  - `/lib …` — manage the plugin library (list, subscribe, unsubscribe, remove)
- **User-curated plugins.** Each plugin is a markdown file describing an API. Write one in 60 seconds; subscribe from any project. No MCP server to spawn.
- **Multi-key swarm.** Self-provisioning chat keys; the orchestrator dispatches read-only worker agents in parallel, each on its own key.
- **Plan mode + the coding rail.** Plan mode = read-only investigation → numbered plan → approve → execute. The rail is stricter: six stages (Requirements → Architecture → Edge Cases → Pseudocode → Implementation → Self-Review) with writes locked at the tool layer until the design stages are done.
- **Workflow recipes.** `/howto workflow` maps familiar Cursor-style patterns onto xlii's native surfaces: `/plan`, `/rail`, `/loop`, `/swarm`, `/verify`, `/peer`, `/consult`, and `/cursor`.
- **Autonomous loop.** `xlii loop` (headless) / `/loop` (in-session) runs full build → test → fix cycles unattended until tests *and* judges pass or a stop rule fires. Judges range from a shell test oracle to same-vendor cold reviewers to cross-vendor outside eyes (with honesty guards against test-weakening). Optionally fan out parallel **writer-workers** in isolated git worktrees (`--swarm N`, merged sequentially) and auto-commit as it goes.
- **Independent review on demand.** `/verify` (cold-context check of uncommitted work vs. the task), `/peer` (blind review of a committed range — no author intent), and `/consult` (a second, cross-vendor model for an outside opinion).
- **Curated loadouts, workspaces & marks.** Personas carry a frontmatter loadout (plugins/docs/refs/model); `/workspace` saves named durable attachment sets; `/mark` + `/recall` build a cross-persona idea library you can pull turns from across identities.
- **Optional full-screen TUI.** `xlii code --tui` (or `/tui` in-session) renders shell runs, tool calls, and answers as one unified block grammar, with a `/` command popup, pinned question, context meter, and command history — see [HOWTO §TUI](HOWTO.md).
- **Graphical face skins.** The desktop face is an undecorated window: CSS-variable packs (dark/light/slate/mojo) plus file-based **skin packs** (`pack:<name>`) with 9-slice chrome. `xlii skin` lists, lints, and installs packs; authoring lives in the selfwiki.
- **Real terminal programs, handled.** A curated registry hands full-screen programs (`vim`, `less`, `htop`, `lazygit`, …) a real TTY instead of capturing them; `/interactive` manages the list, and `!!cmd` is the always-raw escape hatch.
- **Grok Build bridge.** Expose curated workspaces as **DeepContexts** over an MCP server (`xlii mcp deep-contexts`) so the Grok Build TUI can attach xlii as durable memory + capabilities.
- **xAI server-side tools as first-class.** `web_search`, `x_search`, `code_execute` (Python sandbox) callable as ordinary tools.
- **xAI docs as DNA.** The house brain reads the hosted xAI docs MCP (`xai_docs`) so it knows how Grok and the API actually run. xlii's own wiki/howto stay the garage engine's self-docs (docgen). Not a marketplace of MCP servers.
- **Local-only mode + path snapshots.** "Midnight Commander on steroids" for directories you don't want uploaded — NAS, photo libraries, PDFs, archives. `--snapshot` caches a paths-and-sizes index for fast structural search.
- **Auto-syncing.** Files on disk are the source of truth; changed files push to the Collection at the end of every turn.
- **Hallucination guard.** A yellow warning when the model claims work was done but called zero tools.
- **Self-managing credentials.** One management key in env, all chat keys auto-created, auto-expiring (180 days), rotatable in place.
- **Cost tracking.** Per-turn token + USD totals, broken out orchestrator vs workers.

---

## Requirements

- **Python 3.11+**
- **Linux / macOS** (Windows untested)
- An **xAI account** with a **management API key** (xAI console → Team Settings) and at least one team you have admin access to (auto-discovered)
- `openai>=1.50` recommended (server-tool calls use the Responses API)

You do **not** need to manually create chat API keys — xlii provisions them for you.

---

## Install

```bash
git clone <your-repo-url> xlii
cd xlii
python3 -m venv venv
./venv/bin/pip install -e .          # add '.[tui]' for the optional Textual UI
sudo ln -s "$(pwd)/venv/bin/xlii" /usr/local/bin/xlii   # put it on PATH
```

---

## First-time setup

**1. Export your management key** (add it to your shell rc):

```bash
export XAI_MANAGEMENT_API_KEY=xai-...your-management-key...
```

It's the **only** privileged credential — it creates, rotates, and revokes other keys and manages your collections. xlii **never** stores it on disk.

**2. Run `xlii setup`:**

```bash
xlii setup            # tune the worker count with --workers N
```

One command writes `~/.config/xlii/config.json` (chmod 600), discovers your `team_id`, creates **1 primary + 8 worker** chat keys (180-day expiry) via the Management API, saves them, and auto-detects the best orchestrator/worker models.

**3. (Optional) pricing** — add per-model USD rates to the config's `pricing` block to get cost numbers (xlii never fabricates a price; without rates it shows token counts only). Full schema in [REFERENCE §Configuration](REFERENCE.md).

**4. Verify:**

```bash
xlii status           # management key found · team cached · 9 keys · models set
```

A full guided walkthrough from here — start at Home, then enter a folder — is in **[docs/HOWTO.md](HOWTO.md)**. Day-one map: **Home** is the building (roam / OS / never-sync); a **folder you make** is a room (`xlii code`); **mojo** is talk (`[M]`); **`[$]`** is the lab. `xlii chat` sits with **iXaac** (a costume, not a `/role`), not a third home.

---

## Places and postures

| | Command | Use case |
|---|---|---|
| **Home** | `xlii scratch [here\|NAME] [--tui]` | Where the day starts — roam from `$HOME`. Never-sync. `/sh` · explain · `/ops` when the machine is the job. Not a project. |
| **A folder** | `xlii code [TARGET]` · `xlii init` | A room you make. Lab memory and RAG stay here. Enter when you mean to work a tree. |
| **Talk `[M]`** | face talk · `/mojo` | **mojo** — one journal; walks into folders. |
| **Chat** | `xlii chat` · `/chat` | **iXaac** — a costume, not a `/role`. Other personas are other rooms (`chat/<name>`). |
| **Lab `[$]`** | face lab · code REPL | Shell, `/sh`, `/ops`, edits. Teeth, not a second persona. |
| **Local-only folder** | `xlii init --local [--snapshot]` | A room you don’t want uploaded. `--snapshot` caches a paths+sizes index. |

> **Naming note:** scratch *sessions* (`xlii scratch` / `~/.xlii/scratch/<name>/`) are not the same as tool-output spill under `.xlii/scratch/tool-output/` inside a project — different things, same word.

Inside `code` (and scratch, which rides the code runner), input is **shell-primary**: a bare line runs as a live shell command in a tracked cwd (`cd` moves it), `?<text>` summons the agent, `!<cmd>` forces a shell command at the project root, and `/<cmd>` is a slash command. Inside `chat`, a bare line talks to the persona. (Details in [HOWTO](HOWTO.md); toggle shell-primary with `XLII_SHELL_PRIMARY=0`.)

---

## The two ways to ask for help

xlii has **two** distinct help surfaces, for two distinct moments. Knowing which is which saves a lot of confusion:

**`xlii help`** — run it from your **shell, outside a session**. A grouped tour of the CLI subcommands (PROJECT · CHAT · KNOWLEDGE · SETUP · MAINTENANCE) — *how to set up, launch, and manage xlii itself*: provisioning keys, creating projects and personas, plugins, `doctor`, `gc`. It answers **"what can I run, and how do I drive the tool?"** (`xlii <cmd> --help` gives the flags for any one command; bare `xlii` prints usage.)

**`/help`** — type it **inside a running `xlii code` or `xlii chat` session**. The slash commands for *that* REPL, pulled live from the registry — *how to drive the agent and the session you're in*: plan/rail modes, attachments and workspaces, `/sync`, `/tui`, the knowledge layer (`/ref` `/doc` `/lib` `/get`). It answers **"what can I do in this session?"** The two REPLs have different sets, so `/help` only shows what applies where you are.

| | `xlii help` | `/help` |
|---|---|---|
| **Where** | your shell, before/around a session | inside a `code`/`chat` REPL |
| **Answers** | "how do I run & manage xlii" | "what can I do in this session" |
| **Scope** | global — all subcommands | per-REPL — `code` and `chat` differ |
| **Source** | curated CLI overview | the live slash registry |

Rule of thumb: **`xlii help` is the front door; `/help` is the controls once you're inside.** Both are reproduced — always current, generated from the code — in [Command reference](#command-reference) below.

---

## A tour of the surface

Brief orientation; the **[HOWTO](HOWTO.md)** walks each of these end-to-end, and the **[REFERENCE](REFERENCE.md)** covers internals.

- **`xlii scratch`** — home / roam. Bare = ephemeral from `$HOME`; `here` = local-only `.xlii` in the cwd; `NAME` = persisted under `~/.xlii/scratch/<name>/`. Always never-sync. `--tui` for the full-screen UI (parity with code/chat).
- **`xlii code`** — the project agent. Initialize with `xlii init` (or `xlii new NAME`), open with `xlii code`. Read/write/edit files, run `bash`, `grep`/`glob`, dispatch parallel read-only workers, query the project's RAG index with `search_project`. Plan mode (`/plan`) and the rail (`/rail`) gate risky work.
- **`xlii chat`** — persona conversations. Each persona is a markdown system prompt + its own Collection of past turns, so it remembers. `xlii chat --new NAME` to author one; switch mid-session with `/persona`, or jump between coding and chatting in place with `/code` ↔ `/chat`.
- **The knowledge layer** — `/doc` attaches rules/conventions into the prompt; `/ref` pulls another persona's memory into this session; `/lib` curates your plugin library; `/get <intent>` finds and runs the right plugin. Plugins are markdown API descriptors you write.
- **Autonomous loop & review** — `xlii loop` / `/loop` builds → tests → fixes on its own until green (cross-vendor judges + optional parallel writers in git worktrees); `xlii pr sweep` / `xlii pr watch` turn GitHub PR review comments and failing checks into inbox goals that `xlii loop --drain-inbox` runs; `/verify`, `/peer`, and `/consult` give you cold-context, blind, and cross-vendor second opinions on demand.
- **Interactive programs** — full-screen tools (`vim`, `htop`, `lazygit`, …) get a real terminal automatically via a curated registry; tune it with `/interactive`, or force raw passthrough with `!!cmd`.
- **Local lint/format tools** — `/xtool ls` lists a project-sniffed catalog of linters and formatters (Ruff, Biome, cargo fmt/clippy, gofmt, …) grouped by language, with availability checked against your PATH and your project's language group listed first. `/xtool <tool-id> [target] [--fix]` seeds the exact `!<argv>` into the command line for review-before-run — it never executes, and destructive variants (`--fix`, `--write`) show their flag in the seeded line. The same catalog backs the TUI's **Tools → Shell tools…** menu: missing binaries are dimmed, legacy tools (flake8, black, eslint, prettier, …) sit under **More…**, and a pick made with a Dock file focused fills the `<path>` placeholder from that file.
- **Text hygiene** — `/hygiene scan [path…]` reports cross-platform / paste / harness text issues (CRLF, BOM, encoding) plus one **credibility** counter for hidden injection-class Unicode; `/hygiene strip [path…]` applies the default cleanse (LF · drop BOM · strip that junk). Alias `/sanitize`. Use after a foreign harness dump or a weird paste — then `/xtool` if you still want a language formatter.
- **DeepContexts / Grok Build bridge** — bundle a curated workspace and serve it over MCP (`xlii mcp deep-contexts`) so Grok Build can attach xlii as durable memory.
- **The TUI** — `xlii code --tui` for a full-screen Textual view, or `/tui` to enter it from a running session (no nested process). Behind the styled presentation layer (`XLII_SHELL_STYLE=styled`) shared with the inline REPL.
- **Multi-machine** — send-only encrypted notifications (`xlii notify`) and an experimental inbound command daemon (`xlii daemon`), behind the `[daemon]` extra. House XMPP addresses are minted with `xlii jid add` (the rider, not an agent over SSH). Named advisory farm jobs (`xlii job post` / `watch`) let a node pick up `explore` work from a members-only MUC on the hub (file board is the local mirror). Overlay (Tailscale) is optional. See [REFERENCE §Multi-machine](REFERENCE.md).

---

## Command reference

> Generated from the code by `python -m xlii.docgen` — **do not hand-edit** the
> tables between the GENERATED markers. CI (`scripts/check_docs.py`) fails if they
> drift or if any documented command stops existing.

### CLI subcommands — what `xlii help` lists

Run from your shell. `xlii <cmd> --help` for flags on any one.

<!-- BEGIN GENERATED: subcommands -->
| Command | Flags | What it does |
| --- | --- | --- |
| `xlii init [name]` | `--path` `--collection-id` `--id` `--no-sync` `--yes` `--force` `--local` `--snapshot` `--kind` | Initialize an xlii project. Positional NAME labels the collection. |
| `xlii new <name>` | `--path` `--local` `--kind` | Create a new project directory and initialize it. |
| `xlii projects [filter] [query]` | `--open` `--yolo` `--no-sync` | List all registered xlii projects (filter by substring). |
| `xlii find <query>` | `--open` `--yolo` `--no-sync` | Find one registered project by exact name or unique substring. |
| `xlii sync [path]` | `--dry-run` | Push local changes to the project's collection. |
| `xlii status [path]` | — | Show config + project state. |
| `xlii gc` | `--dry-run` `--yes` | Find and delete orphan xAI collections. |
| `xlii project rm [name]` | `--yes` `--dry-run` `--keep-local` `--local-only` | Remove a project: delete its Collection(s) + registry entry + local .xlii/ (NEVER your source files). Sweeps orphan journal Collections too. |
| `xlii scratch [name]` | `--no-chat` `--tui` `--tauri` `--resume` `--replace` `--yolo` `--force` | Scratch mode: an ephemeral, unbound, never-sync session (bare = from home; `here` = local .xlii in the cwd; NAME = ~/.xlii/scratch/NAME). |
| `xlii ask <prompt>` | `--attach` `--workspace` `--persona` `--no-accrue` `--no-sync` `--outbox` `--yolo` `--session` `--new-session` | Run a single agent turn and print the reply (headless; for scripts + the XMPP daemon). |
| `xlii loop [goal]` | `--status` `--resume` `--workspace` `--judge` `--max` `--test` `--budget` `--from-plan` `--drain-inbox` `--commit` `--push` `--read-budget` `--swarm` `--merge` `--merge-judge` `--yolo` | Autonomous build→test→fix loop (headless; walk away to green). |
| `xlii code [target]` | `--yolo` `--rail` `--discovery` `--ops` `--no-sync` `--no-startup` `--tui` `--tauri` `--replace` `--keep-session` `--resume` `--force` `--preview` `--init` `--launch` | Project-scoped code agent REPL. Pass a project NAME (registry lookup) or PATH; default cwd. |
| `xlii chat [name]` | `--new` `--list` `--edit` `--delete` `--yolo` `--tui` `--yes` `--force` | Persona-based conversational agent with persistent memory (each persona has its own Collection). |
| `xlii make <name> <description>` | `--domain` `--conn` `--root` `--dir` `--no-publish` `--no-yolo` | Build a static web app from a description and publish it live (scaffold → iXaac builds → publish to <name>.<domain>). |
| `xlii config` | — | Create the config template at ~/.config/xlii/config.json (only if absent; never clobbers keys). |
| `xlii shell-init [shell]` | — | Print a shell wrapper so `cd` inside `xlii code` follows you out on exit (opt-in; add `eval "$(xlii shell-init)"` to your rc file). |
| `xlii setup` | `--workers` `--expire-days` `--force` `--journal` | One-shot first-time setup: writes config, checks env mgmt key, provisions primary + workers. |
| `xlii bootstrap` | `--count` `--prefix` `--expire-days` `--force` `--revoke` `--yes` | Provision worker API keys via the management API (lower-level than `setup`). |
| `xlii models list` | — | List models the team has access to. |
| `xlii models recommended` | — | Show heuristic best-of-class picks. |
| `xlii models set` | `--orchestrator` `--worker` `--chat` `--help-model` | Pin orchestrator, worker, chat, and/or help model(s). |
| `xlii models profile list` | — | Show built-in and custom profiles. |
| `xlii models profile set <name>` | — | Apply a profile to orchestrator, worker, chat, and help (persisted). |
| `xlii keys list` | — | List local chat keys with their server-side expiration. |
| `xlii keys rotate` | `--label` | Rotate the secret of one or all keys (same key_id, new value). |
| `xlii keys expire` | `--days` `--label` | Update expireTime on existing key(s). |
| `xlii keys revoke` | `--prefix` `--yes` | Delete keys by label prefix (server-side + local). |
| `xlii keys prune` | `--name` `--any-name` `--older-than` `--include-active` `--dry-run` `--yes` | Delete orphaned xlii-provisioned keys not in this machine's pool. |
| `xlii keys migrate` | `--dry-run` `--no-backup` | Move plaintext chat-key secrets from config.json into the encrypted vault (local, no network). |
| `xlii auth set <plugin_id> <env_var>` | — | Store a credential: xlii auth set <plugin-id> <ENV_VAR> (value prompted, not echoed). |
| `xlii auth list` | — | List plugins + env var names in the vault (never values). |
| `xlii auth clear <plugin_id> [env_var]` | — | Remove a credential or a plugin's whole entry. |
| `xlii account [what]` | `--days` | xAI account: status, keys, usage, billing (read-only). |
| `xlii artifact image [prompt]` | `--path` `--redo` `--edit-prompt` `--ref` `--save` `--last` `--no-preview` `--inline` `--model` `--yolo` | Generate an image via xAI Imagine. |
| `xlii artifact video [prompt] [request_id]` | `--path` `--model` `--wait` | Generate a video via xAI Imagine (async: returns a resumable request id). |
| `xlii artifact edit [prompt]` | `--ref` `--path` `--model` `--yolo` | Edit / remix reference image(s) via xAI Imagine (paid). |
| `xlii artifact pdf [source]` | `--path` `--out` `--engine` `--open` | Render markdown/text to a local PDF in .xlii/artifacts/ (free, ungated). |
| `xlii plugin` | `--new` `--list` `--show` `--edit` `--delete` `--yes` `--lint` `--install-stock` `--force` | Manage plugins (markdown API descriptors used via /lib + /get). |
| `xlii doc` | `--new` `--list` `--edit` `--delete` `--yes` | Manage reference docs (markdown files attached via /doc in any REPL). |
| `xlii export <dest>` | — | Serialize personas, docs, plugins + registry to a directory you own (secrets excluded). |
| `xlii import <src>` | `--force` | Restore an `xlii export` tree (keeps existing files unless --force). |
| `xlii email accounts add <name>` | `--imap-host` `--imap-port` `--smtp-host` `--smtp-port` `--user` `--default` | Add an account (password prompted → vault). |
| `xlii email accounts list` | — | List configured accounts (no secrets). |
| `xlii email list` | `--account` `--unread` `--limit` | List recent messages from INBOX. |
| `xlii email search <query>` | `--account` `--unread` `--limit` | Search messages (IMAP TEXT). |
| `xlii email read <message_id>` | `--account` | Fetch and display a message by id. |
| `xlii email send` | `--account` `--to` `--subject` `--body` `--html` `--yolo` | Send a message (typed 'send' confirm unless --yolo). |
| `xlii ls <address>` | — | List the contents of an address (browse the VFS). e.g. `xlii ls .` or `xlii ls conv://.` |
| `xlii cat <address>` | — | Print the bytes of an address (read a VFS leaf). e.g. `xlii cat ./README.md` |
| `xlii cp <src> <dst>` | `--force` | Copy any readable address to any writable one (cross-root). e.g. `xlii cp conv://./turn.md ./backup.md` |
| `xlii mv <src> <dst>` | `--force` | Move/rename a file address (read+write+delete). Files only for now. |
| `xlii rm <address>` | `--yes` `--recursive` | Remove an address (a leaf, or an empty dir; -r for non-empty). |
| `xlii mkdir <address>` | — | Create a directory at an address (parents as needed). |
| `xlii stat <address>` | — | Show an address's kind/size. e.g. `xlii stat conv://./turn.md` |
| `xlii map [path]` | `--depth` `--detail` | Repo map: file tree + Python class/function signatures — orientation, offline, deterministic (same bytes as /map and the map tool). |
| `xlii remote add <name>` | `--host` `--port` `--user` `--protocol` `--key-path` `--insecure` `--base-url` `--auth` `--share` `--domain` | Add/replace a named connection (secret prompted, stored in the vault). |
| `xlii remote list` | — | List configured connections (no secrets shown). |
| `xlii remote rm <name>` | `--yes` | Remove a connection and its vault secret. |
| `xlii remote test <name>` | — | Smoke check: connect and list the login home. |
| `xlii remote publish <local> <dest>` | `--delete` | Mirror a local dir into a remote docroot (skips size-unchanged files; docroot is relative to the connection's login root; a leading / is stripped, never honored). |
| `xlii remote unpublish <dest>` | `--yes` | Recursively delete a published remote docroot (docroot is relative to the connection's login root; a leading / is stripped, never honored). |
| `xlii role list` | — | List available roles |
| `xlii role show <name>` | — | Show a role's loadout + identity |
| `xlii serve-inbox` | `--host` `--expose` `--port` `--token` `--insecure-no-token` `--workspace` | Localhost webhook that queues POST bodies into the goal inbox (B2.1) |
| `xlii serve` | `--host` `--expose` `--port` `--preview` `--public` `--base-url` `--ws` `--face` `--handshake` `--token` `--workspace` `--yolo` `--force` `--replace` `--view` | Serve the full-screen TUI in a browser tab (requires the [web] extra; NO AUTH — localhost/tailnet only; use --public for code-gated public entry). |
| `xlii serve mint` | `--preview` `--email` `--state-dir` | Mint a single-use pairing code into the grant spool (public gate). |
| `xlii serve sessions` | `--state-dir` | List live public-serve sessions from the session mirror. |
| `xlii serve revoke <session_id>` | `--state-dir` | Revoke a live public-serve session (or all). |
| `xlii skin list` | — | List compiled skins and discovered packs. |
| `xlii skin check <path>` | — | Lint a pack directory (scope, absolute urls, extension/size). |
| `xlii skin install <path>` | — | Copy a local pack directory into ~/.config/xlii/skins/ (no network fetch). |
| `xlii help` | — | Show grouped command listing. |
| `xlii doctor` | `--online` `--migrate-legacy` `--dry-run` | Check install + project health and print fixes. |
| `xlii mcp deep-contexts` | — | Expose DeepContexts + live workspaces via MCP (for Grok Build attachment). Requires the [mcp] extra. |
| `xlii daemon` | `--config` | Run the XMPP/OMEMO command daemon (experimental; requires the [daemon] extra). |
| `xlii daemon trust <jid> <fingerprint>` | `--config` `--distrust` | Pin a sender device's OMEMO fingerprint as trusted (for blind_trust=false). |
| `xlii daemon pair` | `--invite` `--ttl` `--no-wait` `--config` | Mint a one-time QR pairing window (pins a device; never flips blind_trust). |
| `xlii daemon totp` | — | Generate a TOTP secret for the daemon's elevation gate (add to your authenticator, export XLII_DAEMON_TOTP_SECRET). |
| `xlii daemon panic-phrases` | `--set` | Set the three panic-mail subject phrases (vault; not git). |
| `xlii daemon panic-check` | — | Fetch unseen panic mail now (same as daemon/Face wake). |
| `xlii pair` | `--rail` `--invite` `--ttl` `--no-wait` `--config` | Mint a one-time QR pairing window (pins an OMEMO device; never flips blind_trust). |
| `xlii fabric pull` | `--node` `--persona` `--dry-run` | Pull a node's accrued persona turns to the throne and archive them to the shared Collection (run on the throne — needs the management key). |
| `xlii fabric nodes` | — | List the configured fabric nodes. |
| `xlii fabric add-node <name>` | `--remote` `--chat-state` `--persona` | Register a node: a name + an existing remote-fs connection (xlii remote add). |
| `xlii fabric rm-node <name>` | — | Remove a node from the roster. |
| `xlii fabric sync-projects` | `--dry-run` | Pull every node's project registry onto this throne, then push the shared catalog back. Throne Home never travels. |
| `xlii fabric new <node> <name>` | `--kind` | Mint a Collection-first project attributed to a fabric node. No box is the file home until someone adopts. |
| `xlii jid house` | `--domain` `--admin-remote` | Set or show the XMPP house (domain + optional admin remote that can sudo prosodyctl). |
| `xlii jid ls` | — | List ledgered house JIDs. |
| `xlii jid add [localpart]` | `--role` `--node` `--domain` `--password` `--adopt` | Mint a JID: register on the house Prosody when an admin remote is set, else print the command. |
| `xlii jid show <localpart>` | `--reveal` | Show one ledgered JID. |
| `xlii job post` | `--job` `--task` `--context` `--accept` `--project` `--rev` `--budget` `--max-iters` `--kind` | Post an advisory job ad (throne). |
| `xlii job ls` | — | List open / claimed / done jobs. |
| `xlii job show <id>` | — | Print one ticket (and result if done). |
| `xlii job result <id>` | `--json` | Print a done job's text. |
| `xlii job watch` | `--once` `--poll` | Node loop: claim the next eligible ad and run it (explore, no bash). |
| `xlii job cancel <id>` | — | Withdraw a ticket (cooperative). Running node aborts within one iteration. |
| `xlii job bench <node>` | `--until` | Flip a node pickup → observe (cooperative). Daemon stays up. |
| `xlii job evict <node>` | — | Kick / revoke MUC membership (enforced, XEP-0045). Requires owner affiliation. |
| `xlii job accept <id>` | `--fp` | Accept a quarantined market result (bumps fingerprint rep; never auto-fuses). |
| `xlii job invite <fp>` | `--venue` | Open a deal room and invite a market badge (fingerprint). Terms first. |
| `xlii node setup [name]` | `--remote` `--key-path` `--new-key` `--gig` `--mint-xai` `--journal` | Mint daemon + Face JIDs for a named limb and roster it when --remote is set. |
| `xlii notify <message>` | `--config` | Send an OMEMO-encrypted notification to your phone (requires the [daemon] extra). |
| `xlii journal key` | `--force` `--expire-days` | Provision an API key used exclusively by the journal (isolates its LLM spend for cost auditing). |
| `xlii journal install` | — | Opt in to bash-wide capture: add one marked source block to ~/.bashrc. |
| `xlii journal uninstall` | — | Remove the bash-wide capture block from ~/.bashrc and stop the daemon. |
| `xlii journal serve` | `--once` `--stop` | Run the journal daemon that summarizes bash-wide capture (detached, idempotent). |
| `xlii wiki list` | — | List the project's wiki pages with trust markers. |
| `xlii wiki show <name>` | — | Print a wiki page (with its sources). |
| `xlii wiki new <name>` | — | Create a wiki page from the template (born unverified). |
| `xlii wiki verify <name>` | — | Mark a page verified (manual promote; AI pass is /wiki verify in the REPL). |
| `xlii wiki rm <name>` | `--yes` | Delete a wiki page. |
| `xlii sweep` | `--empty` `--test` `--ghosts` `--keys` `--yes` | Throne housekeep: list collections / ghosts / keys; sweep empties, tests, dead keys. |
| `xlii pr sweep [pr]` | `--marker` `--all-comments` `--workspace` | One poll pass: new PR events → .xlii/inbox files (never drains). |
| `xlii pr watch [pr]` | `--marker` `--all-comments` `--workspace` `--interval` `--drain` `--no-drain` | Foreground loop over sweep. Drains by default after a pass that queued work. |
| `xlii destroy-all` | `--dry-run` `--keys-and-local` `--local-only` `--aim` `--target` | Human-only deny ladder — dry-run default; keys-and-local wipes this body. |
<!-- END GENERATED: subcommands -->

### Slash commands — code REPL (`xlii code`) — what `/help` lists there

<!-- BEGIN GENERATED: slash-code -->
```
SESSION
  /account [status|keys|usage|billing|budget [off]] [--days N]
                         xAI account hub: status · keys · usage · billing · budget (read-only).
  /btw <note>  (bare: show active turn + pending steering)
                         Steer the running agent turn — a note folded in at the next tool boundary
  /chat [--id NAME] [--read-proj]
                         Switch to a chat persona in place (its own detached thread; --read-proj opts into read-only project awareness)
  /checkpoint [label]    Snapshot the working tree now (manual checkpoint).
  /clear  |  /cls  |  /clear-screen
                         Clear the transcript (pixels only — talk stays; /reset forgets)
  /code                  Switch back to the code surface in place
  /compact [--recent N] [--dry-run]  |  /compact auto on|off
                         Summarize older turns and continue with a shorter context window
  /config                Open the config panel — models & temps, budget, iterations, no-sync, hotkey, theme, identity & privacy (TUI)
  /cwd [path]            Return the live shell to the project root (or /cwd <path> to go elsewhere)
  /describe <name>  (alias /man; try /describe modes)
                         Describe a slash command, agent tool, or plugin from the live registries
  /diff [N]              Diff working tree against a prior checkpoint.
  /edit [--id <name> | --file <path> | --doc <name> | --plugin <id>] [--new]
                         Open a known artifact in $EDITOR: persona, project file, doc, or plugin
  /editthis <file>  |  /editthis --draft "<desc>" [<file>]
                         Read · live-edit · discuss a file inside xlii (TUI surface; $EDITOR fallback)
  /help [compose|power|all] [--search <keyword>] [--topics]
                         Show slash commands (daily by default; compose · power · all); --search finds commands + topics, --topics lists the manual index
  /history               Open the input history panel — browse, prefill, or clear typed lines (TUI + face)
  /howto [topic | wiki [question] | fix [symptom] | latest [topic] | project [new|edit|rm <name>] | off]
                         Enter howto mode — ask xlii how to use itself (bare input, /howto off to leave)
  /imagine "prompt" | --redo | --editprompt "…" | --ref <path|name> | --from-locker | --save <path> | --last
                         Generate an image via xAI Imagine (local artifact under .xlii/artifacts/)
  /lock                  Lock this Face. Overlay to unlock. Not /kill.
  /mail list|search <q>|read <id>|send --to … --subject … --body …
                         Inbox triage — list, search, read, send (send confirms with 'send')
  /mute me | /mute <jid> Drop me@ (or a JID) from the daemon allowlist.
  /name <spelling>       Name the mobile journal. Unnamed = mojo. Not a switch
  /os [--refresh]        Show detected OS/distro profile (injected as [SYSTEM] in prompts).
  /remote-control [open [--glass] [--for <device>]|lock|unlock|drop|status]
                         Open this Face window to me@ — Mojo on the phone, lab worker for files.
  /render <source> [--out NAME] [--engine ENGINE] [--open]
                         Render markdown/text to PDF (.xlii/artifacts/)
  /replay                Re-print the last captured output verbatim (token-free; survives /clear).
  /reset                 Forget this chat (working talk only — journal, wiki, typed lines stay)
  /rewind [N]            Restore the working tree to before recent write-turn(s).
  /send <program> <target>   (target: focus · last · reply · shell · path · address; --wait to round-trip)
                         Open a file or the last output in an external program.
  /session [on | off | list | resume [id]]
                         Opt-in episode continuity — snapshot the live run; resume it after a restart
  /sh <task> | /sh --explain | /sh --transform [instruction]
                         Natural language → shell command; or --explain / --transform on last output
  /status [--probe [name]]
                         Stack status: vendor · session/project state · downstream fleet (remotes + fabric roles; --probe rechecks reachability)
  /sync                  Force a full sync of the project to the collection now
  /terminal              Leave the full-screen TUI and return to the inline REPL (inverse of /tui)
  /theme                 Open the theme picker panel — click a theme to apply it (TUI)
  /tui                   Open the full-screen Textual UI in this session (no nested process)
  /unlock [code]         Unlock this Face (desk; phone cannot hostage).
  /unmute <jid>          Put a JID back on the daemon allowlist.
  /workbench [type] [--bind-persona] | /workbench new <type> <name> [path] [--bind-persona]
                         Pack home|chat|code — face slot dropdown + Panel Workbench follow the pack. Not a mode. Chat holds research doors; home leads with switch; optional --bind-persona restores old persona apply

MODE
  /approve <category…>  |  /approve --none
                         Pre-authorize bash intent categories (between /safe and /yolo)
  /cancel                Exit plan mode without executing
  /debug [next|back|status|consult|config|exit]
                         Debug mode: staged bug hunt (hypothesize→instrument→reproduce→analyze→fix→verify)
  /discovery [on|off|status]
                         Discovery mode: read-only discussion/research — the agent reads & explains but won't change code
  /execute [rail]        Approve the plan and execute it (add 'rail' to run it stage-by-stage)
  /hook-control [on|off|auto|status]
                         Opt into B1 policy hooks (on-turn-stop may drive capped follow-up turns)
  /iterations <1..100>   Set max tool iterations per turn for this session (no arg = show)
  /model [id] [--worker | --chat | --help-model | --profile <name>] [--list] [--session]
                         Show/switch orchestrator/worker/chat/help model live (bare shows; <id> sets; --list fetches the live catalog)
  /off                   Leave every overlay/mode at once (plan·rail·debug·howto·harness) — back to code/chat/scratch
  /ops [on|off|status]   Ops mode: OS diagnostics & workflow — platform-correct shell probes, read-only first
  /plan [--from-mojo] [--with <provider>] | [save|continue|list|show|check|amend|panel] [args]
                         Plan mode — the plan lives in .xlii/plans/current.md; /plan --from-mojo adds recent talk as context (opt-in); /plan save <name> promotes it to a named plan, bare /plan continue resumes it; check items done with receipts (/plan check <id> --receipt <ref>); propose changes (/plan amend [--re <id>] <text>)
  /rail [next|back|status|off]
                         Coding Rail: stage-gated coding (req→arch→edge→pseudo→impl→review)
  /role [name|off|default <name>]
                         List/activate role descriptors (equip in code, become in chat)
  /safe                  Re-enable bash confirmation gate and clear auto-approve
  /scratch [off|status]  Scratch mode: ephemeral, unbound, never-sync session (free-traversal daily driver)
  /swarm [n] [--save]    Show or set the live ceiling on concurrent worker agents
  /temp <0.0..2.0> [--chat]
                         One-shot temperature override for next turn only
  /tier [fast|expert|heavy|auto|off]
                         Set/show the chat reasoning-depth tier (fast|expert|heavy|auto)
  /yolo  |  /yolo --freeball [<task>]
                         Drop the bash confirmation gate; --freeball adds the trusted-run tier

KNOWLEDGE
  /attach doc <name> | ref <mark>
                         Attach knowledge to the session — a doc (inlined every turn) or a ref (a marked turn as a live pointer)
  /attachments           Show currently attached refs, docs, and locker files
  /bookmarks [--all]     List bookmarks here, or --all to browse the cross-persona library
  /browse [--tree [path] | --changed | --staged | --diff [path] | <path> --attach|--reference|--edit | --popup | --refresh-profile]
                         Git-aware project overview: skeleton, tree, changed files, attach/reference/edit in-repo paths
  /claude [new <n>|@<n> [--bg] <task>|ls|on|off|close <n>] | [--plan|--ask] [--context] <task>
                         Drive Claude Code (ACP) — one-shot or a persistent session/mode
  /clear-attachments     Remove all refs, docs, and locker files from this session (durable)
  /consult [--via <harness>] [--model <name>] [--last N|--turns|--full] [--capture] <question> | --set-to <gigworker>  (harnesses: built-ins + community from .xlii/harness.local.py)
                         Ask a second model for an independent opinion (API or --via harness)
  /context [list | save | show | delete | sync]
                         Manage DeepContexts for Grok Build bridging (durable cross-tool memory)
  /cursor [new <name>|@<name> [--bg] <task>|ls|on|off|close <name>] | [--plan|--ask] [--model <name>] [--context] <task>
                         Drive Cursor's Composer agent (ACP) — one-shot or a persistent session/mode
  /delegate <harness> [new <name>|@<name> <task>|ls|on|off|close <name>] | [--plan|--ask] [--model <m>] [--context] <task>
                         Drive or orchestrate an external agent harness (one-shot or persistent sessions)
  /detach [doc|ref] <name>
                         Detach any attachment by name — a doc, a ref (marked turn), or a recalled point
  /file-tab [on|off] [explorer|locker|vfs|transcript] [--image] [--set left|right]
                         Dock the file explorer; select an item to attach it (TUI) — prefer /panel
  /file-view <path>      Open the file-tab panel to a file's contents (TUI)
  /get <intent>          Invoke a subscribed plugin by natural-language intent
  /gigwork <provider> [--kit explore|bash|general] <task> | ls | presets | add <preset>|--custom … | rm <name> | allow <name> | deny <name> | gaggle <name> <question> | panel
                         Hire a configured non-xAI provider for one read-only worker pass
  /grok-build [new <n>|@<n> [--bg] <task>|ls|on|off|close <n>] | [--plan|--ask] [--context] <task>
                         Drive Grok Build (ACP) — one-shot or a persistent session/mode
  /home                  Open the home hub panel (alias of /panel home)
  /jam <name> <question> | ls | add <name> <backend[:kit][@model]>… [--merge …] [--cap N] | rm <name>
                         Gaggle: named multi-brain preset (home + gigs) and merge the answers
  /journal [--code-on | --code-off | --code-auto | --wiki-on | --wiki-off]
                         Project Shadow journal: show status, toggle the code journal, or the self-building wiki
  /loadout [show | save|load|list|delete|export|import|global-list|global-delete] …
                         Saved loadout bundles (docs, locker, model) — save, load, export
  /locker [add <path> [--once] | on <name> | off <name> | remove <name> | list]
                         Stage local files (images/text) to share with the model on your next turn
  /mark <name> [--window N]
                         Tag the last turn as a named reference point (--window N for a span)
  /media [n] | attach <n|name>
                         The media inbox — files the phone sent mojo; attach one to the Tray
  /mojo <question>       Ask mojo in one shot — its memory fused with this project's journal + wiki
  /panel [home|skills|docs|bookmarks|images|wiki|tasks|jobs|gigwork|plan|files] [off] [--set left|right]
                         Open a side panel — home/projects/skills/docs/… (TUI or face)
  /plugin [all | new <id> | show <id> | subscribe <id> | unsubscribe <id> | remove <id> | call <plugin>.<action> k=v … | panel]
                         Plugins: subscribe, call an action directly, or open the catalog panel
  /providers [run <name> [k=v…] | new <name> <url> | show <name>]
                         Data providers — list manifests (key/quota state), run one into .xlii/provider-results/, scaffold a BYO manifest
  /recall <mark> | <persona>:<mark> | /detach <mark>
                         Inline a bookmarked turn (a /mark) into the conversation — a cut-and-paste, never a persona's whole memory
  /remote [list | connect <name> | ls <name>/<path> | publish <local> <scheme>://<name>/<docroot> | add <name> [flags] | rm <name> | close [<name>]]
                         Remote hosts (FTP/FTPS/SFTP + growing): one manager for every connection; browse via ftp:// sftp:// dav:// smb:// addresses
  /skill [<name> | show <name> | off <name>]
                         List skills (brief), show one in full, or attach it to the session
  /upload [<path> … [--once]]
                         Drag-drop popup to stage files in the locker (or /upload <path> …; headless → /locker add)
  /wiki [list] | show|new|edit|rm <name> | distill <name> <addr>… [-- intent] | verify <name> [--promote]
                         Author the project's semantic-memory wiki: list/show/new/edit/rm, plus AI distill + verify

ADMIN
  /admin [status|unlock|lock|set-key|clear-key] [secret]
                         Capability gate: elevate/lock the session and manage the admin key
  /budget [<usd>]  |  /budget --clear
                         Set or show the soft session spend cap (XLII_BUDGET env)
  /commands [reload | errors]
                         Manage project commands (/commands, /commands reload, /commands errors)
  /cost [--session] [--pricing]
                         Show turn/session cost (use --pricing for the rate table)
  /inspect               Dump live REPLState, agent internals, attachments and registry stats
  /interactive [add <prog> | remove <prog> | list]
                         List/add/remove full-screen programs that run in a real terminal (or use !!)
  /project [filter] | /project find <name> | /project switch <name> | /project startup <task> [--auto] | /project startup --show|--clear|--off | /project rm [name|.] [--dry-run] [--yes] [--keep-local|--local-only]
                         List, find, switch, bind a startup task, or remove registered projects
  /reload                Reload all project commands (alias for /commands reload)
  /sweep [empty|test|ghosts|keys] [yes]
                         Throne housekeep: collections, empty/test leftovers, ghosts, dead keys
  /tools [show <name> | reload]
                         Inspect and reload project agent tools
  /tools-reload          Reload project-defined agent tools (alias)

GENERAL
  /alias <task> | list | rm <name>
                         Promote a saved task into a slash command (live: forwards args to its params).
  /bind <task> [menu=project|tools|xlii] [fkey=f11] [label=…] | list | rm <task|f11>
                         Pin a saved task to a menu row and/or an F-key (symlink to /tasks run).
  /image edit "…" [--ref <path>] | auto|graphics|blocks|path
                         Edit an existing image (Focus or --ref). View is Canvas.
  /jobs [show <id> | cancel <id> | clear]
                         List, inspect, or cancel session-owned background jobs (/tasks --background, fleets).
  /loop <goal> [--from-plan] [--judge tests,xai-verify] [--max N] [--test CMD] [--budget USD] [--commit never|each|final] [--read-budget N] [--allow-dirty]
                         Autonomous build→test→fix loop (walk away to green)
  /peer [--since <ref>]  Blind peer review of a committed range (no author intent)
  /tasks run '<cmd> |> ?prompt |> /slash' [--dry-run] [--yes] [--keep-going] [--background]
                         Run a pipe of steps (shell · ?agent · /slash), carry flows step→step.
  /verify                Cold-context verifier on uncommitted work vs the last turn's task

PROJECT
  /git [status|tree] | stage|unstage|discard <path>… | stage-all|unstage-all | commit <msg>|commit journal|commit summary | generate | push|pull|sync | branch [name] | stash <msg>|-m <msg> [-u]|stash journal|stash list|stash pop|apply|drop [n] | sweep
                         Deprecated — use /gitpain. Gitpanel source control: status, stage/unstage/discard, commit (journal-aware drafts), push/pull/sync, branch, stash-with-message, sweep — the git:// doorway's write side. For full git porcelain, type git in the shell.
  /gitpain [status|tree] | stage|unstage|discard <path>… | stage-all|unstage-all | commit <msg>|commit journal|commit summary | generate | push|pull|sync | branch [name] | stash <msg>|-m <msg> [-u]|stash journal|stash list|stash pop|apply|drop [n] | sweep
                         Gitpanel source control: status, stage/unstage/discard, commit (journal-aware drafts), push/pull/sync, branch, stash-with-message, sweep — the git:// doorway's write side. For full git porcelain, type git in the shell.
  /map [path] | attach [path] | off
                         Repo map: file tree + Python signatures — render it, or attach it as session context
  /nfo [--print] [--show] [--clear] [--full|--brief] [--focus "topic"] [--global|--root]
                         Generate an AI project-status .nfo that becomes the startup splash

SHELL
  <command>              Run as a live shell command in the tracked cwd (cd moves it)
  !<command>             Force a shell command at the project root (ungated)
  ?<text>                Send to the AI / agent
```
<!-- END GENERATED: slash-code -->

### Slash commands — chat REPL (`xlii chat`) — what `/help` lists there

<!-- BEGIN GENERATED: slash-chat -->
```
SESSION
  /account [status|keys|usage|billing|budget [off]] [--days N]
                         xAI account hub: status · keys · usage · billing · budget (read-only).
  /btw <note>  (bare: show active turn + pending steering)
                         Steer the running agent turn — a note folded in at the next tool boundary
  /chat [--id NAME] [--read-proj]
                         Switch to a chat persona in place (its own detached thread; --read-proj opts into read-only project awareness)
  /clear  |  /cls  |  /clear-screen
                         Clear the transcript (pixels only — talk stays; /reset forgets)
  /code                  Switch back to the code surface in place
  /compact [--recent N] [--dry-run]  |  /compact auto on|off
                         Summarize older turns and continue with a shorter context window
  /config                Open the config panel — models & temps, budget, iterations, no-sync, hotkey, theme, identity & privacy (TUI)
  /describe <name>  (alias /man; try /describe modes)
                         Describe a slash command, agent tool, or plugin from the live registries
  /edit [--id <name> | --file <path> | --doc <name> | --plugin <id>] [--new]
                         Open a known artifact in $EDITOR: persona, project file, doc, or plugin
  /editthis <file>  |  /editthis --draft "<desc>" [<file>]
                         Read · live-edit · discuss a file inside xlii (TUI surface; $EDITOR fallback)
  /forget                Wipe current persona's conversation history (with confirm)
  /help [compose|power|all] [--search <keyword>] [--topics]
                         Show slash commands (daily by default; compose · power · all); --search finds commands + topics, --topics lists the manual index
  /history               Open the input history panel — browse, prefill, or clear typed lines (TUI + face)
  /howto [topic | wiki [question] | fix [symptom] | latest [topic] | project [new|edit|rm <name>] | off]
                         Enter howto mode — ask xlii how to use itself (bare input, /howto off to leave)
  /imagine "prompt" | --redo | --editprompt "…" | --ref <path|name> | --from-locker | --save <path> | --last
                         Generate an image via xAI Imagine (local artifact under .xlii/artifacts/)
  /lock                  Lock this Face. Overlay to unlock. Not /kill.
  /mail list|search <q>|read <id>|send --to … --subject … --body …
                         Inbox triage — list, search, read, send (send confirms with 'send')
  /name <spelling>       Name the mobile journal. Unnamed = mojo. Not a switch
  /os [--refresh]        Show detected OS/distro profile (injected as [SYSTEM] in prompts).
  /persona [name]        List personas (bare) or switch to one mid-session
  /render <source> [--out NAME] [--engine ENGINE] [--open]
                         Render markdown/text to PDF (.xlii/artifacts/)
  /replay                Re-print the last captured output verbatim (token-free; survives /clear).
  /reset                 Forget this chat (working talk only — journal, wiki, typed lines stay)
  /send <program> <target>   (target: focus · last · reply · shell · path · address; --wait to round-trip)
                         Open a file or the last output in an external program.
  /status                Show current persona state and attached memory/docs
  /sync                  Force a full sync of the project to the collection now
  /theme                 Open the theme picker panel — click a theme to apply it (TUI)
  /unlock [code]         Unlock this Face (desk; phone cannot hostage).
  /workbench [type] [--bind-persona] | /workbench new <type> <name> [path] [--bind-persona]
                         Pack home|chat|code — face slot dropdown + Panel Workbench follow the pack. Not a mode. Chat holds research doors; home leads with switch; optional --bind-persona restores old persona apply

MODE
  /approve <category…>  |  /approve --none
                         Pre-authorize bash intent categories (between /safe and /yolo)
  /cancel                Exit plan mode without executing
  /execute [rail]        Approve the plan and execute it (add 'rail' to run it stage-by-stage)
  /iterations <1..100>   Set max tool iterations per turn for this session (no arg = show)
  /model [id] [--worker | --chat | --help-model | --profile <name>] [--list] [--session]
                         Show/switch orchestrator/worker/chat/help model live (bare shows; <id> sets; --list fetches the live catalog)
  /off                   Leave every overlay/mode at once (plan·rail·debug·howto·harness) — back to code/chat/scratch
  /plan [--from-mojo] [--with <provider>] | [save|continue|list|show|check|amend|panel] [args]
                         Plan mode — the plan lives in .xlii/plans/current.md; /plan --from-mojo adds recent talk as context (opt-in); /plan save <name> promotes it to a named plan, bare /plan continue resumes it; check items done with receipts (/plan check <id> --receipt <ref>); propose changes (/plan amend [--re <id>] <text>)
  /role [name|off|default <name>]
                         List/activate role descriptors (equip in code, become in chat)
  /safe                  Re-enable bash confirmation gate and clear auto-approve
  /tier [fast|expert|heavy|auto|off]
                         Set/show the chat reasoning-depth tier (fast|expert|heavy|auto)
  /yolo  |  /yolo --freeball [<task>]
                         Drop the bash confirmation gate; --freeball adds the trusted-run tier

KNOWLEDGE
  /attach doc <name> | ref <mark>
                         Attach knowledge to the session — a doc (inlined every turn) or a ref (a marked turn as a live pointer)
  /attachments           Show currently attached refs, docs, and locker files
  /bookmarks [--all]     List bookmarks here, or --all to browse the cross-persona library
  /claude [new <n>|@<n> [--bg] <task>|ls|on|off|close <n>] | [--plan|--ask] [--context] <task>
                         Drive Claude Code (ACP) — one-shot or a persistent session/mode
  /clear-attachments     Remove all refs, docs, and locker files from this session (durable)
  /consult [--via <harness>] [--model <name>] [--last N|--turns|--full] [--capture] <question> | --set-to <gigworker>  (harnesses: built-ins + community from .xlii/harness.local.py)
                         Ask a second model for an independent opinion (API or --via harness)
  /context [list | save | show | delete | sync]
                         Manage DeepContexts for Grok Build bridging (durable cross-tool memory)
  /cursor [new <name>|@<name> [--bg] <task>|ls|on|off|close <name>] | [--plan|--ask] [--model <name>] [--context] <task>
                         Drive Cursor's Composer agent (ACP) — one-shot or a persistent session/mode
  /delegate <harness> [new <name>|@<name> <task>|ls|on|off|close <name>] | [--plan|--ask] [--model <m>] [--context] <task>
                         Drive or orchestrate an external agent harness (one-shot or persistent sessions)
  /detach [doc|ref] <name>
                         Detach any attachment by name — a doc, a ref (marked turn), or a recalled point
  /file-tab [on|off] [explorer|locker|vfs|transcript] [--image] [--set left|right]
                         Dock the file explorer; select an item to attach it (TUI) — prefer /panel
  /file-view <path>      Open the file-tab panel to a file's contents (TUI)
  /get <intent>          Invoke a subscribed plugin by natural-language intent
  /gigwork <provider> [--kit explore|bash|general] <task> | ls | presets | add <preset>|--custom … | rm <name> | allow <name> | deny <name> | gaggle <name> <question> | panel
                         Hire a configured non-xAI provider for one read-only worker pass
  /grok-build [new <n>|@<n> [--bg] <task>|ls|on|off|close <n>] | [--plan|--ask] [--context] <task>
                         Drive Grok Build (ACP) — one-shot or a persistent session/mode
  /home                  Open the home hub panel (alias of /panel home)
  /jam <name> <question> | ls | add <name> <backend[:kit][@model]>… [--merge …] [--cap N] | rm <name>
                         Gaggle: named multi-brain preset (home + gigs) and merge the answers
  /loadout [show | save|load|list|delete|export|import|global-list|global-delete] …
                         Saved loadout bundles (docs, locker, model) — save, load, export
  /locker [add <path> [--once] | on <name> | off <name> | remove <name> | list]
                         Stage local files (images/text) to share with the model on your next turn
  /mark <name> [--window N]
                         Tag the last turn as a named reference point (--window N for a span)
  /media [n] | attach <n|name>
                         The media inbox — files the phone sent mojo; attach one to the Tray
  /mojo <question>       Ask mojo in one shot — its memory fused with this project's journal + wiki
  /panel [home|skills|docs|bookmarks|images|wiki|tasks|jobs|gigwork|plan|files] [off] [--set left|right]
                         Open a side panel — home/projects/skills/docs/… (TUI or face)
  /plugin [all | new <id> | show <id> | subscribe <id> | unsubscribe <id> | remove <id> | call <plugin>.<action> k=v … | panel]
                         Plugins: subscribe, call an action directly, or open the catalog panel
  /recall <mark> | <persona>:<mark> | /detach <mark>
                         Inline a bookmarked turn (a /mark) into the conversation — a cut-and-paste, never a persona's whole memory
  /remote [list | connect <name> | ls <name>/<path> | publish <local> <scheme>://<name>/<docroot> | add <name> [flags] | rm <name> | close [<name>]]
                         Remote hosts (FTP/FTPS/SFTP + growing): one manager for every connection; browse via ftp:// sftp:// dav:// smb:// addresses
  /skill [<name> | show <name> | off <name>]
                         List skills (brief), show one in full, or attach it to the session
  /upload [<path> … [--once]]
                         Drag-drop popup to stage files in the locker (or /upload <path> …; headless → /locker add)

ADMIN
  /admin [status|unlock|lock|set-key|clear-key] [secret]
                         Capability gate: elevate/lock the session and manage the admin key
  /budget [<usd>]  |  /budget --clear
                         Set or show the soft session spend cap (XLII_BUDGET env)
  /commands [reload | errors]
                         Manage project commands (/commands, /commands reload, /commands errors)
  /cost [--session] [--pricing]
                         Show turn/session cost (use --pricing for the rate table)
  /inspect               Dump live REPLState, agent internals, attachments and registry stats
  /interactive [add <prog> | remove <prog> | list]
                         List/add/remove full-screen programs that run in a real terminal (or use !!)
  /reload                Reload all project commands (alias for /commands reload)
  /sweep [empty|test|ghosts|keys] [yes]
                         Throne housekeep: collections, empty/test leftovers, ghosts, dead keys
  /tools [show <name> | reload]
                         Inspect and reload project agent tools
  /tools-reload          Reload project-defined agent tools (alias)

GENERAL
  /alias <task> | list | rm <name>
                         Promote a saved task into a slash command (live: forwards args to its params).
  /bind <task> [menu=project|tools|xlii] [fkey=f11] [label=…] | list | rm <task|f11>
                         Pin a saved task to a menu row and/or an F-key (symlink to /tasks run).
  /image edit "…" [--ref <path>] | auto|graphics|blocks|path
                         Edit an existing image (Focus or --ref). View is Canvas.
  /jobs [show <id> | cancel <id> | clear]
                         List, inspect, or cancel session-owned background jobs (/tasks --background, fleets).
  /tasks run '<cmd> |> ?prompt |> /slash' [--dry-run] [--yes] [--keep-going] [--background]
                         Run a pipe of steps (shell · ?agent · /slash), carry flows step→step.

PROJECT
  /git [status|tree] | stage|unstage|discard <path>… | stage-all|unstage-all | commit <msg>|commit journal|commit summary | generate | push|pull|sync | branch [name] | stash <msg>|-m <msg> [-u]|stash journal|stash list|stash pop|apply|drop [n] | sweep
                         Deprecated — use /gitpain. Gitpanel source control: status, stage/unstage/discard, commit (journal-aware drafts), push/pull/sync, branch, stash-with-message, sweep — the git:// doorway's write side. For full git porcelain, type git in the shell.
  /gitpain [status|tree] | stage|unstage|discard <path>… | stage-all|unstage-all | commit <msg>|commit journal|commit summary | generate | push|pull|sync | branch [name] | stash <msg>|-m <msg> [-u]|stash journal|stash list|stash pop|apply|drop [n] | sweep
                         Gitpanel source control: status, stage/unstage/discard, commit (journal-aware drafts), push/pull/sync, branch, stash-with-message, sweep — the git:// doorway's write side. For full git porcelain, type git in the shell.
  /map [path] | attach [path] | off
                         Repo map: file tree + Python signatures — render it, or attach it as session context
  /nfo [--print] [--show] [--clear] [--full|--brief] [--focus "topic"] [--global|--root]
                         Generate an AI project-status .nfo that becomes the startup splash

SHELL
  !<command>             Run a local shell command (no model turn)
  ?<text>                Send to the AI / agent (alias for bare input)
```
<!-- END GENERATED: slash-chat -->

---

## Configuration & storage (at a glance)

| Path | Holds |
|------|-------|
| `~/.config/xlii/config.json` | keys, models, temperatures, pricing (chmod 600) |
| `~/.config/xlii/personas/<name>.md` | a persona's system prompt |
| `~/.config/xlii/docs/<name>.md` | reference docs (`/doc`) |
| `~/.config/xlii/plugins/<id>.md` | plugin descriptors (`/lib`, `/get`) |
| `~/.config/xlii/projects.json` | the project registry |
| `<project>/.xlii/` | per-project state (collection id, manifest, session) |
| `~/.xlii/chat/<persona>/.xlii/` | per-persona chat state |

The full config schema, every `XLII_*` environment variable, and the on-disk layout are in **[docs/REFERENCE.md](REFERENCE.md)**.

---

## Gigwork — hire a non-xAI brain for a worker pass

Workers normally run on the home xAI plane. **`/gigwork`** (alias `/gig`) hires a
named OpenAI-compatible endpoint — Kimi, DeepSeek, OpenRouter, a local Ollama —
as the *brain* for one bounded, read-only worker pass on xlii's own tools:

```text
/gigwork presets                               # the built-in endpoint catalog
/gigwork add kimi                              # write the provider block; then export KIMI_API_KEY
/gigwork ls                                    # configured providers + key status
/gigwork kimi review this race for missed awaits
/gigwork kimi --kit bash run the failing test and read the traceback
```

Presets cover the usual suspects — `kimi`, `deepseek`, `anthropic` (Claude via
its OpenAI-compat layer, same `ANTHROPIC_API_KEY` `/consult` uses), `gemini`,
`huggingface` (the Inference Providers router: one `HF_TOKEN`, any hosted
slug), `openrouter`, `groq`, `together`, `mistral`, `fireworks`, `openai`, and
a local `ollama` (no key needed). `add --as`/`--model` rename or repoint;
`add --custom <name> <base_url> <env> <model>` covers anything OpenAI-shaped;
`rm <name>` removes (and scrubs the allowlist). Presets only write endpoint +
env-var *name* + a default model into your config — never a secret.

The tools, role gating, and read-only contract stay xlii's; only the chat brain
changes. A gig never sees the xAI server plane — `search_project`,
`web_search`, and `x_search` are stripped from its tool schemas by capability,
and gigs cannot write files or dispatch further workers. `xai_docs` stays
(outbound HTTP to docs.x.ai, not the Responses plane). Providers land
under `gigwork.providers` in `config.json` with the API key referenced from the
**environment** (`api_key_env`) — an inline key in the file is refused:

```json
{
  "gigwork": {
    "providers": {
      "kimi": {"kind": "openai_compat",
               "base_url": "https://api.moonshot.cn/v1",
               "api_key_env": "KIMI_API_KEY",
               "model": "moonshot-v1-128k"}
    },
    "defaults": {"allow": ["kimi"]}
  }
}
```

`defaults.allow` is the **orchestrator's** hiring permit: the main agent may
pass `gig="kimi"` on `dispatch_subagent` only for names listed there (the reply
comes back under a `gigwork[kimi]` badge; an empty list means only you, via the
slash command, can hire). Toggle it without editing config — `/gigwork allow
kimi` / `/gigwork deny kimi`. `xlii doctor` shows each provider and whether its
key env is set. Foreign spend is never billed as xAI — token counts are
reported, and costs stay blank until you add rates. Home plane stays xAI until
you choose otherwise.

Two optional per-provider knobs ride the same block. `temperature` pins a
sampling value for endpoints with their own rules (otherwise the endpoint's
default applies — the caller's is never forwarded). `cache` (`true` | `false` |
`"auto"`, default auto) controls **prompt-cache marks**: Anthropic-style
endpoints cache nothing unless the request carries `cache_control` breakpoints,
so when the knob is effective xlii marks the system prompt and the task message
— a multi-iteration worker pass then re-reads its harness prefix instead of
re-buying it. `auto` sends marks for `api.anthropic.com` only; DeepSeek,
OpenAI, and Gemini cache server-side and need nothing; set `true` to opt in an
aggregator that honors the marks (OpenRouter fronting Claude).

For Claude worker passes where prompt-cache accounting matters, set
`"kind": "anthropic_native"` on the provider block (same `ANTHROPIC_API_KEY` and
`https://api.anthropic.com/v1` base URL). That path speaks Anthropic's native
`/v1/messages` API — `cache_control` on system, tools, and the last user
message — and surfaces `cache_read_input_tokens` in worker stats. Judges and
`/consult` stay on the OpenAI-compat layer; `anthropic_native` is for workers
and `/plan --with` non-streaming turns only (`stream` is refused until a native
stream adapter ships).

**Gaggles** (`/gaggle`, synonym `/jam`) are named multi-brain presets over the
same seam — *who* + *cap* + *merge policy* + *budget*, not a graph editor.
`write: false` is locked. Caps are mandatory.

```text
/gaggle ls                                  # stock + configured presets
/gaggle second-opinion is this migration safe to run twice?
/gigwork gaggle second-opinion is this migration safe to run twice?
/gaggle add trio xai kimi:bash anthropic@claude-haiku-4-5 --cap 3
/gaggle rm trio
```

Stock crews: `second-opinion` (home xAI explore + your default gig, then a
synthesis that lists agreements, conflicts, and a verdict), `debate` (same, gig
speaks first), `scout` (two gig passes, plain digest, no synthesis call). The
stock `"gig"` slot binds to the first allowlisted — else the sole — configured
provider, so gaggles work the moment one `/gigwork add` exists. `/gaggle add`
composes your own from `backend[:kit][@model]` tokens — kit picks the tool
palette (`explore`|`bash`|`general`), `@model` overrides that member's model;
the same shape lives under `gigwork.jams` for hand-editing, and adding under a
stock name shadows the stock preset (`rm` un-shadows it). Members are read-only
workers; a failed member is reported and the rest proceed; a failed synthesis
degrades to the raw digest — paid passes are never thrown away.

The orchestrator may pass `gaggle="second-opinion"` on `dispatch_subagent`
only when every foreign member is on `gigwork.defaults.allow`. A heavy
investigate hop accepts `gaggle=` / `gig=` on `request_deep_search`, and
falls through to `gigwork.defaults.investigate_gaggle` or `investigate_gig`
when the call site is unset (not both). Do not pass `gig=` and `gaggle=`
together. See `/howto gigwork`.

The **Gigwork panel** puts all of it on one screen: `/gigwork panel`, `/panel
gigwork`, Alt-H, or Panels → Gigwork in the TUI. Providers (model, key state,
agent-allow, cache marks) and gaggles (members, merge, cap, origin) as one
list; every action **seeds the matching command into the input line** —
Hire, Allow/Deny, New provider, Ask, Remove, and Edit-as-command, which
prefills the selected gaggle's full `/jam add` round-trip for on-the-fly
recomposition. The panel executes nothing; you review, edit, Enter.

Gigworkers can be **perma-hired for jobs**, not just per-task: `/consult
--set-to <provider>` assigns one as the default consult judge, and any
`judges` profile can carry `"gig": "<provider>"` instead of re-declaring an
endpoint — loop judges included. The assignment is a binding by name: edit
the provider once under `gigwork.providers` (rotate a key env, change the
model) and every job that binds it follows. One registry says *who* exists;
jobs say *which one* they use.

One room of the MAIN loop is hireable too: `/plan --with <provider>` puts a
gig brain in the planner's chair for that plan session — read-only palette,
writes path-gated to `plans/`, xAI server tools stripped, and `/execute`
always back on the home plane. The plan file it leaves is indistinguishable
from a home-planned one.

---

## Public serve posture (`xlii serve --public`)

> **Sibling of the fabric's trusted-tailnet posture** ([HOWTO §13](HOWTO.md#13-the-multi-machine-fabric-xmppomemo--prerequisites--status)):
> that section keeps Prosody and the daemon off the public internet. This
> section is how you *do* face the internet with `xlii serve` — TLS and a
> pairing-code gate in front, never a bare bind.

### Threat model (read this first)

**What the design guarantees:** no credential is ever typed on the borrowed
machine; the code is single-use and dead in minutes; the session is
revocable from the phone and mortal by timeout; nothing persists in the tab.

**What it cannot fix:** "a computer you trust" is load-bearing. Everything
typed or displayed in-session is exposed to that machine (extensions,
keyloggers, shoulder surfing). A paired session **is a shell** on the node
as the daemon user — the gate is at entry, not inside; we do not pretend
otherwise. The design's job is to make the exposure *end with the session*:
smaller blast radius, not zero. `webcode preview` is the offered downgrade
when trust is partial.

**Public endpoint surface:** entry form + POST only, throttled, locked out,
audited; TLS at Caddy; grant cookie httpOnly/Secure/SameSite=Strict; serve
never listens publicly itself.

### How it works (one paragraph)

`--public` does **not** loosen the bind. Serve still listens on `127.0.0.1`
only; a wildcard / `0.0.0.0` bind in public mode is a **startup error**.
Caddy owns the public interface and TLS and reverse-proxies to the loopback
port. The entry page at your `base_url` is a code form; a short pairing code
(from `xlii serve mint` on-box, or the daemon's `webcode` verb from the
phone) exchanges for a session grant cookie. No terminal stream, no
WebSocket, no page beyond the form without a live grant.

### Caddy recipe (auto-TLS + reverse proxy)

Pick an **operator-owned** domain for the entry page — never the fabric
domain (`xlii-remote.com` stays content-free forever). Point DNS at the
node, install Caddy, and put something like this in the Caddyfile (port
matches `xlii serve --port`, default `8042`):

```caddyfile
# Replace with your operator-owned domain — not the fabric domain.
code.example.com {
	reverse_proxy 127.0.0.1:8042
}
```

Caddy obtains and renews the certificate. Its `reverse_proxy` also sends
`X-Forwarded-For` by default — **don't strip it**: the gate trusts that header
from its loopback proxy to attribute pairing attempts, lockouts, and audit
lines to the real client address instead of one shared `127.0.0.1` bucket. On the node:

```bash
# loopback only — required in --public; 0.0.0.0 refuses to start
xlii serve --public --host 127.0.0.1 --port 8042
```

Mint a code on-box (`xlii serve mint`) or from a pinned phone (`webcode`),
open `https://code.example.com`, enter the code, work, then revoke
(`xlii serve revoke` / `webcode kill`) or let idle/session TTL end it.
Revokes are queued and land at the serve process's next sweep (within
~15 s). Phone mints are additionally throttled: 3 per 5 minutes per sender,
on top of the daemon's global rate limit.

### Public-mode banner

On start, public mode prints a loud banner that states:

- the configured `base_url` (the code-gated entry page),
- that the entry page is pairing-code gated (no stream without a grant),
- where the serve audit log lives under the state dir,
- that the process itself is bound to loopback only (Caddy is the public face).

Plain `xlii serve` (no `--public`) keeps the W1 banner: **NO AUTH**,
localhost/tailnet only — the port is still a shell on the host.

### `[serve.public]` config

Required when using `--public`. Nothing is hardwired to a product site —
bring your own domain.

The block lives in `~/.config/xlii/config.json` (JSON — the one global
config file), nested under `serve.public`:

```json
{
  "serve": {
    "public": {
      "base_url": "https://code.example.com",
      "code_ttl_s": 300,
      "session_ttl_s": 28800,
      "idle_timeout_s": 1800,
      "face_linger_s": 1800,
      "face_attach_existing": false,
      "max_sessions": 3,
      "closed_door": false,
      "redirect_off_url": ""
    }
  }
}
```

`base_url` (the operator's own domain) is **required** and has no default;
the rest fall back to the values shown — pairing codes live 5 minutes,
grants are hard-capped at 8 h, idle grants are reaped after 30 minutes,
and at most 3 sessions run at once. `face_linger_s` (default: same as
`idle_timeout_s`) controls how long a face backend lingers at zero browser
views before reap; set `0` for immediate reap (legacy behaviour).
`face_attach_existing` (default `false`) lets a new public grant attach to
a live desk face instead of failing with 502 when the face lock is held. `--public` without `base_url` is a config error with a friendly
hint; `--base-url` on the command line overrides the file for one run.

Minted codes print as a magic link — `base_url/?code=<code>` — which opens
the entry form with the code prefilled. The `?code=` probe is read-only:
GET never consumes a pairing code; only `POST /pair` does.

`closed_door` (default `false`) hides the entry page: a `GET /` carrying
neither a live grant cookie nor a live `?code=` is 302-redirected to
`redirect_off_url` (required when `closed_door` is on) instead of being
shown the code form. This is surface reduction, not the lock — the grant
cookie remains the gate on every stream route.

---

## Design philosophy (the load-bearing thesis)

**The model and primitives are commodities; the user's curation is the most valuable resource.**

- Personas are **user-curated memories**.
- Docs are **user-curated rules**.
- Plugins are **user-curated capabilities**.
- Refs cross-pollinate them.
- xAI provides Grok + Collections + server tools; **the user composes the system from there.**

This is the inverse of Claude Code / Codex / Cursor, which assume "vendor provides capability, user provides project files." xlii assumes "vendor provides primitives, user composes the system."

**Closest spiritual ancestor: Emacs** — not a finished product, a substrate that rewards investment. The platform provides composable primitives; the user composes.

**Honest tradeoffs.** Power users (dotfiles, NixOS, self-hosted services) get it immediately and the investment compounds. Casual users bounce — "why isn't there a button for X?" → "because it's yours, not ours." Niche-but-deeply-loved is a valid position (Emacs, NixOS, Org-mode); it isn't mass-market and shouldn't try to be.

**What it isn't:** not a Cursor replacement (no editor wrapper — bring your own), not built on MCP for capabilities (plugins are markdown, not subprocess servers — though xlii *does* speak MCP outward via `xlii mcp deep-contexts`), not vendor-curated (no app store — you write the plugins/personas/docs).

---

## License

MIT — see [`LICENSE`](../LICENSE).

---

## For agents reading this cold

If you're a fresh agent session picking up this project:

1. **Read [`ROADMAP.md`](../ROADMAP.md)** — the phased plan (0–6) with exit gates; the source of truth for done vs. deferred.
2. **If you use a local agent memory store** (for example Claude Code project memory under `~/.claude/projects/<encoded-project-path>/memory/`), read those notes when present — they may carry thesis and ongoing-work state. Do not assume a particular home or user path.
3. **Don't propose features that violate the thesis.** Vendor-curated tool palettes, kitchen-sink sprawl, finished products instead of composable primitives — anti-patterns here.
4. **Reasoning models are weaker at instruction-following.** When debugging "the doc/persona isn't sticking," check the model first.
5. **Workers are read-only investigators.** Never give them write access — that's a load-bearing safety property.
6. **Ship small slices that work end-to-end.** The user works incrementally and trusts the substrate to compose; defer L2/L3 elaborations until L1 friction is real.
