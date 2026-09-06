---
id: telegram_send
name: Telegram Send (Bot API)
description: "Send a message to yourself (or any chat) via a Telegram bot. HTTPS, no daemon, no public URL. The poor-cousin of OMEMO XMPP for cases where Tailscale isn't reachable — Telegram's servers see your text."
categories: [messaging, notifications]
effect: external-write
trust: subscription
auth_type: query_param
auth_env_vars:
  - TELEGRAM_BOT_TOKEN
  - TELEGRAM_CHAT_ID
actions:
  - id: set
    description: Store the bot token and chat id in the vault
    params:
      TELEGRAM_BOT_TOKEN: {secret: true, store: true, required: true, description: "Bot token from @BotFather"}
      TELEGRAM_CHAT_ID: {store: true, required: true, description: "Numeric chat id (message the bot, then getUpdates)"}
    output: raw
  - id: send
    description: "Send a message via your Telegram bot. Defaults to TELEGRAM_CHAT_ID (your usual chat). Use this for build notifications, agent replies, anything you'd text yourself."
    method: POST
    url: https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage
    params:
      chat_id: {default: "${TELEGRAM_CHAT_ID}", description: "Numeric chat ID (e.g. 1234567890) or @channel_username. Defaults to TELEGRAM_CHAT_ID."}
      text: {required: true, input: textarea, description: "Message body. Supports Telegram HTML/Markdown via parse_mode (omit for plain text)."}
      parse_mode: {description: "HTML, MarkdownV2, or Markdown. Omit for plain text.", enum: [HTML, MarkdownV2, Markdown]}
      disable_notification: {description: "true = silent (no sound/vibrate on the recipient device)"}
    response_shape: "{ok, result: {message_id, chat, date, text}}"
    output_schema:
      type: object
      additionalProperties: false
      required: [delivered, message_id, chat_id, chat_title, sent_at, preview]
      properties:
        delivered: {type: boolean, description: "From response.ok"}
        message_id: {type: integer, description: "From response.result.message_id"}
        chat_id: {type: integer, description: "From response.result.chat.id"}
        chat_title: {type: string, description: "From response.result.chat.title, .first_name, or .username — whichever identifies the destination"}
        sent_at: {type: integer, description: "From response.result.date (unix timestamp)"}
        preview: {type: string, description: "First 80 chars of response.result.text"}
    output: schema
    output_transforms:
      - {op: lift, from: result}     # message_id, chat, date, text → root
      - {op: rename, from: chat.id, to: chat_id}
      - {op: first_of, from: [chat.title, chat.first_name, chat.username], to: chat_title}
      - {op: rename, from: date, to: sent_at}
      - {op: rename, from: text, to: preview}
    output_renderer: text_template
    output_renderer_args:
      template: |-
        ✓ Telegram delivered to {chat_title} (chat {chat_id})
          message id: {message_id}
          sent at:    {sent_at}
          preview:    {preview}
---

# Telegram Send (Bot API)

Send a one-shot message to yourself (or any chat) via a Telegram bot.
HTTP-only, no daemon, no public URL needed for outbound. The natural
companion to `xmpp_send` for cases where you want notifications on a
phone without Tailscale — at the cost of letting Telegram's servers
see your text.

## Setup

### 1. Make a bot via `@BotFather`

Open Telegram, search for `@BotFather`, send `/newbot`, follow prompts.
You'll get a token like `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`.

### 2. Get your chat_id

Send any message to your new bot first (otherwise the bot can't message
you back — Telegram's anti-spam rule). Then:

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | jq '.result[0].message.chat.id'
```

That number is your `TELEGRAM_CHAT_ID`. Save it.

### 3. Store both via the `set` form

Run `telegram_send.set`. Token is a secret field; chat id is plain.
`plugin_call` injects them into the URL and `chat_id` at request time.
The agent must not ask for the token.

## Usage

```
/get telegram send "build complete"
/get telegram message my phone "tests passed"
```

Or in chat without `/get`:

> ask the agent to "ping me on telegram when the build finishes"

The schema-locked render produces a clean confirmation:

```
✓ Telegram delivered to YourName (chat 987654321)
  message id: 42
  sent at:    1714762800
  preview:    build complete
```

## Trade-offs vs `xmpp_send`

| | `xmpp_send` (OMEMO) | `telegram_send` |
|---|---|---|
| Setup | local Prosody + Tailscale | `@BotFather` |
| Encryption | end-to-end (your keys) | server-side (Telegram's keys) |
| Reachability | requires Tailscale on recipient | works on any phone with the app |
| Daemon mode | yes (`xlii daemon --xmpp`) | not yet (would need `getUpdates` long-polling) |
| Rate limits | none (your server) | 30 msgs/sec/bot, 1/sec/chat |

For "notify me when the build's done" telegram is fine. For "agent
forwards a snippet of the unredacted DB schema" it's not — that goes
over OMEMO/XMPP only.

## Notes

- `parse_mode=HTML` lets you bold/italic/code-block via standard tags.
  Omit it for plain text (avoids the silent failure mode where Markdown
  special chars trip up Telegram's parser).
- `disable_notification=true` is the move when you want the message to
  arrive silently (e.g. logging-style pings that shouldn't wake you).
- Inbound (your phone → agent) is not yet wired. The shape would be a
  `xlii daemon --telegram` long-polling `getUpdates`, dispatching the
  same verb catalog + agent fallback as `xlii daemon --xmpp`. Future work.
