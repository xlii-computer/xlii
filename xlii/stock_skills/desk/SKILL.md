---
name: desk
description: This machine's drop zone and installer rules — ~/Downloads is where user-downloaded AppImages/debs land; project cwd is not Downloads; Linux AppImage vs dpkg vs never dmg.
metadata:
  primitive: "xlii.desk.downloads_dir / desk_context (wired into /sh)"
  use-before: "installing, opening, or running something the user downloaded"
---

# Skill: desk

User-downloaded installers live in the **drop zone** (`~/Downloads`, or
`$XDG_DOWNLOAD_DIR`). The project cwd is a repo. A bare glob
(`cursor-*.AppImage`) searches **cwd only** and will miss Downloads.

## Linux (this desk)

| File | Do |
| --- | --- |
| `*.AppImage` | `chmod +x` that path and run it. Never `dpkg -i`. |
| `*_amd64.deb` | `sudo dpkg -i` that path. |
| `*.dmg` | macOS. Not here. |

Pick the **newest** matching file in the drop zone. Use the **absolute path**.

`/sh` already receives `[DESK]` + matching drop-zone files when the task
sounds like an install. You should do the same on a `?` turn: look in
`~/Downloads` before inventing a command.

The live desk cwd is first-class. `cd` / `ls` / `pwd` (and talk-primary `!`)
move or read that cwd; `/sh` must use it. If `[DESK]` has a Last listing,
those files are what the user just confirmed — use those absolute paths.
