# /attach · /detach

`/attach` is the one verb for wiring knowledge into a session, and `/detach`
removes it again. Both replace a scatter of older commands: `/attach` absorbs
`/doc` (and the bookmark attach path), while `/detach` unifies `/undoc` and
`/unref`. The old spellings still work as hidden aliases — nothing you already
type breaks — but `/attach` and `/detach` are the canonical surface.

The point of one verb is to make the **cost** of an attachment explicit. Two
types, two very different cost shapes:

```
/attach doc <name>        # a reference doc, inlined into the system prompt
/attach ref <mark>        # a saved turn, kept as a live pointer
```

- **`doc`** — *inlined into the system prompt every turn, forever.* The whole doc
  rides in each request. Cheap to reason over, but every attached byte is re-sent
  on every turn — great for a small always-on rule-set, wrong for a large corpus.
  This is exactly the old `/doc <name>`.
- **`ref`** — *a live pointer, neither inlined nor searched.* Attaching a saved
  turn (a `/mark`) keeps a reference to that exchange you can preview on demand
  from the ref tab. It costs nothing per turn and never enters the model's
  context until you look at it. (Contrast `/recall`, which *does* inline a marked
  turn as a doc — the read-don't-inline counterpart.) `bookmark` is the accepted
  alias — the type's old name before the menu-families rename made Ref honest.

> ⚠ **The persona-Collection attach is gone, on purpose.** `/attach ref
> <persona>` used to wire a whole persona's memory into `search_project` — a
> standing cross-persona leak you stop noticing. Personas are sealed islands;
> the deliberate bridges are a marked turn you chose (`/mark` + `/attach ref` or
> `/recall`), or an explicit cross-persona search (pending fabric work). A
> persona name given to `/attach ref` now simply fails as "no such mark."

## Usage

```
/attach                      # list what's attached, grouped by type + cost shape
/attach doc <name>           # inline a reference doc (was /doc <name>)
/attach ref <mark>           # attach a saved turn as a live pointer
/detach <name>               # remove any attachment by name
/detach <type> <name>        # disambiguate a name that lives in >1 channel
```

- Bare `/attach` lists the docs, refs, and recalled points attached this
  session, each group headed by its cost shape. For the full durable view —
  including locker files — use `/attachments`.
- `/detach <name>` searches the channels in order (ref → recalled point → doc)
  and drops the first match. If the same name lives in two channels, name the
  type: `/detach doc notes` vs `/detach ref notes`.
- `/doc`, `/undoc`, and `/unref` keep working as hidden aliases, so existing
  muscle memory and older docs are unaffected. `/doc` also keeps its full grammar
  (`/doc` to list, `/doc --refresh` to re-read from disk).

## Examples

Inline a convention and keep a pointer to a good turn:

```
/attach doc house-style
   → ✓ attached doc house-style (1,240 bytes inlined into system prompt)
/mark spark                      # tag the last turn first
/attach ref spark
   → ✓ attached ref spark — a live pointer to the saved turn
/attach
   → attached (this session):
       docs   · inlined into the system prompt every turn
         · house-style   1,240 bytes
       refs   · a live pointer to a saved turn
         · spark         → saved turn
```

Detach by name, or with a type when a name is ambiguous:

```
/detach house-style        # drops the doc
/detach ref spark          # drops the ref specifically
```

## Gotchas

- **A ref is not a RAG source.** It carries an empty Collection id by design —
  it is a pointer, not a search target.
- **Docs are snapshotted at attach time.** Editing a doc on disk does not update
  an already-attached copy; `/attach doc` again (or `/doc --refresh`) to re-read.
  xlii warns past ~20 KB — very long reference material wants a persona you
  query, not an always-on inline doc.
- **Skills ride the doc channel but aren't docs.** `/attach` and `/detach` leave
  them alone; manage skills with `/skill`.
- Attachments are **durable**: they persist across restarts until you `/detach`
  them (or `/clear-attachments`). Use `/status` to see everything wired in.

See also: `/recall` (inline a marked turn instead of pointing at it),
`/attachments` (the full durable list, incl. locker files), `/loadout` (save a
bundle of docs + refs + model), and `/locker` (stage files for the next turn).
Run `/describe attach` for the one-line signature, or `/howto knowledge` for the
full deep dive on docs, refs, marks, and the locker.
