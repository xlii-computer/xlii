# xlii Roadmap — from alpha to a primo terminal substrate

> **What’s true now (2026-07-08):**  
> Runtime map → **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**  
> First hour → **[docs/GOLDEN-PATH.md](docs/GOLDEN-PATH.md)**  
> Quality track (grades Phases 0–5 shipped on `groked`) → first-look
> [docs/FIRST-LOOK-REVIEW.md](docs/FIRST-LOOK-REVIEW.md); Phase 7 = personal daily driver.  
> XMPP fabric = **separate** deferred plan (not the next silent default).
>
> **Status (2026-06-12):** Phases 0–5 are DONE (commits 7ea36e9 → 09c9a2c),
> except 5.3 (plugin discovery L2 rerank — needs embeddings or LLM rerank;
> deferred with the provider-abstraction work). Phase 5 delivered: local
> FTS5 search index (offline/local-only search_project), xlii export/import
> (secrets excluded), .xlii/hooks/ (5 events), /describe, xlii doctor,
> prompts as per-project-overridable files, registry/pool/transcript/gc
> hardening (gc had been misclassifying every modern .xlii/ project as
> deletable). Historical “next” below is superseded by the links above.
>
> **Status (2026-06-11):** Phases 0–4 are DONE (commits 7ea36e9 → 478a833).
> Deferred from those phases, in priority order:
> - ~~**plugin_call as a real tool** (4.2: HTTP manifest actions)~~ — DONE 2026-06-20
>   (``plugin_call`` tool; exec actions still deferred)
> - ~~**cli.py file extraction** (3.2)~~ — DONE 2026-06-13 (R1–R4; cli.py 3756 →
>   35 lines, handlers → repl_cmds/, cmd_* → cmds/, Console → ui.py).
> - **rotation of the exposed Collection/conversation_id** (0.1: account
>   action — the old ids remain in git history until rotated)
> - end-to-end vault auth verification against live free-tier APIs (4.2 gate)
> Next up *(historical)*: Phase 5 storage/export/doctor — **done**; see top banner.

**Goal:** make xlii the "Emacs for bash" it's reaching for — a substrate that rewards
investment, where every advertised surface actually works, state is never silently lost,
and the user's curation (personas, docs, plugins, marks) is durable and portable.

**How to use this document with an agent:** work one phase at a time, in order. Each
phase has an **exit gate** — a concrete, checkable condition. Do not start phase N+1
until phase N's gate passes. Within a phase, tasks are ordered by dependency. Every
task cites the file and (where known) line of the verified finding it fixes; line
numbers may have drifted — the description is authoritative. After every task: run
`pytest`, then launch the affected REPL and exercise the change by hand (or via the
smoke harness once Phase 2 lands).

**Operating principles (apply to every phase):**

1. **Exercised surface only.** Nothing ships in the package that isn't reachable from
   `xlii` CLI or the REPLs. Dead scaffolding moves to `proposals/` or gets deleted.
2. **One owner per piece of state.** REPLState owns session persistence and surface
   flags (workspaces/loadout slots, yolo, howto_mode); SessionState on Agent owns
   attachment payloads (refs, docs, locker files); Agent reads both. No bidirectional sync.
3. **Atomic writes everywhere.** Any file the system reads back (config, manifest,
   registry, transcripts, OMEMO state) is written temp-file + `os.replace`, and loads
   are wrapped with corrupt-file recovery (warn + sensible default, never a raw traceback).
4. **The model is not a security boundary.** Anything gated on model-declared intent
   needs a code-level check too.
5. **Docs are generated from or verified against the code**, never hand-maintained
   in parallel.

---

## Phase 0 — Stop the bleeding (hours, not days)

The daily loop is broken in five independent ways and private state is in git.
Nothing else matters until a session survives start → work → exit.

### 0.1 Un-commit private runtime state
- Add `.xlii/` and `.xli/` to `.gitignore`; `git rm -r --cached .xlii`.
- Rotate: recreate the exposed Collection and conversation_id (project.json carried
  the author's real `collection_id` and the prompt-cache `conversation_id`; repl_history
  carried real user inputs).
