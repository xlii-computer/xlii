# /tier

Set or show the **chat tier** — the per-conversation reasoning-depth dial for
chat mode. It's the "how hard should you think about this?" selector: pick a
faster, cheaper model for quick turns, a reasoning model for hard ones, or let
xlii route each message for you.

Available in the **code** and **chat** REPLs.

The tier steers which model the chat slot resolves to, read fresh at dispatch, so
a change lands on the **very next turn** — no relaunch. It applies to chat-mode
(conversational) turns; in plain code turns the orchestrator role is used and the
tier is inert. A sticky persona/loadout model pin still wins over a tier.

## The tiers

- **fast** — quick answers on the cheap fast model (the `economy` profile).
- **expert** — deeper reasoning on the reasoning model (the `reason` profile).
- **heavy** — for research-shaped questions that need fresh, broad information.
- **auto** — xlii routes each message to fast, expert, or heavy for you.

## Usage

```
/tier [fast|expert|heavy|auto|off]
>>tier <message>
```

- bare `/tier` — show the current tier and the menu, with the model each resolves
  to.
- `/tier <name>` — set the sticky tier. Written to `config.json`; the next
  session starts on the same pick. Fresh installs default to **auto**.
- `/tier off` — clear it; chat returns to the plain configured chat model
  (also persisted).
- `>>tier <message>` — the **per-message sigil**: steer one message without
  touching the sticky pick (`>>heavy what changed in the EU AI act this month?`).
  One-letter shorthands work too: `>>f` `>>e` `>>h` `>>a`. A bare `>>tier` with
  no message does nothing — the sigil is one-shot steering, not a `/tier`
  synonym. In the TUI the same picker lives under **Console → Chat tier**.

## Examples

Bump reasoning depth for a gnarly question, then drop back:

```
/tier expert
chat tier = expert (deeper reasoning on the reasoning model · grok-4.20-reasoning) — takes effect next turn
/tier off
chat tier cleared (plain chat model — takes effect next turn)
```

Let xlii decide per message (the default for a fresh session):

```
/tier auto
chat tier = auto (routed per message — takes effect next turn)
```

Crank one research question up without moving the dial:

```
>>heavy what changed in the EU AI act this month?
tier ↑ heavy (this message)
```

## Gotchas

- **Sticky across sessions.** The pick is written to `config.json` (`chat_tier`)
  and restored at boot. Options → Chat tier and the config pane cycle the same
  value. `/tier off` persists as off. A brand-new install still defaults to
  `auto`.
- **A pinned model wins.** If a persona or loadout pinned a specific model
  (`/model <id>`), that pin overrides the tier — pinning a model is a deliberate
  override. Clear the pin to let the tier drive again.
- **Inert off the chat role.** Set it in a code turn and normal code work ignores
  it; it only steers chat-mode turns.
- **heavy does coordinated deep search.** On `/tier heavy` — and when `auto`
  escalates a question it can't answer without fresh data — the model can run a
  deep search: it fans out web/X searches across sub-queries in parallel and
  synthesizes a cited answer. Expect it to be slower and pricier than fast or
  expert; reach for it when a question genuinely needs current, broad, or
  external information.

Related: `/model` (pin a specific model or apply a profile), `/temp`.
