# xlii — Overview

A shareable breakdown of what xlii is, where it sits in the market, and who
it's for. For install and first-time setup see the [GUIDE](GUIDE.md); for
a hands-on walkthrough see [HOWTO](HOWTO.md); for internals see
[REFERENCE](REFERENCE.md).

> **Status: alpha** (mid-2026). Works end-to-end against a real xAI account.
> The architecture is largely settled; specific features are still in flux.

---

## One sentence

**xlii is a terminal-native, user-composed AI environment** — built on xAI Grok
and Collections — where you curate personas, rules, and API plugins as plain
files, and the system becomes uniquely yours over time.

The thesis:

> **The vendor provides primitives; the user composes the system.**

Think **Emacs for AI tooling**, not another Cursor clone.

---

## The gap it fills

Most AI tools fall into a few buckets:

| Category | Examples | What you get |
|----------|----------|--------------|
| **Vendor-curated coding agents** | Cursor, Claude Code, Codex | Strong defaults, IDE integration, vendor tool palette + MCP |
| **Hosted chatbots** | ChatGPT, Claude.ai, Grok app | Easy chat, vendor-controlled memory, no file ops |
| **Frameworks** | LangChain, etc. | Building blocks to make *your* product — not a shell you live in |
| **Memory / RAG apps** | Khoj, MemGPT | Retrieval-focused, usually single-purpose |

**xlii sits in the unoccupied middle:** a substrate that rewards investment. You
write a few personas, reference docs, and plugins for APIs you actually use —
and the environment compounds. Power users get it immediately; casual users may
bounce. That's intentional.

---

## What you actually do with it

### How you approach it

Two places. One talk seat. Two postures.

| | What | Command |
|--|------|---------|
| **Home** | The building — roam the machine, never-sync | `xlii scratch` · face |
| **A folder** | A room you make. That’s a project. Lab memory stays here. | `xlii init` · `xlii code` |
| **`[M]` talk** | **mojo** — the journal. Walks with you into a folder. | face talk · `/mojo` |
| **`xlii chat`** | **iXaac** — a chat costume. Not a `/role`. | `xlii chat` · `/chat` |
| **`[$]` lab** | `/sh` · explain · `/ops` · edits. Teeth, not a second persona. | face lab · code REPL |

`xlii chat` sits with iXaac, not a third home. Other personas are other rooms
(`chat/<name>`). Jobs are `/role`. `--local` on a folder means no Collection.

Inside a folder’s lab, the terminal is shell-primary: bare `ls` / `cd` /
`pytest`, `?<text>` for the worker, `/<cmd>` for xlii.

### The knowledge layer (the differentiator)

Four slash commands work together; everything is **files you own**:

| Command | What it does |
|---------|--------------|
| `/ref <persona>` | Pull another persona's memory into this session |
| `/doc <name>` | Attach reference docs (rules, conventions, specs) into the prompt |
| `/get <intent>` | Find and run a subscribed plugin by natural language |
| `/plugin …` | Manage your plugin library |

**Plugins are markdown API descriptors** — write one in about a minute,
subscribe from any project. No MCP server to spawn per capability.

Also: **loadouts** (saved attachment bundles), **marks** (tag turns and recall
them across personas), **roles** and **skills** (packaged behavior).

### Workflow and safety surfaces

