---
name: xlii-context
description: >
  Attach a DeepContext (or live workspace) from an xlii project as durable
  long-term memory + custom capabilities. Use when the user wants persistent
  project memory, attached refs/docs, or project-specific AgentTools inside
  Grok Build. This is the primary bridge between xlii (personal substrate)
  and Grok Build (orchestration layer).
metadata:
  short-description: "Attach xlii DeepContext / live workspace as long-term memory + tools"
  requires_mcp:
    - xlii-deep-contexts   # run `xlii mcp deep-contexts` first
---

# xlii DeepContext Bridge (Grok Build ↔ xlii)

xlii is the long-term personal memory + curation layer (REPL with durable
named workspaces, /ref, /doc, per-project tools, true write-back).
Grok Build is the high-agency orchestration + subagent layer.
This skill lets Grok Build agents **attach** any xlii workspace as first-class
memory and capabilities, and **sync changes back**.

## Setup (one-time per machine)

1. Install xlii with the MCP bridge extra (this pulls in FastMCP):
   ```bash
   pip install "xlii[mcp]"
   # or, if working from the xlii source tree:
   pip install -e ".[mcp]"
   ```

2. Verify the command appears:
   ```bash
   xlii mcp deep-contexts --help
   ```

3. Start the bridge MCP server **in its own terminal** (or tmux/screen):
   ```bash
   xlii mcp deep-contexts
   ```
   Leave it running. It will print a friendly message and wait for Grok Build to connect. It serves the live state and any saved DeepContexts.

3. In Grok Build, register the MCP server:
   - Name it exactly: `xlii-deep-contexts`
   - Launch command: `xlii mcp deep-contexts` (stdio transport)
   - Or use the TUI / MCP panel to add a local stdio server pointing at the xlii binary.

   Once connected you will see tools:
   - `xlii_list_contexts`
   - `xlii_load_context`
   - `xlii_get_live_context`
   - `xlii_sync_context`
   - `xlii_call_project_tool` (invoke the project's custom AgentTools)

## When to use this skill

- User: "use my auth-refactor xlii context"
- User: "attach the durable memory from the payment-service xlii workspace"
- User: "work on the docs site using everything I have in xlii for that project"
- User wants custom project tools (e.g. `deploy_staging`, `verify_migrations`) to be visible/usable
- Long-running multi-turn work that should survive REPL restarts or subagent spawns

## Preferred: Live mode (always-fresh, no manual save)

For active development the **live** path is best — it reads directly from the
project's `.xlii/session.json` on disk:

```python
# Call this MCP tool (the model does it via the connected server)
result = xlii_get_live_context(
    project_path="/home/you/code/my-project",   # or omit for CWD
    workspace="main"                            # or omit for the active one
)
```

What you receive:
- `attached_refs`  → collection ids + paths the user has `/ref`'d
- `attached_docs`  → high-signal named documents (always inject these)
- `tools`          → list of custom AgentTool schemas the project defines
- `source_project_path`, `source_workspace_name`, `last_synced_at`, etc.

**Action after loading live:**
- Treat every item in `attached_docs` as standing instructions / specs / style rules.
- For `attached_refs`, use them for semantic search when the task touches those areas.
- Surface the custom tools in `tools[]` to the user ("I see you have a `check_migrations` tool in xlii — want me to use the equivalent here or shall we run it from the REPL?").

## Alternative: Curated / named DeepContexts

If the user has done the work of curating a stable snapshot:

```bash
# Inside an xlii REPL for that project
/context save main as payment-patterns --desc "Payment domain rules + helpers"
```

Then from Grok Build:

```python
ctx = xlii_load_context("payment-patterns")   # or xlii_list_contexts() first
```

Named contexts are great for:
- Sharing stable "company conventions"
- Multiple named mental models for the same project
- Versioned hand-offs between humans and agents

## What to do with the data (core loop)

1. **Load** (live or named).
2. **Internalize** the attached_docs immediately (they are high priority context).
3. **Work** using the memory + any custom tool schemas you can see.
4. **Sync back** when you discover something durable:

   ```python
   xlii_sync_context(name_or_live_name, {
       "attached_docs": [...],     # add new high-signal docs you created
       "attached_refs": [...],     # or new important paths
       "description": "updated ...",
       "tags": ["grok-build", "refactored"]
   })
   ```

   This writes the changes into the original xlii workspace's session.json
   (the `_sync_attachments_back_to_source_workspace` path). Next time the
   human opens xlii or you call get_live again, the new state is there.

5. Tell the user what you attached and what you synced.

## Custom project tools (now fully callable)

The `tools` array in the returned context gives you the exact name, description,
JSON schema, and safety flags for every user-defined tool in the xlii project.

**You can now call them directly:**

```python
result = xlii_call_project_tool(
    name="run_project_tests",           # or any name from the context's tools[]
    args={"some": "param"},             # must match the tool's JSON schema
    project_path="/path/to/the/project" # optional; defaults to CWD
)
print(result["content"])
```

The call executes the exact same Python handler the user registered in
`.xlii/tools.py` (or `.xlii/tools/**/*.py`), using a minimal but functional
execution context. This makes the custom capabilities advertised by a
DeepContext **first-class and directly usable** from Grok Build.

**Tips:**
- Tools that only need the project root (subprocess, local file logic, custom
  search, etc.) work great.
- Tools that depend on the full xlii runtime (RAG `search_project` with
  attached collections, plugin subscriptions, etc.) will return a clear error.
  In those cases fall back to telling the human to run the tool from their xlii REPL.
- The safety flags (`plan_mode_safe`, `worker_safe`) that were serialized in the
  context are still respected by your own reasoning — the bridge will execute
  whatever you ask it to.

## Write-back & durability

- `xlii_sync_context` is the write-back primitive.
- It updates the global DeepContext (if named) **and** immediately mutates the
  source project's `session.json` under the correct workspace key.
- Human sees the new attachments on next `xlii code` or `/attachments` in that
  workspace. The loop is closed.

## Example real-world dialogue

User: "Start the payment-service refactor. Use everything I have in xlii for it."

You:
- Call `xlii_get_live_context(project_path=".../payment-service")`
- Receive 7 attached_docs + 2 custom tools (`validate_payment_flow`, `seed_test_data`)
- "I've attached your payment-service live context (12 refs, 7 docs, 2 project tools). The key rules around idempotency and the new ledger schema are now in my active memory. Ready when you are."

Later:
- You create a new high-signal decision doc.
- You call `xlii_sync_context(..., {attached_docs: [new_doc, ...]})`
- "Synced the new ledger decision record back into your xlii payment-service workspace."

## Multiple contexts

You can attach several (they compose). Use `xlii_list_contexts()` to discover
what the human has curated globally.

## Quick reference of MCP tools this skill uses

| Tool                      | Purpose                                      | Preferred for              |
|---------------------------|----------------------------------------------|----------------------------|
| xlii_get_live_context     | Fresh disk state, no prep                    | Active development         |
| xlii_load_context         | Curated/named snapshot                       | Stable conventions         |
| xlii_list_contexts        | Discovery                                    | Exploration                |
| xlii_sync_context         | Bidirectional write-back                     | After meaningful work      |
| xlii_call_project_tool    | Execute a custom AgentTool from the context  | Using project capabilities |

This skill + the running `xlii mcp deep-contexts` server + an xlii REPL on the
human side is the complete "personal substrate + high-agency orchestration"
pairing.

Use it. Improve it. Sync it back.
