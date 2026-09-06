"""Closed HTML form for Xlii → Install node.

Seeds ``xlii jid add`` for the two mouths, then the node-setup line.
The heavy stamp (rsync/venv) still belongs to ``xlii node setup``;
this form never carries passwords.
"""

from __future__ import annotations

import html
import json
from typing import Any


def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def form_spec() -> dict[str, Any]:
    from xlii.config import GlobalConfig
    from xlii.jid_house import (
        house_admin_remote, house_domain, list_accounts, next_node_name,
    )
    from xlii.remotefs import manager

    cfg = GlobalConfig.load()
    nodes = list((getattr(cfg, "fabric_nodes", None) or {}).keys())
    try:
        from xlii.persona import factory_persona_id

        journal = factory_persona_id(cfg)
    except Exception:
        journal = "mojo"
    spec = {
        "plugin": "install",
        "action": "make",
        "title": "Install node",
        "lead": (
            "Stamp another limb of this house. Same Mojo, named body — not a second throne. "
            "First mint the JIDs (you, not an agent). Then seed the setup line."
        ),
        "domain": house_domain(cfg),
        "admin_remote": house_admin_remote(cfg),
        "remotes": list(manager.names()),
        "nodes": nodes,
        "next_name": next_node_name(cfg),
        "jids": [m.jid for m in list_accounts(cfg)],
        "journal": journal,
        "rev": ",".join(nodes) + "|" + ",".join(m.jid for m in list_accounts(cfg)),
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
form { display: flex; flex-direction: column; gap: 10px; max-width: 40rem; }
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
.note { opacity: .7; font-size: 12px; margin: 0 0 12px; }
"""


def render_form_html(spec: dict[str, Any]) -> str:
    remotes = spec.get("remotes") or []
    remote_opts = ['<option value="">— pick a wire —</option>']
    for n in remotes:
        remote_opts.append(f'<option value="{_esc(n)}">{_esc(n)}</option>')
    key_opts = [
        '<option value="existing">existing key path</option>',
        '<option value="new">generate a dedicated key</option>',
        '<option value="password">password (prompted, vault)</option>',
    ]
    larynx_opts = [
        '<option value="none">none yet</option>',
        '<option value="xai">mint capped xAI child</option>',
        '<option value="gig">gig (Kimi / …)</option>',
        '<option value="both">xAI child + gig</option>',
    ]
    jids = spec.get("jids") or []
    jid_note = (
        f"House domain: {spec.get('domain') or '(set JIDs first)'}. "
        f"{len(jids)} address(es) on the ledger."
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>{_CSS}</style></head>
<body>
<h1>{_esc(spec.get("title") or "Install node")}</h1>
<p class="lead">{_esc(spec.get("lead") or "")}</p>
<p class="note">{_esc(jid_note)}</p>
<form id="stamp">
  <label>Node name <input name="name" value="{_esc(spec.get("next_name") or "node1")}" placeholder="node1" required></label>
  <label>Remote (SSH/SFTP) <select name="remote">{"".join(remote_opts)}</select></label>
  <label>Auth
    <select name="auth">{"".join(key_opts)}</select>
  </label>
  <label>Key path (if existing) <input name="key" placeholder="~/.ssh/id_ed25519_xlii_node1"></label>
  <label>Larynx <select name="larynx">{"".join(larynx_opts)}</select></label>
  <label>Gig name <input name="gig" placeholder="kimi"></label>
  <div class="go">
    <button type="submit" data-kind="jids">Seed JID mint</button>
    <button type="button" id="setup">Seed node setup</button>
  </div>
</form>
<script>
function seed(line) {{
  parent.postMessage({{ type: "xlii-prefill", text: line }}, "*");
}}
document.getElementById("stamp").addEventListener("submit", function (e) {{
  e.preventDefault();
  const n = this.name.value.trim();
  if (!n) return;
  seed("xlii jid add " + n + " --role node --node " + n);
}});
document.getElementById("setup").addEventListener("click", function () {{
  const f = document.getElementById("stamp");
  const n = f.name.value.trim();
  if (!n) return;
  let line = "xlii node setup " + n;
  const journal = {json.dumps(spec.get("journal") or "mojo")};
  if (journal) line += " --journal " + journal;
  const rem = f.remote.value.trim();
  if (rem) line += " --remote " + rem;
  const auth = f.auth.value;
  if (auth === "new") line += " --new-key";
  const key = f.key.value.trim();
  if (key && auth === "existing") line += " --key-path " + key;
  const larynx = f.larynx.value;
  if (larynx === "xai" || larynx === "both") line += " --mint-xai";
  const gig = f.gig.value.trim();
  if (gig && (larynx === "gig" || larynx === "both")) line += " --gig " + gig;
  seed(line);
}});
</script>
</body></html>
"""