- Add a guard in `ProjectConfig.load`: if project.json's recorded root path doesn't
  match the current checkout, warn and refuse (or offer re-init) instead of silently
  binding a fresh clone to someone else's Collection.

### 0.2 Fix the five session-killing bugs (all small)
- `xlii chat` crashes after first turn: undefined `user_input` in `_chat_post_turn`
  (xlii/cli.py ~2841). Also confirm persona memory actually persists after the fix.
- Ctrl-C/Ctrl-D crashes both REPLs: undefined `on_exit` in `run_repl_loop`
  (xlii/repl.py ~532). `/exit` must exit; Ctrl-D must exit cleanly; Ctrl-C at the
  prompt must clear the line, not the process.
- `/doc` attach, bare `/ref`, bare `/doc`, chat `/help`: undefined globals
  (`agent`, `CHAT_SLASH_HELP`) in handlers (xlii/cli.py ~418).
- `sync_from_agent` after every command reverts state mutations made by handlers
  (`/ref`, `/workspace load`, `/clear-attachments`) (xlii/repl.py ~550). Minimal fix
  now (sync direction-aware); real fix is Phase 3 single-ownership.
- Unguarded startup sync: offline / expired key / deleted collection = raw gRPC
  traceback (xlii/cli.py ~2874). Catch, print one friendly line, enter the REPL in
  degraded (no-sync) mode.

### 0.3 Remove the exception mask
- The blanket `except` in command dispatch that hid all of the above must re-raise
  unexpected exceptions (or print the traceback prominently) — silent failure is how
  this state was reached.

**Exit gate 0:** by hand: `xlii code` and `xlii chat` each survive: start (with and
without network) → one real turn → `/ref`, `/doc`, `/help`, `/status` → Ctrl-C at
prompt → `/exit` and Ctrl-D. Zero tracebacks. `git ls-files | grep .xlii` is empty.

---

## Phase 1 — Trust: secrets, sync safety, shell gate (1–2 days)

A substrate you live in must never exfiltrate or destroy. These are the verified holes.

### 1.1 Upload hygiene (xlii/ignore.py)
- Fix the name mismatch: code reads `.xliignore`, docs say `.xliiignore` (~line 63).
  Read both, prefer `.xliiignore`, warn on the legacy name.
- Extend default ignores: `.env*`, `*.pem`, `*.key`, `id_rsa*`, `id_ed25519*`,
  `.netrc`, `.npmrc`, `.aws/`, `.ssh/`, plus tool-local config (`.claude/`, `.vscode/`,
  `.idea/`, `.direnv/`, `.xlii/`, `.xli/`). Honor **nested** .gitignore files
  (pathspec supports this).
- Add `xlii sync --dry-run`: list exactly what would upload/update/delete, nothing else.
  (The committed manifest proved `.claude/settings.local.json` was uploaded — this
  command makes that class of surprise visible before it happens.)

### 1.2 Sync must never convert local confusion into remote deletion (xlii/sync.py ~273)
- A local scan error or an ignore-filter change currently looks like "file gone" →
  remote delete. Require: deletions only for files positively observed missing, and
  any turn deleting more than N docs (or any docs after a filter change) prompts
  with the list first.

### 1.3 Shell gate becomes a code-level check (xlii/tools.py ~85, ~240)
- Keep the model-declared `intent`, but verify it: a small command classifier
  (deny-by-default list of mutating binaries/flags, redirections, `sudo`, package
  managers, `rm`/`mv` outside project root). Mismatch between classification and
  declared intent → escalate to the approval prompt.
- Workers: "read-only" must be enforced — run worker bash through the classifier with
  the write class hard-blocked, not just un-prompted.
- `--yolo` stays as the explicit human override; it should bypass prompting, not
  classification logging.

### 1.4 Credential hygiene (xlii/config.py ~160, xlii/vault.py)
- `GlobalConfig.save()`: chmod 0o600 like its bootstrap siblings, and **strip the
  management key** before persisting — it is documented as env-only; make the code
  enforce its own invariant.
