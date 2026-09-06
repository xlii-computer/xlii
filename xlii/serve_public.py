"""``xlii serve --public`` — code-gated textual-serve (serve-public P0 / V2).

Subclass of ``textual_serve.server.Server`` per D2 RESOLVED: middleware requires
a live grant cookie on the WebSocket (and download) routes; ``GET /`` is the
static enter-code form until a grant exists; ``POST /pair`` consumes a pairing
code and sets an httpOnly / Secure / SameSite=Strict cookie. Publicness is
Caddy's job — this process still binds loopback.

Imports the V1 gate/spool contract (``serve_gate`` / ``serve_spool``). The
``[web]`` import is deferred so this module stays importable without the extra.
"""

from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass, field
import html
import json
import secrets
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Callable, Optional
from urllib.parse import urlsplit

from xlii.atomicio import write_text_atomic
from xlii.serve_gate import group_code
from xlii.serve_spool import default_state_dir  # noqa: F401 — canonical accessor (#283)

if TYPE_CHECKING:
    from xlii.serve_gate import GateStore, Session

# Cookie carrying a random grant token (NOT the session id — the sid is a
# visible admin handle in `serve sessions` / `webcode ls` / the mirror / audit,
# and must not double as the bearer credential). Flags pinned in tests.
GRANT_COOKIE = "xlii_serve_grant"

# Shown when a magic-link ?code= is spent or the enter-code page is restored
# from bfcache after pair. Soft "need a fresh webcode" — not a live form.
ENTRY_SPENT_MSG = "This pairing code is spent. Ask for a fresh webcode."

# Keep pairing HTML out of bfcache so Android Back cannot resurrect a
# prefilled spent form. pageshow still handles browsers that restore anyway.
_ENTRY_NO_STORE = {"Cache-Control": "no-store"}

# The session bound to the current WebSocket request-task. aiohttp runs each
# request in its own asyncio Task and ContextVars copy per-Task, so this is
# per-connection state — never mutate server attributes per request (the old
# `self.command` swap around an await let one preview handshake turn a
# concurrent full session into a preview or vice versa: privilege confusion).
_WS_SESSION: "contextvars.ContextVar[Optional[Session]]" = contextvars.ContextVar(
    "xlii_serve_ws_session", default=None
)


@dataclass
class _FaceBackend:
    """One shared face subprocess for the desk (P2 attach: many grants, many views).

    ``sockets`` is the source of truth for live browser views. ``pending``
    counts leases held between acquire and socket registration so a concurrent
    release cannot reap the child mid-connect (``views = len(sockets)+pending``).
    ``grants`` tracks live pairing grants attached to this backend.
    """

    proc: Any
    port: int
    token: str
    sockets: set[Any] = field(default_factory=set)
    pending: int = 0
    grants: set[str] = field(default_factory=set)
    idle_since: float | None = None
    linger_task: asyncio.Task | None = None
    session_id: str = ""
    pid: int = 0
    owned: bool = True
    attached: bool = False
    last_activity_touch: float = 0.0
    upstream_ready: asyncio.Event | None = None
    upstream_ws: Any = None
    upstream_client: Any = None
    upstream_task: asyncio.Task | None = None
    pumps_running: bool = False
    wire_cache: list[str] = field(default_factory=list)

DEFAULT_CODE_TTL_S = 300
DEFAULT_SESSION_TTL_S = 28800
DEFAULT_IDLE_TIMEOUT_S = 1800
DEFAULT_MAX_SESSIONS = 3
# Browser tabs are views over one backend; bound fan-out so a single grant
# cannot open unbounded proxy tasks / upstream sockets.
DEFAULT_MAX_FACE_VIEWS_PER_BACKEND = 8
DEFAULT_SWEEP_INTERVAL_S = 15.0

SESSIONS_MIRROR_NAME = "serve-sessions.json"
DEFAULT_AUDIT_NAME = "serve-audit.log"

