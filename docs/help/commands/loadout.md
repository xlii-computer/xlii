# /loadout

A loadout is a saved bundle of your active attachments: the refs and docs you
have pulled in, the locker files you are sharing, and any model/temperature
override in play. Over a session you accumulate a working set — three reference
personas, two docs, a model swap for hard reasoning. `/loadout` lets you bank
that whole arrangement under a name and snap back to it later instead of
reattaching everything by hand.

Reach for it when you keep rebuilding the same context. Common shapes: a
"review" loadout (the codebase docs + a strict-reviewer model), a "debug"
loadout (one ref, verbose locker logs), or a "writing" loadout (no refs, a
cooler temperature). Switching loadouts is how you change *what the model is
working with* without leaving the session. Alias: `/workspace`, `/ws`.

The pieces a loadout bundles are the same ones you wire in with `/doc`,
`/ref`, and `/locker`; use `/attachments` to see them live before you bank.

## Inspect first

```
/loadout
/loadout show
```

Bare `/loadout` prints the active bundle: persona, any frontmatter loadout the
persona declares, active plugins, docs, refs, locker share count, model and
temperature overrides, and a context-budget line. Read this before you save —
it shows exactly what will be captured. (Bare `/workspace` / `/ws` jump
straight to `list` instead.)

## Save, load, switch

```
/loadout save review
/loadout list
/loadout load review
/loadout delete review
```

`save <name>` banks the current bundle; `load <name>` re-applies it (the active
slot is marked with a dot in `list`); `delete <name>` drops it. Loadouts saved
this way are local to the current persona/project context.

## Share across projects

```
/loadout export review            # publish to the global tier
/loadout import review            # pull it into this context
/loadout import review as audit   # import under a different local name
/loadout global-list
/loadout global-delete review
```

`export` lifts a local loadout into a global library so another project can
`import` it. Use `import <global> as <local>` to avoid clobbering a same-named
local loadout. `global-list` / `global-delete` manage the shared tier.

## Gotchas

- Refs are saved *by name*. If a persona a loadout points at no longer exists,
  loading just won't reattach that ref — re-check with `/loadout show` after a
  load. Docs and locker entries are snapshotted, so an attached doc rides along
  even if the file on disk later changes; re-save to pick up edits.
- Loading does not merge; it replaces the active attachment set with the saved
  one. Save your current arrangement first if you want it back.
- Local loadouts are scoped to the persona/project you saved them in. To reuse
  one elsewhere you must `export` then `import` — plain `load` won't see it.
- `import` fails loudly if the named global loadout doesn't exist; check
  `global-list` for the exact name before importing.

Run `/describe loadout` for the bare command shape. Siblings: `/describe doc`,
`/describe locker`, `/describe ref`. Deep dive: `/howto personas-loadouts`.