- Vault: either wire `env_for_command()` into `t_bash` env injection + add
  `xlii auth set/list/clear` (the stock plugins document this workflow), or remove
  the vault from the package until Phase 4. Recommended: wire it — it's the
  load-bearing piece for credentialed plugins and the code already exists.

**Exit gate 1:** a test project containing `.env`, `key.pem`, and a nested-gitignored
file syncs zero of them (verified via `--dry-run` and the manifest); a worker asked to
`rm` a file is blocked by code, not by politeness; config.json never contains the
management key and is 0600.

---

## Phase 2 — The ratchet: CI smoke gate + tests (1–2 days, pays forever)

Every Phase-0 bug would have been caught by a 30-second smoke job. Build the ratchet
before building anything new.

### 2.1 CI (.github/workflows/)
- Add a `test` workflow: `ruff check` + `pytest` on push/PR. (Lint already catches a
  real bug class here: `_PROJECT_TOOLS` and `register_agent_tool` are each defined
  twice in tools.py.)
- Add a **smoke job**: launch `xlii code` and `xlii chat` with a fake key and network
  stubbed/disabled, pipe in `/help`, `/status`, `/ref`, `/doc`, then EOF; assert exit
  code 0 and no `Traceback` in output. This is the single highest-leverage artifact
  in this roadmap — it makes Phase 0 regressions impossible.
- pyproject: add `[project.optional-dependencies] dev = ["pytest", "ruff"]` and
  pytest config.

### 2.2 Testability seams (small refactors, no behavior change)
- Replace import-time path constants in config.py (~line 11) with functions honoring
  an env override (`XLII_CONFIG_DIR`) so tests never touch `~/.config/xlii`.
- Make sync's retry sleeps injectable (pass a sleeper) so the 429 path is testable
  without 31s of real time.
- Give the bash approval prompt an injectable confirm function instead of raw `input()`.

### 2.3 Priority test targets (in order of blast radius)
1. `GlobalConfig.save()` round-trip: management key never persisted, perms 0600.
2. `Agent.run_turn` with a faked client: tool loop, malformed tool JSON, max-iteration
   exit, orphaned-tool-call repair (see 3.4).
3. Sync planner: SHA-skip path, and **the deletion guard from 1.2**.
4. tools.py guards: path-escape check, intent/classifier gate.
5. ignore.py: the full default set + nested gitignore + `.xliiignore`.

**Exit gate 2:** CI is red if any of: tests fail, ruff fails, either REPL can't
boot-and-exit cleanly. The duplicate definitions in tools.py are gone.

---

## Phase 3 — Coherence: one state owner, one module map (2–4 days)

This phase removes the structural cause of the recurring bug class and makes the
package match the product.

### 3.1 Single state ownership (the root fix)
- REPLState becomes the sole owner of: plan_mode, rail state, yolo/safe, temperature,
  attachments (refs/docs), workspace. Agent receives a reference and reads it; the
  per-turn bidirectional `sync_to_agent`/`sync_from_agent` is deleted.
- This subsumes the documented rewrite-handler bug class permanently. Add a regression
  test: every state-mutating slash command, asserted against REPLState after dispatch.

### 3.2 Finish the cli.py extraction — DONE (2026-06-13)
> Delivered via phases R1–R4 in `proposals/done/cli-refactor.md` (full move map + exit
> gates there). cli.py went 3,756 → 35 lines (build parser → each command module
> registers itself → dispatch). REPL slash handlers → `xlii/repl_cmds/`; `cmd_*`
> bodies → `xlii/cmds/`; Console singleton → `xlii/ui.py`. Within `xlii/`, only
> `__main__.py` imports cli (layering inversion gone). Registration is fully
> explicit (`register_all()`), and /help is now generated from the registry — no
> hand-maintained SLASH_HELP/CHAT_SLASH_HELP to drift (ROADMAP principle 5).
- cli.py (3,142 lines) keeps only: argparse wiring + thin `cmd_*` entry points.
- REPL command handlers move to commands.py; the Console singleton moves to a small
  `xlii/ui.py` that nothing imports cli.py for. Lower layers must not import cli.py
  (current layering inversion).
