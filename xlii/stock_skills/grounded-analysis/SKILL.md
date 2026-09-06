---
name: grounded-analysis
description: Map an area to citeable facts before proposing/designing/refactoring — decompose into slices, fan out read-only explorers, demand symbols+file:line (not prose), verify load-bearing claims, then synthesize.
metadata:
  primitive: "dispatch_subagent role=explore (parallel-safe)"
  use-before: "writing any proposal, design, or plan"
---

# Skill: grounded-analysis

A method for the task shape: **"understand area X well enough to do Y"** — where Y is
propose / design / refactor / review / debug, and Y must be grounded in how the code
*actually works*, not in memory or assumption.

A plan that isn't grounded in real symbols is a wish. This skill is how you stop shipping
wishes.

## When to reach for it

- Before writing a proposal, design doc, ADR, or implementation plan.
- Before a non-trivial refactor or a "does X already exist?" decision.
- Any time you'd otherwise describe the code from memory. Don't — map it.

## The method

1. **Decompose, don't sprawl.** Split X into **3–6 non-overlapping slices** (by module,
   subsystem, or concern). Each slice must be independently explorable — no slice should
   depend on another slice's findings. If two slices would read the same files, merge them.

2. **Fan out read-only explorers.** Dispatch **one `explore`-role subagent per slice**,
   all in one batch (`dispatch_subagent` is parallel-safe — send them together, don't
   serialize). Scope each agent tightly to its slice and nothing else.

3. **Demand citeable returns, not prose.** Each explorer must report **exact symbol names,
   signatures, `file:line` references, and on-disk formats/paths** — not impressions.
   Reject "it handles config nicely"; require "loaded by `GlobalConfig.load()` at
   `config.py:232`, JSON at `~/.config/xlii/config.json`." Explorers **locate and quote;
   they do not edit, write, or opine.**

4. **Verify the load-bearing claims yourself.** Before you assert that something *exists*,
   is *already shipped*, or is *verified*, confirm it directly with grep/read. Explicitly
   separate **"exists today"** from **"proposed."** This is the single step that converts a
   confident wish into a buildable plan — and the one most often skipped.

5. **Synthesize with citations.** Compose the answer citing real symbols (`file:line`).
   Every load-bearing claim is either grounded or explicitly flagged unverified.

## Output discipline

- Ground every load-bearing claim with a `file:line` or a named symbol.
- Mark anything you could not verify as **unverified** — never launder an assumption into
  a fact.
- If you put text under a "verified" / "already exists" heading, it must actually be
  checked. (The classic failure: a "verified paths" table with a path nothing produces.)

## Anti-patterns

- **Overlapping explorers** — two agents mapping the same files. Wasted fan-out; re-slice.
- **Accepting prose-only summaries** — you can't cite them. Re-ask for symbols + file:line.
- **Synthesizing from memory** of how the code "probably" works.
- **Skipping verification** on the one claim the whole proposal rests on.
- **Over-fanning** — 12 explorers for a 2-file question. Match fan-out to real breadth.
