"""Tailnet glass — WhoIs-gated face on the sitting-scoped TailnetDoor.

G1 serves assets under CSP after WhoIs. G2 spends a webcode into an
httpOnly grant cookie bound to (device, sitting id). The desktop boot
token never leaves the desk.
"""
from __future__ import annotations

import html
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import parse_qs, unquote_plus

from xlii.occupancy import SITTING_TIER_GLASS
from xlii.occupancy_store import load_live
from xlii.serve_gate import GateStore, is_valid_format, normalize_code

GRANT_COOKIE = "xlii_glass_grant"

VIEW_DESK = "desk"
VIEW_PHONE = "phone"
VIA_GLASS = "tailnet-glass"
PHONE_PANE_ALLOW = frozenset({"bookmarks", "git"})
PHONE_PREVIEW_PANES = frozenset({"bookmarks"})
GIT_RO_ACTIONS = frozenset({"view"})

# Wire verbs permitted on a grant-backed glass WS (``_client_glass``).
# Fail closed: anything not listed is refused at the top of FaceServer.reader.
# Pane ops still honor D2 ``phone_pane_allow`` / deck cuts. ``set_slot`` is
# allowed only because the phone UI uses it for stream/bookmarks — deck
# enforces the pane cut so explorer etc. cannot slip through.
GLASS_FULL_VERBS = frozenset({
    "ping", "confirm", "cancel",
    "input", "turn",
    "pane_action", "open_pane", "focus_slot", "swap_slots", "set_slot",
    "join_project", "go_home",
    "lock", "remote_lock", "mark_last",
    "set_posture",  # handler still refuses posture=code
})
GLASS_PREVIEW_VERBS = frozenset({
    "ping", "confirm", "cancel",
    "pane_action", "open_pane", "focus_slot", "swap_slots", "set_slot",
    "mark_last",
    "input", "turn",  # _glass_input: marks OK, talk refused
    "lock", "remote_lock",
})


def glass_verb_allowed(kind: str, *, mode: str = "full") -> tuple[bool, str]:
    """Return (ok, error_message). Unknown/unguarded verbs fail closed."""
    k = str(kind or "").strip()
    preview = str(mode or "full").strip().lower() == "preview"
    allow = GLASS_PREVIEW_VERBS if preview else GLASS_FULL_VERBS
    if k in allow:
        return True, ""
    tier = "preview" if preview else "full"
    return False, f"glass refuses {k!r} ({tier} grant)"


# Translate Tauri's CSP: self + this origin only. No remote fonts, no
# beacons. connect-src includes ws: so the same-origin upgrade works.
CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; "
    "connect-src 'self' ws: wss:; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

SECURITY_HEADER_BLOCK = (
    f"Content-Security-Policy: {CSP}\r\n"
    "X-Content-Type-Options: nosniff\r\n"
    "X-Frame-Options: SAMEORIGIN\r\n"
    "Referrer-Policy: no-referrer\r\n"
    "Cache-Control: no-store\r\n"
)

_PAIR_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content"/>
<meta name="mobile-web-app-capable" content="yes"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>
<meta name="theme-color" content="#0e1116"/>
<title>xlii — webcode</title>
<style>
  :root {{ color-scheme: dark; }}
  html, body {{ height: 100%; }}
  body {{ margin: 0; min-height: 100dvh; display: grid; place-items: center;
          font: 16px/1.4 ui-sans-serif, system-ui, sans-serif;
          background: #0e1116; color: #e6edf3;
          padding: env(safe-area-inset-top) env(safe-area-inset-right)
                   env(safe-area-inset-bottom) env(safe-area-inset-left); }}
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
  <h1>xlii glass</h1>
  <p>Enter the webcode from this desk. The sitting dies without it.</p>
  {error}
  <form method="post" action="/pair" autocomplete="off">
    <label for="code">Webcode</label>
    <input id="code" name="code" type="text" inputmode="text"
           autocapitalize="characters" autocomplete="one-time-code"
           placeholder="X7K2-M9Q4" value="{code}" required autofocus/>
    <button type="submit">Open glass</button>
  </form>
