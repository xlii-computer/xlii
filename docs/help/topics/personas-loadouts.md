# Personas & loadouts

Two related ideas:

- A **persona** is a chat identity — its own prompt, memory, and detached
  thread. Personas live in the chat REPL (`xlii chat`).
- A **loadout** is a saved context bundle — the attachments and model settings
  active right now (refs, docs, locker, pinned model/temperature). Loadouts work
  in both the code and chat REPLs (`/loadout`, aliases `/workspace`, `/ws`).

A persona can also *declare* a loadout in its prompt frontmatter, so switching to
it auto-attaches that context. See the relationship at the end.

Run `/describe persona`, `/describe loadout`, etc. for the authoritative,
always-current per-command detail. Cross-link siblings with `/howto knowledge`
(refs/docs/locker) and `/howto modes` (code vs chat).

## Personas (chat REPL)

| Command | Effect |
| --- | --- |
| `/personas` | List all personas (● marks the last used). |
| `/persona <name>` | Switch to another persona mid-session. |
| `/persona` | Show the current persona. |
| `/name <spelling>` | Name the mobile journal (unnamed = mojo). Not a switch. |

Switching personas is an **in-place, detached** swap: each persona owns its own
conversation thread. The leaving thread is parked, never carried in, so personas
stay oblivious to one another and to the code surface. The only deliberate bridge
across identities is `/recall <persona>:<mark>` — see `/howto knowledge`.

`/persona <name>` is equivalent to `/chat --id <name>`; `/chat` and `/code` swap
surfaces in place (`/howto modes`). `/persona` only registers in the chat REPL —
enter chat first with `/chat` if you start in code.

Other per-persona commands in chat: `/edit` (edit this persona's prompt in
`$EDITOR`), `/forget` (wipe this persona's history, with confirm), `/status`
(persona state + attached memory/docs). Run `/describe <cmd>` for each.

To create one, see `/howto first-session` (the `xlii chat --new <name>` flow
opens a template in `$EDITOR`).

## Mojo vs a project-bound persona

**Mojo** is the mobile journal — the default persona on the fabric. `default`
and `mojo` are the same *seat* in two languages. You put a *name* on that one
seat (`/name stuart`, or at `xlii setup`). If you never name it, the spelling
is `mojo`. That is all identity is. Do not name a new persona `default` or
`mojo`; those words already mean the journal.

**iXaac** is the shipped chat costume (`xlii chat` / bare `/chat`). A persona
is a voice you edit. Jobs are `/role` (`code-architect`, `debugger`, …) —
we do not ship extra personas that are just roles.

A leftover `default.md` is not an island — talk treats that word as mojo.

A **code folder does not get a persona.** Talk `[M]` there is mojo, plus this
folder's journal/wiki as ambient. `bound_persona` on `project.json` is leftover
workbench glue — drop it. A prompt file is not a persona; mojo is the persona.

`chat/<name>` rows in the projects list are memory islands (the journal's
house). They are not a third face and not a lab.

## Loadouts (`/loadout`, alias `/workspace`, `/ws`)

A loadout snapshots the current context bundle so you can save it, switch between
named bundles, and carry them across machines. It captures:

- attached **refs** (other personas' memory) and **docs** (reference docs),
- the **locker** (staged local files for your next turn),
- any pinned **model** / **temperature** override.

| Command | Effect |
| --- | --- |
| `/loadout` or `/loadout show` | Inspect the active loadout. |
| `/loadout list` | List saved loadouts (● marks the active slot). |
| `/loadout save <name>` | Bank the current bundle under a name. |
| `/loadout load <name>` | Switch to a saved bundle. |
| `/loadout delete <name>` | Remove a saved bundle. |
| `/loadout export <name>` | Publish a bundle to the global (cross-project) store. |
| `/loadout import <global> [as <local>]` | Pull a global bundle into this project. |
| `/loadout global-list` | List global bundles. |
| `/loadout global-delete <name>` | Remove a global bundle. |

Bare `/workspace` and `/ws` list saved loadouts. Run `/describe loadout` for the
full grammar and any flag specifics.

### What persists, and where

- **Local loadouts** are scoped to the active project/persona and live in its
  durable session state — they survive restarts and reattach when you reopen.
- **Global loadouts** (`export` / `import` / `global-list`) live outside any one
  project, so you can reuse a bundle in another project or on another machine.
- The **active slot** is shown by `/loadout list` and `/loadout show`. Saving
  over the current name updates it; `load` switches the active slot.

Loadouts are just attachment + model state — they don't carry conversation
history. Build a bundle with `/ref`, `/doc`, `/locker` (`/howto knowledge`),
confirm it with `/attachments`, then `/loadout save <name>`.

## How personas and loadouts relate

A persona's prompt can carry a **frontmatter loadout** — a declared bundle that
auto-applies every time you switch to that persona. Edit it with `/edit`; the
keys are:

```yaml
---
# plugins: [open-meteo, hackernews]   # subscribe these plugins (invoke via /get)
# docs: [my-conventions]              # auto-attach these reference docs
# refs: [other-persona]               # pull in another persona's memory
# model: grok-4                       # pin a model for this persona
---
```

Order of application on a persona switch:

1. Attachments are cleared to a clean slate (per-surface isolation).
2. The persona's saved session attachments are restored.
3. The persona's **declared** frontmatter loadout is applied.
4. A `/loadout load <name>` you run afterward layers on top for this session.

`/loadout show` prints both the **declared** loadout (from frontmatter) and the
**active** bundle (what's live now), so you can see where each attachment came
from. Use frontmatter for the persona's permanent kit; use `/loadout save` for
ad-hoc bundles you switch between within a session.

## See also

- `/howto knowledge` — refs, docs, locker, marks, recall (the parts a loadout bundles).
- `/howto modes` — `/chat` ↔ `/code` surfaces and when to switch.
- `/howto first-session` — creating personas and starting a chat.
- `/describe <cmd>` — authoritative, always-current detail for any command above.
