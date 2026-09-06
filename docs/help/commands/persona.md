# /persona

Switch to another persona mid-session, in place, without leaving the chat REPL.
This is the chat-surface counterpart to `/code` ↔ `/chat`: instead of changing
*surface*, you change *who you are talking to* — same process, same window.

## When to reach for it

Use `/persona <name>` when you are already in `xlii chat` and want to hand the
conversation to a different identity — a reviewer persona, a planning persona, a
writing voice — without spawning a new process. Run bare `/persona` first to list
who exists (with the current one marked).

It is the live, in-session sibling of launching `xlii chat <name>` from the
shell. Prefer `/persona` mid-flow; reserve a fresh `xlii chat <name>` for
starting cold.

## Usage

```
/persona            # list every persona, current marked
/persona <name>     # switch to that persona
```

There are no flags. `/persona` follows the house convention — **bare lists,
argument acts**: with no argument it lists every persona (the current one marked
with ●), and with a name it switches. The argument is the persona name (the same
name you'd pass to `xlii chat`).

`/personas` is a **hidden alias** of `/persona` — it keeps working (and drops out
of `/help`). The two used to be separate commands; since bare-vs-argument already
tells listing apart from switching, one verb is enough (the same `/marks` →
`/bookmarks` consolidation).

## What actually happens on a switch

Each persona owns a **detached thread**. Switching does **not** carry the live
conversation across — the persona you leave is parked, and the one you enter is
restored (or freshly seeded) from its *own* turn store. A persona stays oblivious
to what other personas (or the code surface) discussed. This isolation is the
whole point, not an accident.

That means: don't expect the new persona to "remember" what you just said to the
previous one. The single, deliberate bridge across identities is `/recall
<persona>:<mark>` — mark a turn worth carrying with `/mark`, then pull it into the
other persona on purpose.

## Examples

List who's available, then hand off to a reviewer persona mid-thread:

```
/persona
   ● ada       your default writing voice
     reviewer  a critical second pass
   /persona <name> to switch
/persona reviewer
```

## Common gotchas

- **No history carries over.** If the new persona needs context from the old one,
  `/mark` it first, switch, then `/recall <oldpersona>:<mark>`.
- **Switching to yourself is a no-op.** `/persona <current>` just prints "already
  chatting as …" — it won't reset or reload anything.
- **Unknown names fail the switch.** Run bare `/persona` to see valid names;
  create new ones outside the session with `xlii chat --new <name>`.
- **Loadout and one-shot `/temp` are reset on entry.** Sticky loadout overrides
  and a pending `/temp` do not follow you into the new persona.
- **This is chat-surface only.** `/persona` lives in the chat REPL; to change the
  *surface* (project work vs. conversation) use `/code` and `/chat`.

See also: bare `/persona` (list identities — `/personas` is the hidden alias),
`/chat` and `/code` (change surface), `/recall` (carry one marked turn across).
Run `/describe persona` for the one-line signature, or `/howto personas-loadouts`
for the full deep dive on personas and saved loadouts.
