# /session

Opt-in **episode** continuity for a code session: while an episode is on, the
FULL live history (user/assistant/tool trail) and the conversation id are
snapshotted to `.xlii/sessions/<id>.json` after every turn. Resuming restores
them exactly — including the same conversation id, so the provider's prompt
cache can stay warm across the restart.

This is the heavier sibling of the default cross-session memory (recent turns
re-seeded as context at launch). The default answers "where did we leave off?";
an episode gives the conversation *back*, turn for turn.

## Usage

```text
/session               # status: active episode id, or how to start one
/session on            # start snapshotting from here
/session off           # stop — a deliberate stop, not crash residue
/session list          # stored episodes for this project
/session resume [id]   # restore one (omit id → latest, or a numbered list if several)
```

The TUI command palette also has **session resume** — one episode resumes
immediately; several open a picker.

## Shell twins

The same continuity from launch, no in-session step needed:

```bash
xlii code --keep-session     # persistent from the first turn AND sticky (see below)
xlii code --resume           # restore the most recent episode, no question asked
xlii code --resume 5a7b      # …or a specific id
```

## Behaviors

- **Sticky.** `--keep-session` (or `/session on`) records a per-project
  preference. Every later bare launch of that project asks one typed question —
  `keep-session is on for this project · kept sess 5a7b (12 turn(s), 38m ago —
  KV likely still warm) · restart there? [Y/n]` — Enter resumes, `n` starts a
  fresh episode with the preference intact. Non-interactive launches (pipes,
  scripts, the daemon) resume silently; a prompt never hangs a pipe.
- **The off-ramp.** `/session off` stops the live episode AND clears the sticky
  preference — "off" means "stop keeping my sessions here."
- **Per-turn snapshots.** The record updates at every turn's finalize on every
  surface — inline REPL and `--tui` alike. A resumed session keeps persisting.
  Oversized tool bodies are trimmed and the trail is capped so rewrites stay
  bounded; user/assistant messages and tool *structure* stay intact.
- **Pointers (hint only).** When a rail/loop/job was live, the record may carry
  pointers shown on resume — they are never auto-reattached.
- **Cache instrumentation.** Last-turn context/cached counters ride in the
  record; resume prints a dim line, and the first post-resume turn may print a
  warm-vs-cold Δ. The status-bar meter always recomputes — never restored.
- **Clean vs crashed.** A normal exit (or `/session off`) marks the episode
  clean. A crash leaves `unclean` residue, and the next launch offers it back
  with a single dim line — never a boot wizard.
- **Storage.** One JSON per episode under the project's `.xlii/sessions/`
  (the sticky preference lives next to them in `keep-session.json`);
  `xlii project rm` removes them with the rest of the project state.
- **Code REPL only.** Chat personas have their own durable memory pipe.
