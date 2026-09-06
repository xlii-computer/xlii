# The first hour — golden path

> **Goal:** in about 60 minutes, a cold operator goes from “I have an xAI key” to
> “this substrate is mine” — without reading the full HOWTO encyclopedia.  
> **Recovery mantra:** when anything breaks → `xlii doctor` (then
> `xlii doctor --online` if network is suspected).  
> **XMPP / multi-machine fabric:** not required. Optional later ([HOWTO §13](HOWTO.md)).

Long form: [HOWTO.md](HOWTO.md). In-session: `/howto first-session` or bare `/howto`.

---

## 0:00 — Install, setup, doctor green

```bash
# venv + install (from a clone), or: pip install xlii
export XAI_MANAGEMENT_API_KEY=xai-...   # never written to disk
xlii setup
xlii doctor                             # must look healthy before continuing
```

If doctor complains, fix what it prints — do not skip this step.

**Start at Home, then enter a folder when you mean to.** Home is never-sync —
roam the machine (`/sh`, explain, `/ops`). A folder you make is the project.
`[M]` is mojo (talk). `[$]` is the lab. `xlii chat` sits with iXaac (a
costume, not a `/role`), not a third home.

```bash
xlii scratch --tui          # Home / roam (or omit --tui; --tauri for the face)
# when ready for a real tree:
mkdir -p ~/xlii-hour && cd ~/xlii-hour
xlii init --local --snapshot    # this folder becomes a room; no Collection
# or: xlii code --init
```

---

## 0:10 — Home or a folder: talk, lab, status, help

```bash
xlii scratch                # Home. Or: xlii code  (in a folder)
```

Inside:

| You type | What it does |
|----------|----------------|
| `[M]` / talk | mojo — the journal |
| `[$]` / lab | shell, `/sh`, `/ops`, edits (memory stays in this room) |
| `ls` | Bare line in the lab = **shell** |
| `? what is this?` | Ask the lab worker |
| `/status` | **mode · trust · surface** first |
| `/help` | **Daily** slash kit. More: `/help compose` · `/help power` · `/help all` |

You should feel: Home is the building. A folder is a room. Talk and lab are
postures, not extra homes.

---

## 0:20 — One small plan

```text
/plan
```

Then ask for a **tiny**, safe change (e.g. add a one-line comment or a README
sentence). When the numbered plan looks right:

```text
/execute
```

(or `/cancel` to abort). Smoke-check with shell (`ls`, `git diff`, a quick test
if you already have one). This is the verification habit in miniature — not a
full rail/loop yet (`/help power` when you want those).

---

## 0:35 — One stock plugin

```bash
# once per machine, if stock plugins are not installed yet:
xlii plugin --install-stock
```

In the REPL:

```text
/plugin subscribe open-meteo
/get weather in Berlin
```

(or `/plugin` to browse). You just composed a capability without writing glue code.

---

## 0:45 — Talk without the desk (optional)

```bash
xlii chat                       # iXaac — chat costume, no desk — not a third home
# or: xlii chat --new hour-bot  # another room (chat/hour-bot)
```

Inside:

```text
/persona                        # list; current marked
/help                           # daily kit on the chat surface
/attach doc <name>              # if you have a doc; or create: xlii doc --new hour-notes
```

Optional: `/loadout` to see what the persona carries. Stock skill example (code
project): `/skill grounded-analysis` when you want structured investigation later.

---

## 0:55 — Switch without losing threads

From chat:

```text
/code
```

From code:

```text
/chat
```

Each surface keeps its own detached thread. Confirm with a short `?` on each side.

---

## 1:00 — Export (no secrets) — “this is mine”

```bash
xlii export ~/xlii-hour-backup
```

Export omits secrets by design. That tree is your portable curation seed
(personas/docs/plugins registry pointers — see export docs). Restore later with
`xlii import` when you need a second machine.

```bash
xlii doctor                     # end the hour the same way you started
```

---

## When stuck (always)

1. `xlii doctor`  
2. `/howto` or `/howto first-session`  
3. `/describe <command>`  
4. `/help all` or `xlii help`  

Do **not** require XMPP, TUI, loop, or swarm for the first hour.

---

## Stock demo pack (what this path actually uses)

| Piece | Id / name | Role in the hour |
|-------|-----------|------------------|
| Mobile journal | setup default (**mojo**) | `[M]` / `/mojo` |
| Chat costume | **iXaac** | `xlii chat` / `/chat` |
| Roles | `code-architect` · `debugger` · … | `/role` |
| Plugin | **open-meteo** | `/get` weather |
| Skill (optional) | **grounded-analysis** | deeper research later |
| Knowledge verb | **`/attach`** | durable docs/refs (aliases `/doc`, …) |

---

## Checklist (print / tick)

- [ ] `xlii setup` + `xlii doctor` green  
- [ ] Project via `--local` (or full init if you want Collections)  
- [ ] Shell + `?` + `/status` + `/help`  
- [ ] `/plan` → `/execute` once  
- [ ] Stock plugin + `/get` once  
- [ ] `xlii chat` + `/persona`  
- [ ] `/code` ↔ `/chat`  
- [ ] `xlii export …`  
- [ ] Doctor again  

Done. Deeper material: [HOWTO.md](HOWTO.md) · [LEGACY.md](LEGACY.md) · [REFERENCE.md](REFERENCE.md).
