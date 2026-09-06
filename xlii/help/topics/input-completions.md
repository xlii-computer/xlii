# Input completions: `:shortcode:` symbols & shell ghost text

The full-screen TUI (`xlii code --tui`, or `/tui` from the inline REPL) has two
keyboard-first completers built into the input box. Both are deliberately *dumb
at the keystroke* — they only ever scan a small in-memory table, so they never
pause, think, or reach the network while you type.

## `:shortcode:` — type a name, get a symbol

For a frame-drawing program, the fastest way to type `→` or `╭` is by name. Start
a token with a colon and at least two characters and a small popup offers matches;
**Tab** (or **Enter**) accepts the highlighted one, **↑/↓** move, **Esc**
dismisses. You never leave the keyboard.

```
:box   →  ┤ ╭ :box-tl:  ┊  ╮ :box-tr:  ┊  ╰ :box-bl:  ┊  ╯ :box-br: ├
:->    →  →
:thu   →  👍  (:thumbsup:)
:fire  →  🔥
```

What's in the table: **symbols and width-safe emoji.**

*Symbols* — box-drawing (`:box-tl:` ╭, `:hline:` ─, `:tee-l:` ├), arrows
(`:->:` →, `:<->:` ↔, `:arrow-ne:` ↗), math and logic (`:neq:` ≠, `:leq:` ≤,
`:sum:` ∑, `:forall:` ∀), Greek (`:pi:` π, `:lambda:` λ), and typographic marks
(`:mdash:` —, `:bullet:` •, `:deg:` °). Every glyph is single-width.

*Emoji* — the common set: `:thumbsup:` 👍 (also `:+1:`), `:fire:` 🔥, `:rocket:`
🚀, `:tada:` 🎉, `:heart:` ❤️, `:check:` ✅, `:warning:` ⚠️, `:eyes:` 👀, `:bug:`
🐛, `:sparkles:` ✨, `:100:` 💯 — and ~170 more. Type a distinctive stem (`:thu`,
`:rock`, `:heart`) and the popup ranks the match. When a name overlaps, symbols
rank ahead of emoji, so `:box` still shows the box-drawing glyphs first.

**Why only *some* emoji.** Emoji width is a cross-terminal minefield: a family
like 👨‍👩‍👧‍👦 is one grapheme joined by zero-width joiners, and a terminal without
ligature support draws it as four separate faces — eight cells where the frame
reserved two, and the box tears. The `:shortcode:` table admits only emoji whose
reserved width equals what the terminal draws: **a single wide character, optionally
with the emoji-presentation selector** (so `:heart:` gives the two-cell ❤️, not the
one-cell text ❤). It deliberately **excludes** the width-divergent shapes — ZWJ
sequences (families, professions, the 🏳️‍🌈 flag), regional-indicator flag pairs
(🇺🇸), combining keycaps (1️⃣), and skin-tone modifiers. For one of those, reach for
your terminal's own unicode input (below) — that way the frame never promises a
width it can't keep.

An ordinary colon never triggers the popup: `12:30`, `http://…`, and
`key: value` are all left alone (the colon must open the token — start of line or
after a space — and needs two or more following characters).

### Meanwhile: your terminal's own unicode input

Many terminals already half-solve this. In **kitty**, `Ctrl+Shift+U` opens a
unicode input kitten (type a name or code point). Most terminals also accept
`Ctrl+Shift+U`, then a hex code point, then Enter/Space. Handy for the occasional
glyph that isn't in the shortcode table.

## Shell ghost text — completions from your habits

Shell ghost text works in **both** the full-screen TUI **and** the plain inline
`xlii code` REPL (the `:shortcode:` popup above is TUI-only for now). In
**bare-shell context** (where bare input runs as a live shell command — the
`code` surface with shell-primary on, not `/chat` or `/plan`), the input shows a
dim **ghost** completion of the command it thinks you're typing, fish-style:

```
git s│tatus          ← "tatus" is dim; press → (right arrow) to accept it
```

**Right arrow** accepts the ghost. Accepting only **inserts** the text — it never
runs it, so a remembered `rm -rf …` line is safe; you still press **Enter** to
execute, exactly as if you'd typed it.

How it learns, and why it's safe:

- **Compiled offline, served instantly.** An occasional background pass distills
  the commands you actually run into a small ranked table (repeated, recent
  commands rank first). The keystroke path just prefix-matches that table — no
  model runs while you type.
- **Stale is nothing to worry about.** If the table hasn't been built yet, or was
  cleared, there's simply no ghost text. Nothing waits, nothing fails.
- **Secrets are redacted before anything is stored.** Tokens in `curl -H
  "Authorization: Bearer …"`, `--api-key …`, `user:pass@host`, `GITHUB_TOKEN=…`
  and similar are scrubbed at compile time; a command whose secret can't be safely
  bounded is dropped entirely. The suggestion table never contains a credential.

If you don't want a suggestion, ignore it and keep typing — the ghost only ever
appears at the end of the line and vanishes as soon as it stops matching.

### Optional: AI-refined suggestions

By default the offline pass is **deterministic** — it ranks and dedups the
commands you actually run, and calls no model. If you'd like the occasional pass
to also *curate* the table with a cheap model (collapse near-duplicate variants,
drop one-off noise), opt in:

```
export XLII_SHELL_SUGGEST_AI=1
```

When set, the periodic compile (every ~35 shell commands) runs a single small
completion on your **worker model**, billed to your **primary chat key**. It's
best-effort — any hiccup falls straight back to the deterministic table — and its
output is re-scrubbed, so a model can never re-introduce a secret. The keystroke
path is unchanged either way (it only ever prefix-scans an in-memory table), and
quitting stays instant: the on-exit flush is always deterministic, never a model
call. Tune the cadence with `XLII_SHELL_SUGGEST_WINDOW` (default 35, clamped
5–200).
