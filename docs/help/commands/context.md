# /context

A DeepContext is a portable, durable snapshot of an xlii workspace — its
attached refs, docs, and custom project tools — saved under a name so an
external agent can pick it up as long-term memory and call back into it.
`/context` is the operator side of that bridge: you curate a context here, then
a Grok Build agent attaches it over MCP. It is durable and cross-tool, which is
what separates it from a loadout (session/project-scoped) — a DeepContext is
meant to outlive this session and travel to another tool entirely.

Reach for it when work you have set up inside xlii — the right refs, the right
docs, a custom tool or two — needs to be handed off to a Grok Build agent and
stay in sync as that agent works. If you only want to bank attachments for your
own reuse inside xlii, use `/loadout` instead; `/context` is specifically the
hand-off to other agents.

## Inspect and save

```
/context list
/context save
/context save my-app-review
```

Bare `save` snapshots the current loadout slot (your active workspace) into a
named DeepContext; pass an explicit `<loadout-slot>` to snapshot a different
one. `list` shows every DeepContext saved globally — these are not project-local,
so a name you save here is visible from any project.

## Show, sync, delete

```
/context show my-app-review
/context sync my-app-review
/context delete my-app-review
```

`show <name>` prints the source project, loadout slot, and the counts of refs,
docs, and tools captured. `sync <name>` is the important one: it is
**bidirectional**. It pushes the named context's refs/docs into your live
session, then writes your current session's attachments back into the saved
context and back to the source workspace, stamping `last_synced_at`. Use it to
pull changes a Grok Build agent made back into your session, or to publish edits
you made in-session out to the shared context.

## Gotchas

- A DeepContext is a snapshot of *references plus custom tools*, not file
  contents. If a ref or doc it points at no longer resolves later, it simply
  won't reattach — re-check with `/context show` after a sync.
- `sync` replaces, it does not merge: it overwrites your session's
  `attached_refs`/`attached_docs` with the context's, then writes back. Save
  your current arrangement (`/loadout save`) first if you might want it.
- The Grok Build side needs the MCP extra and a running server
  (`pip install -e ".[mcp]"`, then `xlii mcp deep-contexts`). Without it,
  `/context` still saves locally but no external agent can reach the contexts.
- `show`, `sync`, and `delete` all require a name; `delete` reports plainly if
  the name is not found. Use `/context list` to confirm the exact spelling.

Deep dive: `/howto bridges`
