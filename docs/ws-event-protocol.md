# WebSocket event protocol (v2)

**Status:** protocol `2` — the wire contract for `xlii serve --ws` (headless turns) and
`xlii serve --face` (the persistent-REPL face server behind the Tauri/browser face)
**Source of truth for event shapes:** `xlii/turn_events.py` (serialize via `xlii/ws_protocol.py`)
**Related:** [browser-ui.md](../proposals/browser-ui.md) Path C · [tauri-shell.md](../proposals/tauri-shell.md) handshake · [serve-web.md](../proposals/done/serve-web.md)

---

## Transport

- **Endpoint:** `ws://127.0.0.1:<port>/?token=<token>` (token mandatory even on loopback).
- **Bind posture:** localhost or tailnet interface only — same rule as `xlii serve` W1.
- **Framing:** one JSON object per WebSocket **text** frame (UTF-8), 8 MiB inbound cap.
- **Engines:**
  - `--ws`: `xlii ask --session <id>` turn loop; one conversation id per connection.
  - `--face`: one **persistent live session** per process (the full REPL — slash commands,
    shell lines, modes); a connection is a view over it. Single live client at a time.
- **Handshake mode:** `xlii serve --ws|--face --handshake --port 0` prints exactly one JSON
  line on stdout then flushes: `{"port": N, "token": "…", "version": "…", "protocol": "2"}`.
  Parent process owns lifecycle — server exits when stdin closes. (`protocol` is additive in
  v2; older hosts ignore unknown keys.)

Upgrade failures:

| Condition | HTTP |
|-----------|------|
| Bad or missing `token` query param | 403 Forbidden |
| Non-WebSocket request | 400 Bad Request |
| Valid token + `Sec-WebSocket-Key` | 101 Switching Protocols |

The `--face` server also answers plain HTTP `GET /` and `GET /assets/*` from the bundled
face assets (ungated — the WS is what the token gates), so a bare browser can render the
face without Tauri.

---

## Client → server

