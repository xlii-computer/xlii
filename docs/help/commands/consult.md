# /consult

Ask a second, cross-vendor model for an independent opinion. `/consult` takes
your question — and optionally a slice of the current conversation — and sends it
to a *different* AI provider than the one driving this session, then prints the
reply under a `[consult · <model>]` header. The whole point is independence: the
outside model never shares the primary's training lineage, so it won't echo the
same blind spots.

It is a one-shot lookup, not a tool and not a turn. The reply is printed and
discarded — it is never appended to your conversation history, so the primary
agent doesn't see it and can't be steered by it. You read the second opinion,
you decide. Works from both the `code` and `chat` REPLs.

## When to reach for it

Use it when you want a tie-breaker, not more output from the same voice:

- A reviewer (`/verify` or `/peer`) flagged something and you're unsure it holds.
- The primary is confidently wrong, or keeps circling the same wrong answer.
- A design call where you want a fresh frame before you commit to it.

If you just want more work done, stay with the primary. `/consult` is for
judgment calls where a second, unrelated perspective is the value.

## Usage

```
/consult [--via cursor|claude|codex|grok] [--model <name>] [--last N | --turns | --full] <question>
```

**Two paths:**

- **Default (API):** sends to a cross-vendor `judges` profile (anthropic, openai,
  xai) using keys you configure. Tier: `cross_org`.
- **`--via cursor|claude|codex|grok`:** routes through an external agent harness
  CLI — no separate API keys for that path (Cursor catalog, Claude/Codex
  headless, Grok ACP). Tier is labeled in the header (`cross_agent` for Cursor,
  `cross_org` for Claude and Codex, `same_vendor` for Grok).

By default `/consult` sends **no history** — just your question, cold. The
history flags decide how much of the current conversation rides along:

- `--turns` — include the last exchange (one user + one assistant pair).
- `--last N` — include the last N exchanges.
- `--full` — include the whole conversation.

Send the least context that makes the question answerable. More history means
more tokens on the outside provider and a noisier prompt; a tightly-scoped
question with `--turns` usually beats `--full`.

## Examples

A cold question with no conversation context:

```
/consult is a token-bucket or a sliding-window the better fit for per-IP rate limiting?
```

Hand the outside model the recent exchange so it can weigh in on what the primary
just proposed:

```
?refactor the auth middleware to cache the JWKS
/consult --turns is caching the JWKS here safe given key rotation?
```

## Gotchas

- It must be **configured first**. `/consult` needs a cross-vendor `judges`
  profile in `~/.config/xlii/config.json` and the API key exported under the env
  var that profile names. If it isn't set up, you'll see "/consult unavailable"
  or "not configured" — run `/describe consult` for the exact config shape, and
  check `/status`, which prints the active provider/model.
- The reply is **not** in your history. Don't expect the primary to remember or
  build on it — copy anything you want to act on back into your own next prompt.
- `--full` on a long session can be expensive and slow on the outside provider.
  Prefer `--last N` or `--turns` and only widen the window if the answer needs it.
- Configure a genuinely *different* vendor than the primary. Pointing it at the
  same family defeats the independence that makes a second opinion worth asking.

Deep dive: `/howto review`
