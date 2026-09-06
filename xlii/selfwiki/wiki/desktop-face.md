# The desktop face — iXaac-first over the protocol-v2 wire

The face is the chat-first surface of the substrate: a native window (Tauri,
`desktop/`) or a browser tab rendering the SAME no-build page
(`xlii/face_assets/` — vanilla ES modules, vendored marked, no CDNs) over one
wire contract (`docs/ws-event-protocol.md`, protocol 2).

## The shape

- **Backend**: `xlii serve --face` (`xlii/serve_face.py`) — ONE persistent
  live session per process; a connection is a view over it. Client #2 of the
  real REPL: input routes through `repl.process_repl_input`, turns through
  `conversation.drive_turn`; the routing is never reimplemented.
- **Postures** (the `[$]`/`[M]` flipmode button): `[M]` chat (the DEFAULT —
  the face lands on iXaac) runs each line as the default persona on its own
  turn store — the same memory the phone daemon writes — fused with the
  project's journal+wiki (`build_mojo_ambient`, shared with `/mojo`). Each
  talk turn prepends a `[bearings]` block (body · surface · desk · reach ·
  hire · last-turn delta). Door tools (`desk_switch`, `desk_new`,
  `pane_open`, …) operate xlii by intent; Face `door` events open panes.
  `[$]` code is the full REPL: slash commands, shell lines, modes.
- **Approvals**: gated tool intents round-trip as `confirm_request`/`confirm`
  wire events (timeout and disconnect deny) — no blanket auto-deny on this
  body, unlike headless `serve --ws`.
- **Media**: uploads (drag-drop, ≤5 MiB) land in the Tray (code posture) or
  ride the next persona turn (chat posture); the session outbox drains to
  `file_out` events — images render inline.
- **Panes (typed-workbenches B1)**: when the project's active workbench type
  (`/workbench`, B0) names a pane set, the face shows the strip — tabs above
  the input bar, a panel per pane (rows + selection-actions). Server-side it
  is `xlii/face_panes.py`: ONE headless `panes/dock.py` Dock projected as
  `pane_deck` snapshots; client `pane_action` ops (select/key/action) come
  back over the wire. Outcomes route to the face's own channels — a task's
  "load" is the FIRST emitter of the long-wired `prefill` event
  (review-before-run); "open in other pane" morphs the same slot (the face
  has one panel per tab). The `chat` type has no panes = the pre-B1 face,
  unchanged.
- **HUD rail (F1)**: a top strip of always-visible cues — model · project +
  workbench badge · persona + posture · provider key readiness — fed by the
  `chrome_state` event (connect, after every input, on posture flips). The
  deck strip wears its dock look: selection rail, tone colors (git status),
  action buttons.
- **Panel world (F3)**: the TUI's panels are deck tabs — `results://`
  sortable provider-run tables (F2), the `sources://` typed source cards,
  the `jobs://` job board, and `menu://` — the whole slash-command registry
  as a menu whose rows seed the input (menus-do-don't-type,
  review-before-run). Product packs: **home** · **chat** · **code**. Every
  pack has the menu tab; `code` adds lab panes (`jobs`, …); `chat` carries
  research power panes (`sources`, `results`, `wiki`, …); `home` leads with
  the **switch** door into a registered project.

## Gating is by network exposure

- Local (Tauri window, `xlii code --tauri`, or a bare browser on the printed
  URL): loopback + `?token=` handshake only — no pairing gate.
- Tailnet door: while a `/remote-control` sitting is open, Face may listen for
  `POST /remote-turn` on this machine's Tailscale IPv4 (`--host tailnet` or the
  sitting-scoped extra listener). Auth is LocalAPI WhoIs +
  `face.toml [tailnet] allowed_devices` — no token in the URL, no mailbox.
  `/remote-control open --glass` upgrades that listener to the **glass
  tier**: assets, skins, and a grant-gated WebSocket (webcode cookie; the
  desk boot token never leaves the loopback). WhoIs on every request; CSP
  on every glass response. Phone posture: thumb bar (mark · panes · lock),
  D2 pane cut (bookmarks + git read-only), elevated Mojo, no `$`. See
  [[skin-packs]] and `proposals/glass-on-phone.md`.
- Internet: the SAME page at `/face/` on `serve --public`, behind the shipped
  pairing-code → grant-cookie wall; each `/face/ws` connection gets its own
  backend subprocess, killed by revoke/sweep via its stdin pipe.
  A granted login lands on the face; `[serve.public] face_default = false`
  keeps the xterm.js TUI as the landing.

## Where things live

`desktop/` — the logic-free Tauri host (spawn sidecar → handshake → webview;
build recipe in its README). The host window is undecorated: no OS title bar.
The face menubar is the title bar (drag, double-click maximize, min/max/close);
window edges still resize. Graphical **skin packs** (bitmap chrome, 9-slice
frames, tiled textures) load from `xlii/face_assets/skins/` and
`~/.config/xlii/skins/` — see [[skin-packs]].
`xlii/face_assets/` — the page. `xlii/serve_face.py`
— the server. `xlii/serve_public.py` — the gated `/face` routes. The frozen
single-binary sidecar is deferred distribution work (`proposals/tauri-shell.md`).
