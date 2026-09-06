"""Agent-driven Chromium session via CDP (typed-workbenches path 3).

v0 substrate for the research assistant: spawn a local Chromium, navigate,
extract page text (and optional screenshot), close. One process-wide session
(thread-safe lock). No Playwright dependency — talks the DevTools protocol
over a tiny stdlib WebSocket client.

Face ``browser:open`` and the agent ``browser`` tool share this module.

Law: this Chromium is the *only* controllable research window. The config
``os browser`` (Firefox, …) is for human opens — we cannot see those tabs.
Keep/archive is ArchiveBox's REST plugin (``archivebox.add`` the CDP URL),
not a Chrome-Web-Store extension. Unpacked ``--load-extension`` on *this*
process is allowed later if a page button is worth it; daily Firefox/Chrome
bridge is path 2 and stays optional.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse


# --------------------------------------------------------------------------- #
#  Chromium discovery
# --------------------------------------------------------------------------- #

# Drop CDP frames bigger than this (a Google SERP innerText over the wire
# was hanging extract for minutes). Extract slices in-page first.
_MAX_CDP_FRAME = 1_500_000


def _extract_js(max_chars: int) -> str:
    """JS that returns title/url/text already capped — never the whole DOM."""
    cap = max(500, min(int(max_chars or 24_000), 80_000))
    return (
        "(() => {"
        f"  const cap = {cap};"
        "  const el = document.querySelector('main, article, [role=\"main\"]')"
        "    || document.body;"
        "  let t = (el && el.innerText) || '';"
        "  if (t.length > cap) t = t.slice(0, cap);"
        "  return { title: document.title || '', url: location.href || '', text: t };"
        "})()"
    )


_CHROME_CANDIDATES = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
)


def find_chromium() -> Optional[str]:
    """Return an absolute path to a Chromium/Chrome binary, or None."""
    env = (os.environ.get("XLII_CHROMIUM") or os.environ.get("CHROME_PATH") or "").strip()
    if env and Path(env).is_file() and os.access(env, os.X_OK):
        return env
    for name in _CHROME_CANDIDATES:
        p = shutil.which(name)
        if p:
            return p
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _safe_url(url: str) -> str:
    """Allow web URLs only — refuse local files and script/data schemes."""
    u = (url or "").strip()
    if not u:
        raise ValueError("url is required")
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"refused scheme {parsed.scheme!r} (http/https only)")
    return u


# --------------------------------------------------------------------------- #
#  Minimal blocking WebSocket client (text frames only — enough for CDP)
# --------------------------------------------------------------------------- #

class _WsError(RuntimeError):
    pass


class _CdpSocket:
    """Blocking WebSocket for Chrome DevTools Protocol JSON messages."""

    def __init__(self, url: str, *, timeout: float = 30.0) -> None:
        from urllib.parse import urlparse as _up

        parsed = _up(url)
        if parsed.scheme not in ("ws", "wss"):
            raise _WsError(f"not a websocket url: {url!r}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        if parsed.scheme == "wss":
            import ssl
            raw = socket.create_connection((host, port), timeout=timeout)
            ctx = ssl.create_default_context()
            self._sock = ctx.wrap_socket(raw, server_hostname=host)
        else:
            self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self._sock.sendall(req.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise _WsError("websocket handshake closed")
            buf += chunk
        header, _, rest = buf.partition(b"\r\n\r\n")
        status = header.split(b"\r\n", 1)[0]
        if b"101" not in status:
            raise _WsError(f"websocket upgrade failed: {status!r}")
        # leftover bytes after handshake headers (rare)
        self._buf = rest
        self._timeout = timeout

    def send_text(self, text: str) -> None:
        import struct
        data = text.encode("utf-8")
        length = len(data)
        mask_bit = 0x80  # client must mask
        if length < 126:
            header = bytes([0x81, mask_bit | length])
        elif length < 65536:
            header = bytes([0x81, mask_bit | 126]) + struct.pack("!H", length)
        else:
            header = bytes([0x81, mask_bit | 127]) + struct.pack("!Q", length)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self._sock.sendall(header + mask + masked)

    def recv_text(self) -> str:
        while True:
            opcode, payload = self._recv_frame()
            if opcode == 0x1:  # text
                if not payload:
                    continue
                return payload.decode("utf-8", errors="replace")
            if opcode == 0x8:  # close
                raise _WsError("websocket closed by peer")
            if opcode == 0x9:  # ping → pong
                self._send_frame(0xA, payload)
                continue
            # ignore binary / pong / continuation for CDP simplicity

    def _recv_frame(self) -> tuple[int, bytes]:
        import struct
        h = self._read_exact(2)
        opcode = h[0] & 0x0F
        masked = bool(h[1] & 0x80)
        length = h[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b""
        if length > _MAX_CDP_FRAME:
            self._discard(length)
            return opcode, b""
        payload = self._read_exact(length)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        import struct
        length = len(payload)
        mask_bit = 0x80
        if length < 126:
            header = bytes([0x80 | opcode, mask_bit | length])
        elif length < 65536:
            header = bytes([0x80 | opcode, mask_bit | 126]) + struct.pack("!H", length)
        else:
            header = bytes([0x80 | opcode, mask_bit | 127]) + struct.pack("!Q", length)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(header + mask + masked)

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(max(4096, min(65536, n - len(self._buf))))
            if not chunk:
                raise _WsError("websocket recv closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _discard(self, n: int) -> None:
        """Drop *n* payload bytes without assembling them (oversized CDP events)."""
        have = min(len(self._buf), n)
        self._buf = self._buf[have:]
        left = n - have
        while left > 0:
            chunk = self._sock.recv(min(65536, left))
            if not chunk:
                raise _WsError("websocket recv closed")
            left -= len(chunk)

    def close(self) -> None:
        try:
            self._send_frame(0x8, b"")
        except Exception:  # noqa: BLE001
            pass
        try:
            self._sock.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
#  Session
# --------------------------------------------------------------------------- #

@dataclass
class BrowserSnapshot:
    """Result of an extract / status probe."""
    ok: bool
    url: str = ""
    title: str = ""
    text: str = ""
    error: str = ""
    screenshot_path: str = ""
    headless: bool = True
    pid: int = 0

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ok": self.ok,
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "headless": self.headless,
            "pid": self.pid,
        }
        if self.error:
            d["error"] = self.error
        if self.screenshot_path:
            d["screenshot_path"] = self.screenshot_path
        return d

    def summary(self, *, max_chars: int = 4000) -> str:
        if not self.ok and self.error:
            return f"browser error: {self.error}"
        lines = [
            f"url:   {self.url or '(none)'}",
            f"title: {self.title or '(none)'}",
        ]
        if self.screenshot_path:
            lines.append(f"shot:  {self.screenshot_path}")
        body = (self.text or "").strip()
        if body:
            if len(body) > max_chars:
                body = body[: max_chars - 1] + "…"
            lines.append("")
            lines.append(body)
        return "\n".join(lines)


@dataclass
class BrowserSession:
    """One Chromium process + CDP page connection."""

    proc: subprocess.Popen = field(repr=False)
    port: int
    user_data_dir: Path
    headless: bool
    ws: Optional[_CdpSocket] = field(default=None, repr=False)
    page_id: str = ""
    last_url: str = ""
    last_title: str = ""
    _msg_id: int = 0
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def cdp(self, method: str, params: Optional[dict] = None, *,
            timeout: float = 30.0) -> dict[str, Any]:
        with self._lock:
            if self.ws is None:
                raise RuntimeError("no CDP websocket")
            self._msg_id += 1
            mid = self._msg_id
            self.ws.send_text(json.dumps({
                "id": mid, "method": method, "params": params or {},
            }))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                remaining = max(0.1, deadline - time.monotonic())
                self.ws._sock.settimeout(remaining)
                try:
                    raw = self.ws.recv_text()
                except Exception as e:  # noqa: BLE001
                    raise RuntimeError(f"CDP recv failed: {e}") from e
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if data.get("id") == mid:
                    if "error" in data:
                        err = data["error"]
                        msg = err.get("message") if isinstance(err, dict) else str(err)
                        raise RuntimeError(f"CDP {method}: {msg}")
                    return data.get("result") or {}
            raise TimeoutError(f"CDP {method} timed out after {timeout}s")

    def attach_page(self) -> None:
        """Connect WS to the first page target (or create one)."""
        pages = _http_json(f"http://127.0.0.1:{self.port}/json/list")
        page = next((p for p in pages if p.get("type") == "page"), None)
        if page is None:
            # open a blank tab
            try:
                page = _http_json(
                    f"http://127.0.0.1:{self.port}/json/new?about:blank",
                    method="PUT",
                )
            except Exception:
                page = _http_json(f"http://127.0.0.1:{self.port}/json/new?about:blank")
        if not isinstance(page, dict):
            raise RuntimeError("no page target from Chromium")
        ws_url = page.get("webSocketDebuggerUrl")
        if not ws_url:
            raise RuntimeError("page has no webSocketDebuggerUrl")
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:  # noqa: BLE001
                pass
        self.ws = _CdpSocket(ws_url, timeout=30.0)
        self.page_id = str(page.get("id") or "")
        self.cdp("Page.enable")
        # Runtime.enable floods console/exception events on ad-heavy pages
        # (Google SERP). evaluate still works without it.

    def goto(self, url: str, *, wait_s: float = 1.5,
             max_chars: int = 24_000) -> BrowserSnapshot:
        url = _safe_url(url)
        with self._lock:
            if not self.alive():
                return BrowserSnapshot(ok=False, error="chromium process died")
            if self.ws is None:
                self.attach_page()
            try:
                self.cdp("Page.navigate", {"url": url}, timeout=15.0)
            except Exception as e:  # noqa: BLE001
                return BrowserSnapshot(ok=False, url=url, error=str(e))
            # Fixed settle — do not wait for network-idle (SERPs never idle).
            time.sleep(max(0.3, wait_s))
            snap = self.extract(max_chars=max_chars)
            self.last_url = snap.url or url
            self.last_title = snap.title
            return snap

    def extract(self, *, max_chars: int = 24_000) -> BrowserSnapshot:
        expr = _extract_js(max_chars)
        with self._lock:
            if not self.alive() or self.ws is None:
                return BrowserSnapshot(ok=False, error="no live browser session")
            try:
                result = self.cdp(
                    "Runtime.evaluate",
                    {"expression": expr, "returnByValue": True, "awaitPromise": False},
                    timeout=12.0,
                )
            except Exception as e:  # noqa: BLE001
                return BrowserSnapshot(ok=False, error=str(e),
                                       pid=self.proc.pid, headless=self.headless)
            val = (result.get("result") or {}).get("value") or {}
            text = str(val.get("text") or "")
            if max_chars > 0 and len(text) > max_chars:
                text = text[: max_chars - 1] + "…"
            url = str(val.get("url") or self.last_url or "")
            title = str(val.get("title") or "")
            self.last_url, self.last_title = url, title
            return BrowserSnapshot(
                ok=True, url=url, title=title, text=text,
                headless=self.headless, pid=self.proc.pid,
            )

    def screenshot(self, dest: Path) -> BrowserSnapshot:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if not self.alive() or self.ws is None:
                return BrowserSnapshot(ok=False, error="no live browser session")
            try:
                result = self.cdp(
                    "Page.captureScreenshot",
                    {"format": "png", "fromSurface": True},
                    timeout=30.0,
                )
            except Exception as e:  # noqa: BLE001
                return BrowserSnapshot(ok=False, error=str(e))
            b64 = result.get("data") or ""
            if not b64:
                return BrowserSnapshot(ok=False, error="empty screenshot data")
            dest.write_bytes(base64.b64decode(b64))
            snap = self.extract(max_chars=500)
            snap.screenshot_path = str(dest)
            return snap

    def close(self) -> None:
        with self._lock:
            if self.ws is not None:
                try:
                    self.ws.close()
                except Exception:  # noqa: BLE001
                    pass
                self.ws = None
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    try:
                        self.proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        # Already killed and still not reaped -- give up rather than blocking teardown any
                        # longer.
                        pass


def _http_json(url: str, *, method: str = "GET", timeout: float = 5.0) -> Any:
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _wait_cdp(port: int, *, timeout: float = 15.0,
              proc: Optional[subprocess.Popen] = None) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_err: Optional[Exception] = None
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(
                f"Chromium exited early (code {proc.returncode}) before CDP on :{port}"
            )
        try:
            return _http_json(f"http://127.0.0.1:{port}/json/version", timeout=1.0)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(0.15)
    raise RuntimeError(f"Chromium CDP did not come up on :{port}: {last_err}")


def _clear_stale_singleton(user_data_dir: Path) -> bool:
    """Remove Chromium Singleton* locks if no live owner process holds them.

    Chromium aborts with connection-refused CDP when a previous run left
    ``SingletonLock`` behind (or a zombie still holds the profile). Returns
    True if anything was cleared.
    """
    lock = user_data_dir / "SingletonLock"
    cookie = user_data_dir / "SingletonCookie"
    # Path.exists() is False for dangling symlinks — use lexists via is_symlink/lstat.
    def _present(p: Path) -> bool:
        try:
            p.lstat()
            return True
        except OSError:
            return False

    if not _present(lock) and not _present(cookie):
        return False

    owner_pid: Optional[int] = None
    try:
        # SingletonLock is a symlink → "{hostname}-{pid}"
        if lock.is_symlink():
            target = os.readlink(lock)
            m = re.search(r"-(\d+)$", target)
            if m:
                owner_pid = int(m.group(1))
    except OSError:
        # owner_pid stays None, so the lock can't be attributed to a live process.
        pass

    if owner_pid is not None:
        try:
            os.kill(owner_pid, 0)  # still running?
            # Live process owns the profile — do not steal.
            return False
        except OSError:
            pass  # dead — safe to clear

    cleared = False
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = user_data_dir / name
        try:
            if _present(p):
                p.unlink(missing_ok=True)  # type: ignore[call-arg]
                cleared = True
        except TypeError:
            # py<3.8 missing_ok — not our floor, but be kind
            try:
                p.unlink()
                cleared = True
            except OSError:
                # The legacy unlink fallback failed too -- leave this singleton file in place.
                pass
        except OSError:
            # An unremovable singleton file is left alone; cleared stays False for it.
            pass
    return cleared


def _default_user_data_dir() -> Path:
    """Unique profile per spawn — avoids SingletonLock fights across restarts."""
    import uuid

    from xlii.project_paths import xlii_user_root

    base = xlii_user_root() / "browser-sessions"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"s-{uuid.uuid4().hex[:12]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _default_headless() -> bool:
    """Hide Chromium only when there is no desk, or the operator asked.

    A live X11/Wayland session should see the research window. CI and
    keyless boxes stay headless. ``XLII_BROWSER_HEADLESS=1`` forces hide;
    ``=0`` forces a window.
    """
    force = os.environ.get("XLII_BROWSER_HEADLESS", "").strip().lower()
    if force in ("1", "true", "yes"):
        return True
    if force in ("0", "false", "no"):
        return False
    if os.environ.get("CI", "").strip().lower() in ("1", "true", "yes"):
        return True
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return False
    return True


def _want_no_sandbox(headless: bool) -> bool:
    """Headless agent browser defaults to --no-sandbox (Linux reliability).

    Opt out with ``XLII_BROWSER_SANDBOX=1``; force on with ``XLII_BROWSER_NO_SANDBOX=1``.
    """
    force = os.environ.get("XLII_BROWSER_NO_SANDBOX", "").strip().lower()
    if force in ("1", "true", "yes"):
        return True
    if force in ("0", "false", "no"):
        return False
    deny = os.environ.get("XLII_BROWSER_SANDBOX", "").strip().lower()
    if deny in ("1", "true", "yes"):
        return False
    # Default: no-sandbox when headless (containers / some Parrot/Debian builds)
    return bool(headless)


# --------------------------------------------------------------------------- #
#  Process-wide session (one agent browser at a time for v0)
# --------------------------------------------------------------------------- #

_LOCK = threading.RLock()
_SESSION: Optional[BrowserSession] = None
_ON_CHANGE: list = []


def on_change(fn) -> None:
    """Call *fn* after the live session starts, stops, or changes mode.

    Listeners APPEND for the process lifetime — FaceServer registers
    ``_on_browser_change`` on construct and never removes it. Tests that
    spawn many FaceServers should clear ``_ON_CHANGE`` (or call
    ``off_change``) so dead servers cannot mutate the current occupancy
    file via chrome_state → live_chrome → mutate_bundle.
    """
    if fn not in _ON_CHANGE:
        _ON_CHANGE.append(fn)


def off_change(fn) -> None:
    """Remove a previously registered ``on_change`` listener (idempotent)."""
    try:
        _ON_CHANGE.remove(fn)
    except ValueError:
        pass


def _emit_change() -> None:
    for fn in list(_ON_CHANGE):
        try:
            fn()
        except Exception:  # noqa: BLE001 — a HUD listener must never kill CDP
            pass


def session_peek() -> BrowserSnapshot:
    """Cheap live-or-not probe — no page extract. For HUD / chrome."""
    with _LOCK:
        s = _SESSION
        if s is None or not s.alive():
            return BrowserSnapshot(ok=False, error="no browser session")
        return BrowserSnapshot(
            ok=True,
            url=s.last_url or "",
            title=s.last_title or "",
            headless=bool(s.headless),
            pid=int(getattr(s.proc, "pid", 0) or 0),
        )


def session_status() -> BrowserSnapshot:
    with _LOCK:
        s = _SESSION
        if s is None or not s.alive():
            return BrowserSnapshot(ok=False, error="no browser session")
        snap = s.extract(max_chars=800)
        if not snap.ok:
            return BrowserSnapshot(
                ok=True, url=s.last_url, title=s.last_title,
                headless=s.headless, pid=s.proc.pid,
                text="(session alive; extract failed: " + (snap.error or "?") + ")",
            )
        return snap


def open_session(
    *,
    url: str = "",
    headless: Optional[bool] = None,
    user_data_dir: Optional[Path] = None,
) -> BrowserSnapshot:
    """Start Chromium (or reuse live session) and optionally navigate.

    ``headless=None`` follows the desk: a window when DISPLAY/Wayland is
    set, hidden in CI / no display. A **visible** session is never
    restarted as headless — that was stealing the window the face opened.
    Headless → headed still restarts (user asked to watch).
    """
    global _SESSION
    chrome = find_chromium()
    if not chrome:
        return BrowserSnapshot(
            ok=False,
            error="no Chromium/Chrome found — install chromium or set $XLII_CHROMIUM",
        )
    if headless is None:
        headless = _default_headless()

    with _LOCK:
        if _SESSION is not None and _SESSION.alive():
            live_headless = bool(_SESSION.headless)
            want_headless = bool(headless)
            # Never hide a window the desk already opened.
            if live_headless and not want_headless:
                try:
                    _SESSION.close()
                except Exception:  # noqa: BLE001
                    pass
                _SESSION = None
            else:
                if url:
                    snap = _SESSION.goto(url)
                    _emit_change()
                    return snap
                return session_status()

        if _SESSION is not None:
            try:
                _SESSION.close()
            except Exception:  # noqa: BLE001
                pass
            _SESSION = None

        port = _free_port()
        ud = Path(user_data_dir) if user_data_dir else _default_user_data_dir()
        ud.mkdir(parents=True, exist_ok=True)
        _clear_stale_singleton(ud)

        cmd = [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={ud}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-translate",
            "--metrics-recording-only",
            "--safebrowsing-disable-auto-update",
            "--disable-dev-shm-usage",
        ]
        if headless:
            cmd.append("--headless=new")
            cmd.append("--disable-gpu")
        if _want_no_sandbox(headless):
            cmd.append("--no-sandbox")
        cmd.append("about:blank")

        err_path = ud / "chromium.stderr.log"
        try:
            err_fh = open(err_path, "wb")  # noqa: SIM115 — closed after wait
        except OSError:
            err_fh = subprocess.DEVNULL  # type: ignore[assignment]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=err_fh,
                start_new_session=True,
            )
        except OSError as e:
            if hasattr(err_fh, "close"):
                try:
                    err_fh.close()
                except Exception:  # noqa: BLE001
                    pass
            return BrowserSnapshot(ok=False, error=f"failed to launch chromium: {e}")

        try:
            _wait_cdp(port, timeout=20.0, proc=proc)
        except Exception as e:  # noqa: BLE001
            detail = str(e)
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            except Exception:  # noqa: BLE001
                pass
            try:
                if hasattr(err_fh, "close"):
                    err_fh.close()
                if err_path.is_file():
                    tail = err_path.read_text(errors="replace")[-600:].strip()
                    if tail:
                        # surface the classic SingletonLock abort line
                        detail = f"{detail} | chromium: {tail.splitlines()[-1]}"
            except Exception:  # noqa: BLE001
                pass
            return BrowserSnapshot(ok=False, error=detail)

        if hasattr(err_fh, "close"):
            try:
                err_fh.close()
            except Exception:  # noqa: BLE001
                pass

        sess = BrowserSession(
            proc=proc, port=port, user_data_dir=ud, headless=headless,
        )
        try:
            sess.attach_page()
        except Exception as e:  # noqa: BLE001
            sess.close()
            return BrowserSnapshot(ok=False, error=f"CDP attach failed: {e}")

        _SESSION = sess
        if url:
            snap = sess.goto(url)
            _emit_change()
            return snap
        _emit_change()
        return BrowserSnapshot(
            ok=True, url="about:blank", title="", text="(browser ready)",
            headless=headless, pid=proc.pid,
        )


def goto(url: str) -> BrowserSnapshot:
    with _LOCK:
        if _SESSION is None or not _SESSION.alive():
            return open_session(url=url)
        return _SESSION.goto(url)


def extract(*, max_chars: int = 24_000) -> BrowserSnapshot:
    with _LOCK:
        if _SESSION is None or not _SESSION.alive():
            return BrowserSnapshot(ok=False, error="no browser session — call open first")
        return _SESSION.extract(max_chars=max_chars)


def screenshot(dest: Optional[Path] = None) -> BrowserSnapshot:
    with _LOCK:
        if _SESSION is None or not _SESSION.alive():
            return BrowserSnapshot(ok=False, error="no browser session — call open first")
        if dest is None:
            from xlii.project_paths import xlii_user_root

            dest = xlii_user_root() / "browser-shots" / f"shot-{int(time.time())}.png"
        return _SESSION.screenshot(Path(dest))


def close_session() -> BrowserSnapshot:
    global _SESSION
    with _LOCK:
        if _SESSION is None:
            return BrowserSnapshot(ok=True, text="(no session)")
        try:
            _SESSION.close()
        except Exception as e:  # noqa: BLE001
            _SESSION = None
            _emit_change()
            return BrowserSnapshot(ok=False, error=str(e))
        _SESSION = None
        _emit_change()
        return BrowserSnapshot(ok=True, text="(browser closed)")


# one-shot fallback when user only wants extract without keeping a session


def dump_dom_once(url: str, *, max_chars: int = 24_000, timeout: int = 25) -> BrowserSnapshot:
    """Headless one-shot extract (open Chromium → navigate → extract → close).

    Uses the same CDP session path as :func:`open_session` — the legacy
    ``--dump-dom`` subprocess often hangs or dies without a sandbox on CI Linux.
    """
    del timeout  # kept for API stability; CDP navigation uses internal waits
    _safe_url(url)  # validate before spawning
    close_session()
    opened = open_session(url=url, headless=True)
    if not opened.ok:
        return opened
    try:
        return extract(max_chars=max_chars)
    finally:
        close_session()
