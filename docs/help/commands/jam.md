# /gaggle (also /jam)

A **gaggle**: named brains answer the same question, then one merge.

Who + how many + merge policy + budget cap. `write: false`. Not swarm, not
fleet, not a crew UI. `/jam` is the shipped synonym. Nested form:
`/gigwork gaggle second-opinion <question>`.

The orchestrator may `dispatch_subagent(gaggle="second-opinion")` when
every foreign member is on `gigwork.defaults.allow`. Investigate hops on
a deep-search turn accept the same pin, or
`gigwork.defaults.investigate_gaggle` when the call site is unset.

## Usage

```
/gaggle <name> <question>
/gaggle ls
/gaggle add <name> <backend[:kit][@model]>… [--merge synth_conflicts|concat_digest] [--cap N]
/gaggle rm <name>
/gigwork gaggle second-opinion <question>
```

Stock: `second-opinion` (home explore + default gig, `synth_conflicts`),
`debate` (gig first), `scout` (two gig passes, digest). `gig` slots bind
to your default provider — `/gigwork add` one first.

Crews live in `gigwork.jams` (`/gaggle add` writes them). A retired
`gigwork.gaggles` block still loads.

## When

Second opinion, a debate, or a scout sweep. If you want one hired brain,
use `/gigwork`. If you want writers in worktrees, that is `/swarm`.
