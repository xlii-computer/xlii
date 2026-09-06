# First-look review: xlii 0.5.0

> Cold-reader architecture and product review of the xlii codebase (Grok 4.5,
> first pass). Grounded in source, docs, tests/CI, and a small high-signal
> pytest slice — not vibes alone.
>
> **Scope:** package shape, core runtime, security, extensibility, TUI, tests,
> docs, proposals, market position.  
> **Not in scope:** a full line-by-line audit or a ranked bug list.
>
> **Related:** [OVERVIEW.md](OVERVIEW.md) (product positioning),
> [ROADMAP.md](../ROADMAP.md). Historical design proposals are archived out of
> the public tree.

**Bottom line:** This is not a demo wrapper around an LLM SDK. It is a real
substrate with a coherent thesis, unusually strong safety instincts for alpha
tooling, and the scars of rapid invention. The idea is strong. The execution is
serious. The main risk is **complexity outrunning composability** — exactly
what [OVERVIEW.md](OVERVIEW.md) already names.

---

## What it is (and why that matters)

**xlii is a terminal-native, user-composed AI environment** on Grok + xAI
Collections:

- Local files are source of truth; Collections are a mirror/RAG layer
- Shell-first REPL (`bare` = shell, `?` = agent, `/` = commands)
- Two live surfaces in one process: **code** and **chat**
- Curation as product: personas, docs, plugins, roles, skills, loadouts, wiki,
  journal
- Verification culture: rail, plan, loop, swarm, cold judges, harnesses for
  external agents

The “Emacs for AI tooling” framing is not empty marketing. The architecture
actually tries to make **user composition** the load-bearing layer (markdown
plugins, project tools/commands, prompt overrides, hooks, VFS schemes). That is
rare and valuable.

---

## Size and shape

| Area | Rough scale |
|------|-------------|
| Package source (`xlii/`) | ~70k LOC, 200+ modules |
| Tests | **~213 files / ~2,600 tests / ~41k LOC** |
| Docs | README + OVERVIEW / GUIDE / HOWTO / REFERENCE + help corpus |
| Proposals | ~50 active/archive design docs |
| CLI surface | ~45 top-level subcommands |
| REPL surface | ~47 `repl_cmds` modules |
| TUI | 26 modules; `tui/app.py` alone **~2,650 LOC** |
| Version | `0.5.0`, MIT, Python ≥3.11, alpha |

This is already a **medium-large product codebase**, not a script collection.

---

## Architecture (what’s actually good)

### Control flow is clear enough

```
xlii.cli (thin)
  → cmds/* (domain CLI)
  → sessions/code|chat → REPL loop (repl.py + repl_state.py)
  → Agent.run_turn (agent.py)
  → tools (tool_schemas + tool_handlers)
  → dirty_paths → end-of-turn sync
```

`cli.py` is intentionally thin after a real extraction. That’s healthy.
Subcommands register themselves; slash commands live in a declarative registry
(`commands.py` + `repl_cmds/*`). Help/docs are partially generated and
CI-checked (`scripts/check_docs.py`, `docgen`). That is mature discipline.

### State ownership was fixed the right way

`SessionState` is explicitly the single owner of session flags/attachments.
`REPLState` delegates into it. The comments document the old `sync_to_agent` /
`sync_from_agent` bug class and why it can’t recur.

That’s not cosmetic. Bidirectional state sync is one of the classic agent-REPL
failure modes. The structural cause was removed.

### Mode system has real teeth

`ModeController` unifies plan / rail / debug / discovery / ops. Important:
**tool palette is restricted in code**, not only by prompt.

Rail is the best example of the product’s philosophy:

- stages 0–3 read-only at the tool layer
- writes unlock only at implementation
- human advances the gate

That’s stricter and more honest than most “plan mode” features.

### Security posture is unusually intentional

Highlights:

1. **Management key is env-only** — never in config template
2. **Chat keys in encrypted vault** (Fernet + keyring / file / env)
3. **Model is not a security boundary** (`shellgate.py`) — independent command
   classification escalates beyond declared intent
4. **Path jail** (`project_paths.py`, `_resolve_in_project`)
5. **Workers are read-only by architecture** (palette + role enforcement, no
   nested dispatch)
6. **Writer swarm can lock tests** (anti-collusion)
7. **Sync delete guard** (`DELETE_GUARD_THRESHOLD`)
8. **Admin capability gates** on slash commands
9. **Atomic writes** for state that must survive crash (`atomicio.py`)
10. **Security CI** (Gitleaks / Trivy / Semgrep) + private disclosure policy

This is better than most agent tools at alpha.

### Extensibility is real, not aspirational

- Project slash commands (`.xlii/commands.py`)
- Project agent tools (`.xlii/tools.py` / `get_tools()`)
- Markdown plugins with structured `actions:` manifests
- Personas / roles / skills as frontmatter markdown
- Hooks
- Prompt overrides per project
- VFS / addressing (`scheme://target`) as a kernel abstraction
- External harness adapters (Cursor / Claude / Codex / Grok Build)