- Fix dispatch scoping in commands.py (~117): per-REPL command registries — chat's
  `/status` must not be shadowed by code's; code-only commands must not run in chat.
  Duplicate registration of a name in the same scope = hard error at import.

### 3.3 Cull or wire the orphans (~1,800 unreferenced lines)
Decide per module, then act — nothing stays in limbo:
- **Wire now (small, high value):** `install_stock_plugins()` → `xlii plugin
  --install-stock`; persona memory attach path (`.xlii/`, legacy `.xli/` drained);
  `/persona` mid-session switch
  (repl.py ~545 — implement the restart loop or remove the command from help).
- **Move to proposals/ (not ready):** daemon.py (XMPP/OMEMO — see Phase 6),
  doc_sources.py, reference_registry.py + points.py (contradict the live /doc design),
  the duplicate workspaces implementation, secondary_ai.py.
- Replace every `from xli.…` import in xlii/ with `from xlii.…`; the shim then exists
  only for external callers and can carry a deprecation note.

### 3.4 Agent-loop correctness (verified mediums)
- Tool batch ordering (agent.py ~788): execute same-batch tools in order when any
  earlier one mutates (or split batches at the first mutator). Reads must not run
  before earlier writes.
- Ctrl-C mid-turn (repl.py ~557): catch KeyboardInterrupt in the turn loop, repair
  history (drop or close the orphaned assistant tool_calls entry), return to prompt.
- Plan-mode preamble (agent.py ~523): inject as ephemeral system content per call,
  not baked permanently into history where it contradicts later execution.

### 3.5 Godzilla-file refactor — mode-controller unification + file splits (PROPOSED)
> Tracked in `proposals/godzilla-refactor.md` (cli-refactor.md house style: two tracks,
> one phase = one commit, exit gate each phase). Continues 3.2 (the next tier of 1000+
> line files) and applies 3.1's state-ownership fix to modes. **Track A** collapses the
> `rail→debug→plan→default` ladder — maintained in five places — into one `ModeController`
> protocol + a single `Agent.active_mode` slot, deleting the four duplicated
> mutual-exclusion clear-blocks (the `/execute`→plan sync-bug class). **Track B** splits
> tools.py / agent.py / repl.py / commands.py behind re-export façades. Sourced from a
> 4-agent deep audit (2026-06-22).

**Exit gate 3:** `grep -rn "from xli\." xlii/` is empty; `sync_from_agent` no longer
exists; every module in the wheel is imported by something reachable from `xlii`;
the state-mutation regression test passes; smoke job still green.

---

## Phase 4 — Make the advertised substrate real (3–5 days)

Everything the system prompt and docs promise, working. This is where it starts
feeling primo instead of aspirational.

### 4.1 Restore the tool surface (all verified breaks)
- `read_file` schema back in `tool_schemas()` (tools.py ~610) — the model currently
  cannot read files; fatal in plan/rail modes.
- Project tools dispatch: the executor must consult `_PROJECT_TOOLS` (or merge into
  REGISTRY at load) so user-defined `.xlii/tools/` actually execute (tools.py ~538).
  This is the substrate's whole thesis — user-composed capability — currently dead.
- End-of-turn sync: consume `dirty_paths` + `__rescan__` in both REPLs (cli.py ~2904)
  so the "files auto-mirror" claim becomes true. Respect `--no-sync`.
- glob/grep: filter through ignore.py and skip `.git`/venv (tools.py ~188) — stops
  burning the 30k truncation budget on noise.
- bash timeout: kill the process group, return partial output with a timeout marker
  (tools.py ~270).

### 4.2 Plugin layer: from prose to enforced manifests
- The manifest layer (plugin_manifest.py — effect/trust, host pinning, param
  validation) is parsed but enforced nowhere. Implement `plugin_call` as a real tool:
  manifest → validated params → direct HTTP — with the curl-via-bash path demoted to
  fallback for manifest-less plugins. (This is also what an early external review
  believed already existed; make it true.)
- `plugin_get` returns full raw markdown including unusable output_schema YAML
  (tools.py ~404) — return the relevant sections only.
