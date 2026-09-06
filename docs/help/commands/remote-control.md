# /remote-control

Opens **this glass** to `me@`. While the sitting is open and unlocked, the
phone talks to **Mojo on this body**. No `?` prefix — `what is this
project?` is talk. File work: Mojo hires a **lab** worker with write/bash
on the current project.

Two ways in, same sitting:

- **Face:** `/remote-control open` on this desk. No `/xsu`.
- **Phone:** `/xsu <code>` then `/remote-control` (bare = open), same as
  `webcode` after `/xsu`. The daemon runs it.

Tailnet door (preferred when the phone is on Tailscale): Face on loopback
grows a `POST /remote-turn` listener on the tailnet IP while the sitting is
open. Allowlist devices in `face.toml`:

```toml
[tailnet]
allowed_devices = ["phone"]
```

`/remote-control open --for phone` binds the sitting to that node — other
allowlisted devices 403. Loopback `?token=` and XMPP ignore `--for`.

`/remote-control open --glass [--for phone]` opens the **glass tier** on
that listener: the phone, after spending a webcode, gets the desk's own
HTML face (WhoIs on every request, CSP on every response). Bare `open`
stays door-only (`POST /remote-turn`). Drop tears the port down either way.

**Glass ceremony (on-tailnet phone):** desk `/remote-control open --glass
--for phone` → mint a webcode (DM `webcode`, or the desk REPL) → open
`http://<magicdns>:<port>/` → enter the code. First paint is fullscreen
dual (stream + bookmarks). Thumb bar: **mark** (tags the last turn),
**panes** (toggle the dual split — bookmarks + git read-only), **lock**
(sitting lock — the phone can lock, never unlock). Talk is elevated Mojo;
`[$]` is refused. Five silent minutes autolocks, same as the door.

Idle **5 minutes** with no activity autolocks — Face-minted or phone-minted.
`/xsu` itself also lapses after 5 minutes of no inbound from that sender.

```
/remote-control open
/remote-control open --for phone
/remote-control open --glass --for phone
/remote-control lock | unlock
/remote-control drop      # hang up. Mojo remains; lab hire gone.
/remote-control status
```

From Conversations, DM **this body's JID** (throne Face: `throne@`; a node
or VM with `xlii daemon`: that daemon JID — one account, not a `*desk`
twin). Bare lines are Mojo. File work: Mojo hires `$` lab on this glass.

Opening sitting pings **me@** with whoami and that `$` is live.

**Live only.** A DM sent while Face is down, or before sitting is open, is
**discarded** — never a backlog that runs later. Resend while sitting is open.

`/kill` still means the **daemon process**. Kill disables systemd restart so
it is not a blink.

`/mute me` drops the rider JID from `daemon.toml` allowlist (Prosody disable
is operator). `/unmute <jid>` puts one back.

See also: `/lock`, `/kill`.
