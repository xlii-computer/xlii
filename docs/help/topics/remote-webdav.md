# WebDAV remotes — Nextcloud, ownCloud & friends over `dav://`

WebDAV is the file protocol most self-hosted clouds already speak: **Nextcloud
and ownCloud** expose every account's files over it, and it's also the door
into Hetzner Storage Boxes, Fastmail file storage, many NAS boxes, and plain
Apache/nginx DAV shares. If a host gives you an `https://…` files URL, xlii can
mount it: a WebDAV share is *just another filesystem*, browsed at `dav://`
addresses with the same panes and verbs as a local directory.

It needs the optional extra once per install:

```
pip install 'xlii[webdav]'
```

(Everything else in xlii works without it; only opening a webdav connection
asks for the package, with this exact hint.)

## Adding a connection

Guided (recommended — it asks only for the WebDAV fields):

```
/remote add mycloud
```

Flag form (scriptable, same fields; also `xlii remote add` from the shell):

```
xlii remote add mycloud --protocol webdav \
  --base-url https://cloud.example.com/remote.php/dav/files/alice \
  --user alice
xlii remote test mycloud
```

- `--base-url` is the server's DAV root **including any path** — for Nextcloud
  that is `https://<host>/remote.php/dav/files/<username>`, for ownCloud
  `https://<host>/remote.php/webdav`. If the share lives at the host root you
  can give `--host` instead and the URL becomes `https://<host>`.
- `--auth` picks the credential style: `basic` (the default — user + the
  prompted password), `digest`, or `bearer` — with `bearer` the secret you
  type at the prompt is sent as an `Authorization: Bearer` token (the shape
  many API-fronted DAV services use).
- The password/token is **always prompted, never a flag**, and lands in the
  encrypted vault — it never appears in an address, the transcript, or
  `config.json`. Tip for Nextcloud: use an *app password* (Settings →
  Security), not your account password.

## The https rule

A WebDAV credential rides inside every request, so the wire must be encrypted:
**a plain `http://` base URL is refused** unless you add the connection with
`--insecure`. That flag is the explicit escape hatch for intra-LAN boxes and
self-signed certs — it both allows `http://` and skips TLS certificate
verification for `https://`. Default posture: https, certificate verified.

## Browsing — no webdav command exists

Setup is the only WebDAV-specific act. After that the connection is an address
like any other (the browse-vs-manage split — see the `/remote` help page):

```
xlii ls dav://mycloud/
xlii cat dav://mycloud/notes/todo.md
xlii cp report.pdf dav://mycloud/inbox/report.pdf
xlii remote publish ./site mycloud/public
```

`dav://` on its own is the picker for your WebDAV connections (`remote://`
lists every connection, whatever the wire). Panes, attach, `cp`/`mv`/`rm` — the
whole address layer works on `dav://` exactly as on `file://` or `sftp://`.

One protocol note: WebDAV's native delete on a folder is recursive, but xlii
keeps the uniform rule — a non-recursive remove refuses a non-empty directory;
recursive delete is always the explicit variant. No surprise wipes.
