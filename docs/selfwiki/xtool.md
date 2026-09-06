---
sources: file://xlii/xtool_catalog.py, file://xlii/repl_cmds/xtool.py, file://xlii/tui/tools_catalog.py, file://xlii/tui/app_menu_mixin.py#L652-661, file://xlii/tui/app_menu_mixin.py#L884-933, file://docs/GUIDE.md#L166
verified: false
---
# xtool

Project-sniffed lint/format catalog: a single in-code table of linter/formatter
recipes surfaced two ways — the `/xtool` REPL command and the TUI **Tools → Shell
tools…** menu. Both surfaces PREFILL a `!<argv>` shell line for review; neither
executes. Shipped as Track J (PR #300, overnight-slam vector). This is the
built-in-as-first-client pattern of the [[command-surface]] and [[panes-and-dock]]
input seams.

## The kernel table

`xlii/xtool_catalog.py` holds `CATALOG`, a tuple of frozen `XToolEntry` rows —
`id`, `label`, `argv_template`, `group`, `binary`, `fix_flag`, `legacy`.

- **Groups** (`GROUP_ORDER`): `python`, `javascript`, `rust`, `go`, `other`.
  Labels map the ids to display names — `javascript` renders as
  "JavaScript/TypeScript". (The brief's "js-ts" is the id `javascript`.)
- **which-gated**: `tool_available()` = `shutil.which(binary) is not None`. Every
  row is checked against PATH; missing rows are marked, never hidden.
- **Placeholders**: `argv_template` may carry `<path>` / `<dir>`. `_fill_placeholders`
  fills them from a target/Dock path — `<dir>` becomes the parent when the path is
  a file, the path itself when it is a directory. With no path, the literal
  placeholder is left in the seeded line.
- **`fix_flag`** marks destructive recipes (`--fix` / `--write` / `-w`):
  `ruff-check-fix`, `biome-check-write`, `biome-format`, `prettier`, `gofmt`,
  `goimports`, `phpcbf`.
- **`legacy=True`** is the More… tier: `flake8`, `pylint`, `black`, `isort`,
  `eslint`, `prettier`, `oxlint`.

## Fingerprint-first ordering

`ordered_groups(fingerprints)` puts the project's language group(s) first, then
appends the rest of `GROUP_ORDER` — other groups are always still listed, never
suppressed. `_FINGERPRINT_GROUP` maps detection keys to groups (`node` →
`javascript`; `php`/`ruby`/`java`/`cpp`/`dotnet` → `other`). Fingerprints come
from `xlii.project_fingerprint` (`load_project_profile` or
`detect_project_fingerprint`).

## `/xtool` (REPL)

`category="console"`, `repls=["code"]`.

- `/xtool ls` — grouped listing via `ls_lines`; each row `id: label (fix)
  [ok|missing]`. The CLI listing shows legacy tools **inline** (not folded under
  More…, unlike the menu).
- `/xtool <tool-id> [target] [--fix]` — `resolve_tool_id` maps a base id + `--fix`
  to its destructive variant (e.g. `ruff-check` → `ruff-check-fix`); `lookup`
  resolves by id, slug, or label. Unknown tools and missing binaries are refused.
- **Prefill only**: sets `state.pending_input = "!<argv>"` and prints a "seeded /
  review the line" hint — it never runs the command. A `target` arg fills `<path>`.

## TUI menu — Tools → Shell tools…

Anchor lives on the existing **Tools** bar as `("tools:shelltools", "  Shell
tools…")` — **D14: no new top-level bar**. The anchor was displaced during the
build and restored to the Tools bar in the conductor fix (commit `7e75509a`).

- `group_menu_items` renders one row per non-empty group, dimmed when no entry in
  it is available. Picking a group opens its tool rows; `group_has_legacy` appends
  a **More…** entry that opens the `legacy=True` rows.
- `_entry_row` sets `enabled = entry_available(entry)` and prefixes `(missing)` —
  absent binaries render dimmed.
- `prefill_line` returns `None` for missing/unknown (nothing seeded), else
  `!<argv>` routed through `_prefill_input`. `dock_path_from_address` parses a
  `file://` address (see [[addressing-and-vfs]]); the focused Dock file fills
  `<path>` via `_xtool_dock_path`.

## Invariants

One catalog, two surfaces, both prefill-only — the review-before-run boundary
that keeps destructive `--fix`/`--write` lines under the operator's eye rather
than auto-running (kin to [[trust-and-gates]]). Missing binaries are refused on
prefill on both paths. Shipped in the overnight-slam round (PR #300).
