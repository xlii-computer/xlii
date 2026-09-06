---
sources: file://xlii/mode_contract.py#L65-238, file://xlii/session_state.py#L44-83, file://xlii/tool_context.py#L104-145, file://xlii/tool_handlers.py#L42-91, file://xlii/shellgate.py#L1-55, file://xlii/repl_cmds/session.py#L28-243, file://xlii/session_boot.py#L651-732, file://xlii/repl_cmds/howto.py#L419-483
verified: false
---
# trust-and-gates

Two orthogonal ladders decide what runs without a human in the loop: a session **trust tier** (how much the operator pre-authorized) and a per-command **intent severity** (how dangerous the classified action is). A gate fires when severity outranks what the tier grants.

## The trust ladder
Three bare-word tiers, low → high: `safe` / `yolo` / `freeball`. Source truth is `TIER_SAFE/YOLO/FREEBALL` in `session_state.py`, mirrored as literals in `mode_contract.py` so the contract layer never imports the session layer (the sync is test-pinned). `TrustState` holds a single `tier` slot; `.yolo` and `.freeball` are derived views of it, so **freeball ⇒ yolo by construction** — it used to be two hand-synced booleans.

- **safe** (default): bash confirmation gate active for gated intents.
- **yolo** (`/yolo`): bash gate OFF — network and modifies-system commands run without prompting. `/safe` restores.
- **freeball** (`/yolo --freeball`): trusted-run tier above yolo — gate off AND spend + per-action prompts auto-yes. Session-only, never persisted; a fresh session always starts safe. `/yolo --freeball <task>` is one-shot: stashes the current tier, runs one turn gates-down, then restores in a `finally` even on interrupt/error. The-fold Vector C consolidated this to one escalating dial (no `/freeball` alias).

Rails still hold at every tier: sync delete-guard, /admin elevation, the debug-marker contract, /budget cap. Freeball drops the confirm gates, not the rails.

## trust_tier_rank — the comparator
`trust_tier_rank(tier) -> int` maps safe/yolo/freeball to 0/1/2, so "tier ≥ N" is one integer compare. Unknown tiers raise `ValueError` rather than defaulting to safe — a typo silently reading as "safe" would be a security decision made by accident. `GateContext` and `EntryGate` both validate their tier strings at construction for the same reason. `EntryGate.min_trust_tier` exists but no mode gates on the ladder today (it defaults to None = any tier).

## Intent severity + the shell confirm gate
Every bash call carries a model-declared `intent`, but the model is not a security boundary. `shellgate.classify_command` independently classifies the command text on a severity ladder: `read-only < modifies-project < network < modifies-system`. SYSTEM_BINARIES (sudo, apt, systemctl, mount…) escalate straight to modifies-system; interpreters and build tools classify as project-mutation so a declared read-only `python -c` mismatches and gets surfaced. The executor gates on the **stronger** of declared vs classified.

`GATED_INTENTS = {modifies-system, network}` — only these two ever prompt; read-only and modifies-project never do. `shell_command_needs_confirm` returns False under yolo (which covers freeball, since freeball ⇒ yolo), False for un-gated intents, and False for a gated intent in `auto_approve` — **except modifies-system, which no grant can bypass** (defense in depth; `/approve` already refuses to grant it, but the policy seam must not trust the caller).

Worker agents are enforced here, not by politeness: read-only workers refuse any non-read-only command; writer-workers refuse network/system without yolo.

## /approve intent categories
`/approve <category…>` pre-authorizes gated intents for the session (read-only, network, modifies-project; `--none` clears; bare `/approve` shows current grants). **modifies-system cannot be auto-approved** — `_parse_approve_categories` rejects it outright ("session-only gate always applies"). Granting read-only/modifies-project is a no-op (they never prompt anyway), so the only grant that actually changes behavior is `network`. `/safe` clears `auto_approve`; DEFAULT_AUTO_APPROVE is empty.

## startup-task --auto gate (D17)
A per-machine startup binding may carry `mode=auto` and a required `tier`. `resolve_startup_mode` downgrades auto → capture-only when the pipeline is missing, its hash drifted from the bound hash, or **the session tier ranks below the binding's required tier** (`trust_tier_rank(session) < trust_tier_rank(binding.tier)`). The exact `/tasks run <name>` line is always printed regardless — review-before-run holds even when auto fires. D17 fix (2026-07-20): the session tier is read from `state.agent.session.trust_tier`; REPLState has no `trust_tier` attribute, so reading it there pinned the gate to "safe" forever. Fail-soft — an unknown tier string in the store skips the binding, never bricks boot.

## Self-repair capability/risk gating
`/howto fix` diagnoses (runs doctor, prints fixes) and surfaces related help, but applying a whitelisted config/CLI repair takes two gates: **elevation** (`session_is_elevated` / `/admin unlock`) AND a **per-fix confirm** (`would run:` shown first, then "apply this fix? [y/N]"). Unelevated sessions see the fixes read-only with a pointer to unlock. Only doctor findings that carry a `runnable_fix` are offered — arbitrary repair is not exposed.

## Standing philosophy: review-before-run
The through-line is PREFILL, not auto-execute: /gitpain describes cleanup then PREFILLs it into the input for review rather than running it; recall-paste stays a review-before-run path; startup --auto prints the exact command line even when it will fire. Gates default closed; escalation is explicit and session-scoped.

Related: [[command-surface]], [[tasks-and-pipes]], [[gitpain]], [[help-and-howto]], [[status-and-chrome]].