| `type` | Fields | Server | Meaning |
|--------|--------|--------|---------|
| `input` | `text` (str, required) | face | One input line — routed by the live session's rules (slash / shell / agent), exactly like typing at the REPL. Answered by a `turn_done`. |
| `turn` | `prompt` (str, required) | both | v1 name. On `--ws`: run one headless agent turn. On `--face`: compat alias for `input`. |
| `set_posture` | `posture` (`"chat"` \| `"code"`) | face | Flip the `[M]`/`[$]` routing posture (see below). Answered by a `mode_state`. |
| `exit_overlay` | — | face | Leave the faux-mode that currently owns the flip chip (`howto` / `ops` / `plan` / …). Same work as `/off`. Answered by a `mode_state`. |
| `confirm` | `id` (str), `approve` (bool) | face | Answer a pending `confirm_request`. Unknown/expired ids are ignored. |
| `cancel` | — | face | Cooperatively cancel the in-flight turn (honored at tool boundaries). |
| `join_project` | `name` or `project` (str) | face | Live-**switch** into a registered project from Home (same as `/project switch`; chat slash cannot run `/project`, so this wire stays). Wire name kept for compatibility; product verb is *switch*. Refreshes chrome/deck; posture → code. |
| `fabric_sync_projects` | — | face | Pull every node's project registry, merge into this desk's Projects panel (a section per node). Home never travels. |
| `fabric_new` | `node` (str), `name` (str) | face | Create the folder on that node, stub here, Files at `sftp://…`. `/sync` later if you want a Collection. |
| `create_project` | `path` (str), `kind` (`code` \| `collection`) | face | Adopt an **existing** folder as an xlii project and live-switch. Relative names resolve against the desk cwd (Home = `~`), never the process cwd. Refuses `~` and the Home desk store. |
| `go_home` | — | face | Leave the bound folder for the scratch/home blank slate. Does **not** flip `[M]`/`[$]`. |
| `open_pane` | `pane` or `id` (str) | face | Ensure a pane is mounted on the deck and return `pane_deck` + `pane_focus` (Home **switch** door when projects was not in the pack). |
| `set_slot` | `slot` (`a`\|`b`), `view` (str) | face | Put `stream` / `stream:<id>` / a pane id / empty in a visual slot. `stream:<id>` is a **look** at another open project tape (focus stays on the live room). |
| `focus_slot` | `slot` (`a`\|`b`) | face | Click a slot. A peeked `stream:<id>` becomes the live project (change stream). |
| `swap_slots` | — | face | Exchange slot a and b (same views, other side). |
| `set_workbench` | `name` or `workbench` (str) | face | Switch the pack (`/workbench` parity — not a mode). Slot catalog + Panel Workbench follow the pack; off-pack slot views evict. Refreshes chrome + deck + `pane_catalog`. |
| `open_browser` | `url` (str, optional) | face | Chat power-tool door: open the OS browser. Empty url → project `file://` root or forge remote when known. |
| `open_terminal` | `run` (str, optional) | face | Open a real terminal emulator at the configured dest (this project / home / root / custom — Options → Config → **new terminal**). Empty `run` is a shell; `run: "mc"` is Midnight Commander. The webview is not a TTY. |
| `jobs_open` | `id` (str, optional) | face | Open the jobs board. With `id`, mark that finished pill seen. |
| `jobs_clear` | — | face | Drop finished jobs and their pills (`/jobs clear`). Live work stays. |
| `task_write` | `spec` (object), `run` (bool) | face | Task maker Save. Writes `.xlii/tasks/<name>.toml` from the step list (bash / slash / ask). `run: true` also prefills `/tasks run <name>`. |
| `plugin_write` | `spec` (object) | face | Plugin maker Save. Writes `~/.config/xlii/plugins/<id>.md`. Existing schema/transforms stay. |
| `set_terminal_cwd_path` | `path` (str) | face | Persist the custom New-terminal folder (from the config claim). Empty / `clear` returns dest to this project. |
| `set_face_skin` | `skin` (`dark` \| `light` \| `slate` \| `mojo` \| `pack:<name>`) | face | Persist the face web skin in user config (Tauri localStorage does not survive relaunch). Pack ids are namespaced so they never shadow the four compiled skins. |
| `set_face_fkeys` | `visible` (bool) | face | Persist Options → F-keys strip. Same `config.json` field the TUI reads. |
| `set_face_bold` | `on` (bool) | face | Persist Options → Bold type (`html[data-bold]`). |
| `save_screenshot` | `b64` (str, optional PNG), `name` (str, optional) | face | Save a PNG of the face window (Options → Save screenshot). Empty `b64` → server grabs the window. A client PNG is accepted; SVG snapshots are rejected (blank in viewers). Desktop (else ``~``). |
| `research_tool` | `tool` (`kg` \| `canvas`) | face | Chat power-tool doors. `kg` → wiki. `canvas` → the work (`canvas://`, one image or PDF, rendered). Not a mode / not a fourth pack. |
| `focus` | `address` (str) **or** `clear` (bool) | face | Pin a feed-view / pane leaf as **next-turn context** (F4 / Focus). Not a rewind — the next prompt discusses this file. Files stage as locker `--once`; `clear: true` drops the pin. |
| `upload` | `name` (str), `b64` (str), `caption` (str, optional) | face | Stage a file into the session (≤ 5 MiB decoded). `code` posture: lands in the Tray (vision on the next turn). `chat` posture: attached to the next persona turn. |
| `pane_action` | `pane` (str), `op` (`"select"` \| `"key"` \| `"action"` \| `"set"`), plus `index`/`address`, `key`, `name`, or `value` | face | Panes on the face (B1): one op against the pane deck. `select{index}` marks a row; `key{"enter"|"back"|…}` is local nav; `action{name}` executes a selection-action server-side (a `PREFILL` outcome comes back as `prefill`, `ENQUEUE_TURN` as a normal turn). `set{address,value}` is a Config ``<select>`` (no click-to-cycle). Answered by a fresh `pane_deck`. |
| `ping` | — | both | Keepalive; server replies `pong`. |
| `lock` | — | face | Lock this Face overlay. Not `/kill`. |
| `unlock` | `code` (str, optional) | face | Unlock overlay on **this** glass. Café/black may send TOTP/PIN. Phone glass refuses (lock only). |
| `remote_lock` | — | face | Phone thumb-lock: `/remote-control lock` on the sitting (tears the tailnet listener down). |
| `mark_last` | `name` (str, optional) | face | Phone thumb-mark: `/mark` the last turn. Empty name → `phone-YYYYMMDD-HHMMSS`. |