The plugin design — markdown descriptors instead of “spawn an MCP server per
capability” — is one of the sharpest product bets here.

---

## What’s impressive (specific)

1. **Thesis consistency.** Docs, persona (`ixaac.md`), rail, workers, vault, and
   roadmap all reinforce the same idea: vendor primitives, user composition,
   verification over vibes.

2. **Self-awareness.** `docs/OVERVIEW.md` already lists the real risks
   (complexity creep, vendor coupling). The project is not delusional about
   where it sits vs Cursor / Aider.

3. **Ratchets instead of hope.** Smoke scripts, docs-truth check, plugin lint,
   security scans, large regression suite. The roadmap’s “Phase 2 ratchet”
   mentality is visible in the tree.

4. **Verification stack.** Loop + judges + peer/consult + harness comparison is
   a genuine differentiator. Most agents stop at “tests passed according to me.”

5. **Operational care.** Orphan collection GC, journal collection isolation, key
   prune, fat-collection delete guards, legacy `.xli` → `.xlii` migration,
   corrupt-file recovery patterns.

6. **UX details that power users feel.** Did-you-mean CLI parser, shell-primary
   mode with full TTY, quit as `BaseException` so nested TUI can’t swallow it,
   spill large tool output to disk, freeball as a loud temporary tier above yolo
   with rails that still hold.

---

## What’s concerning

### 1. Complexity is the primary product risk

There are many concurrent “modes of being”:

plan · rail · debug · discovery · ops · howto · image · yolo · freeball ·
scratch · loop · swarm · jobs · harness sessions · admin elevation · role
namespaces · loadouts · attachments (refs / docs / files / skills / plugins)

`SessionState` alone has on the order of **~36 fields**. `REPLState` has on the
order of **~66 methods/properties**.

Individually each feature is justified. Collectively they create:

- cognitive load for users
- combinatorial state bugs
- onboarding cliff
- “which verb do I use?” fog

The proposal queue already recognizes command consolidation (`/attach`,
`/persona`). That instinct is correct and should stay primary.

### 2. God modules remain

| Module | LOC (approx) | Concern |
|--------|--------------|---------|
| `tui/app.py` | ~2650 | classic god object |
| `addressing/_builtin_providers.py` | ~1530 | all schemes in one file |
| `loop.py` / `repl.py` | ~1200 each | still dense |
| `agent.py` | ~930 | better after splits, still central |
| several `repl_cmds/*` | 700–900 | knowledge knowledge handlers |

“Godzilla” extractions already happened (tools façade, mode protocol, cli
split). Good. But **TUI app and addressing providers** look like the next
structural debt. Big modules are where regressions hide and contributor
onboarding dies.

### 3. Vendor coupling is deep

Despite OpenAI client usage and cross-vendor judges/harnesses, the heart is
still:

- xAI Grok models
- xAI Collections sync/RAG
- management API key provisioning model

That’s fine for a personal substrate on Grok. It’s a strategic constraint if
“Emacs of AI tooling” is the long-term claim. Portability is partially designed
(judges, harnesses, storage backend seam) but not yet the default experience.

### 4. Naming leftovers from `xli` → `xlii`

On the order of **~76 residual `xli` references**: vault keyring service name,
env vars (`XLI_VAULT_KEY`), docs paths in comments, collection prefixes, key
name prefixes, stock plugin comments, etc.

Some are intentional compatibility. Some look like unfinished rename (e.g. vault
docstring paths vs `GLOBAL_CONFIG_DIR`). Not catastrophic, but it erodes polish
and confuses operators.

### 5. Lint bar is still low

```toml
select = ["E9", "F"]
```

That’s “don’t crash / undefined names.” Fine as a starting ratchet; weak for a
~70k-LOC package. Style / complexity / import rules would catch real drift.

### 6. Atomic-write principle is uneven

`atomicio` is excellent and used for important state. But agent file tools
(`t_write_file` / `t_edit_file`) still do plain `path.write_text`. Acceptable
for source edits (editor-like), but it means the “atomic everywhere we read
back” principle is contextual, not universal. Fine if deliberate; document it.

### 7. Surface area vs stock content

Lots of machinery; relatively thin stock:

- ~14 stock plugins
- 3 roles
- 1 skill (`grounded-analysis`)
- 1 default persona

Okay for alpha, but the product promise is “composition compounds.” New users
need **great starter composition**, not just empty extensibility hooks.

### 8. Proposals directory is both strength and smell

The design culture is excellent (handoffs, findings, parallel-build briefs, exit
gates). But a large proposal pile + multiple HANDOFF / fold / kernel-rebuild
tracks also signals ongoing architectural churn. Proposals must not become a
second codebase only the author can navigate.

---

## Security review (condensed)

**Strong**

- Env-only management key
- Vault for secrets
- Intent classification independent of model claims
- Worker role tool-layer enforcement
- Path jails on project tools and several slash commands
- Delete guards / admin elevation / freeball rails that don’t drop
- SECURITY.md + scanning workflow

**Watch**

- Shell still runs with `shell=True` in places (necessary for UX, inherent risk;
  gate quality is the mitigation)
