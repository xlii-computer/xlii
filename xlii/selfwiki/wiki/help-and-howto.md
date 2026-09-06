---
sources: file://docs/help/manifest.yaml, file://docs/help/topics/help-system.md, file://scripts/bundle_help.py, file://scripts/check_docs.py#L104-223, file://xlii/help_corpus.py#L1-16, file://xlii/help_corpus.py#L146, file://xlii/help_corpus.py#L233-272, file://xlii/repl_cmds/apropos.py, file://xlii/repl_cmds/meta.py#L22-60, file://xlii/repl_cmds/meta.py#L250-262, file://xlii/repl_cmds/howto.py#L206-503, file://proposals/howto-wiki.md
verified: false
---
# help-and-howto

xlii documents itself through **one help corpus** consumed by three read surfaces
(`/help`, `/describe`, `/howto`), fronted by a discovery keystone (`/apropos`), and
kept honest by a CI ratchet (`check_docs.py`). See [[command-surface]] for the verbs.

## The corpus: source → tiers

`docs/help/` is the **source of truth**: `manifest.yaml` + topic shards
(`topics/*.md`) + per-command expansive docs (`commands/*.md`). The manifest routes
everything (id, title, path, tier, aliases, tags, see_also).

- **Core tier** — bundled into `xlii/help/` by `scripts/bundle_help.py` so offline
  `/howto` works with no network. **Never hand-edit the bundle**; edit `docs/help/`
  and re-run the script. Eight core topics: index, install, first-session,
  troubleshoot, terminal-selection, input-completions, help-system, workflows.
- **Extended tier** (most of the corpus) — GitHub-sourced from
  `raw.githubusercontent.com/{repo}/{ref}/{base_path}/…`, **etag-cached** at
  `~/.cache/xlii/help/` so it is always current yet fast and offline after first fetch.

Topic load order is `help_corpus.py#L261`: **project override** (a project's own
`.xlii/help/topics/<topic>.md`) → bundled (core) → `docs/help` (dev tree) →
cache/GitHub (extended). `XLII_HELP_REF` pins the fetch ref (default = manifest's
`ref: main`; repo `n3r4-life/iXaac-lab`).

## Three surfaces

- **`/help`** — the menu: every slash command in the current REPL, grouped, generated
  live from the registry (no hand-written strings to drift). It is also the *one door*
  for discovery: `--search <keyword>` (the old `/apropos`) and `--topics` (the old
  `/manuals`). Those names — plus `/search-help` — survive as **hidden aliases** that
  dispatch back into the search/index handlers (`meta.py#L250`).
- **`/describe <name>`** (alias `/man`) — the introspection deep-dive, the `C-h f` of
  the tool. Prints **live registry facts from this exact build** (category, aliases,
  REPLs, usage, flags; tool params; plugin actions/risk/auth) then appends the
  **expansive GitHub prose** (`commands/<cmd>.md`, etag-cached), a **see-also graph**,
  and a pointer to the covering `/howto` topic. Facts-from-code + prose-from-GitHub =
  **never stale**.
- **`/howto [topic]`** — enters a conversational mode and attaches a task guide to the
  system prompt (ask in plain language, no `?`). Bare `/howto` = operator guide + topic
  index + your live command list; `/howto <topic>` = a focused shard. Sub-verbs:
  `wiki [question]` (ask the shipped self-wiki — see the two-doors section below),
  `latest [topic]` (force a fresh GitHub pull — bare `/howto latest` warms the whole
  corpus via one tarball `sync_corpus_cache`), `off`, `project [list|new|edit|rm]`
  (author per-project overrides), and `fix`.

## `/apropos` — the discovery keystone

Keyword search across **live slash commands + help topics** (`help_corpus.search_corpus`)
— the entry point when you don't yet know the command's name. Renders hits, then routes
onward: `/describe <name>` for detail, `/howto <topic>` to load a guide. It is a pure
read surface (prints, attaches nothing) and now rides `/help --search`.

## `/howto fix` and the self-repair capstone

`/howto fix [symptom]` runs `xlii doctor`, then — **discrepancy with the older brief** —
it is no longer diagnose-only: `_offer_apply_fixes` will **run whitelisted doctor fixes**
(`apply_doctor_fix`) when the session is elevated (`/admin unlock`; see
[[trust-and-gates]]), with per-fix confirmation; read-only otherwise. It then searches
the corpus for the symptom, falling back to queueing the question for the AI. The
**broad self-repair "building" capstone** (diagnose → fix arbitrary → write the page
about it) remains **unbuilt**; only this narrow gated config/CLI apply-path exists.

## `check_docs.py` — the honesty ratchet

The CI gate that stops docs from drifting from code. Four
guarantees: (1) generated regions in `docs/GUIDE.md` are fresh (`xlii.docgen --check`);
(2) **no fake commands** — every inline-code `` `xlii <cmd>` ``/`` `/<slash>` `` (plus
fenced `xlii <sub>` lines) resolves to a real subcommand / registered slash, across
README/GUIDE/HOWTO **and the whole help corpus**; (3) the help bundle matches the core
tier (`bundle_help.check`); (4) **coverage** — every subcommand and slash command is
named in a hand-written doc or help page. The grandfather allowlist is **empty**, so
the coverage ratchet ships fully hard and can only stay so.

## The two doors and the two wikis

Scope is not mode: one door per **subject**, each with its own wiki tier, and the
tiers never merge.

- **`/howto` = xlii the tool.** The help corpus, plus `/howto wiki [question]` —
  retrieval from **`xwiki://`**, the shipped vendor tier (these very pages:
  read-only, version-locked, identical in every project). Citations print as
  `⌂ xwiki://page#section`, the ranked results open in a side panel
  (`xwiki://?q=…` mounts the [[panes-and-dock]] WikiPane in results mode), and
  just those sections ride the turn — **retrieval, not standing attachment**.
- **`/mojo` = this project.** iXaac answers with its own memory FUSED with the
  project's journal (episodic) plus the project's own **`wiki://`** (semantic,
  editable, ✓/? trust-laddered): `/mojo <question>` folds `.xlii/wiki/` sections
  and the journal into a one-shot answer, and you stay in the code surface. The
  journal's `--wiki-on` auto-distill writes the pages that get read back:
  record → distill → promote → read, one identity. (`/askjo` is a hidden alias.)

Remaining direction: regenerating the vendor pages from the live registry per
release (autobuild) and extending `check_docs` into a wiki drift ratchet. See
[[kernel-architecture]].
