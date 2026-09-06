// The wire: WebSocket client for the protocol-v2 event stream.
// Owns connect/reconnect (capped backoff) and typed dispatch; render logic
// lives in transcript.js/input.js. docs/ws-event-protocol.md is the contract.

export class Wire {
  constructor(resolveUrl, handlers) {
    this._resolveUrl = resolveUrl;   // async () => ws url (re-run per attempt)
    this._handlers = handlers;       // {onEvent, onState}
    this._ws = null;
    this._backoff = 500;
    this._closed = false;
    this._hadOpen = false;
    this._sessionDead = false;
    this._failCount = 0;
    this._maxRetries = 8;
  }

  async start() {
    this._closed = false;
    this._sessionDead = false;
    this._failCount = 0;
    await this._connect();
  }

  stop() {
    this._closed = true;
    if (this._ws) this._ws.close();
  }

  send(obj) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  async _connect() {
    if (this._closed || this._sessionDead) return;
    this._handlers.onState("connecting");
    let url;
    try {
      url = await this._resolveUrl();
    } catch (e) {
      this._handlers.onState("closed", `no endpoint: ${e.message || e}`);
      if (e && e.fatal) return;
      this._retry();
      return;
    }
    const ws = new WebSocket(url);
    this._ws = ws;
    ws.onopen = () => {
      this._backoff = 500;
      this._failCount = 0;
      this._hadOpen = true;
      this._handlers.onState("open");
    };
    ws.onmessage = (m) => {
      let ev;
      try {
        ev = JSON.parse(m.data);
      } catch {
        return;
      }
      this._handlers.onEvent(ev);
    };
    ws.onclose = (ev) => {
      const code = ev && ev.code;
      const reason = (ev && ev.reason) || "";
      let detail = reason;
      if (!detail && code) detail = `code ${code}`;
      if (code === 4000 || code === 4001) {
        this._sessionDead = true;
        detail = "session expired — pair again at /";
      }
      this._handlers.onState("closed", detail || undefined);
      if (!this._sessionDead) this._retry();
      else this._handlers.onState("session_ended", detail);
    };
    ws.onerror = () => { /* onclose follows */ };
  }

  async _retry() {
    if (this._closed || this._sessionDead) return;
    this._failCount += 1;
    if (this._hadOpen && this._failCount >= this._maxRetries) {
      const dead = await this._probeGrantDead();
      if (dead) {
        this._sessionDead = true;
        this._handlers.onState(
          "session_ended",
          "session expired — pair again at /",
        );
        return;
      }
    }
    const wait = this._backoff;
    this._backoff = Math.min(this._backoff * 2, 8000);
    setTimeout(() => this._connect(), wait);
  }

  async _probeGrantDead() {
    if (!location.pathname.startsWith("/face")) return false;
    try {
      const r = await fetch("/face/", {
        credentials: "include",
        redirect: "manual",
      });
      // redirect:manual surfaces redirects as opaqueredirect (status 0), not 303.
      return r.status === 401 || r.type === "opaqueredirect";
    } catch {
      return false;
    }
  }
}

// Endpoint resolution — the three ways the face is served:
// 1. Live face URL (browser, or Tauri after navigate): token in the page query.
// 2. Tauri asset shell (pre-navigate): poll the `handshake` IPC command.
// 3. Public body /face route: same-origin, grant cookie is the auth.
//
// IMPORTANT: prefer (1) over (2). After the desktop navigates to
// http://127.0.0.1:<port>/?token=…, __TAURI__ may still exist but custom
// IPC is denied for remote origins ("Command handshake not allowed by ACL").
// The token in the URL is enough — never call invoke on a live face page.
export async function resolveEndpoint() {
  const tokenInUrl = new URLSearchParams(location.search).get("token");
  const host = location.hostname;
  const liveLocal =
    tokenInUrl &&
    (host === "127.0.0.1" || host === "localhost") &&
    (location.protocol === "http:" || location.protocol === "https:");
  if (liveLocal) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${location.host}/?token=${encodeURIComponent(tokenInUrl)}`;
  }

  const tauri = window.__TAURI__;
  if (tauri && tauri.core && tauri.core.invoke) {
    try {
      const err = await tauri.core.invoke("spawn_error");
      if (err) {
        const e = new Error(err);
        e.fatal = true;
        throw e;
      }
      const h = await tauri.core.invoke("handshake");
      if (!h) throw new Error("sidecar starting");
      return `ws://127.0.0.1:${h.port}/?token=${encodeURIComponent(h.token)}`;
    } catch (e) {
      const msg = (e && e.message) || String(e);
      // ACL / remote: fall through if we somehow have a token elsewhere.
      if (/not allowed by ACL|forbidden/i.test(msg) && tokenInUrl) {
        return `ws://127.0.0.1:${location.port || "80"}/?token=${encodeURIComponent(tokenInUrl)}`;
      }
      throw e instanceof Error ? e : new Error(msg);
    }
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  if (tokenInUrl) {
    return `${proto}://${location.host}/?token=${encodeURIComponent(tokenInUrl)}`;
  }
  // Public body: grant cookie on /face/ws. Tailnet glass: grant cookie on /.
  if (location.pathname.startsWith("/face")) {
    return `${proto}://${location.host}/face/ws`;
  }
  return `${proto}://${location.host}/`;
}