- Plugin system can inject network/side effects; effect/trust model looks good,
  but trust UX must stay crisp
- Daemon / XMPP / OMEMO is experimental attack surface — keep it clearly
  optional and fail-closed when extras are missing
- Symlink / outside-root cases need ongoing edge tests

Overall: **security thinking is a strength of this project**, not an
afterthought.

---

## Tests and quality engineering

Among the most test-heavy personal AI tools at this stage:

- ~2.6k tests
- CI: ruff + pytest + coverage + docs truth + plugin lint + offline smoke
- Coverage across tools, switch, swarm, remotefs, addressing, harness sessions,
  freeball, admin gate, etc.

Gaps / notes:

- Many modules lack a named 1:1 test file (normal if covered via integration
  tests)
- Giant TUI tests (e.g. `test_tui_textual.py`) will become slow/brittle if not
  modularized with the app
- A small high-signal local slice (atomicio / shellgate / config / paths /
  tools) passed cleanly at review time

The docs ratchet (`check_docs.py`) is especially good — docs that can’t silently
invent commands.

---

## Product / UX feedback

### Wins

- Shell-first is the right default for this audience
- Code ↔ chat switch without losing threads is a killer feature
- `/rail`, `/loop`, `/consult`, workers, and plugins form a real workflow
  language
- “Local first, remote second” is coherent and trustworthy

### Friction

- Discoverability still depends on `/help`, `/commands`, `/apropos`, and a large
  REFERENCE
- Too many attachment verbs/channels for one mental model
- TUI is ambitious but optional/experimental; don’t let it fork the product
  brain from the REPL
- Alpha status is honest; onboarding still needs a golden path that feels
  inevitable in ~10–60 minutes

OVERVIEW’s success criterion is exactly right:

> make composability feel inevitable instead of overwhelming

That’s the north star. Feature velocity is secondary.

---

## Design patterns worth calling out

- **Declarative registries** for slash commands with hard fail on scope
  collisions
- **ModeController protocol** instead of pure boolean soup
- **Dirty-path end-of-turn sync** rather than chatty per-write network
- **Spill large tool output to disk** for context hygiene
- **Freeball as temporary elevated trust**, not permanent config
- **Cross-vendor judges** so the writer doesn’t grade its own homework
- **Addressing/VFS kernel** (`scheme://`) as a unifying abstraction for panes,
  tools, remotes
- **Generated docs + CI truth check**

These are senior-systems choices.

---

## Prioritized recommendations (joining-the-project view)

### Near-term (highest leverage)

1. **Command/attachment consolidation** — finish `/attach` / verb-unification;
   reduce surface synonyms
2. **Break up `tui/app.py` and `_builtin_providers.py`** — structural debt with
   real regression risk
3. **Finish the `xli` rename cleanup** where safe; document intentional legacy
   aliases in one place
4. **Raise ruff gradually** (imports, unused, bugbear) without a style holy war
5. **Golden-path polish** — one excellent first-hour story: setup → code → rail
   → plugin → verify

### Medium-term

6. **SessionState factorization** — group fields into nested value objects
   (modes, trust, attachments, metering) so flat fields stop growing forever
7. **Stock composition pack** — more roles/skills and 2–3 opinionated loadouts
   that showcase the thesis
8. **Portability story** — keep xAI-first, but make “local-only + external
   harnesses” a first-class happy path for non-Collections users
9. **Proposal hygiene** — archive shipped work aggressively; one queue of truth

### Don’t do

- Don’t chase Cursor feature parity
- Don’t add another mode before collapsing two existing ones
- Don’t grow TUI and REPL as separate products with divergent state rules

---

## Verdict

| Dimension | Grade (alpha context) | Note |
|-----------|------------------------|------|
| Thesis / product vision | **A** | Clear, differentiated, self-aware |
| Core architecture | **A-** | Strong ownership model; a few remaining gods |
| Safety / security thinking | **A** | Better than most agent CLIs |
| Extensibility | **A-** | Real seams; needs more stock content |
| Complexity management | **C+** | The existential risk |
| Tests / ratchets | **A-** | Unusually strong for personal tooling |
| Docs | **A-** | Deep; still heavy; good CI truthing |
| Polish / naming / onboarding | **B-** | Alpha-honest; rename leftovers; steep curve |
| Market readiness | **B** as personal substrate; **C** as broad product | Correctly niche |

**Overall:** A **strong alpha with adult engineering instincts**. The codebase
looks like someone who got burned by silent state bugs, prompt-only “safety,”
and doc drift — and then built systems against those failure modes.

If it succeeds, it will not be because it became another Cursor. It will be
because the composition model stays legible while the workshop keeps growing.

---

## Follow-ups

Possible deeper passes:

- TUI architecture only
- Loop / swarm verification only
- Plugin security model only
- A “what to delete/merge first” complexity diet
- Graded upgrade path for B/C → A (complexity, polish, market readiness)

---

*Reviewer note: first contact with the tree; line counts and field counts are
approximate and will drift. Prefer code + generated help over this document if
they disagree.*
