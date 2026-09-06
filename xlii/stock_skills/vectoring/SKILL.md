---
name: vectoring
description: >
  Seam-driven parallel refactor — cut file-disjoint vectors, fan a fleet,
  conductor merges and ratchets. Use when the user says "vector this",
  "godzilla", "fleet", "mothra", or wants a week of kernel work in a weekend.
  Not for a one-file fix or two agents that would share a mixin.
metadata:
  primitive: "file-disjoint vectors + merge contract + conductor"
  use-before: "any multi-agent refactor of xlii/"
  see: "this skill body"
---

# Skill: vectoring

Latency compression: pay for a true decomposition so N agents can write
at once without merge hell. **Vector** = one lane. **Fleet** = N at once.
**Conductor** = one closer who merges, fills gaps, runs gates.

Godzilla v Mothra was a campaign that used this. The method is the
skill. The names are optional.

## When

- Several modules, several days of serial work, **and** you can cut
  lanes that do not share files.
- User says vector this / godzilla / run a fleet.

## When not

- One file, one bug, one PR.
- Two concerns that **must** edit the same mixin or module — that is
  one agent with a **sequence**, not a fleet (`20-stage2-common.md`).
- You have not grounded the tree this week. Memory of the code is a
  wish. Run `grounded-analysis` first.

## Method

1. **Intake** — dump the work unsorted. Do not design yet.
2. **Ground** — real symbols, `file:line`. Half the ideas change shape.
3. **Synthesize** — cluster. Same seam = same vector.
4. **Decompose** — file-disjoint vectors + a **merge contract** + an
   ownership map (who may touch which path). The contract is the
   artifact. If two vectors need the same file, merge the vectors or
   sequence them.
5. **Package** — one campaign doc + one brief per vector (owns, does
   not own, gates, body test). Then fan out.

## Fleet (builders)

- One agent, one worktree, one branch. Never the live main checkout.
- Never `git add -A`. Stage owned files only.
- Do not merge. Push the branch; stop.
- Do not `pip install -e .` in a shared env.
- Customizability stays: stubs, subscribe, makers. Do not invent a
  locked app to feel tidy.

## Conductor (one, at the end)

Merge in the contract's order. Fill gaps. Then:

```
ruff check xlii/ tests/
env -u XAI_MANAGEMENT_API_KEY python -m pytest -q
python -m xlii.docgen && python scripts/bundle_help.py && python scripts/check_docs.py
XLII_CONFIG_DIR=$(mktemp -d) python -m xlii plugin --lint
```

Update the campaign map. Stamp only if the suite is the mint.

## Body test

A new body (face, WS, daemon) can wrap the module without editing it.
Line count is a symptom. Brain-matter-in-the-face is the disease.

## Canonical paper

The method is this skill body. Historical campaign briefs were archived out of
the public tree.
