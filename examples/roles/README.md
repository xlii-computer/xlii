# Example roles (author reference)

**Role descriptors** for the roles feature
([`../../proposals/roles.md`](../../proposals/roles.md)). A role is a persona with a
stocked loadout — it names skills, docs, plugins, and a model to bring on activation.

## Status: SHIPPED

The roles feature is live (R0–R3). The **coding three** below ship *bundled* with xlii as
**stock roles** ([`../../xlii/stock_roles/`](../../xlii/stock_roles/)) — no copying needed:

```
/role                     # list (incl. the shipped stock roles)
/role code-architect      # equip in code (its grounded-analysis skill attaches), or
                          # become in chat (its own thread + memory)
/role off                 # drop an equipped loadout (code)
```

The `grounded-analysis` skill they reference also ships ([`../../xlii/stock_skills/`](../../xlii/stock_skills/)),
so activation produces a real attachment out of the box. This directory stays as **author
reference** (incl. `inbox-assistant`, which awaits the email core module) — copy any file
into your role search path (`~/.config/xlii/roles/` or `.xlii/roles/`) and edit to taste.

## The roles

| Role | What it's for | Skills it bundles |
|------|---------------|-------------------|
| `code-architect` | grounded designs, ADRs, phased plans (no prod code) | `grounded-analysis` |
| `test-engineer` | tests that mirror existing conventions, stub at the boundary | `grounded-analysis` |
| `debugger` | trace the failure surface, reproduce, fix the cause | `grounded-analysis` |
| `fleet-conductor` | file-disjoint vectors + merge contract; conductor owns the mint | `vectoring`, `grounded-analysis` |
| `inbox-assistant` | email triage, drafting, gated send | `grounded-analysis` (+ `inbox-triage`, future) |

The coding three share the **one** `grounded-analysis` skill — the proposal's core point:
the skill is the *unit*, the role is the *bundle*. One skill, many roles; no skill is
redefined per role.

`inbox-assistant` additionally depends on the **email core module**
([`../../proposals/email.md`](../../proposals/email.md)) for its read/search/send tools —
email is core, not a plugin, so there's no `plugins:` connection to wire.

## Customizing

Uncomment the `docs:` / `plugins:` lines and point them at *your* real `/doc` names and
plugin ids. The body is the role's system-prompt identity — edit it to match how you work.
