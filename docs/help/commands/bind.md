# /bind

Pin a saved task to a menu row and/or an F-key. The task is the recipe. The
bind is a symlink — chrome only starts `/tasks run <name>`.

Available in the **code** and **chat** REPLs.

## Usage

```
/bind                         # list + usage; face form: /panel bindmake
/bind <task> [menu=project|tools|xlii] [fkey=f11] [label=…]
/bind list
/bind rm <task|f11>
```

**Bind chrome** (Options → Bind chrome, or Tasks pane → Bind chrome…) is a
closed form: available tasks, F-keys with commander defaults, menus. It
seeds `/bind` — send writes the file.

Default menu is **Project**. A bind can have a menu, an F-key, or both.
Reusing an F-key moves it off the previous task.

User chrome lives in `~/.config/xlii/binds.toml`. A project may add rows in
`.xlii/binds.toml` (user F-keys win on a clash). `/bind` writes the user file.

If the task has required params with no default, the bind **seeds**
`/tasks run <task> ` so you fill them. Otherwise it runs with `--yes`.

Commander F1–F10 stay until you rebind them. F11 and F12 are free slots.
`/bind rm f7` gives F7 back to **add**.

Related: `/alias` (slash, not chrome), `/tasks show`, `/describe tasks`.
