# /model

Switch the model the agent runs on — **live, without restarting**. By default it
targets the orchestrator (the main code agent); pass `--worker` for subagents,
`--chat` for persona chat, or `--help-model` for `/howto`. Roles are resolved
fresh at dispatch time, so the change lands on the **very next turn**.

Available in **code** and **chat** REPLs.

## When to reach for it

Reach for `/model` when you want a different model for the work in front of you
and don't want to quit, edit `config.json`, and relaunch:

- **Step up to a flagship** (e.g. `grok-4`) for a turn of hard reasoning, a
  tricky design call, or to send images to a vision-capable model.
- **Step down to the build model** (e.g. `grok-build-0.1`) for fast, cheap,
  high-volume edits once the thinking is done.
- **Split orchestrator vs. worker** — keep a strong planner up top while the
  fan-out workers run a cheaper model, or vice versa, with `--worker`.
- **Tune persona chat** — pin the conversational model with `--chat` without
  touching the code build model.
- **Tune /howto** — pin the cheap help slot with `--help-model` without moving
  chat or orchestrator.
- **Apply a preset** — `build`, `reason`, `economy`, or `vision` via
  `--profile` without hand-picking every role id.
- **Check what's live** by running it bare — it prints role models
  (with temperatures), the active slot for this surface, and any session pin in
  effect.
- **Catalog what you can pick** with `--list` — the known ids (configured +
  priced) plus the built-in profile names.

> `/model` and the old `/models` are now **one command**. The standalone
> `/models` status view folded into bare `/model` (same roles + temps), and
> `models` survives only as a **hidden alias** — it dispatches to `/model`, so
> `/models`, `/models <id>`, and `/models --list` all behave identically. There
> is no separate `/models` surface to learn.

## Usage

```
/model [id] [--worker | --chat | --help-model | --profile <name>] [--list] [--session]
```

- bare `/model` — print orchestrator, worker, chat, help (temps where applicable), the active slot for this surface, and the session override if one is pinned.
- `/model <id>` — switch the orchestrator model to `<id>`.
- `--worker` — target the worker (subagent) model.
- `--chat` — target the persona / conversational chat model.
- `--help-model` — target the `/howto` help model.
- `--list` — catalog the known model ids you can switch to (configured + priced) and the built-in profile names. This is the old `/models` listing, now a flag.
- `--profile <name>` — apply a named preset (`build`, `reason`, `economy`, `vision`, or a custom entry in `model_profiles`). Clears any session model pin so the profile drives the role slots.
- `--session` — apply for **this session only**; it reverts to `config.json` on
  next launch. Without it the change is **persisted** to `config.json`.

Unlike `/swarm`, `/model` **persists by default** — the assumption is that a
model switch is a deliberate choice you want to keep. Use `--session` for a
one-off.

## Examples

See where you stand, then step the orchestrator up to the flagship for a hard
turn (persisted):

```
/model
orchestrator: grok-build-0.1
worker:       grok-build-0.1
chat:         grok-4.3
help:         grok-build-0.1
active slot:  grok-build-0.1  (orchestrator role)
/model grok-4
orchestrator model = grok-4 (saved to config.json) — takes effect next turn
```

Run a strong planner over cheap workers, just for this session:

```
/model grok-4
/model grok-build-0.1 --worker --session
worker model = grok-build-0.1 (this session; reverts to config.json on next launch) — takes effect next turn
```

Pin a faster chat model for this persona session:

```
/model grok-4.20-reasoning --chat
chat model = grok-4.20-reasoning (saved to config.json) — takes effect next turn
```

Switch to the vision preset for locker / image work (session only):

```
/model --profile vision --session
profile vision applied — orch=grok-4.3 worker=grok-build-0.1 chat=grok-4.3 help=grok-build-0.1 (this session) — takes effect next turn
```

Catalog the ids you can switch to (this is the old `/models` listing):

```
/model --list
known models (configured + priced; /model <id> to switch)
  · grok-4
  · grok-build-0.1
profiles: build, economy, reason, vision (`/model --profile <name>`)
```

## Gotchas

- **Persists by default.** A bare `/model <id>` writes `config.json`. Pass
  `--session` if you only want it for this run.
- **Next turn, not mid-turn.** The switch is read at the start of the next turn;
  an in-flight turn finishes on the model it started with.
- **Persona/skill pins.** If a persona, role, or skill pinned a model (a sticky
  session override), `/model` on the orchestrator slot moves that pin to the new
  id so the switch is visible — and a `/code`↔`/chat` switch can clear session
  pins, after which the persisted `config.json` value takes over.
- **Active slot vs. role config.** On persona chat the active slot uses the
  `chat` role; under `/howto` it uses `help`. A loadout pin can still override
  the model id. `/model --chat` / `--help-model` change those defaults;
  `/model <id>` without flags changes orchestrator (code surface).
- **Unknown ids apply anyway.** If the id isn't in your configured/known set it's
  still applied (xAI may add models faster than your config knows about); an
  invalid id surfaces as an error at call time, not here.
- **`/models` is a hidden alias, not a second command.** It routes straight to
  this handler, so the old muscle memory still works — but there's nothing it
  does that `/model` doesn't. Bare `/models` shows status; use `/model --list`
  (or `/models --list`) for the id catalog.

Deep dive: `/howto config-models`
