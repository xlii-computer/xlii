---
id: bluesky_chat
name: Bluesky Chat (DMs)
description: Bluesky direct messages — list conversations, fetch messages, send messages. Authenticated via app password + accessJwt.
categories: [social, messaging]
effect: external-write
trust: subscription
auth_type: bearer
auth_setup: bluesky_login
auth_env_vars:
  - BSKY_ACCESS_JWT
  - BSKY_DID
  - BSKY_PDS_HOST
actions:
  - id: list_convos
    description: List the authenticated user's chat conversations
    method: GET
    url: https://${BSKY_PDS_HOST}/xrpc/chat.bsky.convo.listConvos
    headers:
      Authorization: "Bearer ${BSKY_ACCESS_JWT}"
      Atproto-Proxy: "did:web:api.bsky.chat#bsky_chat"
    params:
      limit: {default: "20", description: "Max convos to return (1-100)"}
      cursor: {description: "Pagination cursor from a previous response"}
    response_shape: ".convos[] → {id, members[], lastMessage, unreadCount, muted}"
  - id: get_messages
    description: Fetch messages in a specific conversation
    method: GET
    url: https://${BSKY_PDS_HOST}/xrpc/chat.bsky.convo.getMessages
    headers:
      Authorization: "Bearer ${BSKY_ACCESS_JWT}"
      Atproto-Proxy: "did:web:api.bsky.chat#bsky_chat"
    params:
      convoId: {required: true, description: "Convo ID from list_convos"}
      limit: {default: "30", description: "Max messages to return (1-100)"}
      cursor: {description: "Pagination cursor from a previous response"}
    response_shape: ".messages[] → {id, sender, text, sentAt}"
  - id: get_convo_for_members
    description: Find or create a convo with a specific participant by DID
    method: GET
    url: https://${BSKY_PDS_HOST}/xrpc/chat.bsky.convo.getConvoForMembers
    headers:
      Authorization: "Bearer ${BSKY_ACCESS_JWT}"
      Atproto-Proxy: "did:web:api.bsky.chat#bsky_chat"
    params:
      members: {required: true, description: "Participant DID(s) — repeat the param for multi-member convos. Use chat.bsky.actor.declaration to confirm DID accepts DMs."}
    response_shape: ".convo → {id, members[]}"
  - id: send_message
    description: Send a text message into a convo
    method: POST
    url: https://${BSKY_PDS_HOST}/xrpc/chat.bsky.convo.sendMessage
    headers:
      Authorization: "Bearer ${BSKY_ACCESS_JWT}"
      Atproto-Proxy: "did:web:api.bsky.chat#bsky_chat"
      Content-Type: "application/json"
    params:
      convoId: {required: true, description: "Convo ID from list_convos or get_convo_for_members"}
      message: {required: true, description: "Object with text field — pass {\"text\":\"your message body\"}"}
    response_shape: "{id, sender, text, sentAt}"
  - id: compose
    description: Send a Bluesky DM. Form is handle + message. Use for /get send bluesky.
    method: POST
    url: https://${BSKY_PDS_HOST}/xrpc/chat.bsky.convo.sendMessage
    headers:
      Authorization: "Bearer ${BSKY_ACCESS_JWT}"
      Atproto-Proxy: "did:web:api.bsky.chat#bsky_chat"
      Content-Type: "application/json"
    params:
      to: {required: true, description: "Handle (name.bsky.social) or DID"}
      text: {required: true, input: textarea, description: "Message"}
    compose:
      steps:
        - id: resolve
          skip_if: did
          url: https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle
          query: {handle: $to}
          take: {did: did}
        - id: convo
          url: https://${BSKY_PDS_HOST}/xrpc/chat.bsky.convo.getConvoForMembers
          headers:
            Authorization: "Bearer ${BSKY_ACCESS_JWT}"
            Atproto-Proxy: "did:web:api.bsky.chat#bsky_chat"
          query: {members: $did}
          take: {convoId: convo.id}
      body:
        convoId: $convoId
        message:
          text: $text
    output: raw
    response_shape: "{id, sender, text, sentAt}"
---

# Bluesky Chat (DMs)

Bluesky's DM namespace lives under `chat.bsky.convo.*`. Unlike the public-read
endpoints (covered by the `bluesky` plugin), chat requires:

- An **app password with DM scope enabled** (regular login passwords don't work)
- A short-lived **accessJwt** obtained by calling `com.atproto.server.createSession`
- The `Atproto-Proxy` header on every chat call so the PDS routes to the chat service

This plugin handles routing once `bluesky_login.login` has filled the vault.
Chat endpoints hit **your** PDS (`${BSKY_PDS_HOST}`), not `bsky.social`.

## Auth

Subscribe `bluesky_login` and run `bluesky_login.login`. The form takes
handle + app password (DM access checked). Vault write is automatic.
Re-run login when the JWT expires (~2 hours). The agent must not ask
for the password.

## Usage

### List your conversations

```bash
curl -s -H "Authorization: Bearer ${BSKY_ACCESS_JWT}" \
     -H "Atproto-Proxy: did:web:api.bsky.chat#bsky_chat" \
     "https://bsky.social/xrpc/chat.bsky.convo.listConvos?limit=20"
```

### Fetch messages in a convo

```bash
curl -s -H "Authorization: Bearer ${BSKY_ACCESS_JWT}" \
     -H "Atproto-Proxy: did:web:api.bsky.chat#bsky_chat" \
     "https://bsky.social/xrpc/chat.bsky.convo.getMessages?convoId=<convoId>&limit=30"
```

### Find a convo with a specific user (by DID)

```bash
curl -s -H "Authorization: Bearer ${BSKY_ACCESS_JWT}" \
     -H "Atproto-Proxy: did:web:api.bsky.chat#bsky_chat" \
     "https://bsky.social/xrpc/chat.bsky.convo.getConvoForMembers?members=did:plc:..."
```

### Send a DM

`/get send bluesky` (or Plugins → bluesky_chat → compose) opens handle +
message. Submit sends. Or:

```
/plugin call bluesky_chat.compose to=name.bsky.social text="hello"
```

## Response shape (get_messages)

```json
{
  "messages": [
    {
      "id": "3jzfcijpj2z2a",
      "rev": "...",
      "sender": { "did": "did:plc:..." },
      "text": "Hey, just saw your post about CNC tooling",
      "sentAt": "2026-05-03T14:22:00.123Z"
    }
  ],
  "cursor": "..."
}
```

## Notes

- **DM scope is per-app-password**, not per-account. If your existing app password
  doesn't have DM scope, generate a new one rather than trying to upgrade.
- **Recipient DID lookup**: to DM someone, you need their DID, not their handle.
  Use the public `bluesky.get_profile` action (in the `bluesky` plugin) to map
  `handle → did`. Note that some users have DM permissions set to "no one" or
  "people I follow" — you'll get an error on `send_message` if you don't qualify.
- **Custom PDS users**: the host above (`bsky.social`) is correct for default
  Bluesky accounts. If your account is on a self-hosted PDS, replace the host
  with your PDS's xrpc endpoint.
- **The `Atproto-Proxy` header is non-negotiable.** Without it the PDS doesn't
  know to route the request to the chat service and returns 404.
- **Rate limits** are unpublished but tighter than public reads. Don't poll
  `list_convos` aggressively — Bluesky has a websocket subscription endpoint
  (`chat.bsky.convo.subscribe`) for real-time updates that's not yet wired
  into this plugin.
