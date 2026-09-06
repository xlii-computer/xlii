# /ref

`/ref` is a **legacy alias of `/recall`** — it inlines a bookmarked turn (a
`/mark`) into the conversation, a cut-and-paste of exactly that marked window and
nothing more. The canonical verb is `/recall`; `/ref` keeps working for muscle
memory but no longer appears in `/help`.

> **If you're looking for the old `/ref`:** the word "ref" used to imply
> "attach another persona's *memory Collection*." That behavior was never what
> the live `/ref` did — the registered command has long been *recall a marked
> turn*, and the help page describing persona-Collection attach was stale. The
> Fold (Vector A) reconciled the two: recall is now honestly named `/recall`, and
> the persona-Collection and bookmark ideas became first-class **`/attach`
> types**. See the table below for where each old behavior lives now.

## What `/ref` does today

```
/ref <mark>            # paste that marked window in (== /recall <mark>)
/ref <persona>:<mark>  # qualify only when a mark name lives in >1 persona
/ref                   # list the points recalled this session
```

A recall inlines the marked exchange as a `point:` doc — so it *does* cost
context every turn, exactly like a `/doc`. It never pulls in a persona's whole
memory, and no persona label rides into what the model sees. Drop a recalled
point with `/detach <mark>` (`/unref` also still works). This is identical to
`/recall`; read that page for the full story, `--window`, and cross-persona
addressing.

## Where the old "ref" meanings went

The concept of a typed *reference* survives — it just moved under `/attach`,
where each type teaches its own cost shape:

| You want to… | Old idea | Now |
|---|---|---|
| Inline a saved turn into context | `/ref <mark>` (live) | `/recall <mark>` (or `/ref`, the alias) |
| Keep a live pointer to a saved turn | `/ref --bookmark <mark>` (stale docs) | `/attach ref <mark>` |
| Search another persona's memory | `/ref <persona>` (stale docs) | **banned** — sealed islands; cross-persona search is pending fabric work |
| Drop any of the above | `/unref <name>` | `/detach <name>` |

- **`/attach ref <mark>`** attaches a saved turn as a live pointer: neither
  inlined nor searched (it carries an empty Collection id — RAG-inert by
  construction), previewable from the ref tab. `bookmark` is the accepted alias.
- **The whole-persona attach is gone.** It was a standing cross-persona leak;
  the deliberate bridges are a marked turn you chose, or an explicit
  cross-persona search when the fabric work ships it.

## Examples

Recall a marked turn (the live behavior), then drop it:

```
/mark spark            # tag the last turn
/ref spark             # ↳ recalled spark — pasted in (/detach spark to drop)
/ref                   # → recalled points:  · spark
/detach spark          # ✓ dropped recalled point spark
```

Keep a pointer to a good turn without inlining anything:

```
/mark spark            # tag the last turn first
/attach ref spark      # ✓ attached ref spark — a live pointer to the saved turn
```

## Gotchas

- **A persona name never attaches memory.** A bare word is a *mark lookup*,
  searched across every persona — so `/ref ada` (or `/attach ref ada`) looks for
  a mark named `ada`; it does **not** and cannot attach ada's Collection.
- A recall is a doc-shaped cost: the pasted window rides every turn until you
  `/detach` it. For a pointer with zero per-turn cost, prefer `/attach ref`
  (previewed on demand, never inlined).
- If a mark name lives in more than one persona, `/ref` lists the candidates as
  `<persona>:<mark>` and waits for you to qualify — nothing is pasted until it
  resolves unambiguously.

See also: `/recall` (the canonical spelling of this command), `/attach` (the doc
/ ref / bookmark umbrella), `/detach` (drop an attachment), `/mark` and
`/bookmarks` (create and browse saved turns). Run `/describe recall` for the
signature, or `/howto knowledge` for the deep dive.
