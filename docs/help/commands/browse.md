# /browse

Git-aware orientation for *this* repo. `/browse` answers the questions you ask at
the start of a session — "what kind of project is this, what changed, and which
part do I mean?" — from local `git` and the filesystem alone. No GitHub/GitLab
API: a self-hosted `git@forge:…` remote is as first-class as a github.com URL.

It is the project-anchored complement to `/upload`. Where `/upload` is the OS-wide
picker for arbitrary files (a screenshot on the Desktop, a PDF in Downloads),
`/browse` stays inside the project root: it maps the repo, shows what's dirty, and
can attach in-repo paths to the locker without you hand-typing them.

`/browse` is headless-first — every view prints to the terminal and works over
SSH. It reads the same ignore rules (`.gitignore` + `.xliiignore` + defaults) that
the agent's tools and `/sync` use, so `node_modules/`, `.venv/`, and build dirs
never clutter the view.

## Usage

```
/browse [--tree [path] | --changed | --staged | --diff [path] | <path> --attach|--reference|--edit | --popup | --refresh-profile]
```

Bare `/browse` prints the **skeleton**: the detected ecosystem(s), branch + change
count + ahead/behind pulse, remote URL, and the project's semantic zones (entry,
config, source, tests, tooling, generated) with a file count and a `●` badge on
zones that contain uncommitted changes. Polyglot/monorepo repos also get a
`packages` line listing sub-directory roots that carry their own ecosystem markers.

- `--tree [path]` — directory tree from `path` (default: project root) with git
  status badges on changed files; ignored dirs are pruned, large trees truncated.
- `--changed` — just the working-tree dirty files, each with its status letter.
- `--staged` — files with staged (index) changes.
- `--diff [path]` — read-only `git diff --stat` of unstaged changes (optionally
  scoped to a path); never stages, commits, or pushes.
- `<path> … --attach` — stage in-repo file(s) into the locker (jailed to the
  project root; paths that escape the root are refused). Same sink as `/upload`.
- `<path> … --reference` — queue project-relative path(s) into your next prompt's
  editable buffer (so you can mention them to the agent without retyping).
- `<path> --edit` — open the file in `$EDITOR` (jailed; via the shared editor
  handoff, the same one `/edit` and `!!nvim` use).
- `--popup` — open the tkinter file picker (a subprocess, like `/upload`); pick
  files + an action, and the choice flows back through the same sinks. Headless →
  falls back to the views above.
- `--refresh-profile` — re-scan marker files and rewrite the cached fingerprint at
  `.xlii/project-profile.json`.

The three actions (attach / reference / edit) share one jailed sink, so the popup
and the headless flags can never diverge.

## Examples

Orient at the start of a session, then drill into the source tree:

```
/browse
/browse --tree xlii
```

See what you've touched, then attach a changed file to talk about it:

```
/browse --changed
/browse xlii/repl_cmds/browse.py --attach
```

## Gotchas

- The skeleton caches a project fingerprint at `.xlii/project-profile.json` on
  first view. If you add a new ecosystem marker (e.g. a `package.json`), run
  `/browse --refresh-profile` to pick it up.
- File counts and the tree honor ignore rules, so a zone like `generated` lists
  its dirs without counts — they're collapsed baggage, not tracked source.
- `/browse` lives in the code REPL (it needs a project root); it is not available
  in `chat`.
- It orients and picks — it does not stage, commit, or push. For a full git
  workflow, hand off to a terminal tool like lazygit (`!!lazygit`).

See also: `/upload` (OS-wide file staging), `/locker` (inspect what's staged),
`/status` (session + project state). Exact flags: `/describe browse`. Deep dive:
`/howto sessions-projects`.