| Surface | What it does |
|---------|--------------|
| `/plan` → `/execute` | Read-only investigation → numbered plan → approve → write |
| `/rail` | Six-stage coding flow; writes locked at the tool layer until design stages finish |
| `/loop` | Autonomous build → test → fix until green (optional parallel writers in git worktrees) |
| `/verify` | Cold-context check of uncommitted work vs. the task |
| `/peer` | Blind review of a committed diff (reviewer doesn't see author intent) |
| `/consult` | Cross-vendor second opinion |
| **Multi-key swarm** | Parallel read-only worker agents, each on its own API key |

### Other notable pieces

- **Optional full-screen TUI** — `xlii code --tui`
- **Grok Build bridge** — expose workspaces over MCP (`xlii mcp deep-contexts`)
- **Auto-sync** — local disk is source of truth; changes push to xAI Collections after each turn
- **Self-managed keys** — one management key in env; chat keys auto-provisioned and rotatable
- **Cost tracking** — per-turn tokens and USD, orchestrator vs. workers

---

## How it's built (brief)

| | |
|---|---|
| **Language** | Python 3.11+ |
| **Scale** | ~30k lines of core code, ~13k lines of tests |
| **Vendor coupling** | xAI-first (Grok, Collections, Management API, server tools) |
| **Storage** | Files on disk — `~/.config/xlii/` for global artifacts, `.xlii/` per project |

**Naming / legacy:** intentional `xli` leftovers (keyring service, collection
prefixes, env aliases) are contracted in **[LEGACY.md](LEGACY.md)** — not bugs.

**Architecture / queue:** control flow and state ownership —
**[ARCHITECTURE.md](ARCHITECTURE.md)**. What to build next —
**[ROADMAP.md](../ROADMAP.md)** (historical design proposals are archived out
of the public tree). Quality track (first-look
grades) — **[FIRST-LOOK-REVIEW.md](FIRST-LOOK-REVIEW.md)** + golden path
**[GOLDEN-PATH.md](GOLDEN-PATH.md)**.

Engineering signals worth noting:

- CLI help and slash-command tables are **generated from code** and CI-checked
- Phased roadmap with explicit exit gates ([ROADMAP.md](../ROADMAP.md))
- Design principle: **the model is not a security boundary** — gating enforced in
  code, not prompts alone

---

## Assessment

### Ratings (by audience)

| Lens | Score | Notes |
|------|-------|-------|
| **Product idea / thesis** | **8.5/10** | Clear, defensible, genuinely different |
| **Alpha implementation** | **7/10** | Unusually complete for the stage |
| **Recommend to any developer today** | **5/10** | xAI account, alpha edges, steep learning curve |
| **Power-user terminal substrate on Grok** | **8/10** | Rail, loop, swarm, judges, loadouts punch above weight |

Don't average these into one number — xlii is strong at being what it claims,
weak at being what most people want AI tooling to be.

### What's working

1. **Thesis is load-bearing** — not marketing on a coding agent; reflected in
   plugins-as-markdown, user-owned memory, composable attachments.
2. **Feature depth** — verification stack, coding rail, autonomous loop with
   judges, multi-key swarm — ahead of typical side projects and some commercial
   agents.
3. **Honest scope** — no editor wrapper (bring your own); delegates cloud-agent
   patterns to Cursor via `/cursor` instead of cloning them.
4. **Disciplined shipping** — generated docs, atomic state writes, broad test
   coverage on critical paths.

### What needs a serious rethink (not just polish)

1. **Too many ways to inject knowledge** — docs, refs, locker, loadouts,
   skills, roles, marks. Each works; together they overlap. Needs one
   composition story, not more features.
2. **Mode surface complexity** — plan, rail, debug, loop, verify, peer, consult.
   Powerful individually; overwhelming collectively. Needs a clearer user-facing
   narrative.
3. **xAI coupling** — fine for v1; long-term needs provider + search backends
   as first-class ports if adoption should broaden.
4. **Structural debt** — large coordination modules still growing; finish
   kernel/mode separation before piling on REPL features.
5. **Naming / legacy** — `.xli` vs `.xlii`, workspace vs loadout. Papercuts
   that make a substrate feel unfinished.

### What does *not* need a rethink

- Python for this workload
- Two REPLs in one process
- Shell-primary code mode
- Markdown plugins instead of MCP-inward
- Read-only workers
- Local disk as sync source of truth
- Niche, power-user positioning

---

## Market comparison

### vs. Cursor / Claude Code / Codex CLI

| | xlii | Vendor coding agents |
|---|------|----------------------|
| **Onboarding** | Steep | Gentle |
| **Editor** | BYO | Native |
| **Tools** | User-composed | Vendor-curated + MCP |
| **Verification** | Strong (cold judges, peer, consult) | Mostly "tests passed" |
| **Memory** | User-owned, cross-persona | Vendor/session-scoped |
| **Polish** | Alpha | Production |

**Not the same race.** Cursor wins default developer workflow. xlii wins when
**your curation compounds** — personas, plugins, loadouts nobody else has.

### vs. Aider and other terminal agents

Aider is a **wrench** — git-centric, simpler, multi-model. xlii is a
**workshop** — personas, sync, swarm, rail, loop, judges, TUI. More ambitious;
more ceremony.

### vs. frameworks and RAG apps

Frameworks help you build a product; xlii *is* the product you live in. RAG apps
are memory-focused; xlii's knowledge layer is broader and tied to a full agent
loop.

---

## Where xlii is ahead of the market

1. **Verification depth** — the same model that wrote code isn't the only judge
2. **Coding rail** — six-stage, tool-gated design flow stricter than typical plan mode
3. **Composable knowledge** — memory as curated files, not a black box
4. **Multi-key parallelism with safety** — workers read-only by architecture
5. **Substrate discipline** — integrates with Cursor instead of re-hosting cloud agents

## Where the market is ahead

1. UX polish and discoverability
2. Editor-native experience (inline diffs, visual context)
3. Model portability
4. Ecosystem (MCP marketplaces, integrations)
5. Production hardening and support

---

## Who it's for

**Good fit:**

- Terminal power users (dotfiles, self-hosted, composable tooling)
- People on xAI/Grok who want to **own** their AI environment
- Builders who'll invest in personas, docs, and plugins and want that investment
  to compound
- Users who want stricter verification (rail, loop, peer review) than typical
  agents offer

**Poor fit:**

- "Install and go" developers who want IDE-integrated magic
- Teams needing multi-model portability out of the box
- Anyone who wants a finished product with buttons, not a substrate to compose
- Users without an xAI account / team admin access for key provisioning

---

## Bottom line

**xlii is a strong idea executed as a solid alpha** — high praise in a space
full of shallow demos.

It owns real whitespace: **user-composed AI substrate for terminal power
users**, not another Cursor. The main risks aren't wrong language or wrong
thesis — they're **complexity creep** (too many attachment paths, too many
modes) and **vendor coupling** (Grok/Collections as implicit foundation).

Success looks less like "add every feature Cursor has" and more like **making
composability feel inevitable instead of overwhelming** — so the Emacs analogy
lands as a promise, not a warning.

---

## Quick reference

| | |
|---|---|
| **Name** | xlii (package) / XLII (user-facing) |
| **Tagline** | Personal AI substrate — the Emacs of AI tooling |
| **Stack** | Python 3.11+, xAI Grok + Collections |
| **Status** | Alpha |
| **License** | MIT |
| **Get started** | `xlii setup` → `xlii code` / `xlii chat` |
| **Docs** | [README](../README.md) · [GUIDE](GUIDE.md) · [HOWTO](HOWTO.md) · [REFERENCE](REFERENCE.md) · [ROADMAP](../ROADMAP.md) |

---

## Further reading

| Doc | For |
|-----|-----|
| [README](../README.md) | The one-screen pitch + 60-second start |
| [GUIDE](GUIDE.md) | Install, setup, command map, design thesis |
| [HOWTO](HOWTO.md) | Start-to-finish walkthrough on a fresh machine |
| [REFERENCE](REFERENCE.md) | Architecture, config schema, env vars, security model |
| [ROADMAP](../ROADMAP.md) | Phased plan and exit gates |
