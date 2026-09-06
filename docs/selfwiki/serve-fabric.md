---
sources: file://xlii/serve_public.py, file://xlii/serve_gate.py, file://xlii/serve_spool.py, file://xlii/cmds/serve_web.py, file://xlii/cmds/serve_ws.py, file://xlii/cmds/serve_admin.py, file://xlii/ws_server.py#L1-33, file://xlii/daemon_gate.py#L305-372, file://xlii/config.py#L174-181, file://docs/GUIDE.md#L744-860, file://pyproject.toml#L38-45
verified: false
---
# serve-fabric

The browser/remote **body** family: ways to reach one running xlii substrate from something other than the local terminal. Three shipped surfaces, all layered on the unmodified Textual TUI ([[panes-and-dock]]). It serves the one-substrate/any-body thesis.

## Three surfaces

- **`xlii serve`** (proposals/serve-web.md W1) — the TUI in a browser tab. `textual-serve` wraps a small aiohttp server that spawns `xlii code --tui` as a subprocess per tab and pipes its terminal over a WebSocket to xterm.js. The app is unmodified; the browser *becomes* the terminal, so every TUI change ships to the browser free. One subprocess per tab. `cmds/serve_web.py`.
- **`xlii serve --ws`** (browser-ui W2) — a JSON event protocol over a stdlib WebSocket instead of textual-serve, for embedding (Tauri sidecar via `--handshake`: bind an ephemeral port, print one JSON handshake line on stdout, exit when stdin closes). Token auth is mandatory (auto-generated if omitted); the turn engine is `run_headless_turn`; `--yolo` skips per-intent gates. The RFC6455 codec/accept loop lives in the kernel at `xlii/ws_server.py` (godzilla-mothra B5); `cmds/serve_ws.py` is a thin façade. Inbound frames are capped at 8 MiB (this is a remote-execution channel).
- **`xlii serve --public`** (serve-public P0) — code-gated public REPL. The internet-facing body.

**No auth by default.** Plain `xlii serve` has NO AUTHENTICATION — the port is a shell on the machine (bare REPL input runs in the project shell). Default bind `127.0.0.1`; to reach it from another device bind a tailnet interface address, never `0.0.0.0`. The loud startup banner says so.

## The public gate (serve-public)

`--public` is a different threat model (vector PRs #278–#282). The process still binds loopback only — `public_bind_error` refuses wildcard/non-loopback binds; **Caddy owns TLS and the public face**. `PublicServer` subclasses `textual_serve.server.Server` and installs a grant middleware: `GET /` is a static enter-code form until a grant cookie exists (GET never consumes a code — messenger-prefetch safe), `POST /pair` consumes a pairing code exactly once and sets an httpOnly/Secure/SameSite=Strict cookie, and `/ws` + `/download/*` require a live grant. Pairing codes are Crockford base32 minus confusables (I/L/O/U), 8 chars ≈40 bits, shown grouped `X7K2-M9Q4` (`serve_gate.py`). Lockout: 5 fails / 300 s → 300 s locked, per client. A background sweep (every 15 s) drains new grants, applies queued revokes, reaps idle/TTL sessions, and refreshes the session mirror. Session cap (default 3) is enforced both before and after `consume` to close a two-codes-racing overflow.

The gate/spool logic (`serve_gate.py`) is pure and time-injected — no `[web]` extra, no live server needed to unit-test (sibling of `daemon_gate.py`). See [[trust-and-gates]].

## State-dir handshake

Three files under one canonical state dir (`serve_spool.default_state_dir` → `journal_daemon.runtime_dir()`, overridable via `XLII_STATE_DIR`) couple the daemon, the CLI, and the serve process:

- **`serve-grants.json`** — the grant spool. Producers (fabric-daemon mint, `xlii serve mint`, `webcode`) append pending codes; serve drains and owns them from pairing on (D6). RMW under `flock` — three writers share it.
- **`serve-sessions.json`** — the session mirror. Serve writes it; `serve sessions` and `webcode ls` read it.
- **`serve-revokes.json`** — the revoke queue. `xlii serve revoke <sid|all>` and `webcode kill` append; serve drains on its sweep and closes the sockets (upstream tears down the PTY when the socket closes). Revoke is **queue-only** — the admin side never edits the mirror serve owns.

## webcode — phone verb

The fabric daemon exposes `webcode` (serve-public P1, PR #281) as a built-in verb like `kill`, riding the daemon whitelist + device pinning + rate limiter, plus a stricter per-JID mint window (3 mints / 5 min — each mint is a shell grant). Actions: `mint` / `preview` / `ls` / `kill`. A successful mint returns a pinned OMEMO line — `code: X7K2-M9Q4  ·  <base_url>  ·  expires in 5m`. Preview mode spawns `xlii code --preview --tui` (read-only); full mode `xlii code --tui`. `xlii serve mint|sessions|revoke` is on-box CLI parity (PR #280). See [[command-surface]].

## Hardening round (#283–#287)

Owner-filed field issues, fixed in two commits:
- **#283** — one canonical state dir + queue-only revoke (a state-dir mismatch was silent: codes just never arrived).
- **#284** — serve actually drains the revoke queue on its sweep tick.
- **#285** — per-client lockout: `X-Forwarded-For` honored only when the immediate peer is loopback (our own proxy hop) and only the **rightmost** entry (leftmost is client-forgeable); else all of the internet collapses into one lockout bucket and every audit line reads `127.0.0.1`.
- **#286** — the served command (full vs preview) resolves per request-task from a `ContextVar`, not by mutating shared `self.command`; the old swap let one preview handshake escalate a concurrent session to full (privilege confusion).
- **#287** — the CSRF `Origin`/`Referer` check runs *before* any gate accounting, so a cross-site auto-submit can neither pair the victim silently nor lock them out.
- **cookie ≠ sid** — the grant cookie carries a random token mapped to the sid server-side; the sid is a visible admin handle (`serve sessions` / mirror / audit) and must not double as the bearer credential.

## Config + deferred

`--public` requires `[serve.public]` in `~/.config/xlii/config.json` (JSON, nested `serve.public`): `base_url` is **required, no default**; `code_ttl_s` 300, `session_ttl_s` 28800 (8 h cap), `idle_timeout_s` 1800 (30 min grant reap), `face_linger_s` 1800 (backend linger at zero views; `0` = immediate reap), `face_attach_existing` false, `max_sessions` 3 (`config.py`, docs/GUIDE.md). `--base-url` overrides the file for one run. The `[web]` extra pins `textual-serve>=1.1` plus the `[tui]` deps (serving without the TUI is meaningless). The owner connected remotely on 2026-07-17 — the testbed milestone. **P2 attach** (a paired tab resumes an existing fabric session) is shipped for the public Face path: desk-keyed backend, `face_linger_s`, and grant re-attach while the backend lingers.

Related: [[trust-and-gates]], [[command-surface]], [[kernel-architecture]].