Posture (`--face` only): `chat` (`[M]`, the default) routes **bare** lines to the
default persona (journal+wiki fused — the phone model). Slash in `[M]` is the
chat-safe allowlist (help, reset, clear/cls, tier, …) — same list as `xlii chat`.
`code` (`[$]`) is the full REPL (`/` slash → `!` bang → shell/agent by mode).

---

## Server → client

### Control (wire-only — not in `turn_events.py`)

| `type` | Fields | When |
|--------|--------|------|
| `hello` | `protocol`, `version`, `session`, `view_posture` (`desk` \| `phone`) | Immediately after 101 upgrade. Phone = tailnet glass (D2 pane cut). |
| `stream_sync` | `project` (str), `turns[]` `{role, text}` | Face tape **is** this project's history. Sent on connect, project switch, and Home. Client clears the live feed and replays user/assistant lines (no tools). |
| `stream_peek` | `slot` (`a`\|`b`), `id`, `project`, `turns[]` | Parked tape for a `stream:<id>` slot (look, not live). |
| `assistant_chunk` | `text` | Streaming token delta during a turn (`on_content_chunk`). |
| `turn_done` | `ok` (bool), `exit_code` (int, optional) | Input unit finished (slash/shell, or a completed agent/persona turn). Clears **hard** busy on the face. |
| `busy_state` | `hard` (bool), `agent` (bool) | Face bg-default (M2.2): `hard` blocks submit; `agent` means a background agent/persona turn is running — input free for `/btw`, `/jobs`, shell; heartbeat shows “input free · /btw”. Sent when an agent turn is backgrounded (`hard=false, agent=true`) and when it ends (`agent=false`). |
| `session_end` | `reason` (str), `message` (str, optional) | Face only: graceful `/exit`/`/quit` finished (save / journal / habits). Client should close the Tauri window (or show bye in a bare browser). Server then stops accepting. |
| `pane_focus` | `pane` (str) | Face only: open this pane id after a remount (`open_pane` response). Empty pane closes the dock. |
| `workbench_catalog` | `active`, `workbenches[]` | Face only: packs for the Workbench menu. |
| `pane_catalog` | `panes[]` `{id,label}` | Face only: Panel Workbench menu + slot-dropdown rows for the **active workbench pack** (stream is only on `slot_catalog`). |
| `error` | `message` | Parse/validation failure on an inbound frame. Also refuse mid-agent disallowed input (`agent working — /btw…`). |
| `door` | `action` (str) | Face only: Mojo door tool. `pane:git` opens that pane; `land:<name>` is informational (the server already switched). |
| `pong` | — | Reply to `ping`. |

### Typed events (mirror `xlii/turn_events.py`)

Each object includes a `type` field. Remaining keys match the dataclass fields.

