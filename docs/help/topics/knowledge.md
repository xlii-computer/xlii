# Knowledge & context

How to give the model context, and where each kind lives. xlii has five
distinct channels — they cost differently, persist differently, and are reached
by different commands. Pick the right one and your turns stay cheap and sharp.

| Channel | Command | What it does | Cost shape |
| --- | --- | --- | --- |
| Inlined docs | `/doc` | Folds a static doc into the system prompt | Every turn, forever |
| RAG refs | `/ref` | Adds a persona's Collection (or a bookmark) to context | Only when retrieved |
| Bookmarks & recall | `/mark` `/recall` | Pin a past turn, re-attach it later as a doc | Every turn once recalled |
| Locker | `/locker` `/upload` | Stage local files (images/text/PDF) for a turn | Per turn it's shared |
| Loadouts | `/loadout` | Save/restore a whole bundle of the above | — |

All four attachment channels are **durable**: refs, docs, locker, and recalled
points survive a restart of the same session. Run `/attachments` to see
everything currently attached and the running context budget.

For the authoritative, always-current flags on any command below, run
`/describe <cmd>` — it reads the live registry, so it never drifts from the build.

## Inlined docs — `/doc`, `/undoc`

A *doc* is a small piece of static knowledge: a coding convention, a project
spec, a glossary. Attaching it inlines its full text into the system prompt for
the rest of the session — so it's **always in context**, no retrieval, no RAG
cost, but you pay for it on *every* turn.

```
/doc                 # list docs attached this session
/doc conventions     # attach the doc named "conventions"
/undoc conventions   # detach it
```

Docs live at `~/.config/xlii/docs/<name>.md` and are hand-editable. Create one
with `xlii doc --new <name>`. Keep them tight — rules and conventions, not big
reference manuals. xlii warns past a soft cap (~20 KB) because large inline docs
bloat every turn. For long reference material, build a persona and `/ref` it
instead (next section).

`/doc` lists and detaches **docs only** — *skills* ride the same attachment
channel but are a `/skill` concern, so they're filtered out of every `/doc`
listing and detach. You won't `/undoc` a skill you didn't know was attached; use
`/skill` for those.

## RAG refs — `/ref`, `/unref`

A *ref* attaches **another persona's Collection** so the `search_project` tool
can retrieve from their conversation history. Unlike a doc, nothing is inlined —
content only enters context when the model actually searches and a chunk
matches. That makes refs the right tool for *large* bodies of knowledge.

```
/ref                       # list attached refs
/ref ada                   # attach persona "ada"'s memory to search_project
/ref --bookmark fix-plan   # attach a named bookmark as a live pointer
/unref ada                 # detach
```

The persona must already have a Collection — run `xlii chat <name>` once to
initialize it if `/ref` reports there's none yet.

A ref's target is one of two types — **collection** (a persona's chunked memory,
above) or **bookmark** (a single `/mark`ed turn, via `/ref --bookmark <mark>` or
`<persona>:<mark>`). A bookmark ref attaches the saved turn as a previewable
pointer rather than widening `search_project`, so it's the lighter sibling of
`/recall`: `/recall` inlines the turn into the system prompt every turn, while a
bookmark ref keeps it as a pointer you can preview.

## Bookmarks & recall — `/mark`, `/bookmarks`, `/recall`

Bookmarks let you pin a *moment in a conversation* and bring it back later, even
across personas. `/mark` is the **verb** (you *do* something — tag a turn);
`/bookmarks` is the **view** (you *look* at what's stashed). The old `/marks`
spelling still works as a **hidden alias** — muscle memory and older docs don't
break — but it no longer shows in `/help`.

```
/mark fix-plan                # tag the last turn as "fix-plan"
/mark design --window 4       # tag the last turn plus 4 preceding ones
/bookmarks                    # list bookmarks in the active store
/bookmarks --all              # browse the cross-persona bookmark library
/recall fix-plan              # re-attach that point as a durable reference doc
/recall ada:design            # reach across identities: persona "ada"'s mark
/recall design --window 2     # override the stored span on attach
```

`/recall` materializes the marked turn(s) into an attached reference doc, so once
recalled it rides every turn like any `/doc`. The `<persona>:<mark>` form is how
you carry context from one identity into another. To attach a bookmark as a
lighter *pointer* instead of inlining it, use `/ref --bookmark <mark>` (above).

## The locker — `/locker`, `/upload`

The locker stages **local files** (images, text, PDFs) to fold into your *next*
turn. This is the multimodal path: text is embedded, images are sent to a vision
model, PDFs are sent, and anything else is named but not embedded.

```
/locker                       # list the locker (● shared · ○ held)
/locker add ./diagram.png     # stage a file for upcoming turns
/locker add notes.txt --once  # stage it for one turn, then auto-drop
/locker off diagram.png       # hold a staged file back without removing it
/locker on diagram.png        # re-enable it
/locker remove diagram.png    # drop it from the locker
```

`/upload` opens a drag-and-drop popup to stage files (needs a GUI); on a
headless box it falls back to `/locker add`. You can also pass paths directly:
`/upload <path> … [--once]`.

Heads up: if you stage an image but your orchestrator model can't see images,
xlii warns you — the turn comes back text-only. Switch to a vision-capable model
(`xlii models set --orchestrator <name>`); see `/howto config-models`.

## Loadouts — `/loadout`

A *loadout* is a saved bundle of the whole context state: refs, docs, locker,
and model/temperature overrides. Bank a setup once, switch into it anytime.

```
/loadout                      # show the active loadout + budget
/loadout save reviewing       # bank the current bundle as "reviewing"
/loadout load reviewing       # switch to it
/loadout list                 # list saved loadouts
/loadout delete reviewing     # remove one
/loadout export reviewing     # export for sharing; import with /loadout import
```

Aliases: `/workspace`, `/ws`. Personas can also declare a loadout in their
frontmatter (`/edit` to add one). Run `/describe loadout` for the full
save/load/export/import/global surface.

## Plugins as context — `/lib`, `/get`

Plugins are subscribed knowledge/action packs scoped to a project. They're not
inlined; the model reaches them through `plugin_search` / `plugin_get`.

```
/lib                          # list plugins subscribed in this project
/lib all                      # browse every installed plugin
/lib subscribe <id>           # subscribe
/lib unsubscribe <id>         # unsubscribe
/get the weather in seattle   # invoke a subscribed plugin by intent
```

See `/howto plugins` for the full plugin lifecycle.

## DeepContexts — `/context`

`/context` manages durable cross-tool memory for Grok Build bridging — a
separate channel from session attachments. See `/howto bridges`.

```
/context list | save | show | delete | sync
```

## Inspecting & clearing

```
/attachments          # everything attached now + context budget
/clear-attachments    # remove all refs, docs, and locker files (durable)
```

`/clear-attachments` (aliases `/clearatt`, `/forget-attachments`) wipes the lot
and persists the empty state. To remove things individually, use `/unref`,
`/undoc`, and `/locker remove`. `/status` also surfaces attached refs/docs.

## Choosing a channel

- Small, always-relevant rules → `/doc` (cheap to write, costs every turn)
- Large reference corpus → persona + `/ref` (pay only on retrieval)
- A past conversation you want back → `/mark` then `/recall`
- An image, log, or PDF on disk → `/locker` / `/upload`
- A repeatable setup → `/loadout save` once, `/loadout load` after

Per-command depth lives in `/describe <cmd>`. Related guides:
`/howto personas-loadouts`, `/howto config-models`, `/howto plugins`,
`/howto bridges`.
