---
sources: file://xlii/addressing/__init__.py, file://xlii/addressing/builtins/register.py, file://xlii/addressing/builtins/git.py#L37, file://xlii/addressing/_shell_arg.py, file://xlii/addressing/_content_type.py, file://xlii/addressing/builtins/wiki.py, file://xlii/addressing/builtins/remote.py, file://xlii/addressing/builtins/home.py, file://xlii/addressing/builtins/xlii_root.py
verified: false
---

# addressing-and-vfs

Everything in xlii is an address: `scheme://target[?k=v&...][#anchor]`. One resolver
(`xlii.addressing.resolve`) parses the string and dispatches to the provider registered
for its `scheme`. The load-bearing idea is **a scheme is a provider** — kernel-rebuild
vectors V1 (resolver core) + V2 (VFS surface) + V3 (write side). Addresses are the currency
between panes, attachment, retrieval, and turns: panes mint addresses, sinks consume them.

## Address grammar
`Address.parse` splits on the first `://` (scheme-less tokens follow the bare-token rule
below), then peels a `#anchor` fragment, then `?query`. Two derived accessors drive
name-keyed schemes: `Address.key` is the first `/`-segment of the target (the entity id),
`Address.subpath` is the remainder (the path inside it). A scheme-less token is sniffed:
path-like (contains `/`, starts with `.`/`~`, or exists on disk) → `file`, else the
surface's `default_scheme`.

## The provider protocol (three capability tiers)
- `Provider` — answers `resolve(Address) -> Resolution` (`ok`, `handle`, `path`, `kind`,
  `matches`, `reason`). Every scheme implements this.
- `VfsProvider` (adds `stat`/`list`/`read`/`exists`) — its addresses are **browseable**;
  `supports_vfs(scheme)` gates whether a pane offers navigation.
- `WritableVfs` (adds `write`/`delete`/`mkdir`) — its leaves can be **written**;
  `supports_write(scheme)` gates edits, and this is what the `cp`/`mv` fs tools drive.
Capability is by `isinstance` against the runtime-checkable Protocols — a provider that only
answers `resolve()` stays valid; browse/write are opt-in, never assumed.

## Registered schemes (24, `builtins/register.py`)
Writable VFS: `file://` (paths, abs/rel/`~`), `wiki://`, and the remote family
`ftp://` `sftp://` `dav://` `smb://` `remote://` (one `RemoteFsProvider` class, one shared
connection registry; `remote://` is the union picker, the others filter by honest protocol).
Browseable read-only VFS: `conv://` (a project's `.xlii/turns/`, with a synthetic
`__inflight__.md` live leaf), `config://` (a config JSON as a tree), `docs://`, `skills://`,
`mark://` (bookmarked turn spans), `jobs://` (session background jobs), `plan://`
(`.xlii/plans/*.md`), `tasks://` (`.xlii/tasks/*.toml`), `locker://` (the Tray — files staged to ride the turn), `artifacts://` (the gallery of what xlii made),
`git://`, `gigwork://` (providers + jam presets), `map://` (the repo map, computed on
every read — never cached), `xwiki://` (xlii's shipped self-wiki — the read-only vendor
counterpart of `wiki://`, same provider class over the bundled root, writes refused),
`xlii://` (the kernel mounted as its own VFS root — the addresser
addressing itself), and `home://` (the Dock chassis: a directory of panel surfaces). Two
resolve-only schemes carry path/name sniffing: `project://` and `persona://`.

Read-only schemes reach the live turn store / registry / project through the ambient session
(`xlii.active_session`), so a chip or F-key doorway resolves with no session in hand; outside
a session or project they list empty rather than raising (the graceful-empty contract).

## #anchor — the precision unit
The fragment after `#` is a span *within* the target. `wiki://page#section` reads just that
heading's span (the anchor-honoring read that makes a wiki section a precise provenance
unit); `file://notes.md#L42-51` a line range; `conv://nick/turn#mark:thesis` a marked span.
Providers that understand spans honour the anchor in `vfs_read`; providers that don't ignore
it. It is the primitive precise marks are built on.

## to_shell_arg — the export seam (inverse of resolve)
`resolve` turns a token into an address; `to_shell_arg` turns an address into something a
shell command or foreign body can consume. The outcome is **declared by the provider** via
`shell_export(Address) -> ShellExport`, never inferred, with three kinds: `path` (a real
file/dir; rendered cwd-relative when inside cwd, else absolute), `address` (exists only inside
xlii — a jobs root, a panel hub), `content` (real bytes with no file behind them — a diff, a
mark's span; materialized to a caller-owned temp snapshot that never round-trips). A `#L42`
line anchor renders as `path:NN`, the grep/editor dialect. Shell quoting is *not* owned here —
`ShellArg.quoted()` delegates to `xlii.tasks.substitute`, the single `shlex.quote` in the tree
(see [[tasks-and-pipes]]).

## Content-type facet
`classify(Node) -> type` answers "what kind of thing?" (`image`/`doc`/`dir`/`file`/…),
distinct from `Node.kind` which answers "container or leaf?". It is a small registry —
resolution order: explicit `node.extra["type"]` wins, then registered predicates, then the
structural default. One source of truth feeds both view-renderer choice and tab-strip
bucketing (`[image 5]`).

## Known gap
`GitProvider._KEYS = ("", "diff", "staged", "log")` — there is no `stash` key, so
`git://stash/<n>` never resolves (no explicit TODO in the tree; a silent omission, open
follow-up). Every other git doorway (changed set, per-file diff/staged, commit log) is minted.
See [[gitpain]].

## Related
The kernel-as-VFS-root idea and pane mounting live in [[kernel-architecture]] and
[[panes-and-dock]]; `home://` is the Dock hub. Scheme-specific pages: [[gitpain]] (`git://`),
[[xliiwiki]] (`wiki://` write side — a body edit keeps `sources:` but resets `verified`, per
[[trust-and-gates]]), [[gigwork-and-foreign-brains]] (`gigwork://`), [[plan-surface]]
(`plan://`), and the remote family (`ftp/sftp/dav/smb/remote`). Mutations through these
schemes are review-before-run via the [[command-surface]].
