# /lock

`/lock` shuts this Face's input. Serve stays up. Mojo stays. It is **not**
`/kill` and not public-serve idle-reap.

Unlock is the **overlay** on this glass (you cannot type `/unlock` into a dead
box — except the overlay's own field). Stolen phone cannot hostage the desk:
unlock here, or reboot.

## Levels (`~/.config/xlii/face.toml` `[glass]`)

| Place | Unlock |
|-------|--------|
| `home` | just the verb |
| `cafe` | TOTP (same secret as `/xsu`) or `[glass] pin` |
| `black` | same as café, plus opaque overlay |

Face TOTP fail counter is **not** the daemon elevation lockout.

Idle auto-lock uses `[glass] idle_s` (0 = off).

Occupancy: when `me@` has the mouth, this Face accepts **lock** and **unlock**
only.

See also: `/remote-control`, `/unlock`.
