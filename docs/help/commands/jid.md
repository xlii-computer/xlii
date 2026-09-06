# jid (CLI)

Mint **house XMPP addresses** from the throne. You create JIDs. A node does
not self-enroll. In-band signup stays off.

```
xlii jid house --domain home.xlii-remote.com --admin-remote xliiec2
xlii jid add me --role me
xlii jid add throne --role throne
xlii jid add daemon --role daemon
xlii jid add node1 --role node --node node1
xlii jid add --role node --node node1          # → node1@ (one JID per body)
xlii jid ls
xlii jid show node1
```

`--admin-remote` is an `xlii remote` name that can `sudo -n prosodyctl register`
on the Prosody host (owned house, e.g. xlii-remote.com). Empty admin remote:
the command is printed for your own server. Passwords go in the vault
(`xlii:xmpp`), never `config.json`.

`--adopt` records an account that already exists (the three machines we
stood up by hand). Face glass is `{node}desk@`, never `daemon@`.

Face: **Xlii → XMPP addresses…** (`jidmake://`). Install node seeds
`xlii jid add` for both mouths, then `xlii node setup`.
