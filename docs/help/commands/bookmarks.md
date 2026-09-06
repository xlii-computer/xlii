# /bookmarks

`/bookmarks` lists the named turns you've saved in this session — the *view* side
of marking. You create one with `/mark <name>` (the *verb*), then `/bookmarks`
shows what you've stashed and `--all` browses every persona's library at once.

A bookmark is just a turn tagged with a name. It's the lightweight way to pin a
moment in a conversation — a decision, a working snippet, a thread you'll want to
pull back later — without copying anything out of the chat. The pair fixes the
old `/mark` ÷ `/marks` confusion: those two read almost identically and people
constantly typed one meaning the other. Now the verb keeps its name (`/mark`, you
*do* something) and the view is spelled out (`/bookmarks`, you *look*). The old
`marks` name still works as a **hidden alias**, so muscle memory and older docs
don't break — it just never shows up in `/help`.

## Usage

```
/mark <name> [--window N]
/bookmarks [--all]
```

- `/mark <name>` — tag the **last** turn with `name`.
- `/mark <name> --window N` — also carry the `N` turns *before* it, so the mark
  travels as one idea-unit when recalled.
- `/bookmarks` — list the marks in the active store (this persona or project).
- `/bookmarks --all` — browse every persona's marks, each shown addressably as
  `<persona>:<mark>`.

Both surfaces work in `/chat` and `/code`. A mark name can't end in a
`(window: N)` suffix — that re-parses as a span on reload, so it's refused.

## Examples

Mark a turn, then list what's saved here:

```
/mark auth-decision
   → ✓ marked last turn as auth-decision  (/recall auth-decision to pull it back)
/bookmarks
   → bookmarks
       · auth-decision   2026-06-27 14:02
```

Capture a turn *and its lead-up*, then scan the whole library:

```
/mark migration-plan --window 3
/bookmarks --all
   → bookmarks — across all personas
       ada
         · ada:auth-decision   …
       reviewer
         · reviewer:rubric      …
```

## Related

A bookmark isn't just for listing — it's a knowledge primitive. `/recall <mark>`
(or `<persona>:<mark>`) inlines the marked turn back into the system prompt;
`/attach bookmark <mark>` instead attaches it as a *live pointer* you can preview
(neither inlined nor searched). The `<persona>:<mark>` form is the one deliberate
bridge for carrying context across `/persona` switches. See `/recall`, `/attach`,
and `/persona`. Run
`/describe bookmarks` for the one-line signature, or `/howto knowledge` for the
full deep dive on docs, refs, marks, and the locker.
