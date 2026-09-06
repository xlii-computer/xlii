---
sources: file://xlii/wiki.py, file://xlii/wiki_author.py, file://xlii/repl_cmds/wiki.py, file://xlii/panes/wiki.py, file://proposals/howto-wiki.md
verified: false
---
# xliiwiki

The wiki is xlii's **semantic memory** — the third tier of the memory hierarchy
(`conv://` episodic · `xlii://` working · `wiki://` semantic). Pages are plain
markdown files under a project's `.xlii/wiki/`: distilled, linked, durable knowledge
you'd want to read months from now, not a raw log. `xlii/wiki.py` is the store;
addressing is the `WikiProvider` surface (see [[addressing-and-vfs]]) and browse is
the [[panes-and-dock]] `WikiPane`.

## Two anti-confabulation guards

Both design guards are **data on the page**, so every consumer (journal, retrieval,
the skeptical editor) shares one source of truth:

- **Provenance** — every page carries `sources:`, a list of *addresses*
  (`file://…#L4-10`, `conv://…`, `job://…`) its claims came from. The `#anchor`
  grammar is the precise-provenance unit.
- **Verify-before-trust** — a page is born `verified: false` and is only **promoted**
  (`mark_verified`) after a skeptical pass checks it against its sources. Write →
  refute → *then* promote. Unverified pages still browse and attach, but wear the
  flag loudly. See [[trust-and-gates]].

## Front matter & storage

Front matter is a hand-rolled `key: value` block (no YAML dependency) with just two
fields, `sources:` and `verified:`. `render_page` always writes the block so trust
state is never implicit; `parse_page` only consumes a leading `---…---` block that is
*genuinely* front matter, so a body that opens with a `---` thematic break is not
silently eaten. A file with no front matter is still a valid page (unverified, no
sources) — the wiki never rejects plain markdown. Writes are atomic. A **rewrite
resets `verified`** unless the caller re-asserts it: changed claims need a fresh pass.
Page names are `[A-Za-z0-9._-]+`.

## The lifecycle: write → refute → promote

Two model-powered steps live in `xlii/wiki_author.py`, both pure functions over an
injected `complete(messages) -> str` callable (unit-testable without a live model):

- **`distill`** — turn a set of source *addresses* into a page, born unverified, with
  `sources:` set to exactly the addresses that resolved through the VFS. The
  episode→semantic compression the wiki exists to do. Unreadable sources are kept as
  an explicit error line, not silently dropped.
- **`verify`** — the promote gate. Re-read the page's own `sources:` and ask a
  skeptical, adversarial editor whether *every* substantive claim is supported. Only a
  clean `VERIFIED` first line promotes. A page with **no recorded sources is never
  auto-verified** — trusting unprovenanced text is the confabulation the guard stops.
- **`propose_pages`** — the journalist's autobuild: from recent activity + the list of
  pages that already exist, propose durable, page-worthy topics (often NONE). Written
  **create-only**, never clobbering — the operator promotes ([[journal-and-receipts]]).

## Verbs (`/wiki`)

`list · show · new · edit · rm`, plus `distill <name> <addr>… [-- intent]` and
`verify <name> [--promote]`. `--promote` trusts a page manually, without the AI check
(**propose-you-promote**: the owner is the promoter). Editing a verified page whose
body changed **resets the flag** and prompts a re-verify. The scope is always the
ambient session's `.xlii/wiki/`. See [[command-surface]].

## Browse & retrieval

The `WikiPane` renders one row per page: a trust marker (`✓` verified · `?`
unverified) then the name, with a green accent dot on any page currently riding the
turn (attached via the `wiki:` /doc channel). View morphs the pane to the page's
markdown; attach/detach ride or unride it — unverified pages attach wearing their
warning. `search_pages` is BM25 over the corpus via an **ephemeral in-memory FTS5
index rebuilt every call**, so a page is searchable the instant it's written; verified
and unverified pages both match and the caller tags trust.

## Sections & addressing

`wiki://page#section` is the anchor unit. `heading_slug` turns a heading into its slug
(`## The event seam` → `the-event-seam`); `extract_section` returns the span from that
heading to the next heading of equal-or-higher level. This is what lets a `sources:`
address point at one section, not a whole file.

## The two tiers: your wiki and xlii's

The wiki has two scopes that never merge. **`wiki://`** is this project's own tier —
editable, trust-laddered (✓/?), auto-distillable by the journal, browsed with
`/wiki` and folded into `/mojo` answers. **`xwiki://`** is the **vendor tier**: xlii's shipped self-docs
(these very pages), bundled with the package, read-only, version-locked to the
installed build, identical in every project, asked through `/howto wiki` —
**retrieval, not standing attachment** (only the sections a question needs ride
the turn). Vendor rows wear `⌂` instead of the trust ladder — shipped truth is
version-accurate by construction. Both scopes are the same `WikiProvider` over a
different root; the same pane, search, and `#anchor` grammar serve both. See
[[help-and-howto]]; the wiki loop as its own first customer ties into
[[kernel-architecture]].
