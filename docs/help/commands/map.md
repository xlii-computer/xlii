# /map

`/map` renders the **repo map** — the project's shape as one deterministic
markdown outline: the file tree plus every Python class and function with its
full signature and docstring first line. It answers *"what is there?"*, the
question that comes **before** *"where is X?"* — orientation, not search (for
finding things, `grep` and `search_project` remain the tools).

One engine, four thin clients that all render the **same bytes** for the same
scope: `/map` in the REPL, the agent's `map` tool (the model pulls orientation
on demand), `xlii map` from the shell, and the `map://` address space in panes.
No surface grows its own walker.

## Using it

```
/map                    ← the whole project's outline, printed
/map xlii/tui           ← just that subtree
/map attach             ← keep the outline in the system prompt (session-long)
/map attach xlii/tui    ← attach a scoped outline instead
/map off                ← detach it
```

- **Bare `/map [path]`** regenerates and prints on every run — the map is
  never cached (a stale shape map is worse than none; generation is
  subsecond).
- **`/map attach [path]`** rides the same attachment seam as `/doc` and
  `/howto`: the reserved doc name is `map`, so it shows in `/attachments`,
  re-running **replaces** the prior copy, and `/detach map` also drops it.
  You'll get the usual size warning if the outline is large for every-turn
  context.
- **`/map off`** detaches.

## The shell client

```
xlii map                      # whole project, from the cwd
xlii map xlii/tui             # scoped to a subtree
xlii map --depth 1            # tree levels capped
xlii map --detail files       # tree only, no symbols (huge scopes)
```

`xlii map` is doctor-shaped — offline, no key, no session — and pipeable.
It's the surface *other* agents consume: a vector brief for a fanned-out
worker can embed `xlii map xlii/tui` output verbatim.

## The address space

`map://` mounts the same outline as a read-only, computed address space:
`map://` is the project root (browse it with `ls`, or `cat` it for the whole
outline), and `map://xlii/agent.py` reads that file's symbol outline.

## Guarantees

- **Deterministic**: sorted entries, relative paths, no timestamps — two runs
  on an unchanged tree are byte-identical (diffable).
- **Budgeted, loudly**: output is hard-capped (~16 KB), and the cap wins over
  everything. Over budget, symbol detail drops for the largest files first,
  then the deepest tree levels — and the outline always ends with an explicit
  `… truncated (N files elided, M stripped to tree entries …)` marker whose
  two counts are each honest. Even at a pathological cap smaller than the
  marker itself you get a (truncated) marker, never a silently clipped map.
- **Degrades where honest, errors where not**: a Python file that fails to
  parse becomes a tree entry marked `(unparseable)`; non-Python files list
  with their size; gitignore is respected. But a path that doesn't exist is an
  *error*, never an empty map that reads as "empty directory".

## When to reach for it

- Starting work in an unfamiliar repo or subtree — orient before reading.
- Briefing another agent: paste `xlii map <scope>` output into the brief.
- Shape-heavy refactors: `/map attach` keeps the current structure in context.
