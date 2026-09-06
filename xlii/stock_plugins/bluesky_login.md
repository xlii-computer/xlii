---
id: bluesky_login
name: Bluesky Login
description: Exchange handle + app password for the vault values bluesky_chat needs. Form only — the password never enters chat.
categories: [setup, social]
effect: read-only
trust: subscription
auth_type: none
auth_env_vars: []
actions:
  - id: login
    description: Log in. Opens a form for handle + app password. Stores JWT / DID / PDS host for bluesky_chat.
    method: POST
    url: https://bsky.social/xrpc/com.atproto.server.createSession
    headers:
      Content-Type: "application/json"
    params:
      identifier:
        required: true
        description: "Bluesky handle (e.g. yourname.bsky.social)"
      password:
        required: true
        secret: true
        description: "App password from https://bsky.app/settings/app-passwords (DM access checked). Not the account password."
    response_shape: "{accessJwt, did, handle, didDoc.service[]}"
    store:
      plugin: bluesky_chat
      map:
        BSKY_ACCESS_JWT: accessJwt
        BSKY_DID: did
        BSKY_PDS_HOST: pds_host
    output: schema
    output_renderer: text_template
    output_renderer_args:
      template: "Logged in as {handle}. Credentials stored for bluesky_chat."
---

# Bluesky Login

Setup-only. One POST to `com.atproto.server.createSession`. The password is a
**form secret** — it never goes through the agent or the input bar.

On success the vault for `bluesky_chat` gets `BSKY_ACCESS_JWT`, `BSKY_DID`,
and `BSKY_PDS_HOST` (host stripped from the AtprotoPersonalDataServer
endpoint). No paste command.

## Setup

1. App password at https://bsky.app/settings/app-passwords — check
   **Allow access to your direct messages**.
2. Subscribe `bluesky_login` (and `bluesky_chat` if you want DMs).
3. Run `bluesky_login.login` — the form opens. Fill handle + app password.
4. Token lasts ~2 hours. Run login again to refresh.

Self-hosted PDS: change the action `url:` to that PDS's createSession.

Keep this plugin subscribed or unsubscribe after login — it does nothing
until invoked.