- `xlii plugin --lint`: validate frontmatter, id-vs-filename, manifest schema; run it
  over stock_plugins in CI. Malformed plugins currently degrade silently with
  fail-open risk defaults (plugin.py ~133) — make them loud.
- Vault-backed auth from 1.4 + stock plugins: every stock plugin's documented auth
  flow must work end-to-end against at least the free-tier APIs.

### 4.3 Cost honesty
- Pass `cached_tokens` through to `estimate_cost` (agent.py ~209) — costs are
  currently overstated.
- Server tools: never collapse unknown cost to $0 (server_tools.py ~31) — display
  "unknown"; don't silently substitute the hardcoded fallback model without a notice.
- Raise pyproject's openai floor to `>=1.50` (the README's own troubleshooting
  documents the failure at 1.40).

**Exit gate 4:** in a live session: model reads a file in plan mode; a custom
`.xlii/tools/` tool executes; an edit syncs to the Collection at end of turn and
`search_project` sees it; a credentialed stock plugin completes a `/get` round trip
with its key injected from the vault; `/cost` matches the dashboard within noise.

---

## Phase 5 — The Emacs layer: what makes it a substrate, not an app (1–2 weeks)

With the floor solid, build the things that make investment compound. Prioritized
by leverage:

### 5.1 Storage backend seam + export (the strategic de-risk)
- Define a thin `StorageBackend` protocol (upload/delete/search/list) with two
  implementations: the existing Collections backend, and a local filesystem +
  ripgrep/simple-index backend.
- The local backend is simultaneously: the offline mode (REPL degrades instead of
  dying), the test double, and the escape hatch.
- `xlii export` / `xlii import`: every persona memory, doc, plugin, mark, and project
  manifest serializes to a plain directory tree. **User curation must never be
  trapped in a beta vendor API.** This is the single most Emacs-like commitment the
  project can make: your config and accumulated state are files you own.

### 5.2 Hooks and introspection (Emacs DNA)
- A hook system: `.xlii/hooks/` with named events (pre-turn, post-turn, pre-sync,
  post-tool, on-plan-approved) running user scripts with a small JSON contract.
  This converts "feature requests" into "things users wire themselves" — the
  substrate thesis.
- `/describe <command|tool|plugin>`: self-documentation from the registries —
  the equivalent of `C-h f`. The registries already exist; this is mostly formatting.
- `xlii doctor`: one command that checks config perms, key expiry, collection
  reachability, ignore-file presence, orphaned manifest entries, and prints fixes.
  (Replaces "read the source" as the failure-mode answer.)

### 5.3 Discovery upgrade (standing recommendation from the early external review)
- Plugin `/get` routing is keyword overlap (plugin.py "L1"). Add the L2: embed or
  LLM-rerank over manifest action summaries. Keep L1 as the no-network fallback.

### 5.4 Prompt hygiene
- Extract MAIN_SYSTEM_PROMPT / WORKER_SYSTEM_PROMPT / plan + rail preambles into
  `xlii/prompts/*.md`, loaded at import — diffable, reviewable, and overridable by
  the user (`.xlii/prompts/` shadows package prompts — another Emacs move).

### 5.5 Session ergonomics
- Transcript round-trip safety (transcript.py ~198): unparseable/colliding turn files
  must surface, not vanish from persona memory.
- Registry hygiene (registry.py ~39): corrupt-load recovery, atomic save, and `gc`
  must cross-check collection_id against the candidate's project.json before offering
  deletion (a moved project must not get its live Collection deleted).
- Pool (pool.py ~38): exclude the primary key from worker rotation; quarantine keys
  on repeated auth failure.

**Exit gate 5:** pull the network cable: `xlii code` opens, search works against the
local backend, and a banner says degraded mode. `xlii export` then `xlii import` on a
fresh machine reproduces personas, docs, plugins, and marks. A user-written post-turn
hook fires.

---

## Phase 6 — Docs that tell the truth, then the fabric (ongoing)

