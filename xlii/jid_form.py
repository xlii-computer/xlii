"""Closed HTML form for Xlii → JIDs.

Seeds ``xlii jid house`` / ``xlii jid add``. Passwords are never on the form.
"""

from __future__ import annotations

import html
from typing import Any

from xlii.jid_house import ROLES, house_admin_remote, house_domain, list_accounts


def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def form_spec() -> dict[str, Any]:
    from xlii.config import GlobalConfig
    from xlii.remotefs import manager

    cfg = GlobalConfig.load()
    remotes = list(manager.names())
    rows = []
    for m in list_accounts(cfg):
        rows.append({
            "jid": m.jid,
            "role": m.role,
            "node": m.node,
            "vault": m.password_stored,
        })
    spec = {
        "plugin": "jid",
        "action": "make",
        "title": "XMPP addresses",
        "lead": (
            "You mint JIDs. In-band signup stays off. "
            "If this house has an admin remote, add registers on Prosody. "
            "Otherwise Seed prints the command for your server."
        ),
        "domain": house_domain(cfg),
        "admin_remote": house_admin_remote(cfg),
        "remotes": remotes,
        "roles": list(ROLES),
        "accounts": rows,
        "rev": ",".join(r["jid"] for r in rows) or "empty",
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
form { display: flex; flex-wrap: wrap; gap: 10px; max-width: 40rem; align-items: end; }
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
.empty { opacity: .7; font-size: 13px; }
"""


def render_form_html(spec: dict[str, Any]) -> str:
    domain = spec.get("domain") or ""
    admin = spec.get("admin_remote") or ""
    remotes = spec.get("remotes") or []
    roles = spec.get("roles") or []
    accounts = spec.get("accounts") or []
    remote_opts = ['<option value="">— none (print command) —</option>']
    for n in remotes:
        sel = " selected" if n == admin else ""
        remote_opts.append(f'<option value="{_esc(n)}"{sel}>{_esc(n)}</option>')
    role_opts = []
    for r in roles:
        sel = " selected" if r == "node" else ""
        role_opts.append(f'<option value="{_esc(r)}"{sel}>{_esc(r)}</option>')
    lis = []
    for a in accounts:
        bits = [a.get("role") or "", a.get("node") or ""]
        tag = " · ".join(x for x in bits if x)
        lis.append(
            f'<li><code>{_esc(a.get("jid"))}</code>'
            f'<span>{_esc(tag)}</span></li>'
        )
    account_html = (
        f'<ul class="cur">{"".join(lis)}</ul>' if lis
        else '<p class="empty">none yet</p>'
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>{_CSS}</style></head>
<body>
<h1>{_esc(spec.get("title") or "XMPP addresses")}</h1>
<p class="lead">{_esc(spec.get("lead") or "")}</p>
<h2>House</h2>
<form id="house">
  <label>Domain <input name="domain" value="{_esc(domain)}" placeholder="home.xlii-remote.com"></label>
  <label>Admin remote <select name="admin">{"".join(remote_opts)}</select></label>
  <div class="go"><button type="submit" data-seed="house">Seed house</button></div>
</form>
<h2>New address</h2>
<form id="add">
  <label>Localpart <input name="local" placeholder="me · throne · node1"></label>
  <label>Role <select name="role">{"".join(role_opts)}</select></label>
  <label>Node (face) <input name="node" placeholder="node1"></label>
  <div class="go"><button type="submit" data-seed="add">Seed add</button></div>
</form>
<h2>On this house</h2>
{account_html}
<script>
function seed(line) {{
  parent.postMessage({{ type: "xlii-prefill", text: line }}, "*");
}}
document.getElementById("house").addEventListener("submit", function (e) {{
  e.preventDefault();
  const d = this.domain.value.trim();
  const a = this.admin.value.trim();
  let line = "xlii jid house";
  if (d) line += " --domain " + d;
  if (a) line += " --admin-remote " + a;
  seed(line);
}});
document.getElementById("add").addEventListener("submit", function (e) {{
  e.preventDefault();
  const local = this.local.value.trim();
  const role = this.role.value.trim() || "node";
  const node = this.node.value.trim();
  let line = "xlii jid add";
  if (local) line += " " + local;
  line += " --role " + role;
  if (node) line += " --node " + node;
  seed(line);
}});
</script>
</body></html>
"""
