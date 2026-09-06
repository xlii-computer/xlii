# /remote

`/remote` manages **named connections to remote hosts** — FTP, FTPS, SFTP,
WebDAV (`dav://`), and SMB (`smb://`); S3 is the planned next round. It is the
*one* connection manager for every wire (`/ftp` still works as a hidden legacy
alias).

## Browse vs. manage — the split that keeps commands few

**Browsing needs no command of its own.** Every configured host is just another
address: `ftp://site/public_html`, `sftp://box/logs/app.log`. The same panes,
`ls`/`cat`/`cp`/`mv`/`rm`/`mkdir`, and attach that work on `file://` work on
remote addresses — one address space for every backend, and **no per-backend
command ever exists** (no sftp command, no webdav command — ever; a per-backend
verb would just multiply identical list/ls/publish spellings).

**Connecting is the only backend-specific job** — a remote host needs a one-time
credential + address setup that a local file doesn't. That setup is what
`/remote` owns.

Each connection is addressed by the scheme matching its **actual wire**:
`ftp`/`ftps → ftp://` · `sftp → sftp://` · `webdav → dav://` · `smb → smb://`.
A bare scheme (`ftp://`) is the picker for that protocol's connections, and
`remote://` is the **union picker** — every connection whatever its wire, each
entry addressed by its honest scheme (it's what the TUI's remote doorway
opens). No scheme ever lies about the wire.

## Setting up a connection

```
/remote add mysite                    ← guided: prompts for the wire's fields
/remote add mysite --host ftp.example.com --user me --protocol ftps
```

The guided form asks for the chosen protocol's fields only — host, port, user,
then per-wire extras (an SSH key path for sftp, TLS options for ftps, a base
URL + auth mode for webdav, a share + domain for smb). **In the full-screen TUI
this is a one-screen wizard docked in the side panel** — pick the protocol and
only its fields render, the transcript stays visible (copy that hostname), Esc
closes it, and on Save the equivalent flag command is echoed for the record
(secret omitted). In the inline REPL it's a question-by-question walk. The
password/passphrase is **always prompted, never a flag**, and goes straight to
the encrypted vault: no secret ever appears in an address, the transcript, or
`config.json`. The flag form is the scriptable path (same as `xlii remote add`
from the shell).

## Day-to-day verbs (identical for every backend)

- `/remote` or `/remote list` — connections with live/idle markers.
- `/remote connect <name>` — open the wire now (it's lazy otherwise).
- `/remote ls <name>/<path>` — quick listing (sugar for `ls <scheme>://…`).
- `/remote publish <local-dir> <scheme>://<name>/<docroot>` — mirror a built
  site up; unchanged files (by size) are skipped.
- `/remote rm <name>` — drop the connection *and* its vault secret.
- `/remote close [<name>]` — hang up (all connections when no name).

## Notes

- **Attach works on remote files**: on a text leaf like
  `ftp://site/public_html/index.html`, attach rides the *actually-deployed*
  page into the turn — "fix the nav on my site" reads the real file.
- SFTP host-key policy is trust-on-first-use in v1; transfers are blocking with
  timeouts (a dead host fails fast).
- An sftp connection saved before the honest-scheme split may live in history as
  `ftp://<name>/…` — old addresses still resolve (names are shared across remote
  schemes); everything xlii prints now uses the honest scheme.
