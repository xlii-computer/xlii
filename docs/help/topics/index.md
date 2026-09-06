# xlii help — topic index

Attach a focused guide with `/howto <topic>`, then ask in plain English (no `?`
needed while howto mode is on). To go deep on a single *command*, use
`/describe <name>` — it prints live facts from this build plus an always-current
description, so it never goes stale. `/describe modes` prints the decision tree.

## Getting started

| Topic | Command | Covers |
| --- | --- | --- |
| Install & setup | `/howto install` | Clone, venv, `xlii setup`, keys, `xlii doctor` |
| First session | `/howto first-session` | `xlii code` vs `xlii chat`, input routing |
| Getting help | `/howto help-system` | `/help` vs `/howto` vs `/describe` |
| Troubleshooting | `/howto troubleshoot` | Common errors, sync/TUI/model fixes |
| Workflow recipes | `/howto workflow` | Cursor-style patterns mapped to `/plan`, `/rail`, `/loop`, `/swarm`, review |

## Doing the work

| Topic | Command | Covers |
| --- | --- | --- |
| Modes & gates | `/howto modes` | `/plan`, `/rail`, `/loop`, `/yolo` — which to use |
| The coding rail | `/howto rail` | The six-stage gated pipeline |
| Loop & swarm | `/howto loop-swarm` | Autonomous `/loop` + parallel writers |
| Review & verify | `/howto review` | `/verify`, `/peer`, `/consult` |
| Knowledge & context | `/howto knowledge` | `/doc`, `/ref`, marks, locker, loadouts |

## Power features

| Topic | Command | Covers |
| --- | --- | --- |
| Personas & loadouts | `/howto personas-loadouts` | `/persona`, `/loadout` |
| Plugins | `/howto plugins` | Markdown capabilities |
| Bridges | `/howto bridges` | DeepContext (Grok Build) + Cursor/Composer |
| Sessions & projects | `/howto sessions-projects` | `xlii init`/`new`/`sync`, `/status` |
| Config, models & keys | `/howto config-models` | `/models`, `/temp`, `xlii config`/`keys` |
| Gigwork & gaggles | `/howto gigwork` | `/gigwork`, `/gaggle second-opinion` |
| Storage backends | `/howto storage-backends` | The 5-method protocol, conformance suite, adapter authoring |
| WebDAV remotes | `/howto remote-webdav` | Nextcloud/ownCloud shares via `dav://`, `/remote` setup, https rule |
| SMB remotes | `/howto remote-smb` | NAS / Windows shares via `smb://`, `/remote` setup, LAN/VPN posture |

**Fresh from GitHub:** `/howto latest [topic]` pulls the newest copy (falls back
to bundled/cached when offline). **Leave howto mode:** `/howto off`.

For deep reference outside a session, see `docs/HOWTO.md` and `docs/REFERENCE.md`,
or run `xlii help`.
