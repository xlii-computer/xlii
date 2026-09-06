---
sources: file://xlii/repl_cmds/__init__.py, file://docs/GUIDE.md#L122-152, file://docs/GUIDE.md#L269-604, file://scripts/check_docs.py, file://xlii/docgen.py, file://xlii/commands.py#L100-220
verified: false
---
# command-surface

How a line of input becomes an action, and how the command catalog stays honest against the code. See [[help-and-howto]] for the discovery surfaces.

## The input model

One input line, four routes, decided by the first character:

- **bare line** → a live shell command in the tracked cwd (`cd` moves it). This is *shell-primary*, the default for the `code` runner (scratch rides it too).
- **`?<text>`** → an agent turn (the model).
- **`!<cmd>`** → force a shell command at the project root, ungated.
- **`/<cmd>`** → a slash command.

Shell-primary is a `code`-surface property, not a universal one. In the `chat` REPL a bare line talks to the persona and `?` is merely an alias for bare input; `!<cmd>` still runs a local shell command. Toggle shell-primary off with `XLII_SHELL_PRIMARY=0` (docs/GUIDE.md#L133). The TUI is a face over this same kernel input path — it reimplements the bare/shell/`?`/`!`/`/` routing rather than inventing its own (see [[panes-and-dock]], [[status-and-chrome]]).

## The slash registry

Every slash command is a declarative `REPLCommand` (xlii/commands.py): `name`, `handler`, `aliases`, `description`, `usage`, `category`, `repls` (subset of `{code, chat}`), `source` (`builtin`/`project`/`plugin`), `role` (namespace axis), and `capability` (the gate — see [[trust-and-gates]]). Resolution in `find_repl_command` is scoped on two axes: the **surface** (`repls`) and the **role namespace** — a global command is addressed bare (`/plan`), a role command by prefix (`/architect:review-design`) and only while that role is active.

Registration is a **hard error on duplicate names** within an overlapping REPL+role scope (xlii/commands.py#L100-120) — silent shadowing is banned (it once ate chat's `/status`). The one exception: a `project`-sourced command may override a `builtin`.

## The command modules

`xlii/repl_cmds/__init__.py` holds `_MODULES`, an **append-only, one-statement-per-feature** tuple. Each feature adds its module on its own `from … import` line and its own `_MODULES = _MODULES + (mod,)` statement, with a `noqa: E402` comment — this exists purely for merge-collision avoidance so parallel fleet vectors don't stomp each other in one edit. `register_all()` walks the tuple once and is idempotent (a module-level `_registered` guard makes a second call a no-op).

Ordering in `_MODULES` is cosmetic (the registry is keyed by name) but kept stable for predictable help walks. `apropos` is no longer in `_MODULES`: `/apropos`·`/search-help`·`/manuals` were absorbed as hidden aliases of `/help`; `apropos.py` is now the implementation library those flags dispatch into.

## Categories

`/help` groups commands by `category`: **SESSION**, **MODE**, **KNOWLEDGE**, **ADMIN**, **GENERAL**, **PROJECT**, **SHELL** (docs/GUIDE.md#L269-604). The `code` and `chat` REPLs carry different sets, so `/help` shows only what applies where you are.

## The two helps

Two distinct surfaces for two moments (docs/GUIDE.md#L138-152):

- **`xlii help`** — from the shell, *outside* a session. A grouped tour of the CLI subcommands (PROJECT · CHAT · KNOWLEDGE · SETUP · MAINTENANCE): "what can I run, how do I drive the tool?" `xlii <cmd> --help` gives one command's flags.
- **`/help`** — *inside* a running `code`/`chat` REPL. The slash commands for that REPL, pulled live from the registry: "what can I do in this session?"

`/describe <name>` (alias `/man`) is the deep single-item lookup — describe one slash command, agent tool, or plugin from the live registries.

## docgen: single source of truth

`xlii/docgen.py` generates the reference tables from live code — the argparse tree (`xlii.cli.build_parser`) and the slash registry (`get_repl_help`) — into delimited `<!-- BEGIN/END GENERATED -->` regions. Four regions: `subcommands`, `slash-code`, `slash-chat` in docs/GUIDE.md, and `cli-reference` (the flag-level deep reference) in docs/REFERENCE.md. Hand-maintained tables drift; these don't. Run `python -m xlii.docgen` to rewrite, `--check` to fail on staleness. Argparse aliases that map to the same subparser (e.g. `remote`/hidden `ftp`) are emitted once under the canonical name.

## check_docs: the doc-truth ratchet

`scripts/check_docs.py` is the CI guard that keeps docs from drifting from code. Its guarantees:

1. **Fresh** — every generated region is up to date (`docgen.check()`).
2. **No fake commands** — every inline-code `` `xlii <cmd>` `` and `` `/<slash>` `` in README/GUIDE/HOWTO (and the help corpus) resolves to a real subcommand or registered slash command. Only single-backtick inline spans are authoritative; prose and fenced output are illustrative.
3. **Coverage** — every top-level subcommand and every registered slash command must be *named* in at least one hand-written doc (README/GUIDE/HOWTO/REFERENCE) or help page; generated regions are stripped before the scan. The grandfather allowlists (`COVERAGE_ALLOWLIST_SUBS`/`_SLASH`) are empty — the ratchet ships fully hard.
4. Help-bundle and help-corpus checks (xlii/help vs docs/help; see [[help-and-howto]]).

## Completions and the slash popup

Two separate mechanisms that never mix. Fish-style completion for bare *shell* input has an inline suggester (xlii/tui/input_chrome.py); the TUI's typed input completions are owned on the input surface (xlii/tui/input_surface.py), distinct from the app's `/` slash menu (the popup that lists slash commands as you type `/`).

## Related

Command families each have their own page: [[tasks-and-pipes]] (`/tasks`, `/alias`, `/jobs`, `/project startup`), [[gitpain]] (`/gitpain`, deprecated `/git`), [[gigwork-and-foreign-brains]] (`/gigwork`, `/jam`, `/consult`), [[plan-surface]] (`/plan`, `/rail`, `/execute`), [[addressing-and-vfs]] (`xlii ls/cat/cp`, `/remote`), [[journal-and-receipts]] (`/journal`, `/mojo`), [[xtool]] (`/xtool`), [[xliiwiki]] (`/wiki`).