</main>
<script>
(function () {{
  var form = document.querySelector("form");
  if (!form) return;
  form.addEventListener("submit", function (ev) {{
    ev.preventDefault();
    var el = document.documentElement;
    var go = el.requestFullscreen || el.webkitRequestFullscreen;
    var boot = function () {{
      var body = new URLSearchParams();
      var input = form.querySelector("[name=code]");
      body.set("code", input ? input.value : "");
      fetch("/pair", {{
        method: "POST",
        headers: {{ "Content-Type": "application/x-www-form-urlencoded" }},
        body: body.toString(),
        credentials: "same-origin"
      }}).then(function (res) {{
        if (!res.ok) {{
          return res.text().then(function (doc) {{
            document.open();
            document.write(doc || "");
            document.close();
          }});
        }}
        document.body.innerHTML = "";
        document.body.style.cssText = "margin:0;overflow:hidden;background:#101014";
        var frame = document.createElement("iframe");
        frame.src = "/";
        frame.setAttribute("allow", "fullscreen");
        document.body.appendChild(frame);
        var dock = function () {{
          var vv = window.visualViewport;
          var h = vv ? vv.height : window.innerHeight;
          var t = vv ? vv.offsetTop : 0;
          var l = vv ? vv.offsetLeft : 0;
          var w = vv ? vv.width : window.innerWidth;
          frame.style.cssText = "position:fixed;border:0;display:block;left:" + l +
            "px;top:" + t + "px;width:" + w + "px;height:" + h + "px";
        }};
        dock();
        if (window.visualViewport) {{
          window.visualViewport.addEventListener("resize", dock);
          window.visualViewport.addEventListener("scroll", dock);
        }}
        window.addEventListener("resize", dock);
      }}).catch(function () {{ HTMLFormElement.prototype.submit.call(form); }});
    }};
    if (!go) {{ boot(); return; }}
    try {{
      var p = go.call(el, {{ navigationUI: "hide" }});
      if (p && p.then) p.then(boot, boot); else boot();
    }} catch (e) {{ boot(); }}
  }});
}})();
</script>
</body>
</html>
"""


def pairing_html(*, error: str | None = None, code: str = "") -> bytes:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return _PAIR_HTML.format(
        error=err, code=html.escape(code or "", quote=True),
    ).encode("utf-8")


def cookie_value(headers: dict[str, str], name: str = GRANT_COOKIE) -> str:
    raw = headers.get("cookie") or ""
    for part in raw.split(";"):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        if key.strip() == name:
            return val.strip()
    return ""


def set_cookie_header(token: str, *, max_age: int = 3600) -> str:
    # HTTP on the tailnet — no Secure flag (the public spine sets it because
    # Caddy terminates TLS). HttpOnly + SameSite=Strict still hold.
    return (
        f"Set-Cookie: {GRANT_COOKIE}={token}; HttpOnly; SameSite=Strict; "
        f"Path=/; Max-Age={int(max_age)}\r\n"
    )


def form_code(body: bytes) -> str:
    """Pull ``code`` from a urlencoded POST body or a query string."""
    raw = unquote_plus(body.decode("utf-8", errors="replace"))
    params = parse_qs(raw, keep_blank_values=True)
    vals = params.get("code") or []
    return (vals[0] if vals else "").strip()


@dataclass(frozen=True)
class GlassGrant:
    token: str
    device: str
    sitting_id: str
    mode: str
    paired_at: float


class GlassGrantStore:
    """In-process grants for one TailnetDoor. Drop the sitting → drop these."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._grants: dict[str, GlassGrant] = {}
        self.gate = GateStore()

    def issue(
        self, *, device: str, sitting_id: str, mode: str, now: float | None = None,
    ) -> GlassGrant:
        token = secrets.token_urlsafe(24)
        grant = GlassGrant(
            token=token,
            device=device,
            sitting_id=sitting_id,
            mode=mode if mode in ("full", "preview") else "full",
            paired_at=now if now is not None else time.time(),
        )
        with self._lock:
            self._grants[token] = grant
        return grant

    def get(self, token: str) -> Optional[GlassGrant]:
        if not token:
            return None
        with self._lock:
            return self._grants.get(token)

    def valid(
        self, token: str, *, device: str, sitting_id: str,
    ) -> Optional[GlassGrant]:
        grant = self.get(token)
        if grant is None:
            return None
        if grant.sitting_id != sitting_id or not sitting_id:
            return None
        if grant.device != device:
            return None
        return grant

    def drop_all(self) -> None:
        with self._lock:
            self._grants.clear()


