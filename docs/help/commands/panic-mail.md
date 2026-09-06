# Panic mail

Email is **not** mojo and not `$`. It is the deny door when you have no keyboard.

Three gates — all must pass, else **silent drop** (no bounce):

1. Known From (`[panic] from` in `daemon.toml`, or `$XLII_PANIC_FROM`).
2. DKIM **and** SPF pass on `Authentication-Results`.
3. Subject is one of **three owner phrases** (`xlii daemon panic-phrases`).

Kill is one mail: body names `kill` (this daemon). Destroy needs **round 2**
(TOTP by default, same fob as `/xsu`). Round 2 is a second mail; the challenge
does not repeat the phrases.

Wake: daemon and Face call `check_on_wake` on start. A powered-off stolen
laptop that never runs xlii again is FDE's job.

See `/destroy-all`, `/lock`, `xlii daemon panic-check`.
