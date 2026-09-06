# /role

`/role` activates a **role** — a named specialist bundle (a persona with a stocked
loadout: skills, docs, plugins, a model, and an identity). One verb, two modes:

- **In code, a role *equips*** — it applies the loadout onto your current project
  session (the named skills/docs/plugins attach, the model pins) without touching
  the project's own identity or memory. `/role off` drops it.
- **In chat, a role *becomes*** — it switches to the role as a persona with its own
  detached thread and Collection memory (under `chat/<name>`), loadout and all.
  `/code` parks it.

A role *references* skills; it never redefines them. The skill is the unit, the role
is the bundle — so the same `grounded-analysis` skill backs several roles.

## Usage

```
/role                 list available roles (marks the active one)
/role <name>          activate — equip (code) or become (chat)
/role off             drop an equipped loadout (code)
/hire <name>          alias of /role <name>
```

`xlii role list` and `xlii role show <name>` do the same from the CLI (headless).

## Where roles live

Three tiers, project overriding global overriding the shipped stock roles:

```
.xlii/roles/<name>.md            project-local
~/.config/xlii/roles/<name>.md   your global roles
(bundled with xlii)              stock starter roles
```

A descriptor is a markdown file: YAML frontmatter loadout
(`skills:`/`docs:`/`plugins:`/`refs:`/`model:`) + a body that becomes the identity.

## Starter roles (shipped)

| Role | For | Bundles |
|------|-----|---------|
| `code-architect` | grounded designs, ADRs, phased plans (no prod code) | `grounded-analysis` |
| `test-engineer` | tests that mirror existing conventions, stub at the boundary | `grounded-analysis` |
| `debugger` | trace the failure surface, reproduce, fix the cause (pairs with `/debug`) | `grounded-analysis` |
| `fleet-conductor` | file-disjoint vectors + merge contract; conductor owns the mint | `vectoring`, `grounded-analysis` |

## Examples

```
/role code-architect      # equip: the grounded-analysis skill attaches to this session
# … design work …
/role off                 # drop it

/chat                     # over in chat:
/role debugger            # become the debugger persona (its own thread + memory)
```

## Gotchas

- **Equip is additive; `/role off` reverses what the role declared.** A doc you also
  attached by hand may be dropped too — re-attach with `/doc` if needed.
- **Switching surfaces drops the active role.** A `/code`↔`/chat` switch clears the
  equip marker (its attachments are per-surface), like rail/debug mode.
- **A missing skill/doc/plugin warns and is skipped** — a bad loadout entry never
  blocks activation.

Deep dive: `/describe role`
