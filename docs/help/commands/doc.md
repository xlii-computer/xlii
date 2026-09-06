# /doc

Attach a named reference doc into the system prompt for the rest of the
session. A doc is static knowledge you want the model to *always* see — a
GROK.md-style guide, your project conventions, a glossary, a small spec.
Once attached, it rides in every turn's system prompt with no retrieval cost
and no chance of being missed.

The canonical spelling is now `/attach doc <name>`; `/doc` is a hidden alias that
keeps working (and keeps its full grammar — bare `/doc` to list, `/doc --refresh`
to re-read). Reach for it when you keep re-explaining the same rules ("always
httpx, never requests", "run pytest -x after edits", "snake_case filenames").
Encode them once as a doc, attach it, and stop repeating yourself. A doc is for
small always-on rule-sets — large reference material wants a persona you query
on demand, not an always-inlined doc.

## Usage

```
/doc            # list docs attached this session
/doc <name>     # attach <name> into the system prompt
/undoc <name>   # detach it
```

Docs live on disk and are managed from the shell, not the REPL:

```
xlii doc --new <name>     # create; opens $EDITOR on a template
xlii doc --list           # show all docs + one-line summaries
xlii doc --edit <name>    # reopen in $EDITOR
xlii doc --delete <name>  # remove (add --yes to skip the prompt)
```

Names are single tokens — letters, digits, `_`, `.`, `-` only.

**`/doc` is docs-only.** Skills also inline into the system prompt — they ride
the same `attached_docs` channel under a `skill:` prefix — but they are *not*
docs and `/doc` deliberately excludes them. Bare `/doc` lists only real docs
(it appends a one-line "N skills attached — manage with `/skill`" reminder when
any are present), and `/undoc <name>` refuses to detach a skill, pointing you at
`/skill off <name>` instead. Manage skills with `/skill`, not here.

## Examples

Codify a convention once, then attach it at the start of a session:

```
xlii doc --new house-style    # write your rules in $EDITOR, save
xlii code
/doc house-style              # ✓ attached, inlined into the system prompt
/doc                          # confirm what's attached + byte sizes
```

Drop it again when it stops being relevant:

```
/undoc house-style
```

## Gotchas

- The attachment is **durable**: it's saved to the project's session and
  survives REPL restarts. A doc you `/doc` today is still inlined next time you
  open this REPL, until you `/undoc` it. The live "this session" wording in the
  listing refers to the active session state, not a one-turn toggle.
- Bare `/doc` with nothing attached just prints usage; it does not create
  anything. You must `xlii doc --new <name>` first — `/doc` only attaches docs
  that already exist on disk.
- Every attached byte is re-sent every turn. xlii warns past ~20 KB; if a doc
  is that large it wants chunking — build a persona around it and query that
  persona when you need it, instead of inlining.
- Editing a doc on disk does not update an already-attached copy — the content
  is snapshotted at attach time (and that snapshot is what's restored on
  restart). `/undoc` then `/doc` again to pick up changes.
- A typo'd name gets "did you mean" suggestions from your existing docs; check
  `xlii doc --list` if you're unsure of the exact token.
- **Skills won't show up here.** Even though a skill is attached through the same
  channel as a doc (under a `skill:` prefix), `/doc` filters it out of the
  listing and `/undoc` won't drop it — that's a `/skill off <name>` job. So the
  byte counts and detaches you see from `/doc` are docs only; the listing just
  flags how many skills are riding alongside.

See also: `/attach` (the doc / ref umbrella), `/recall` (inline a marked turn),
`/loadout` (bundle docs, refs, and model into a saved workspace), and `/locker`
(stage files for the next turn).
Exact flags: `/describe doc`. Deep dive: `/howto knowledge`.
