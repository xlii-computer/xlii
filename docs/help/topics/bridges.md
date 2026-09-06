# Bridges — DeepContext & Cursor/Composer

xlii has two outward bridges to other agents. They face opposite directions and
**compose**:

| Bridge | xlii's role | Wire | Drives |
| --- | --- | --- | --- |
| DeepContext / Grok Build | **MCP server** (others pull from you) | stdio MCP | `xlii mcp deep-contexts`, `/context` |
| Cursor / Composer | **ACP client** (you drive them) | JSON-RPC ACP | `/cursor` |

A DeepContext is a curated, durable snapshot of an xlii workspace — its attached
refs, docs, and custom project tools — that an external agent can attach as
long-term memory and call back into. Composer is Cursor's edit-and-apply agent;
`/cursor` steers it inside your project and can hand it the same DeepContext.

Run `/describe <cmd>` for the authoritative, always-current flags of any command
below.

---

## Bridge 1 — DeepContext over MCP (Grok Build)

### Expose your contexts as an MCP server

```bash
pip install -e ".[mcp]"     # one-time: the MCP extra
xlii mcp deep-contexts      # start the stdio MCP server
```

This serves a FastMCP server named `xlii-deep-contexts`. It is **stdio** — when
an MCP client (Grok Build, or Cursor — see Bridge 2) spawns it, stdout carries
JSON-RPC, so all human banners go to stderr. You normally don't run it by hand;
you register the command and let the client spawn it.

It exposes:

- Resources: `xlii://contexts` (list) and `xlii://contexts/{name}` (one).
- Tools an external agent can call:
  - `xlii_list_contexts()` — metadata for every saved DeepContext.
  - `xlii_load_context(name)` — full saved/curated DeepContext.
  - `xlii_get_live_context(project_path?, workspace?)` — a fresh snapshot read
    straight from a project's `.xlii/session.json`, **no `/context save`
    required**. This is the recommended entry point for the latest state.
  - `xlii_sync_context(name, updates)` — write-back: merges the external agent's
    changes and pushes refs/docs back into the source workspace's session.json.
  - `xlii_call_project_tool(name, args, project_path?)` — invoke a custom project
    AgentTool (from `.xlii/tools.py`) so the "custom capabilities" advertised in
    a context become callable from the other agent.

The write-back path (`xlii_sync_context`) is what makes this bidirectional: an
external agent can revise attachments and have them land back in your workspace.

### Manage DeepContexts from the REPL

Inside `xlii code`, `/context` curates the named, durable contexts the server
hands out:

```
/context list      # show saved DeepContexts
/context save      # snapshot the current workspace as a DeepContext
/context show      # inspect one
/context delete    # remove one
/context sync      # force a write-back round-trip
```

Saved contexts live globally (alongside global workspaces), so they survive
across sessions and projects. Note that `xlii_get_live_context` reads disk
directly — for "just expose what I have open right now," you don't even need to
`/context save` first; the server reads the live `session.json`.

Run `/describe context` for the exact subcommand surface.

---

## Bridge 2 — Cursor's Composer over ACP

`/cursor` makes xlii the **ACP client**: it spawns `cursor-agent acp` as a
subprocess and drives Composer's full read / propose / diff / apply loop in the
current project, streaming its output back into your REPL and surfacing the files
it touched.

```
/cursor <task>          # agent mode (default) — Composer may edit files
```

Read-only and steering flags (run `/describe cursor` for the authoritative list):

- `--plan` — propose a plan, no edits (read-only).
- `--ask` — read-only Q&A over the project.
- `--agent` — explicit agent mode (the default).
- `--model <name>` — pick any Cursor model (default `composer-2.5`).
- `--context` — attach xlii's own DeepContext MCP server (see "How they
  compose").
- `--reject` — dry-run: auto-reject every permission request.

### Prerequisites

- The Cursor CLI installed and logged in: `cursor-agent login`. If it isn't,
  `/cursor` reports that the session is unavailable.
- For `--context`, the `[mcp]` extra (`pip install -e ".[mcp]"`), since that flag
  starts the same `xlii mcp deep-contexts` server.

### What you get back

Agent mode lists the files Composer changed (uncommitted) so you can review and
commit them yourself; `--plan` / `--ask` never write. xlii answers Composer's
permission and filesystem callbacks and confines writes to the workspace root.

---

## How they compose

The two bridges are inverses of one another, and `--context` joins them:

```
xlii (ACP client)  ──drives──▶  cursor-agent / Composer
       │                              │
       │                         pulls DeepContext
       ▼                              ▼
xlii mcp deep-contexts  ◀──MCP──  (xlii as MCP server)
```

When you run `/cursor --context <task>`, xlii:

1. Registers its DeepContext server (`xlii-deep-contexts`) in the project's
   `.cursor/mcp.json` (Composer only loads MCP servers from that file).
2. Approves it for headless use (equivalent to
   `cursor-agent mcp enable xlii-deep-contexts`).
3. Drives the Composer session over ACP.

The net effect: you steer Composer *and* Composer can pull your curated
DeepContext through xlii's MCP server in the same run — xlii is simultaneously
the ACP client and the MCP server. If auto-approval fails, xlii prints the manual
`cursor-agent mcp enable` command to run.

---

## Quick recipes

- Let Grok Build (or any MCP client) read your live workspace:
  register `xlii mcp deep-contexts`, then point the client at `xlii_get_live_context`.
- Curate a stable memory snapshot to hand out: `/context save`, then
  `/context list` to confirm it.
- Ask Composer to plan a refactor without touching files: `/cursor --plan <task>`.
- Run Composer with your DeepContext attached: `/cursor --context <task>`.
- Probe what Composer *would* do without granting permissions:
  `/cursor --reject <task>`.

---

## Troubleshooting

- "MCP bridge requires the optional mcp extra" → `pip install -e ".[mcp]"`.
- "`/cursor` unavailable" → install and log in: `cursor-agent login`.
- Composer can't see your context under `--context` → re-run
  `cursor-agent mcp enable xlii-deep-contexts`, then retry.
- Server "may not be connected" → don't pipe banners to stdout; let the client
  spawn `xlii mcp deep-contexts` rather than running it inside a pipe by hand.

See also: `/howto knowledge` (refs, docs, locker, loadouts that feed a
DeepContext), `/howto plugins` (other external capabilities), `/howto
sessions-projects` (workspaces), and `/describe context` / `/describe cursor` for
live, per-command detail.