| `type` | Dataclass | Notes |
|--------|-----------|-------|
| `user_turn` | `UserTurn` | `text` — user prompt for this turn. |
| `assistant_answer` | `AssistantAnswer` | `markdown`, `model`, `streamed`, `mode`, `mode_color`, `role`, `skill` |
| `shell_ran` | `ShellRan` | `command`, `cwd` (string), `stdout`, `stderr`, `returncode`, `duration_s`, `source`, `intent` |
| `tool_started` | `ToolStarted` | `name`, `args_preview` |
| `tool_finished` | `ToolFinished` | `name`, `args_preview`, `content`, `is_error`, `duration_s` |
| `meta_message` | `MetaMessage` | `text`, `level` (`info` \| `success` \| `warn` \| `error`) |
| `confirm_request` | `ConfirmRequest` | `id`, `prompt`, `danger` — a gated action awaits `confirm{id,approve}`. Timeout/disconnect denies. |
| `mode_state` | `ModeState` | `mode`, `color`, `exit_hint`, `placeholder`, `hint`, `ask_primary`, `posture`, `overlay` — input-chrome snapshot; sent on connect, after every handled input, and after posture flips. `overlay` is the faux-mode that owns the flip (`howto`/`ops`/…) or `""` for talk/lab `?`/`$`. |
| `file_out` | `FileOut` | `name`, `kind`, `b64` (images ≤ 5 MiB), `path` (durable file, usually `.xlii/artifacts/…`), `address` (`artifacts://name` or `file://…` for Focus). |
| `prefill` | `Prefill` | `text` — seed the input box, un-executed (review-before-run). First emitted by pane actions (B1: a saved task's "load" seeds `/tasks run <name>`). |
| `claim_line` | face | `prompt`, `initial`, `submit` — morph the face input for one ask. `submit: set_terminal_cwd_path` persists the custom New-terminal folder. Esc / empty cancels. |
| `pane_deck` | `PaneDeck` | `workbench`, `posture` (`desk` \| `phone`), `panes[]` (`id`, `title`, `rows[]`, `actions[]`, `empty`, `note`, `image_b64`, `image_alt`) — the workbench's pane strip (B1). Phone grants the D2 cut (bookmarks + git read-only). `image_b64` is an optional PNG (a scanned PDF page with no text layer). Sent on connect, after every handled input, and after every `pane_action`; re-projection IS the refresh. Absent entirely when the active workbench has no panes (the `chat` type = the pre-B1 face). |
| `chrome_state` | `ChromeState` | `project`, `workbench`, `persona`, `model`, `posture`, `surface`, `providers_ready`, `providers_total`, `quick_launch[]`, `chat_tier`, `trust` (`safe` \| `yolo` \| `freeball`), `meter`, `session`, `cwd`, `jobs_active`, `jobs_unseen`, `jobs[]` (`id`, `kind`, `name`, `status`, `glyph`, `label`, `unseen` — in-flight plus finished-unseen pills), `slot_a` / `slot_b` (`stream`, `stream:<id>`, or a pane id; empty = closed), `slot_focus`, `slot_catalog[]` (`id`, `label` — live **stream** first, then other open `stream:<id>` tapes, then **home**, then pack views), `journal` (`on` when the folder's project journal is recording), `browser` (`""` \| `window` \| `hidden`), `browser_url`, `recent[]` (`name`, `path`, `kind` lab\|talk, `label`, `current`), `term_cwd` (Tools → New terminal dest hint), `binds[]`, `face_skin`, `face_fkeys`, `face_bold`, `glass` (`""` \| `locked` \| `black`), `mouth` (`desk` \| `me` \| `none`), `mouth_via` (fabric node that holds me@, empty = this glass) — HUD + desk slots + occupancy + sticky chrome prefs. Same view cannot occupy both slots. Agent Chromium chip: click `hidden` to show the window. Xlii menu lists `recent` under Home. |
| `slot_state` | face | `a`, `b`, `focus` — visual desk occupancy after `set_slot` / `open_pane`. |
| `feed_view` | face | `kind` (`file`\|`task`\|`skill`\|`doc`\|`image`), `address`, `title`, `text`, `truncated`, `lines`, `b64` (images) — pane **View** on a text/image leaf projects into the main transcript (bordered, collapsible, Focus/F4). A **PDF** leaf opens the sticky `pdf` viewer in the other visual slot (listing \| PDF) instead of the feed. A **taskmake/pluginmake** address opens a closed HTML form (`srcdoc`, no open web) in the other slot (listing \| form; solo stream parks skinny). |
| `focus_state` | face | `items[]` `{address, title, kind, once}` — what is pinned for the next turn. Sent on connect, after `focus`, and after a turn consumes `--once` files. |
| `clear_input_history` | face | History pane **Clear typed lines** — drop face ↑/↓ (`localStorage xlii-face-history`). Does **not** follow `/reset` (working talk only). |
| `clear_transcript` | face | `/clear` · `/cls` · `/clear-screen` (and Xlii → Clear transcript). Empty the glass; working talk stays. Not `/reset`. |
| `console_catalog` | face | `package_manager`, `categories[]` (`name`, `commands[]`) — Commands menu OS shortcuts; Packages lines follow detected pkg mgr. |
| `command_catalog` | `CommandCatalog` | `commands[]` (`name`, `description`) — slash names for the face live-narrowing popup **and** the Commands panel. Scoped to the live posture: `[M]` = chat-safe verbs (help, reset, clear/cls, tier, …); `[$]` = the lab catalog. Sent on connect and after every `set_posture`. Surface-bound commands the face cannot run (`/tui`, `/terminal`) are omitted. |
| `plugin_catalog` | `PluginCatalog` | `plugins[]` (`id`, `name`, `description`, `effect`, `trust`, `subscribed`, `ready`, `actions[]` with `id`/`description`/`params[]`) — face **Plugins** menu (M2.1). Sent on connect and after subscribe/call. Chat power path is this menu + inbound `plugin_call` (same `invoke_action` runner as `/plugin call`), not slash. |

### Client → server (face additions)

| `type` | Fields | Notes |
|--------|--------|-------|
| `set_slot` | `slot` (`a`\|`b`), `view` (`stream` \| `stream:<id>` \| pane id \| `""`) | Assign a view to a visual slot. Empty view closes it (last slot becomes `stream`). A `stream:<id>` pick is a look — typing stays on the live room until `focus_slot`. Mirror of the other slot is refused. |
| `plugin_call` | `plugin`, `action`, `params` (object) | Run a subscribed plugin action; works in chat and code. High-risk actions use `confirm_request`. Ends with `turn_done`. |
| `plugin_subscribe` | `plugin` | Toggle project subscription (same gate as the TUI plugins panel). Refreshes `plugin_catalog`. |

Path fields (`ShellRan.cwd`) serialize as strings. Enum-like literals (`source`, `level`)
stay as strings.

---

## Example stream (one `--face` input in `code` posture)

```json
{"type": "hello", "protocol": "2", "version": "1.26204.4419", "session": "face-a1b2c3"}
{"type": "mode_state", "mode": "code", "color": "green", "exit_hint": "", "placeholder": "code", "hint": "", "ask_primary": false, "posture": "code"}
{"type": "user_turn", "text": "list files"}
{"type": "tool_started", "name": "list_dir", "args_preview": "."}
{"type": "tool_finished", "name": "list_dir", "args_preview": ".", "content": "…", "is_error": false, "duration_s": 0.01}
{"type": "assistant_chunk", "text": "Here"}
{"type": "assistant_chunk", "text": " are"}
{"type": "assistant_answer", "markdown": "Here are the files…", "model": null, "streamed": true, "mode": "code", "mode_color": "green", "role": "", "skill": ""}
{"type": "mode_state", "mode": "code", "color": "green", "exit_hint": "", "placeholder": "code", "hint": "", "ask_primary": false, "posture": "code"}
{"type": "turn_done", "ok": true, "exit_code": 0}
```

---

## Versioning

- `protocol` in `hello` (and the handshake line) is the wire schema generation
  (`ws_protocol.PROTOCOL_VERSION`).
- `version` is the installed `xlii` package version.
- New event kinds require a dataclass in `turn_events.py` first, then a serializer entry
  in `ws_protocol.py` — never ad-hoc parallel types on the wire.
- v1 → v2: added `confirm_request`/`mode_state`/`file_out`/`prefill` outbound; added
  `input`/`set_posture`/`confirm`/`cancel`/`upload` inbound (`turn` kept as an alias);
  handshake line gained `protocol`.
- v2 additive (typed-workbenches B1, 2026-08-02): `pane_deck` outbound, `pane_action`
  inbound; `prefill` gained its first emitter (pane actions). Same `protocol: "2"` —
  older clients ignore the new event; older servers answer `pane_action` with an
  `error` frame.
- v2 additive (F1, 2026-08-02): `chrome_state` outbound (the HUD rail), sent on
  connect / after inputs / on posture flips. Older clients ignore it.