_ENTRY_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>xlii — enter code</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
          font: 16px/1.4 ui-sans-serif, system-ui, sans-serif;
          background: #0e1116; color: #e6edf3; }}
  main {{ width: min(22rem, 92vw); }}
  h1 {{ font-size: 1.1rem; font-weight: 600; margin: 0 0 .75rem; }}
  p {{ margin: 0 0 1rem; color: #8b949e; font-size: .9rem; }}
  label {{ display: block; font-size: .8rem; color: #8b949e; margin-bottom: .35rem; }}
  input[type=text] {{ width: 100%; box-sizing: border-box; padding: .65rem .75rem;
                      font: inherit; letter-spacing: .08em; text-transform: uppercase;
                      border: 1px solid #30363d; border-radius: 6px;
                      background: #161b22; color: #e6edf3; }}
  button {{ margin-top: .85rem; width: 100%; padding: .65rem;
            font: inherit; font-weight: 600; cursor: pointer;
            border: 0; border-radius: 6px; background: #238636; color: #fff; }}
  .err {{ color: #f85149; margin: 0 0 .85rem; font-size: .9rem; }}
</style>
</head>
<body>
<main>
  <h1>xlii</h1>
  <p>Enter the pairing code from your phone. Nothing is installed on this machine.</p>
  {error}
  <form method="post" action="/pair" autocomplete="off">
    <label for="code">Pairing code</label>
    <input id="code" name="code" type="text" inputmode="text"
           autocapitalize="characters" autocomplete="one-time-code"
           placeholder="X7K2-M9Q4" value="{code}" required autofocus/>
    <button type="submit">Pair</button>
  </form>
</main>
</body>
</html>
"""

# Appended after .format() so JS braces never collide with the template.
# pageshow / bfcache: jump to /face/ (live grant stays sitting; dead grant
# 303s home). fetch + location.replace on pair so the enter-code GET is
# not left under /face/ in history.
_ENTRY_HISTORY_JS = r"""
<script>
(function () {
  var SPENT = __SPENT__;
  var FLAG = "xlii-entry-spent";

  function showSpent() {
    var input = document.getElementById("code");
    if (input) {
      input.value = "";
      input.removeAttribute("value");
    }
    var form = document.querySelector("form");
    var err = document.querySelector(".err");
    if (!err && form) {
      err = document.createElement("p");
      err.className = "err";
      form.parentNode.insertBefore(err, form);
    }
    if (err) err.textContent = SPENT;
  }

  try {
    if (sessionStorage.getItem(FLAG) === "1") {
      sessionStorage.removeItem(FLAG);
      showSpent();
    }
  } catch (e) {}

  window.addEventListener("pageshow", function (e) {
    var nav = (performance.getEntriesByType && performance.getEntriesByType("navigation")[0]) || {};
    if (!e.persisted && nav.type !== "back_forward") return;
    try {
      if (sessionStorage.getItem("xlii-face-leave") === "1") {
        sessionStorage.removeItem("xlii-face-leave");
        showSpent();
        return;
      }
    } catch (err) {}
    try { sessionStorage.setItem(FLAG, "1"); } catch (err) {}
    location.replace("/face/");
  });

  var form = document.querySelector("form");
  if (!form || !window.fetch) return;
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var body = new URLSearchParams();
    var input = form.querySelector("[name=code]");
    body.set("code", input ? input.value : "");
    fetch("/pair", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
      credentials: "same-origin",
      redirect: "follow"
    }).then(function (res) {
      if (res.redirected && res.url) {
        location.replace(res.url);
        return;
      }
      if (res.ok) {
        location.replace("/face/");
        return;
      }
      return res.text().then(function (doc) {
        document.open();
        document.write(doc || "");
        document.close();
      });
    }).catch(function () {
      HTMLFormElement.prototype.submit.call(form);
    });
  });
})();
</script>
""".replace("__SPENT__", json.dumps(ENTRY_SPENT_MSG))


def entry_html(*, error: str | None = None, code: str | None = None) -> str:
    """Render the enter-code page. Pure — tests pin GET-never-consumes via this.

    ``code`` prefills the input (the magic-link ``?code=`` path). Escaped here
    so the function stays safe for arbitrary input, though the caller only
    passes codes a live peek validated.
    """
    err = f'<p class="err">{error}</p>' if error else ""
    page = _ENTRY_HTML.format(error=err, code=html.escape(code or "", quote=True))
    return page.replace("</body>", _ENTRY_HISTORY_JS + "</body>", 1)


def entry_response(
    *,
    error: str | None = None,
    code: str | None = None,
    status: int = 200,
) -> Any:
    """HTML enter-code response with no-store so bfcache cannot resurrect it."""
    from aiohttp import web

    return web.Response(
        text=entry_html(error=error, code=code),
        content_type="text/html",
        charset="utf-8",
        status=status,
        headers=_ENTRY_NO_STORE,
    )


def public_banner_lines(
    *,
    base_url: str,
    host: str,
    port: int,
    audit_log: Path | str,
) -> list[str]:
    """Loud public-mode banner. Pure so tests can pin the wording offline."""
    return [
        f"serve --public: {base_url}  →  code-gated entry; no terminal stream without a grant",
        f"serve --public: binding {host}:{port} (loopback only — TLS/public face is Caddy's job)",
        f"serve --public: audit log → {audit_log}",
        "serve --public: grants are mortal (idle / TTL / revoke); "
        "the face lingers face_linger_s",
        "serve --public: Ctrl-C to stop.",
    ]


def public_bind_error(host: str) -> str | None:
    """Return a startup error if ``host`` is illegal in ``--public`` mode (D3)."""
    h = (host or "").strip().lower()
    if h in {"0.0.0.0", "::", ""}:
        return (
            "serve --public: refusing wildcard bind "
            f"{host!r} — public mode always binds loopback; put Caddy in front"
        )
    if h not in {"127.0.0.1", "localhost", "::1"}:
        return (
            f"serve --public: refusing non-loopback bind {host!r} — "
            "public mode always binds 127.0.0.1 (Caddy owns the public face)"
        )
    return None


def resolve_public_settings(args: Any = None) -> dict[str, Any]:
    """Gather ``[serve.public]`` knobs via getattr-with-default (V3 owns accessors).

    CLI ``--base-url`` (when present on ``args``) wins over config so V2 is
    testable before V3 merges.
    """
    cfg = None
    try:
        from xlii.config import GlobalConfig
        cfg = GlobalConfig.load()
    except Exception:
        cfg = None

    def _get(name: str, default: Any) -> Any:
        if args is not None:
            cli = getattr(args, name, None)
            if cli is not None and cli != "":
                return cli
        if cfg is not None:
            return getattr(cfg, f"serve_public_{name}", default)
        return default

    return {
        "base_url": _get("base_url", None),
        "code_ttl_s": int(_get("code_ttl_s", DEFAULT_CODE_TTL_S)),
        "session_ttl_s": int(_get("session_ttl_s", DEFAULT_SESSION_TTL_S)),
        "idle_timeout_s": int(_get("idle_timeout_s", DEFAULT_IDLE_TIMEOUT_S)),
        "max_sessions": int(_get("max_sessions", DEFAULT_MAX_SESSIONS)),
        "closed_door": bool(_get("closed_door", False)),
        "redirect_off_url": _get("redirect_off_url", None),
        "face_default": bool(_get("face_default", True)),
        "face_linger_s": int(
            _get("face_linger_s", int(_get("idle_timeout_s", DEFAULT_IDLE_TIMEOUT_S)))
        ),
        "face_attach_existing": bool(_get("face_attach_existing", False)),
    }


_LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


def client_addr(request: Any) -> str:
    """The real client address for lockout accounting and audit attribution.

    Behind the documented Caddy front every socket peer is loopback, so keying
    on ``request.remote`` alone collapses all of the internet into one lockout
    bucket (an attacker's 5 bad codes lock everyone out) and stamps every audit
    line ``127.0.0.1``. Honor ``X-Forwarded-For`` ONLY when the immediate peer
    is loopback (our own proxy hop); take the RIGHTMOST entry — that is the
    address our proxy itself saw, while leftmost entries are client-supplied
    and trivially forged."""
    peer = request.remote or ""
    if peer in _LOOPBACK_PEERS:
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            last = fwd.split(",")[-1].strip()
            if last:
                return last
    return peer


def _origin_host(url: str) -> "tuple[str, str, Optional[int]]":
    parts = urlsplit(url.strip())
    port = parts.port
    if port is None:
        port = {"https": 443, "http": 80}.get(parts.scheme)
    return (parts.scheme.lower(), (parts.hostname or "").lower(), port)


def origin_mismatch(request: Any, base_url: str) -> bool:
    """True when a browser-shaped POST comes from a foreign origin (login CSRF).

    The attack is the POST itself — a malicious page auto-submits a code the
    attacker minted, silently pairing the victim's browser to the attacker's
    session — so ``SameSite`` on our cookie is no defense. Reject when
    ``Origin`` (or ``Referer``) is present and disagrees with ``base_url``;
    header-less clients (curl, tests) pass, they have no browser to ride."""
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if not origin:
        return False
    try:
        return _origin_host(origin) != _origin_host(base_url)
    except ValueError:
        return True


class _TouchingWS:
    """Wrap a WebSocketResponse so real input frames mark session activity.

    Only ``stdin`` envelopes count — the browser client pings on a timer, and
    touching on pings would make ``idle_timeout_s`` unreachable for an open
    tab, exactly the walked-away-borrowed-browser case the timeout exists for.
    Everything else forwards to the wrapped socket untouched."""

    def __init__(self, ws: Any, on_input: Callable[[], None]) -> None:
        self._xlii_ws = ws
        self._xlii_on_input = on_input

    def __aiter__(self):
        inner = self._xlii_ws.__aiter__()
        on_input = self._xlii_on_input

        async def _gen():
            async for message in inner:
                data = getattr(message, "data", None)
                if isinstance(data, str) and data.startswith('["stdin"'):
                    on_input()
                yield message

        return _gen()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._xlii_ws, name)


_FACE_PING_MAX_BYTES = 256

_FACE_BACKEND_ACTIVITY_TYPES = frozenset({
    "assistant_chunk", "tool_started", "tool_finished", "busy_state",
})


def _face_backend_frame_is_activity(data: str) -> bool:
    try:
        msg = json.loads(data)
    except (TypeError, ValueError):
        return False
    if not isinstance(msg, dict):
        return False
    return msg.get("type") in _FACE_BACKEND_ACTIVITY_TYPES


def _face_client_message_is_activity(data: str) -> bool:
    """True for user actions on the face wire; keepalive pings stay idle-neutral.

    Only small frames are parsed: a ``ping`` envelope is a few dozen bytes, so
    anything bigger (an ``upload`` carrying base64, say) is activity by size
    alone and never has to be decoded just to read its ``type``."""
    if len(data) > _FACE_PING_MAX_BYTES:
        return True
    try:
        msg = json.loads(data)
    except (TypeError, ValueError):
        return False
    if not isinstance(msg, dict):
        return False
    return msg.get("type") in {
        "input",
        "turn",
        "upload",
        "confirm",
        "cancel",
        "set_posture",
    }


def default_audit_log(state_dir: Path | None = None) -> Path:
    root = state_dir if state_dir is not None else default_state_dir()
    return root / DEFAULT_AUDIT_NAME


def _append_audit(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


def _mirror_sessions(state_dir: Path, sessions: list) -> None:
    """Write the live-session mirror V3/V5 read for ``sessions`` / ``webcode ls``."""
    payload = {
        "version": 1,
        "sessions": [
            {
                "id": s.id,
                "paired_at": s.paired_at,
                "last_activity": s.last_activity,
                "mode": s.mode,
                "remote": s.remote,
            }
            for s in sessions
        ],
    }
    write_text_atomic(
        state_dir / SESSIONS_MIRROR_NAME,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )


def _set_grant_cookie(resp: Any, sid: str, *, max_age: int) -> None:
    """Pin cookie flags: httpOnly, Secure, SameSite=Strict."""
    resp.set_cookie(
        GRANT_COOKIE,
        sid,
        max_age=max_age,
        httponly=True,
        secure=True,
        samesite="Strict",
        path="/",
    )


def _install_reaping_app_service() -> type:
    """Replace textual-serve's ``AppService`` with one that actually reaps.

    Upstream (textual-serve 1.1.3, ``app_service.py:187``) stops a child by
    sending a ``quit`` meta down its stdin and then awaiting the reader task
    with **no timeout and no kill**::

        await self.send_meta({"type": "quit"})
        await self._task

    A child that cannot service that message — blocked in a synchronous
    inference call or a tool's ``bash`` step — never exits, so ``stop()``
    blocks forever. And ``stop()`` is called from the ``finally`` in
    ``handle_websocket`` (``server.py:341``/``:351``), so the cleanup path *is*
    the hang: one leaked process (20–133 MB) **and** one leaked handler task
    per connection. On 2026-07-24 that filled a 928 MB box and all 2 GB of its
    swap with 20+ orphans, some 18 hours old, until it could no longer fork
    sshd. ``max_sessions`` did not help: it counts *grants*, and the orphans
    had outlived their own grants.

    Two defects to fix, not one:

    1. **No escalation.** Bound the cooperative wait, then SIGTERM, then
       SIGKILL.
    2. **The handle points at the wrong process.** ``create_subprocess_shell``
       returns a handle on ``/bin/sh``, which forks the real app — signalling
       it alone orphans the child to init. So spawn into a new session and
       signal the whole process *group*.

    Upstream constructs ``AppService`` by direct name reference inside
    ``handle_websocket``, so there is no factory seam to override; rebinding
    the name in ``textual_serve.server`` is the least invasive hook. Idempotent
    — returns the installed class.
    """
    import os
    import signal
    import subprocess

    from textual_serve import server as _ts_server
    from textual_serve.app_service import AppService

    installed = getattr(_ts_server, "_xlii_reaping_app_service", None)
    if installed is not None:
        return installed

    class ReapingAppService(AppService):  # type: ignore[misc, valid-type]
        """``AppService`` whose ``stop()`` is guaranteed to terminate."""

        # Cooperative first: a healthy app quits well under a second. The
        # grace only has to cover a child that is *between* blocking calls.
        quit_grace_s = 5.0
        signal_grace_s = 3.0

        async def _open_app_process(self, width: int = 80, height: int = 24):
            """Upstream's spawn plus ``start_new_session`` — the child leads its
            own process group, so ``killpg`` reaches the app and not just the
            shell that forked it."""
            environment = self._build_environment(width=width, height=height)
            self._process = process = await asyncio.create_subprocess_shell(
                self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
                start_new_session=True,
            )
            assert process.stdin is not None
            self._stdin = process.stdin
            # Latch the pgid now, while the leader is certainly alive. Read
            # lazily it would be unavailable exactly when it matters most —
            # after the shell has exited but its children have not.
            can_signal_group = hasattr(os, "getpgid") and hasattr(os, "killpg")
            if can_signal_group:
                try:
                    self._pgid = os.getpgid(process.pid)
                except OSError:
                    self._pgid = None
            else:
                self._pgid = None
            return process

        def _group_alive(self) -> bool:
            """True while ANY member of the child's process group survives.

            The tracked handle is ``/bin/sh``, which forks the real app. The
            shell dies to a plain SIGTERM while an app ignoring signals does
            not, so 'the handle exited' says nothing about whether the work is
            still running — this is what must gate the escalation."""
            pgid = getattr(self, "_pgid", None)
            if pgid is None:
                proc = getattr(self, "_process", None)
                return proc is not None and proc.returncode is None
            try:
                os.killpg(pgid, 0)
            except (ProcessLookupError, PermissionError, OSError):
                return False
            return True

        @staticmethod
        def _kill_tree(proc) -> None:
            """Force-kill ``proc`` and, on Windows, its descendants.

            POSIX reaping uses ``killpg``; without a job object the portable
            tree-kill on Windows is ``taskkill /T``. Falls back to
            ``proc.kill()`` which maps to TerminateProcess on the handle."""
            if os.name == "nt" and getattr(proc, "pid", None):
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                    )
                    return
                except OSError:
                    # taskkill missing or refused — fall through to proc.kill().
                    pass
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                # The child already exited — nothing left to kill.
                pass

        def _signal_group(self, sig: int | None = None, *, force: bool = False) -> None:
            """Signal the child's whole process group.

            Deliberately does NOT skip when the tracked handle has exited: the
            shell dying is the common case in which the app is still resident
            and still needs signalling. Falls back to the direct handle only
            when there is no group to aim at.

            Callers must not read ``signal.SIGKILL`` bare — it is missing on
            Windows. Pass ``force=True`` for the last-resort path instead.
            """
            pgid = getattr(self, "_pgid", None)
            sigkill = getattr(signal, "SIGKILL", None)
            if force and sig is None:
                # POSIX SIGKILL is 9; Windows has neither the name nor killpg.
                sig = sigkill if sigkill is not None else (9 if pgid is not None else None)
            if pgid is not None and sig is not None:
                try:
                    os.killpg(pgid, sig)
                    return
                except (ProcessLookupError, PermissionError, OSError):
                    # No such group, or not ours to signal — fall through to
                    # terminating the process directly.
                    pass
            proc = getattr(self, "_process", None)
            if proc is None or proc.returncode is not None:
                return
            if force or (sigkill is not None and sig == sigkill):
                self._kill_tree(proc)
                return
            try:
                proc.terminate()
            except (ProcessLookupError, OSError):
                # The child already exited.
                pass

        def _force_kill_group(self) -> None:
            """Last-resort kill. Never references ``signal.SIGKILL`` bare."""
            self._signal_group(getattr(signal, "SIGKILL", None), force=True)

        @staticmethod
        async def _settled(task, timeout: float) -> bool:
            """True when ``task`` finished within ``timeout``. Shielded so a
            timeout does not cancel the reader mid-drain — we want it still
            running to observe the child dying after the signal."""
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except asyncio.TimeoutError:
                return False
            except Exception:
                return True  # raised == finished
            return True

        async def _reap(self) -> None:
            """Run the full quit → TERM → KILL sequence once.

            ``_task`` stays set until reaping finishes so a cancelled waiter
            cannot make a retried ``stop()`` believe there is nothing to do.
            """
            task = self._task
            if task is None:
                return
            try:
                loop = asyncio.get_running_loop()
                quit_deadline = loop.time() + self.quit_grace_s
                try:
                    await self._download_manager.cancel_app_downloads(
                        app_service_id=self.app_service_id
                    )
                except Exception:
                    # Cancelling downloads is cleanup — the reap proceeds and
                    # the quit deadline still applies.
                    pass
                try:
                    remaining = max(0.0, quit_deadline - loop.time())
                    await asyncio.wait_for(
                        self.send_meta({"type": "quit"}), remaining
                    )
                except Exception:
                    pass  # stdin already gone — go straight to signalling

                cooperative_grace = max(0.0, quit_deadline - loop.time())
                settled = await self._settled(task, cooperative_grace)
                # Both conditions matter. The task settling means the SHELL
                # exited; the group being empty means the APP did too. Return
                # on the handle alone and a signal-ignoring child orphans to
                # init — the original leak, reintroduced one layer down.
                if settled and not self._group_alive():
                    return

                self._signal_group(getattr(signal, "SIGTERM", 15))
                settled = await self._settled(task, self.signal_grace_s)
                if settled and not self._group_alive():
                    return

                self._force_kill_group()

                if not await self._settled(task, self.signal_grace_s):
                    # Force-kill cannot be caught, so the process is gone; only
                    # the reader is still hung (a wedged pipe). Drop it
                    # explicitly rather than leaking the task the way upstream
                    # does.
                    task.cancel()
            finally:
                # Only mark shutdown complete after reaping finishes.
                self._task = None

        async def stop(self) -> None:
            """Ask, then insist. Never blocks indefinitely.

            Concurrent and retried callers share one in-flight reap task.
            The work itself is shielded so a cancelled first ``stop()`` cannot
            abandon a live process group while a later ``stop()`` no-ops.
            """
            inflight = getattr(self, "_stop_task", None)
            if inflight is None:
                if self._task is None:
                    return
                inflight = asyncio.get_running_loop().create_task(self._reap())
                self._stop_task = inflight
            try:
                await asyncio.shield(inflight)
            finally:
                if inflight.done() and getattr(self, "_stop_task", None) is inflight:
                    self._stop_task = None

    _ts_server.AppService = ReapingAppService
    _ts_server._xlii_reaping_app_service = ReapingAppService
    return ReapingAppService


def make_public_server(
    command: str,
    *,
    gate: "GateStore",
    base_url: str,
    preview_command: str | None = None,
    state_dir: Path | None = None,
    idle_timeout_s: int = DEFAULT_IDLE_TIMEOUT_S,
    session_ttl_s: int = DEFAULT_SESSION_TTL_S,
    code_ttl_s: int = DEFAULT_CODE_TTL_S,
    max_sessions: int = DEFAULT_MAX_SESSIONS,
    audit_log: Path | None = None,
    sweep_interval_s: float = DEFAULT_SWEEP_INTERVAL_S,
    host: str = "127.0.0.1",
    port: int = 8042,
    title: str = "xlii",
    public_url: str | None = None,
    now_fn: Callable[[], float] | None = None,
    closed_door: bool = False,
    redirect_off_url: str | None = None,
    face_default: bool = True,
    face_linger_s: int | None = None,
    face_attach_existing: bool = False,
) -> Any:
    """Build a grant-gated ``textual_serve.server.Server`` subclass instance.

    Raises ``ImportError`` if the ``[web]`` extra is missing, ``ValueError``
    when ``closed_door`` is on without a ``redirect_off_url``.
    """
    if closed_door and not (redirect_off_url or "").strip():
        from xlii.config import MISSING_SERVE_PUBLIC_REDIRECT_OFF_URL

        raise ValueError(MISSING_SERVE_PUBLIC_REDIRECT_OFF_URL)

    from textual_serve.server import Server
    from aiohttp import web
    import aiohttp_jinja2
    import jinja2

    # Upstream never force-kills a child; without this every dropped
    # connection leaks a process. See the docstring for the 2026-07-24 wedge.
    _install_reaping_app_service()

    state = Path(state_dir) if state_dir is not None else default_state_dir()
    audit = Path(audit_log) if audit_log is not None else default_audit_log(state)
    preview_cmd = preview_command or command
    clock = now_fn or time.time
    linger_s = (
        int(face_linger_s)
        if face_linger_s is not None
        else int(idle_timeout_s)
    )

    class PublicServer(Server):
        """Grant-gated textual-serve (D2 option 1 — middleware on Server._make_app)."""

        def __init__(self) -> None:
            super().__init__(
                command,
                host=host,
                port=port,
                title=title,
                public_url=public_url or base_url.rstrip("/"),
            )
            self.gate = gate
            self.base_url = base_url.rstrip("/")
            self.preview_command = preview_cmd
            self.state_dir = state
            self.idle_timeout_s = idle_timeout_s
            self.session_ttl_s = session_ttl_s
            self.code_ttl_s = code_ttl_s
            self.max_sessions = max_sessions
            self.audit_log = audit
            self.sweep_interval_s = sweep_interval_s
            self.closed_door = closed_door
            self.redirect_off_url = (redirect_off_url or "").strip()
            self.face_default = face_default
            self.face_linger_s = linger_s
            self.face_attach_existing = face_attach_existing
            # One desk may drive several grants → a set of live sockets per sid,
            # registered while the connection is being SERVED (see
            # _process_messages) so revoke/sweep can actually close them.
            self._session_ws: dict[str, set[web.WebSocketResponse]] = {}
            # Face backends: one `xlii serve --face` subprocess per desk (P2
            # attach). The face backend itself treats browser sockets as views
            # over a single live REPL session; spawning one child per tab would
            # create independent writer sessions for the same project.
            # `_face_spawner` is the injectable seam for tests: an async
            # callable returning (proc, port, token).
            self._face_backend: _FaceBackend | None = None
            self._face_lock = asyncio.Lock()
            self._face_spawner = None
            # Cookie token → sid. The bearer credential is minted here and
            # never written to the mirror / audit / ls surfaces.
            self._cookie_tokens: dict[str, str] = {}
            self._sweep_task: asyncio.Task | None = None
            self._now_fn = clock

        # `self.command` is what upstream hands the spawned process — resolve
        # it per-connection from the request-task's session instead of mutating
        # shared server state (#286: the old swap let concurrent handshakes
        # steal each other's mode, including preview → full escalation).
        @property
        def command(self) -> str:
            session = _WS_SESSION.get()
            if session is not None and getattr(session, "mode", None) == "preview":
                return getattr(self, "preview_command", self._base_command)
            return self._base_command

        @command.setter
        def command(self, value: str) -> None:  # Server.__init__ assigns through here
            self._base_command = value

        def _write_mirror(self) -> None:
            try:
                _mirror_sessions(self.state_dir, self.gate.sessions())
            except OSError:
                # The on-disk mirror is for inspection only; the gate holds the
                # authoritative session list in memory.
                pass

        def _audit(self, msg: str) -> None:
            try:
                _append_audit(self.audit_log, f"{self._now_fn():.3f} {msg}")
            except OSError:
                # An unwritable audit log must not take the service down.
                pass

        def _note_face_end(self, sid: str, reason: str, extra: str = "") -> None:
            """Leave a receipt the daemon can query (not a live wire)."""
            try:
                from xlii.face_receipt import write_face_receipt

                write_face_receipt(
                    reason=reason,
                    grant=sid,
                    extra=extra,
                    state_dir=self.state_dir,
                    now=self._now_fn(),
                )
            except (ImportError, OSError):
                # Best-effort receipt; optional module or state-dir I/O must not fail revoke/idle.
                pass

        def _live(self, sid: str | None) -> Optional["Session"]:
            if not sid:
                return None
            for s in self.gate.sessions():
                if s.id == sid:
                    return s
            return None

        def _live_from_request(self, request: web.Request) -> Optional["Session"]:
            """Resolve the grant cookie (a random token) to its live session."""
            token = request.cookies.get(GRANT_COOKIE)
            if not token:
                return None
            return self._live(self._cookie_tokens.get(token))

        def _forget_session(self, sid: str) -> None:
            """Drop the cookie tokens of a dead session (post revoke/sweep)."""
            self._cookie_tokens = {
                t: s for t, s in self._cookie_tokens.items() if s != sid
            }

        def _drain_spool(self) -> None:
            try:
                from xlii.serve_spool import drain_pending
            except ImportError:
                return
            try:
                pending = drain_pending(self.state_dir)
            except OSError:
                return
            now = self._now_fn()
            for item in pending:
                try:
                    self.gate.add_pending(
                        item["code"],
                        ttl_s=int(item["ttl_s"]),
                        mode=str(item["mode"]),
                        now=float(item.get("minted_at", now)),
                    )
                except (KeyError, TypeError, ValueError):
                    continue

        def _drain_revoke_queue(self) -> list[str]:
            """Take queued revoke targets (sids / the ``"all"`` sentinel) from
            `xlii serve revoke` and the fabric's ``webcode kill`` (#284)."""
            try:
                from xlii.serve_spool import drain_revokes
            except ImportError:
                return []
            try:
                return drain_revokes(self.state_dir)
            except OSError:
                return []

        async def _close_session_ws(self, sid: str, *, code: int, message: bytes) -> None:
            for ws in list(self._session_ws.pop(sid, ()) or ()):
                if not ws.closed:
                    try:
                        await ws.close(code=code, message=message)
                    except Exception:
                        # The peer is already gone — the close frame is a
                        # formality at this point.
                        pass

        async def _revoke_now(self, sid: str, *, hard_kill_face: bool = False) -> bool:
            """The actual kill: gate, cookie tokens, live sockets (→ upstream
            tears down the PTY subprocess when its socket closes; face
            backends die by stdin-close, their handshake-contract backstop)."""
            died = self.gate.revoke(sid)
            if died:
                self._audit(f"session_revoked sid={sid}")
                self._note_face_end(sid, "revoke")
                self._forget_session(sid)
                await self._close_session_ws(sid, code=4001, message=b"revoked")
                await self._detach_grant_from_backend(
                    sid, reason="revoke", hard_kill=hard_kill_face,
                )
            return died

        async def _sweep_tick(self) -> None:
            """One sweep: drain grants, apply queued revokes, reap idle/TTL."""
            self._drain_spool()

            targets = self._drain_revoke_queue()
            if targets:
                live = [s.id for s in self.gate.sessions()]
                victims = live if "all" in targets else [t for t in targets if t in live]
                hard_kill_faces = "all" in targets
                for sid in victims:
                    await self._revoke_now(sid, hard_kill_face=hard_kill_faces)

            now = self._now_fn()
            sessions_before = {s.id: s for s in self.gate.sessions()}
            dead = self.gate.sweep(
                now=now,
                idle_timeout_s=self.idle_timeout_s,
                session_ttl_s=self.session_ttl_s,
            )
            for sid in dead:
                session = sessions_before.get(sid)
                if session is not None:
                    age = now - session.paired_at
                    reason = "ttl" if age >= self.session_ttl_s else "idle"
                else:
                    reason = "idle"
                self._audit(f"session_expired sid={sid}")
                self._note_face_end(sid, reason)
                self._forget_session(sid)
                await self._close_session_ws(sid, code=4000, message=b"session expired")
                await self._detach_grant_from_backend(sid, reason=reason)

            # Refresh every tick (not only on deaths): the mirror is what
            # `serve sessions` / `webcode ls` show, and last_activity moves.
            self._write_mirror()
            await self._check_face_linger_expiry()

        async def _sweep_loop(self) -> None:
            while True:
                await asyncio.sleep(self.sweep_interval_s)
                try:
                    await self._sweep_tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # One bad sweep must not kill the loop; the next tick retries.
                    pass

        async def _on_startup_gate(self, app: web.Application) -> None:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self._loop = asyncio.get_running_loop()
            self._drain_spool()
            self._write_mirror()
            self._sweep_task = asyncio.create_task(self._sweep_loop())

        async def _on_shutdown_gate(self, app: web.Application) -> None:
            if self._sweep_task is not None:
                self._sweep_task.cancel()
                try:
                    await self._sweep_task
                except asyncio.CancelledError:
                    # Expected: we cancelled it on the line above.
                    pass
                self._sweep_task = None
            async with self._face_lock:
                backend = self._face_backend
                self._face_backend = None
            if backend is not None:
                await self._reap_face_backend(backend, reason="shutdown", sid="")

        def _make_grant_middleware(self):
            @web.middleware
            async def grant_middleware(request: web.Request, handler):
                path = request.path
                if path == "/pair" or path.startswith("/static"):
                    return await handler(request)
                if path == "/" and request.method in {"GET", "HEAD"}:
                    return await handler(request)

                if (
                    path in ("/ws", "/face/ws")
                    or path.startswith("/download/")
                    or path == "/face"
                    or path.startswith("/face/")
                ):
                    session = self._live_from_request(request)
                    if session is None:
                        if path in ("/face", "/face/"):
                            raise web.HTTPSeeOther("/")
                        # path is user-controlled (percent-decoded) — !r keeps
                        # embedded newlines from forging audit lines.
                        self._audit(
                            f"ws_rejected remote={client_addr(request)!r} path={path!r}"
                        )
                        raise web.HTTPUnauthorized(
                            text="pairing required — open / and enter a code",
                        )
                    request["serve_session_id"] = session.id
                    request["serve_session"] = session
                    self.gate.touch(session.id, now=self._now_fn())
                    if (
                        getattr(session, "mode", None) == "preview"
                        and (path == "/face" or path.startswith("/face/"))
                    ):
                        self._audit(f"face_preview_refused sid={session.id}")
                        raise web.HTTPForbidden(text="face is unavailable for preview grants")
                    return await handler(request)

                if self._live_from_request(request) is None:
                    raise web.HTTPUnauthorized(text="pairing required")
                return await handler(request)

            return grant_middleware

        async def _make_app(self) -> web.Application:
            app = web.Application(middlewares=[self._make_grant_middleware()])
            aiohttp_jinja2.setup(
                app, loader=jinja2.FileSystemLoader(str(self.templates_path))
            )
            app.router.add_get("/", self.handle_entry_or_app, name="index")
            app.router.add_post("/pair", self.handle_pair, name="pair")
            app.router.add_get("/ws", self.handle_websocket, name="websocket")
            app.router.add_get(
                "/download/{key}", self.handle_download, name="download"
            )
            # The face (tauri-face V4): same static build the desktop app
            # renders, behind the same grant wall. Explicit routes BEFORE the
            # static mount so /face/ws resolves to the proxy, not a file.
            app.router.add_get("/face", self.handle_face_redirect, name="face")
            app.router.add_get("/face/", self.handle_face_index, name="face_index")
            app.router.add_get("/face/ws", self.handle_face_ws, name="face_ws")
            face_assets = self._face_assets_dir()
            if face_assets is not None:
                app.router.add_static("/face", face_assets, name="face_static")
            app.router.add_static(
                "/static", self.statics_path, show_index=True, name="static"
            )
            app.on_startup.append(self.on_startup)
            app.on_startup.append(self._on_startup_gate)
            app.on_shutdown.append(self.on_shutdown)
            app.on_shutdown.append(self._on_shutdown_gate)
            return app

        async def handle_entry_or_app(self, request: web.Request):
            """GET / — Face with a grant (TUI if ``face_default`` is off);
            enter-code form without one, prefilled from a live ``?code=``;
            closed-door 302s everyone else away.

            GET never consumes a pairing code (messenger prefetch safe) — the
            ``?code=`` probe is a read-only :meth:`GateStore.peek`.
            """
            session = self._live_from_request(request)
            if session is not None:
                if self.face_default and getattr(session, "mode", None) != "preview":
                    # Default landing is the face. ``face_default = false``
                    # keeps the xterm.js TUI at GET /.
                    raise web.HTTPFound("/face/")
                return await self.handle_index(request)
            raw = request.query.get("code", "")
            if raw:
                if self.gate.peek(raw, now=self._now_fn()):
                    return entry_response(code=group_code(raw))
                if self.closed_door:
                    # Spent / bogus ?code= stays a silent off-redirect —
                    # closed_door is surface reduction, not a soft-re-pair form.
                    raise web.HTTPFound(self.redirect_off_url)
                # Live public door, spent magic link: never look like an
                # open sitting with the used code still in the box.
                return entry_response(error=ENTRY_SPENT_MSG)
            if self.closed_door:
                # No live grant, no live code: send the visitor off without
                # explaining what lives here. Surface reduction — the grant
                # cookie stays the real lock.
                raise web.HTTPFound(self.redirect_off_url)
            return entry_response()

        async def handle_pair(self, request: web.Request) -> web.StreamResponse:
            """POST /pair — consume code exactly once; set grant cookie or re-render."""
            remote = client_addr(request)
            now = self._now_fn()

            # CSRF first, before ANY gate accounting (#287): a cross-site POST
            # rides the victim's browser, so even registering it as a failed
            # attempt would let a hostile page lock the victim out.
            if origin_mismatch(request, self.base_url):
                self._audit(f"pair_cross_origin remote={remote!r}")
                return entry_response(
                    error="Cross-origin request refused.", status=403,
                )

            if self.gate.is_locked(remote, now=now):
                self._audit(f"pair_locked remote={remote!r}")
                return entry_response(
                    error="Too many attempts — try again later.", status=429,
                )

            if len(self.gate.sessions()) >= self.max_sessions:
                self._audit(f"pair_full remote={remote!r}")
                return entry_response(
                    error="Session limit reached — revoke one first.", status=503,
                )

            data = await request.post()
            raw = str(data.get("code", "") or "")
            session = self.gate.consume(raw, now=now, remote=remote)
            if session is None:
                self._audit(f"pair_fail remote={remote!r}")
                locked = self.gate.is_locked(remote, now=self._now_fn())
                err = (
                    "Too many attempts — try again later."
                    if locked
                    else "Invalid or expired code."
                )
                return entry_response(error=err, status=403)

            # Re-check the cap AFTER consume: two valid codes racing past the
            # pre-check (both awaiting request.post) could exceed max_sessions;
            # this post-consume check runs sync on the loop, so exactly one of
            # them sees the overflow and gives its slot back.
            if len(self.gate.sessions()) > self.max_sessions:
                self.gate.revoke(session.id)
                self._audit(f"pair_full remote={remote!r}")
                return entry_response(
                    error="Session limit reached — revoke one first.", status=503,
                )

            self._audit(
                f"pair_ok sid={session.id} mode={session.mode} remote={remote!r}"
            )
            self._write_mirror()
            # The cookie carries a random token mapped to the sid server-side —
            # the sid itself is shown by `serve sessions` / `webcode ls` and
            # must not be a usable bearer credential.
            token = secrets.token_urlsafe(24)
            self._cookie_tokens[token] = session.id
            landing = (
                "/face/"
                if self.face_default and getattr(session, "mode", None) != "preview"
                else "/"
            )
            resp = web.HTTPSeeOther(landing)
            _set_grant_cookie(resp, token, max_age=int(self.session_ttl_s))
            raise resp

        async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
            """WS upgrade — grant already checked by middleware.

            The session rides the request-task's ContextVar: the ``command``
            property picks full/preview from it, and ``_process_messages``
            registers the live socket + touch hook off it. No shared mutation."""
            token = _WS_SESSION.set(request.get("serve_session"))
            try:
                return await super().handle_websocket(request)
            finally:
                _WS_SESSION.reset(token)

        async def _process_messages(self, websocket, app_service) -> None:
            """Serve one connection: register the LIVE socket for revoke/sweep
            (the old code registered it only after upstream returned — i.e.
            after it had already closed, so the kill switch never had anything
            to close), and count real typing as activity so idle_timeout_s
            measures input-idle, not connection age."""
            session = _WS_SESSION.get()
            if session is None:
                return await super()._process_messages(websocket, app_service)

            sid = session.id

            def _on_input() -> None:
                self.gate.touch(sid, now=self._now_fn())

            self._session_ws.setdefault(sid, set()).add(websocket)
            try:
                return await super()._process_messages(
                    _TouchingWS(websocket, _on_input), app_service
                )
            finally:
                live = self._session_ws.get(sid)
                if live is not None:
                    live.discard(websocket)
                    if not live:
                        self._session_ws.pop(sid, None)

        # ------------------------------------------------------------- face

        @staticmethod
        def _face_assets_dir():
            """The bundled face page (None when the install lacks it)."""
            try:
                from xlii.serve_face import default_assets_dir
                d = default_assets_dir()
                return d if d.is_dir() else None
            except Exception:
                return None

        async def _detach_grant_from_backend(
            self, sid: str, *, reason: str, hard_kill: bool = False,
        ) -> None:
            """Remove a dead grant from the desk backend; linger when empty."""
            async with self._face_lock:
                backend = self._face_backend
                if backend is None:
                    return
                backend.grants.discard(sid)
            await self._close_session_ws(sid, code=4000 if reason in ("idle", "ttl") else 4001,
                                          message=b"session ended")
            if hard_kill and not backend.grants:
                reap: _FaceBackend | None = None
                async with self._face_lock:
                    if backend is self._face_backend and not self._face_view_count(backend):
                        self._face_backend = None
                        reap = backend
                if reap is not None:
                    await self._reap_face_backend(reap, reason="revoke", sid=sid)
                    return
            await self._maybe_arm_face_linger(backend)

        @staticmethod
        async def _drain_upstream_task(backend: _FaceBackend) -> None:
            """Cancel or join the upstream pump without surfacing prior failures."""
            task = backend.upstream_task
            if task is None:
                return
            backend.upstream_task = None
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        async def _reap_face_backend(
            self, backend: _FaceBackend, *, reason: str, sid: str,
        ) -> None:
            """Close stdin (owned backends) and audit. Caller clears ``_face_backend``."""
            if backend.linger_task is not None:
                backend.linger_task.cancel()
                backend.linger_task = None
            try:
                await self._drain_upstream_task(backend)
                if backend.upstream_ws is not None:
                    try:
                        await backend.upstream_ws.close()
                    except Exception:
                        pass
                    backend.upstream_ws = None
                if backend.upstream_client is not None:
                    try:
                        await backend.upstream_client.close()
                    except Exception:
                        pass
                    backend.upstream_client = None
            finally:
                pid = backend.pid or getattr(backend.proc, "pid", None) or "?"
                grants = ",".join(sorted(backend.grants)) or "-"
                self._audit(
                    f"face_reaped sid={sid or grants} reason={reason} pid={pid}"
                )
                if backend.owned and not backend.attached:
                    try:
                        if getattr(backend.proc, "stdin", None) is not None:
                            backend.proc.stdin.close()
                    except Exception:
                        pass
                wait = getattr(backend.proc, "wait", None)
                if backend.owned and wait is not None:
                    try:
                        await asyncio.wait_for(wait(), timeout=5)
                    except Exception:
                        try:
                            backend.proc.kill()
                        except Exception:
                            pass

        async def _evict_face_backend(
            self, backend: _FaceBackend, *, reason: str, sid: str,
        ) -> None:
            """Drop a crashed backend from the desk and reap its child."""
            async with self._face_lock:
                if backend is not self._face_backend:
                    return
                self._face_backend = None
            await self._reap_face_backend(backend, reason=reason, sid=sid)

        def _close_face_backend(
            self, backend: _FaceBackend, *, reason: str, sid: str = "",
        ) -> None:
            """Schedule reap for a backend being dropped (sync entry point)."""
            loop = getattr(self, "_loop", None)
            if loop is not None and loop.is_running():
                asyncio.ensure_future(
                    self._reap_face_backend(backend, reason=reason, sid=sid)
                )
            else:
                if backend.owned and not backend.attached:
                    try:
                        if getattr(backend.proc, "stdin", None) is not None:
                            backend.proc.stdin.close()
                    except Exception:
                        pass

        @staticmethod
        def _face_view_count(backend: _FaceBackend) -> int:
            return len(backend.sockets) + max(0, backend.pending)

        def _total_face_views(self) -> int:
            backend = self._face_backend
            return self._face_view_count(backend) if backend is not None else 0

        def _face_view_cap(self) -> int:
            return max(1, self.max_sessions) * DEFAULT_MAX_FACE_VIEWS_PER_BACKEND

        def _refuse_face_view_cap(self, sid: str, reason: str) -> None:
            self._audit(f"face_cap_refused sid={sid} reason={reason}")
            raise web.HTTPServiceUnavailable(text="face session cap reached")

        def _touch_grants_from_backend_frame(self, backend: _FaceBackend, data: str) -> None:
            if not _face_backend_frame_is_activity(data):
                return
            now = self._now_fn()
            touch_gap = min(30.0, self.idle_timeout_s / 4.0)
            if now - backend.last_activity_touch < touch_gap:
                return
            backend.last_activity_touch = now
            for grant_sid in list(backend.grants):
                self.gate.touch(grant_sid, now=now)

        async def _maybe_arm_face_linger(self, backend: _FaceBackend) -> None:
            async with self._face_lock:
                if backend is not self._face_backend:
                    return
                if self._face_view_count(backend):
                    backend.idle_since = None
                    if backend.linger_task is not None:
                        backend.linger_task.cancel()
                        backend.linger_task = None
                    return
                if self.face_linger_s <= 0:
                    self._face_backend = None
                    reap = backend
                else:
                    backend.idle_since = self._now_fn()
                    if backend.linger_task is not None:
                        backend.linger_task.cancel()
                    loop = asyncio.get_running_loop()
                    backend.linger_task = loop.create_task(
                        self._face_linger_timer(backend)
                    )
                    grants = ",".join(sorted(backend.grants)) or "-"
                    self._audit(f"face_linger_armed sid={grants}")
                    return
            await self._reap_face_backend(reap, reason="views0", sid="")

        async def _face_linger_timer(self, backend: _FaceBackend) -> None:
            try:
                await asyncio.sleep(self.face_linger_s)
            except asyncio.CancelledError:
                return
            async with self._face_lock:
                if backend is not self._face_backend:
                    return
                if self._face_view_count(backend):
                    return
                self._face_backend = None
            await self._reap_face_backend(backend, reason="linger", sid="")

        async def _check_face_linger_expiry(self) -> None:
            backend = self._face_backend
            if backend is None or backend.idle_since is None:
                return
            if self.face_linger_s <= 0:
                return
            elapsed = self._now_fn() - float(backend.idle_since)
            if elapsed + 1e-9 < float(self.face_linger_s):
                return
            if self._face_view_count(backend):
                return
            async with self._face_lock:
                if backend is not self._face_backend:
                    return
                if backend.linger_task is not None:
                    backend.linger_task.cancel()
                    try:
                        await backend.linger_task
                    except asyncio.CancelledError:
                        pass
                    backend.linger_task = None
                self._face_backend = None
            await self._reap_face_backend(backend, reason="linger", sid="")

        @staticmethod
        def _face_proc_dead(backend: _FaceBackend) -> bool:
            return getattr(backend.proc, "returncode", None) is not None

        async def _get_face_backend(self, sid: str) -> _FaceBackend:
            evict: _FaceBackend | None = None
            async with self._face_lock:
                backend = self._face_backend
                if backend is not None:
                    if self._face_proc_dead(backend):
                        evict = backend
                        self._face_backend = None
                    else:
                        if self._face_view_count(backend) >= DEFAULT_MAX_FACE_VIEWS_PER_BACKEND:
                            self._refuse_face_view_cap(sid, "per_backend_views")
                        if self._total_face_views() >= self._face_view_cap():
                            self._refuse_face_view_cap(sid, "total_views")
                        backend.grants.add(sid)
                        backend.pending += 1
                        backend.idle_since = None
                        if backend.linger_task is not None:
                            backend.linger_task.cancel()
                            backend.linger_task = None
                        return backend
                if self._total_face_views() >= self._face_view_cap():
                    self._refuse_face_view_cap(sid, "total_views")
            if evict is not None:
                await self._reap_face_backend(evict, reason="crash", sid="")
            spawned = await self._spawn_face_backend(sid)
            async with self._face_lock:
                if self._face_backend is not None:
                    backend = self._face_backend
                    if spawned is not backend:
                        await self._reap_face_backend(spawned, reason="race", sid=sid)
                    if self._face_view_count(backend) >= DEFAULT_MAX_FACE_VIEWS_PER_BACKEND:
                        self._refuse_face_view_cap(sid, "per_backend_views")
                    backend.grants.add(sid)
                    backend.pending += 1
                    backend.idle_since = None
                    if backend.linger_task is not None:
                        backend.linger_task.cancel()
                        backend.linger_task = None
                    return backend
                spawned.grants.add(sid)
                spawned.pending = 1
                self._face_backend = spawned
                return spawned

        async def _restart_face_upstream(self, backend: _FaceBackend) -> None:
            await self._drain_upstream_task(backend)
            backend.upstream_ws = None
            backend.upstream_client = None
            backend.upstream_ready = None
            backend.pumps_running = False
            backend.wire_cache.clear()
            await self._ensure_face_upstream(backend, "")

        async def _replay_wire_cache(
            self, backend: _FaceBackend, ws: web.WebSocketResponse
        ) -> None:
            for frame in list(backend.wire_cache):
                if ws.closed:
                    return
                try:
                    await ws.send_str(frame)
                except Exception:
                    return

        async def _attach_face_socket(
            self, backend: _FaceBackend, ws: web.WebSocketResponse
        ) -> None:
            """Move one pending lease onto a live browser socket."""
            was_empty = False
            async with self._face_lock:
                was_empty = not backend.sockets
                backend.pending = max(0, backend.pending - 1)
                backend.sockets.add(ws)
            if was_empty and backend.pumps_running:
                await self._restart_face_upstream(backend)
            elif backend.wire_cache:
                await self._replay_wire_cache(backend, ws)

        async def _release_face_backend(
            self, sid: str, backend: _FaceBackend, ws: web.WebSocketResponse
        ) -> bool:
            views_after = 0
            async with self._face_lock:
                if self._face_backend is not backend:
                    return False
                if ws in backend.sockets:
                    backend.sockets.discard(ws)
                else:
                    backend.pending = max(0, backend.pending - 1)
                views_after = self._face_view_count(backend)
                grants = ",".join(sorted(backend.grants)) or sid
                self._audit(f"face_view_closed sid={grants} views={views_after}")
            if views_after:
                return False
            await self._maybe_arm_face_linger(backend)
            return False

        async def _ensure_face_upstream(self, backend: _FaceBackend, sid: str) -> None:
            if backend.pumps_running:
                if backend.upstream_ready is not None:
                    await backend.upstream_ready.wait()
                return
            backend.upstream_ready = asyncio.Event()
            backend.pumps_running = True

            import aiohttp

            async def _pump():
                client = aiohttp.ClientSession()
                backend.upstream_client = client
                try:
                    async with client.ws_connect(
                        f"ws://127.0.0.1:{backend.port}/?token={backend.token}"
                    ) as upstream:
                        backend.upstream_ws = upstream
                        backend.upstream_ready.set()

                        async def backend_to_browser():
                            async for msg in upstream:
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    self._touch_grants_from_backend_frame(
                                        backend, msg.data
                                    )
                                    backend.wire_cache.append(msg.data)
                                    if len(backend.wire_cache) > 200:
                                        backend.wire_cache.pop(0)
                                    async with self._face_lock:
                                        socks = list(backend.sockets)
                                    for sock in socks:
                                        if not sock.closed:
                                            try:
                                                await sock.send_str(msg.data)
                                            except Exception:
                                                pass
                                else:
                                    break

                        await backend_to_browser()
                finally:
                    backend.pumps_running = False
                    if backend.upstream_ready is not None:
                        backend.upstream_ready.set()
                    if backend.upstream_client is not None:
                        try:
                            await backend.upstream_client.close()
                        except Exception:
                            pass
                        backend.upstream_client = None
                    backend.upstream_ws = None

            backend.upstream_task = asyncio.create_task(_pump())
            await backend.upstream_ready.wait()

        async def handle_face_redirect(self, request: web.Request):
            # /face → /face/ so the page's RELATIVE asset links (css/…, js/…)
            # resolve under /face/ — the same tree the Tauri webview serves.
            raise web.HTTPFound("/face/")

        async def handle_face_index(self, request: web.Request):
            face_assets = self._face_assets_dir()
            if face_assets is None:
                raise web.HTTPNotFound(text="face assets not installed")
            html = (face_assets / "index.html").read_text(encoding="utf-8")
            # Public /face/ is the phone door (daemon webcode). Inject first
            # paint so CSS does not wait on hello.
            html = html.replace(
                '<html lang="en"',
                '<html lang="en" data-view="phone"',
                1,
            )
            return web.Response(
                text=html,
                content_type="text/html",
                charset="utf-8",
            )

        async def _spawn_face_backend(self, sid: str) -> _FaceBackend:
            """Start or attach to `xlii serve --face --handshake` for the desk."""
            import secrets as _secrets

            session_id = f"face-{_secrets.token_hex(6)}"
            if self._face_spawner is not None:
                proc, port, token = await self._face_spawner()
                attached = False
            elif self.face_attach_existing:
                try:
                    from xlii.face_instance import discover_live

                    rec = discover_live()
                except Exception:
                    rec = None
                if rec is not None:
                    proc = SimpleNamespace(pid=rec.pid, stdin=None)
                    port, token = rec.port, rec.token
                    attached = True
                else:
                    proc, port, token = await self._spawn_face_child()
                    attached = False
            else:
                proc, port, token = await self._spawn_face_child()
                attached = False
            pid = getattr(proc, "pid", None) or 0
            backend = _FaceBackend(
                proc=proc,
                port=port,
                token=token,
                session_id=session_id,
                pid=pid,
                attached=attached,
                owned=not attached,
            )
            self._audit(f"face_spawned sid={sid} pid={pid} port={port}")
            return backend

        async def _spawn_face_child(self):
            """Raw child spawn — returns (proc, port, token)."""
            if self._face_spawner is not None:
                return await self._face_spawner()
            import json as _json
            import sys as _sys
            proc = await asyncio.create_subprocess_exec(
                _sys.executable, "-m", "xlii", "serve", "--face",
                "--handshake", "--view", "phone",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=45)
                hs = _json.loads(line.decode())
                return proc, int(hs["port"]), str(hs["token"])
            except Exception:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                raise

        async def handle_face_ws(self, request: web.Request) -> web.WebSocketResponse:
            """The face proxy: browser sockets ⇄ one shared upstream per desk."""
            import aiohttp

            sid = request["serve_session_id"]
            try:
                backend = await self._get_face_backend(sid)
            except Exception as e:
                if isinstance(e, web.HTTPException):
                    raise
                self._audit(f"face_spawn_failed sid={sid} err={type(e).__name__}")
                self._note_face_end(sid, "crash", extra=type(e).__name__)
                raise web.HTTPBadGateway(text="face backend failed to start")

            ws = web.WebSocketResponse(heartbeat=30)
            prepared = False
            upstream_failed = False
            try:
                await ws.prepare(request)
                prepared = True
                self._session_ws.setdefault(sid, set()).add(ws)
                await self._attach_face_socket(backend, ws)
                try:
                    await self._ensure_face_upstream(backend, sid)
                except Exception:
                    upstream_failed = True
                    raise web.HTTPBadGateway(text="face upstream unavailable")
                upstream = backend.upstream_ws
                if upstream is None:
                    upstream_failed = True
                    raise web.HTTPBadGateway(text="face upstream unavailable")

                async def browser_to_backend():
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            if _face_client_message_is_activity(msg.data):
                                self.gate.touch(sid, now=self._now_fn())
                            await upstream.send_str(msg.data)
                        else:
                            break

                try:
                    await browser_to_backend()
                except Exception:
                    pass  # a dead proxy leg is a normal disconnect, not a fault
            finally:
                if prepared:
                    live = self._session_ws.get(sid)
                    if live is not None:
                        live.discard(ws)
                        if not live:
                            self._session_ws.pop(sid, None)
                if upstream_failed:
                    await self._evict_face_backend(backend, reason="crash", sid=sid)
                else:
                    await self._release_face_backend(sid, backend, ws)
                if prepared and not ws.closed:
                    try:
                        await ws.close()
                    except Exception:
                        pass
            return ws

        def revoke_session(self, sid: str) -> bool:
            """Kill a live grant from sync context (in-process callers).

            The queue (`serve_spool.append_revoke`) is the normal path — this
            immediate form schedules the socket close on the running loop."""
            died = self.gate.revoke(sid)
            if died:
                self._audit(f"session_revoked sid={sid}")
                self._forget_session(sid)
                self._write_mirror()
                loop = getattr(self, "_loop", None)
                if loop is not None and loop.is_running():
                    loop.call_soon_threadsafe(
                        lambda: loop.create_task(
                            self._close_session_ws(sid, code=4001, message=b"revoked")
                        )
                    )
            return died

    return PublicServer()
