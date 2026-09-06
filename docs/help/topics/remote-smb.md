# SMB / CIFS shares — NAS & Windows over `smb://`

Your NAS, a Windows file share, a Samba box in the closet — to xlii each one is
*just another filesystem*. An SMB connection is set up once with `/remote` (or
`xlii remote` from the shell), and from then on it's browsed at an `smb://`
address with the same panes, verbs, and doorway keys as a local directory. No
SMB-specific browse command exists, by design: browsing is the address layer's
job, `/remote` is the sole connection manager for every wire.

SMB support rides an optional dependency, imported only when an smb connection
actually opens:

```
pip install 'xlii[smb]'
```

## What an SMB connection stores

Beyond the shared host/port/user set, an smb connection carries two fields of
its own:

| field | required | meaning |
| --- | --- | --- |
| `share` | **yes** | the share name — every SMB path lives under one (`\\host\share\…`) |
| `domain` | no | AD domain / workgroup; folded into the login as `DOMAIN\user` |

Paths in addresses are **share-relative**: `smb://nas/movies/list.txt` reaches
`\\nas.local\media\movies\list.txt` when the `nas` connection's share is
`media`. The share is part of the connection, not the address — one connection
per share you care about.

The password never touches config or argv: it is prompted and stored in the
encrypted vault, and addresses only ever name the *connection*.

## Adding a connection

Guided (the recommended path — prompts only for what the smb wire needs:
host, share, domain, user, then the secret):

```
/remote add nas
```

Flag form (scriptable; same fields as flags):

```
xlii remote add nas --host nas.local --protocol smb --share media --domain WORKGROUP --user bob
xlii remote test nas
```

Port defaults to 445; pass `--port` only for a nonstandard mapping. `--domain`
is optional — omit it for standalone NAS boxes and most Samba setups.

## Browsing and using the share

Once added, the connection appears under the `smb://` picker and behaves like
any mounted filesystem:

```
xlii ls smb://nas/
xlii cat smb://nas/notes/todo.txt
xlii cp ./report.pdf smb://nas/inbox/report.pdf
xlii mkdir smb://nas/archive/2026
```

In the REPL/TUI the same addresses work everywhere an address does — panes,
`/attach smb://nas/notes/todo.txt`, publish a built site into a share with
`xlii remote publish ./public smb://nas/www`.

## Trust posture (v1)

SMB support is **intra-LAN / VPN oriented** in this round: credentials ride the
vault and the wire uses your account's normal SMB session, but there is no
signing or encryption *policy knob* — xlii doesn't refuse servers that
negotiate weaker settings. Don't point it at an untrusted network path; put a
VPN under it instead. (A signing/encryption policy is a candidate for a later
round.)

## Troubleshooting

- **"needs the optional 'smbprotocol' package"** — run `pip install 'xlii[smb]'`.
- **"has no share configured"** — the connection was added without `--share`;
  re-add it (the guided `/remote add` always asks).
- **Login failures** — try the `DOMAIN\user` split explicitly via `--domain`,
  or drop `--domain` entirely for local NAS accounts.
- **Wrong files listed** — remember paths are share-relative; `smb://nas/` is
  the root *of the share*, not of the server. One connection per share.

See `/howto remote-smb` for this page in-app, and `/remote` for the manager's
verbs (`list · connect · ls · publish · rm · close`).
