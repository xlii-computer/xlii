# /workbench

Named **packs** for the project. On the face the pack **is** the slot
dropdown + **Panel Workbench** list (stream plus this pack's panes). Leftover
quick-launch metadata still describes the same doors. **Not a mode, not a
face, not an identity.**

Product law: three packs only.

| Pack | Desk | Strip doors (shape) |
| --- | --- | --- |
| **home** | Scratch / 7am desk | **switch** first (projects list), plugins, locker, attach… |
| **chat** | Companion | Research *power tools* on chat: plugins · kg · canvas · browser · locker · sources · results · wiki… |
| **code** | Project lab | plan · git · jobs · tasks · explorer · skills · attach |

Research is **not** a fourth pack — those doors live on **chat**. The old names
`research` and `general` still load from `.xlii/workbench.json` as aliases
(`research` → `chat`, `general` → `home`).

## Usage

```text
/workbench                         # list packs; mark active
/workbench home|chat|code          # switch pack (persists .xlii/workbench.json)
/workbench <type> --bind-persona   # opt-in: also bind the pack's suggested persona
/workbench new <type> <name> [path] [--bind-persona]
                                   # create a local-only project of that pack + open it
```

On the face: **Options → Workbench** cycles packs. The L/R slot dropdowns
and **Panel Workbench** list that pack. A view that isn't in the new pack leaves
its slot (last slot becomes stream). `/panel` and Project menu can still
open a pane outside the pack; it stays until the next switch.

Home chrome always offers **switch** (product verb; wire event is still
`join_project` for compatibility).

## What it does *not* do

- Does **not** flip `[M]` / `[$]` posture — you own that.
- Does **not** rebind persona unless you pass `--bind-persona`.
- Does **not** change slash grammar or which surface you are on.

## Override

`.xlii/workbench.toml` — `[[type]]` rows merge over builtins by name (new names
add packs; set columns to override).

## See also

- Face HUD / strip: `chrome_state.quick_launch` — [ws-event-protocol](../../ws-event-protocol.md)
- Panels: `/panel`, `/home` (home hub), **Panel Workbench** menu
- Projects list: `/panel projects` · switch door