def sitting_tier() -> str:
    try:
        occ = load_live()
    except Exception:
        return "door"
    if not occ.remote_lab.open:
        return "door"
    t = (getattr(occ.remote_lab, "tier", "") or "door").strip().lower()
    return t if t == SITTING_TIER_GLASS else "door"


def sitting_id() -> str:
    try:
        occ = load_live()
    except Exception:
        return ""
    if not occ.remote_lab.open:
        return ""
    return str(getattr(occ.remote_lab, "id", "") or "")


def spend_webcode(
    store: GlassGrantStore,
    *,
    code: str,
    device: str,
    remote: str,
    now: float,
) -> tuple[Optional[GlassGrant], str]:
    """Consume one spool entry into a grant. Returns (grant, error)."""
    if store.gate.is_locked(remote, now=now):
        store.gate.register_attempt(remote, ok=False, now=now)
        return None, "too many tries — wait"
    if not is_valid_format(normalize_code(code)):
        store.gate.register_attempt(remote, ok=False, now=now)
        return None, "bad code"
    sid = sitting_id()
    if not sid:
        store.gate.register_attempt(remote, ok=False, now=now)
        return None, "sitting closed"
    from xlii.serve_spool import default_state_dir, take_pending

    entry = take_pending(default_state_dir(), code, now=now)
    if entry is None:
        store.gate.register_attempt(remote, ok=False, now=now)
        return None, "unknown or expired code"
    store.gate.register_attempt(remote, ok=True, now=now)
    grant = store.issue(
        device=device, sitting_id=sid, mode=str(entry.get("mode") or "full"),
        now=now,
    )
    try:
        from xlii.occupancy_store import mutate

        mutate(lambda o: o.record_remote_lab_agent(now=now), now=now)
    except Exception:
        pass
    return grant, ""


def phone_pane_allow(*, preview: bool = False) -> frozenset[str]:
    """D2 v1 cut: marks + git-ro. Preview drops git (transcript + marks only)."""
    return PHONE_PREVIEW_PANES if preview else PHONE_PANE_ALLOW


def phone_open_dual(server: Any) -> None:
    """First phone paint is stream + bookmarks — dual split, not a solo tape."""
    deck = getattr(server, "deck", None)
    if deck is None:
        return
    try:
        deck.open_pane("bookmarks")
    except Exception:
        pass


def take_glass_mouth(*, now: float | None = None) -> None:
    from xlii.occupancy_store import mutate

    clock = time.time() if now is None else now

    def _claim(occ) -> None:
        occ.take_mouth("me", via=VIA_GLASS)
        occ.record_remote_lab_agent(now=clock)

    mutate(_claim, now=clock)


def release_glass_mouth() -> None:
    """Return the mouth to desk when glass held it.

    Fail-closed: any mouth==me is treated as glass occupancy and released.
    Do not require via==tailnet-glass — a via mismatch (race / stale writer)
    must not leave the mouth stuck on me after desk reattach.
    """
    from xlii.occupancy_store import mutate

    def _rel(occ) -> None:
        if occ.mouth == "me":
            occ.take_mouth("desk")

    mutate(_rel)