### 6.1 Documentation reset — DONE (2026-06-13)
> Delivered via the documentation-reset phases D1–D5 (see git history). README is now the
> single doc surface: dead `ref-system.md` links and the `xli` binary name gone,
> License → MIT, flagship features (rail, /context + MCP bridge, /attachments,
> /workspace, durable /ref //doc) documented, File-layout regenerated. Command
> and slash tables are generated from the code (`xlii.docgen`); USAGE.md retired.
> A CI doc-truth check (`scripts/check_docs.py`) fails the build if any documented
> command stops existing or a generated region drifts. Remaining in Phase 6: 6.2
> (XMPP/OMEMO fabric).
- README: fix install (`venv/bin/xlii`, not `xli`); document the rail, `/context`,
  `/attachments`, and the MCP deep-contexts bridge (the actual flagship features —
  currently documented nowhere); fix the /ref//doc durability claim (they ARE durable
  via `.xlii/session.json`); remove the four dangling `proposals/ref-system.md`
  references; regenerate the File layout section; License = MIT.
- USAGE.md (described a different branch — ~half its command surface didn't exist)
  was **retired** (D4): its only real content was already in README, so it was
  deleted rather than rewritten. README is now the single doc surface; its command/
  slash tables are generated from the argparse tree + registries (D2), and a CI
  check that every documented command exists is D5.
- CRITIQUE.md (the early external review) was retired in the later docs cleanup.

### 6.2 The XMPP/OMEMO fabric — only now
> Scoped in detail (phases F1–F5, security-forward exit gates, verified defect
> list) in `proposals/xmpp-fabric.md`. Decisions locked: `[daemon]` extra; new
> `xlii ask` one-shot subcommand for the agent fallback (the daemon currently
> calls a phantom `xli ask`); Phase-1 notify rail over XMPP `xmpp_send`. F1
> (send-only, no inbound RCE) ships first; the command daemon ships only once its
> device-trust + whitelist boundary is correct (F4).
daemon.py is currently unreachable (no subcommand), its deps undeclared, it dies
permanently on transient disconnect with exit 0, blind-trusts OMEMO devices for an
arbitrary-code-execution channel, and rewrites crypto state non-atomically. When the
fabric becomes the priority: declare a `daemon` extra, add the subcommand, reconnect
loop, device-trust pinning, atomic OMEMO storage — and a smoke test. Until then it
lives in proposals/, per the Phase 3 rule.

**Exit gate 6:** a stranger with an xAI account goes from `git clone` to a working
`/get weather in berlin` using only README.md, with zero contradictions encountered.

---

## Phase 7 — Autonomous loop (DONE 2026-06-22)

The macro-loop over full turns — `build → test → judge → fix` — with persisted state
(`/loop resume`), tiered oracles (free shell tests gate before any LLM spend),
user-selectable named judge profiles (same-vendor, cross-vendor Anthropic/OpenAI/alt-xAI,
external Cursor), a builder/judges cost split, and honesty guards (cold `VerdictBundle`,
pinned judges, test-lock, weakened-tests alarm). Plus the goal inbox
(`xlii loop --drain-inbox`) for unattended batch runs. Shipped across `xlii/loop*.py` +
`/loop` + `xlii loop`; 64 loop tests green. Plan + retro: `proposals/done/autonomous-loop.md`.

## Sequencing summary

| Phase | Theme | Size | Unblocks |
|-------|-------|------|----------|
| 0 | Session survives; private state out of git | hours | everything |
| 1 | Never exfiltrate, never destroy | 1–2 d | trusting it with real projects |
| 2 | CI ratchet: smoke + tests + lint | 1–2 d | landing changes safely |
| 3 | One state owner; package = product | 2–4 d | velocity without regressions |
| 4 | Advertised surface actually works | 3–5 d | daily-driver status |
| 5 | Hooks, export, local backend, discovery | 1–2 wk | "Emacs for bash" identity |
| 6 | Truthful docs; fabric when ready | ongoing | other users |
| 7 | Autonomous loop: walk away to green, selectable judges | **DONE** | unattended, verified delivery |

The full verified findings behind every task (68 confirmed findings with evidence and
verifier notes, May 2026 multi-agent review) were produced from this tree; line numbers
in this document came from that review and may drift — trust the descriptions.
