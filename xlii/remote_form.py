"""Closed HTML form for Tools → Remotes.

Seeds ``/remote add`` with flags (the scriptable path). The secret is still
prompted by ``/remote add`` — it never rides this form or the transcript.
"""

from __future__ import annotations

import html
from typing import Any


def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def form_spec() -> dict[str, Any]:
    from xlii.remotefs import PROTOCOLS, manager, scheme_for_protocol

    rows = []
    try:
        open_names = set(manager.open_names())
        for name in manager.names():
            spec = manager.spec(name) or {}
            proto = spec.get("protocol") or "ftp"
            scheme = scheme_for_protocol(proto)
            host = spec.get("host") or spec.get("base_url") or "?"
            rows.append({
                "id": name,
                "protocol": proto,
                "host": host,
                "scheme": scheme,
                "open": name in open_names,
            })
    except Exception:
        rows = []
    spec = {
        "plugin": "remote",
        "action": "make",
        "title": "Remotes",
        "lead": "Name a wire. Seed reviews /remote add — send writes it. Password is asked after, never here.",
        "protocols": list(PROTOCOLS),
        "remotes": rows,
        "rev": ",".join(r["id"] for r in rows) or "empty",
    }
    spec["html"] = render_form_html(spec)
    return spec


_CSS = """
:root { color-scheme: dark light; }
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body {
  font: 14px/1.45 system-ui, sans-serif;
  color: CanvasText;
  background: transparent;
  padding: 16px 18px 20px;
}
h1 { font-size: 15px; font-weight: 600; margin: 0 0 4px; }
.lead { margin: 0 0 14px; font-size: 12px; opacity: .7; }
h2 { font-size: 12px; font-weight: 600; margin: 16px 0 8px; opacity: .75; }
form { display: flex; flex-direction: column; gap: 10px; max-width: 36rem; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; opacity: .85; }
select, input {
  font: 14px/1.3 inherit; color: inherit;
  background: color-mix(in srgb, CanvasText 6%, transparent);
  border: 1px solid color-mix(in srgb, CanvasText 22%, transparent);
  border-radius: 6px; padding: 8px 10px;
}
.go { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; }
button {
  font: 13px inherit; color: inherit; cursor: pointer;
  background: color-mix(in srgb, CanvasText 10%, transparent);
  border: 1px solid color-mix(in srgb, CanvasText 28%, transparent);
  border-radius: 6px; padding: 8px 12px;
}
button:hover { background: color-mix(in srgb, CanvasText 16%, transparent); }
.cur { list-style: none; margin: 0; padding: 0; font-size: 13px; }
.cur li { display: flex; justify-content: space-between; gap: 8px; padding: 4px 0; flex-wrap: wrap; }
.cur button { padding: 2px 8px; font-size: 12px; }
.empty { opacity: .55; font-size: 13px; }
"""


def render_form_html(spec: dict[str, Any]) -> str:
    remotes = spec.get("remotes") or []
    protos = spec.get("protocols") or ["ftp", "sftp", "ftps", "webdav", "smb"]
    proto_opts = [f'<option value="{_esc(p)}">{_esc(p)}</option>' for p in protos]
    lis = []
    for r in remotes:
        mark = "●" if r.get("open") else "○"
        lis.append(
            f'<li><span>{mark} {_esc(r["id"])} · {_esc(r.get("scheme") or "")}://{_esc(r.get("host") or "")}</span>'
            f'<span>'
            f'<button type="button" data-ls="{_esc(r["id"])}">browse</button>'
            f'<button type="button" data-rm="{_esc(r["id"])}">remove</button>'
            f'</span></li>'
        )
    cur = (
        f'<ul class="cur">{"".join(lis)}</ul>'
        if lis else '<p class="empty">no remotes yet</p>'
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{_CSS}</style></head><body>
<h1>{_esc(spec.get("title") or "Remotes")}</h1>
<p class="lead">{_esc(spec.get("lead") or "")}</p>
<h2>now</h2>
{cur}
<h2>add</h2>
<form id="f">
<label>name<input name="name" placeholder="box" autocomplete="off" spellcheck="false"></label>
<label>protocol<select name="protocol">{"".join(proto_opts)}</select></label>
<label>host<input name="host" placeholder="host or IP" autocomplete="off" spellcheck="false"></label>
<label>port (optional)<input name="port" placeholder="protocol default" autocomplete="off"></label>
<label>user (optional)<input name="user" autocomplete="off" spellcheck="false"></label>
<label>ssh key path (sftp)<input name="key" placeholder="~/.ssh/id_ed25519" autocomplete="off" spellcheck="false"></label>
<label>base url (webdav)<input name="base" placeholder="https://host/dav" autocomplete="off" spellcheck="false"></label>
<label>share (smb)<input name="share" autocomplete="off" spellcheck="false"></label>
<div class="go"><button type="button" id="seed">Seed /remote add</button></div>
</form>
<script>
function seed(text){{ parent.postMessage({{type:"xlii-prefill", text:text}}, "*"); }}
document.getElementById("seed").onclick = () => {{
  const fd = new FormData(document.getElementById("f"));
  const name = (fd.get("name")||"").trim();
  const proto = (fd.get("protocol")||"ftp").trim();
  const host = (fd.get("host")||"").trim();
  const base = (fd.get("base")||"").trim();
  if (!name) return;
  if (!host && !base) return;
  let line = "/remote add " + name + " --protocol " + proto;
  if (host) line += " --host " + host;
  const port = (fd.get("port")||"").trim();
  const user = (fd.get("user")||"").trim();
  const key = (fd.get("key")||"").trim();
  const share = (fd.get("share")||"").trim();
  if (port) line += " --port " + port;
  if (user) line += " --user " + user;
  if (key) line += " --key-path " + key;
  if (base) line += " --base-url " + base;
  if (share) line += " --share " + share;
  seed(line);
}};
document.querySelectorAll("[data-ls]").forEach((btn) => {{
  btn.onclick = () => {{
    const t = btn.getAttribute("data-ls") || "";
    if (t) seed("/remote ls " + t);
  }};
}});
document.querySelectorAll("[data-rm]").forEach((btn) => {{
  btn.onclick = () => {{
    const t = btn.getAttribute("data-rm") || "";
    if (t) seed("/remote rm " + t);
  }};
}});
</script>
</body></html>"""
