"""Plugin maker — the file: badges, actions, doc. Save writes the .md.

New and edit are the same form. ``/plugin new`` seed is not this. Extra
action fields (schema, transforms, compose) stay in ``keep`` so a Save of
an existing plugin does not strip them.
"""

from __future__ import annotations

import html
import json
from typing import Any

from xlii.plugin_scaffold import AUTH_RING, EFFECT_RING, OUTPUT_RING, TRUST_RING


class PluginSubscribeGated(ValueError):
    """The plugin file was written, but the requested project subscribe was
    refused by the high-risk gate (``can_subscribe``) — ``/admin unlock`` first.

    Carries the written ``path`` so a caller can still acknowledge the save.
    """

    def __init__(self, reason: str, *, path: Any = None):
        super().__init__(reason)
        self.path = path


def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def empty_spec(plugin_id: str = "") -> dict[str, Any]:
    return {
        "id": plugin_id or "",
        "name": "",
        "description": "",
        "categories": ["misc"],
        "effect": "read-only",
        "trust": "subscription",
        "auth": "none",
        "auth_env": [],
        "actions": [{
            "id": "ping",
            "description": "Stub GET — replace url and params",
            "method": "GET",
            "url": "https://example.com/",
            "output": "interpret",
            "params": [],
            "keep": {},
        }],
        "body": "",
        "exists": False,
        "error": "",
        "keep_meta": {},
        "subscribe": True,
    }


def spec_from_plugin(plugin) -> dict[str, Any]:
    """Load a plugin file (or stock text) into the maker spec."""
    data = empty_spec(getattr(plugin, "id", "") or "")
    try:
        meta = plugin.metadata() or {}
    except Exception as e:
        data["error"] = f"{type(e).__name__}: {e}"
        return data
    data["id"] = str(meta.get("id") or plugin.id or "")
    data["name"] = str(meta.get("name") or data["id"])
    data["description"] = str(meta.get("description") or "")
    cats = meta.get("categories") or ["misc"]
    data["categories"] = list(cats) if isinstance(cats, list) else [str(cats)]
    data["effect"] = str(meta.get("effect") or "read-only")
    data["trust"] = str(meta.get("trust") or "subscription")
    auth = str(meta.get("auth_type") or "none")
    data["auth"] = "query" if auth == "query_param" else auth
    envs = meta.get("auth_env_vars") or []
    data["auth_env"] = [str(x) for x in envs] if isinstance(envs, list) else [str(envs)]
    try:
        data["body"] = str(plugin.body() or "")
    except Exception:
        data["body"] = ""
    data["exists"] = True
    data["subscribe"] = False
    known = {
        "id", "name", "description", "categories", "effect", "trust",
        "auth_type", "auth_env_vars", "actions",
    }
    data["keep_meta"] = {k: v for k, v in meta.items() if k not in known}
    actions = []
    for raw in meta.get("actions") or []:
        if not isinstance(raw, dict):
            continue
        params = []
        rp = raw.get("params") or {}
        if isinstance(rp, dict):
            for n, v in rp.items():
                row: dict[str, Any] = {"name": str(n)}
                if isinstance(v, dict):
                    row["required"] = bool(v.get("required"))
                    row["secret"] = bool(v.get("secret"))
                    row["default"] = "" if v.get("default") is None else str(v.get("default"))
                    row["description"] = str(v.get("description") or "")
                    for extra in ("const", "store", "form", "input", "enum"):
                        if extra in v:
                            row[extra] = v[extra]
                elif v is True:
                    row["required"] = True
                params.append(row)
        keep = {k: v for k, v in raw.items() if k not in (
            "id", "description", "method", "url", "params", "output", "command",
        )}
        actions.append({
            "id": str(raw.get("id") or ""),
            "description": str(raw.get("description") or ""),
            "method": str(raw.get("method") or "GET").upper(),
            "url": str(raw.get("url") or ""),
            "command": str(raw.get("command") or ""),
            "output": str(raw.get("output") or "interpret"),
            "params": params,
            "keep": keep,
        })
    if actions:
        data["actions"] = actions
    return data


def form_spec(plugin_id: str = "") -> dict[str, Any]:
    from xlii.plugin import Plugin, is_valid_id, stock_markdown

    pid = (plugin_id or "").strip()
    data = empty_spec(pid)
    err = ""
    if pid:
        p = Plugin(id=pid)
        if p.path.exists() or stock_markdown(pid):
            data = spec_from_plugin(p)
        elif not is_valid_id(pid):
            err = f"invalid id {pid!r}"
            data["id"] = pid
    data["error"] = err
    spec = {
        "title": f"Plugin · {data['id']}" if data.get("id") else "Plugin maker",
        "lead": "The file. + GET · + POST · + set. Save writes ~/.config/xlii/plugins/<id>.md.",
        **data,
        "plugin": "pluginmake",
        "action": "compose",
    }
    spec["rev"] = f"{spec.get('id') or ''}:{int(bool(spec.get('exists')))}:{len(spec.get('actions') or [])}"
    spec["html"] = render_form_html(spec)
    return spec


def emit_plugin_markdown(spec: dict[str, Any]) -> str:
    import yaml

    pid = str(spec.get("id") or "").strip()
    if not pid:
        raise ValueError("plugin needs an id")
    from xlii.plugin import is_valid_id

    if not is_valid_id(pid):
        raise ValueError(f"invalid plugin id: {pid!r}")
    auth = str(spec.get("auth") or "none")
    if auth == "query":
        auth_type = "query_param"
    elif auth in AUTH_RING:
        auth_type = auth
    else:
        auth_type = "none"
    envs = [str(x).strip() for x in (spec.get("auth_env") or []) if str(x).strip()]
    if auth_type != "none" and not envs:
        envs = [f"{pid.upper().replace('-', '_').replace('.', '_')}_KEY"]
    actions = []
    for raw in spec.get("actions") or []:
        aid = str(raw.get("id") or "").strip()
        if not aid:
            continue
        params: dict[str, Any] = {}
        for p in raw.get("params") or []:
            n = str(p.get("name") or "").strip()
            if not n:
                continue
            row: dict[str, Any] = {}
            if p.get("required"):
                row["required"] = True
            if p.get("secret"):
                row["secret"] = True
            if p.get("store"):
                row["store"] = p.get("store") if p.get("store") is not True else True
            elif p.get("secret"):
                row["store"] = True
            if p.get("default"):
                row["default"] = str(p.get("default"))
            if p.get("description"):
                row["description"] = str(p.get("description"))
            if p.get("const") is not None:
                row["const"] = p.get("const")
            if p.get("form") is not None:
                row["form"] = p.get("form")
            if p.get("input"):
                row["input"] = p.get("input")
            if p.get("enum"):
                row["enum"] = p.get("enum")
            params[n] = row or True
        act: dict[str, Any] = {
            "id": aid,
            "description": str(raw.get("description") or ""),
        }
        method = str(raw.get("method") or "").strip().upper()
        if method:
            act["method"] = method
        if raw.get("url"):
            act["url"] = str(raw.get("url"))
        if raw.get("command"):
            act["command"] = str(raw.get("command"))
        act["params"] = params
        if raw.get("output"):
            act["output"] = str(raw.get("output"))
        keep = raw.get("keep") if isinstance(raw.get("keep"), dict) else {}
        for k, v in keep.items():
            if k not in act:
                act[k] = v
        actions.append(act)
    if not actions:
        raise ValueError("a plugin needs at least one action")
    fm: dict[str, Any] = {
        "id": pid,
        "name": str(spec.get("name") or pid).strip() or pid,
        "description": str(spec.get("description") or ""),
        "categories": spec.get("categories") or ["misc"],
        "effect": spec.get("effect") if spec.get("effect") in EFFECT_RING else "read-only",
        "trust": spec.get("trust") if spec.get("trust") in TRUST_RING else "subscription",
        "auth_type": auth_type,
        "auth_env_vars": envs,
    }
    keep_meta = spec.get("keep_meta") if isinstance(spec.get("keep_meta"), dict) else {}
    for k, v in keep_meta.items():
        if k not in fm:
            fm[k] = v
    fm["actions"] = actions
    body = str(spec.get("body") or "").strip()
    if not body:
        title = fm["name"]
        body = f"# {title}\n\nWhat this API does.\n"
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)
    return f"---\n{dumped}---\n\n{body.rstrip()}\n"


def write_plugin_spec(spec: dict[str, Any], *, xli_dir=None, state=None) -> Any:
    """Write ``PLUGINS_DIR/<id>.md``. Overwrites. Optional project subscribe.

    The subscribe step honours the same high-risk gate as ``/plugin subscribe``
    (``can_subscribe``): a high-risk plugin (local-system / destructive /
    always-confirm) needs an elevated session, and no ``state`` fails closed.
    Raises ``PluginSubscribeGated`` when the write succeeded but the subscribe
    was refused.
    """
    from pathlib import Path

    from xlii.plugin import (
        PLUGINS_DIR,
        Plugin,
        add_subscription,
        can_subscribe,
        stock_markdown,
    )

    text = emit_plugin_markdown(spec)
    pid = str(spec.get("id") or "").strip()
    if stock_markdown(pid):
        raise ValueError(
            f"{pid} is a stock plugin — change the id to fork it"
        )
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    path = Plugin(id=pid).path
    path.write_text(text, encoding="utf-8")
    if spec.get("subscribe") and xli_dir is not None:
        allowed, reason = can_subscribe(state, Plugin(id=pid))
        if not allowed:
            raise PluginSubscribeGated(reason or f"{pid} is gated", path=path)
        add_subscription(Path(xli_dir), pid)
    return path


_CSS = """
:root { color-scheme: dark light; }
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; }
body {
  font: 14px/1.45 system-ui, sans-serif;
  color: CanvasText;
  background: transparent;
  padding: 16px 18px 24px;
}
h1 { font-size: 15px; font-weight: 600; margin: 0 0 4px; }
.lead { margin: 0 0 14px; font-size: 12px; opacity: .7; }
h2 { font-size: 12px; font-weight: 600; margin: 16px 0 8px; opacity: .75; }
label.top { display: flex; flex-direction: column; gap: 4px; font-size: 12px; opacity: .85; margin: 0 0 10px; }
.row { display: flex; flex-wrap: wrap; gap: 8px; }
.row label.top { flex: 1; min-width: 8rem; }
input, select, textarea {
  font: 13px/1.35 inherit; color: inherit;
  background: color-mix(in srgb, CanvasText 6%, transparent);
  border: 1px solid color-mix(in srgb, CanvasText 22%, transparent);
  border-radius: 6px; padding: 7px 9px;
}
textarea { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; min-height: 2.6em; resize: vertical; }
.act {
  border: 1px solid color-mix(in srgb, CanvasText 16%, transparent);
  border-radius: 8px; padding: 8px; margin: 0 0 8px;
}
.act-h { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; flex-wrap: wrap; }
.act-h select { width: 6.5rem; }
.act-h input.id { width: 8rem; }
.params { margin: 6px 0 0 8px; }
.par { display: flex; gap: 6px; align-items: center; margin: 4px 0; flex-wrap: wrap; }
.par input.pn { width: 8rem; }
.go { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
button {
  font: 13px inherit; color: inherit; cursor: pointer;
  background: color-mix(in srgb, CanvasText 10%, transparent);
  border: 1px solid color-mix(in srgb, CanvasText 28%, transparent);
  border-radius: 6px; padding: 8px 12px;
}
button:hover { background: color-mix(in srgb, CanvasText 16%, transparent); }
pre#md {
  font: 11px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  white-space: pre-wrap;
  background: color-mix(in srgb, CanvasText 6%, transparent);
  border: 1px solid color-mix(in srgb, CanvasText 18%, transparent);
  border-radius: 6px; padding: 10px 12px;
  max-height: 14rem; overflow: auto;
}
.err { color: #c66; font-size: 13px; min-height: 1.2em; }
.note { font-size: 12px; opacity: .65; }
.check { flex-direction: row; align-items: center; gap: 8px; font-size: 13px; opacity: 1; }
"""


def render_form_html(spec: dict[str, Any]) -> str:
    payload = json.dumps({
        "id": spec.get("id") or "",
        "name": spec.get("name") or "",
        "description": spec.get("description") or "",
        "effect": spec.get("effect") or "read-only",
        "trust": spec.get("trust") or "subscription",
        "auth": spec.get("auth") or "none",
        "auth_env": spec.get("auth_env") or [],
        "actions": spec.get("actions") or [],
        "body": spec.get("body") or "",
        "exists": bool(spec.get("exists")),
        "subscribe": bool(spec.get("subscribe")),
        "keep_meta": spec.get("keep_meta") or {},
        "categories": spec.get("categories") or ["misc"],
    }, ensure_ascii=False, default=str)
    err = spec.get("error") or ""
    effects = "".join(
        f'<option value="{_esc(x)}">'+_esc(x)+"</option>" for x in EFFECT_RING
    )
    trusts = "".join(
        f'<option value="{_esc(x)}">'+_esc(x)+"</option>" for x in TRUST_RING
    )
    auths = "".join(
        f'<option value="{_esc(x)}">'+_esc(x)+"</option>" for x in AUTH_RING
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{_CSS}</style></head><body>
<h1>{_esc(spec.get("title") or "Plugin maker")}</h1>
<p class="lead">{_esc(spec.get("lead") or "")}</p>
<p class="err" id="err">{_esc(err)}</p>
<label class="top">id<input id="pid" autocomplete="off" spellcheck="false"></label>
<label class="top">name<input id="pname" autocomplete="off"></label>
<label class="top">description<input id="pdesc" autocomplete="off"></label>
<div class="row">
  <label class="top">effect<select id="effect">{effects}</select></label>
  <label class="top">trust<select id="trust">{trusts}</select></label>
  <label class="top">auth<select id="auth">{auths}</select></label>
</div>
<label class="top">auth env (spaces)<input id="envs" placeholder="WX_KEY" autocomplete="off" spellcheck="false"></label>
<label class="check"><input type="checkbox" id="sub"> subscribe this project</label>
<h2>actions</h2>
<div id="acts"></div>
<div class="go">
  <button type="button" id="add-get">+ GET</button>
  <button type="button" id="add-post">+ POST</button>
  <button type="button" id="add-set">+ set (vault)</button>
</div>
<h2>doc (after the frontmatter)</h2>
<textarea id="body" rows="6" placeholder="# Name"></textarea>
<h2>markdown this will write</h2>
<pre id="md"></pre>
<div class="go">
  <button type="button" id="save">Save .md</button>
</div>
<p class="note">Save writes <code>~/.config/xlii/plugins/&lt;id&gt;.md</code>. Schema / transforms already on an action stay.</p>
<script>
const INIT = {payload};
const OUTS = {json.dumps(list(OUTPUT_RING))};
let ACTS = (INIT.actions && INIT.actions.length) ? INIT.actions.map(a => Object.assign({{id:"", description:"", method:"GET", url:"", output:"interpret", params:[], keep:{{}}}}, a)) : [];
const KEEP = INIT.keep_meta || {{}};
const CATS = INIT.categories || ["misc"];
document.getElementById("pid").value = INIT.id || "";
document.getElementById("pname").value = INIT.name || "";
document.getElementById("pdesc").value = INIT.description || "";
document.getElementById("effect").value = INIT.effect || "read-only";
document.getElementById("trust").value = INIT.trust || "subscription";
document.getElementById("auth").value = INIT.auth || "none";
document.getElementById("envs").value = (INIT.auth_env||[]).join(" ");
document.getElementById("sub").checked = !!INIT.subscribe;
document.getElementById("body").value = INIT.body || "";

function q(s){{ return JSON.stringify(String(s == null ? "" : s)); }}
function spec(){{
  const envs = (document.getElementById("envs").value||"").trim().split(/\\s+/).filter(Boolean);
  return {{
    id: (document.getElementById("pid").value||"").trim(),
    name: document.getElementById("pname").value||"",
    description: document.getElementById("pdesc").value||"",
    effect: document.getElementById("effect").value,
    trust: document.getElementById("trust").value,
    auth: document.getElementById("auth").value,
    auth_env: envs,
    subscribe: document.getElementById("sub").checked,
    actions: ACTS,
    body: document.getElementById("body").value||"",
    keep_meta: KEEP,
    categories: CATS,
  }};
}}
function mdOf(s){{
  const id = (s.id||"").trim() || "untitled";
  const auth = s.auth === "query" ? "query_param" : (s.auth || "none");
  let env = (s.auth_env||[]).slice();
  if (auth !== "none" && !env.length) env = [id.toUpperCase().replace(/-/g,"_").replace(/\\./g,"_")+"_KEY"];
  let out = "---\\nid: "+id+"\\nname: "+(s.name||id)+"\\n";
  if (s.description) out += "description: "+s.description+"\\n";
  out += "effect: "+(s.effect||"read-only")+"\\ntrust: "+(s.trust||"subscription")+"\\n";
  out += "auth_type: "+auth+"\\nauth_env_vars: ["+env.join(", ")+"]\\nactions:\\n";
  (s.actions||[]).forEach((a) => {{
    if (!a.id) return;
    out += "  - id: "+a.id+"\\n";
    if (a.description) out += "    description: "+a.description+"\\n";
    if (a.method) out += "    method: "+a.method+"\\n";
    if (a.url) out += "    url: "+a.url+"\\n";
    const ps = a.params||[];
    if (!ps.length) out += "    params: {{}}\\n";
    else {{
      out += "    params:\\n";
      ps.forEach((p) => {{
        if (!p.name) return;
        const bits = [];
        if (p.required) bits.push("required: true");
        if (p.secret) bits.push("secret: true");
        if (p.store) bits.push("store: true");
        if (p.default) bits.push("default: "+q(p.default));
        if (p.description) bits.push("description: "+q(p.description));
        out += "      "+p.name+": {{"+(bits.join(", ")||"required: false")+"}}\\n";
      }});
    }}
    if (a.output) out += "    output: "+a.output+"\\n";
    if (a.keep && Object.keys(a.keep).length) out += "    # + kept fields on save\\n";
  }});
  out += "---\\n\\n"+(s.body||"# "+(s.name||id)+"\\n");
  return out;
}}
function paint(){{
  const box = document.getElementById("acts");
  box.innerHTML = "";
  ACTS.forEach((a, i) => {{
    const el = document.createElement("div");
    el.className = "act";
    el.innerHTML =
      "<div class='act-h'>"
      + "<select data-m></select>"
      + "<input class='id' data-id placeholder='action id' autocomplete='off' spellcheck='false'>"
      + "<select data-o></select>"
      + "<button type='button' data-rm>×</button>"
      + "</div>"
      + "<input data-url placeholder='url' autocomplete='off' spellcheck='false'>"
      + "<input data-ad placeholder='description' autocomplete='off' style='margin-top:6px'>"
      + "<div class='params' data-ps></div>"
      + "<button type='button' data-ap>+ param</button>";
    const ms = el.querySelector("[data-m]");
    ["GET","POST","PUT","DELETE","PATCH",""].forEach((v) => {{
      const o = document.createElement("option");
      o.value = v; o.textContent = v || "set";
      if ((a.method||"GET") === v || (!a.method && v === "")) o.selected = true;
      ms.appendChild(o);
    }});
    const os = el.querySelector("[data-o]");
    OUTS.forEach((v) => {{
      const o = document.createElement("option");
      o.value = v; o.textContent = v;
      if ((a.output||"interpret") === v) o.selected = true;
      os.appendChild(o);
    }});
    el.querySelector("[data-id]").value = a.id || "";
    el.querySelector("[data-url]").value = a.url || "";
    el.querySelector("[data-ad]").value = a.description || "";
    const ps = el.querySelector("[data-ps]");
    function paintP(){{
      ps.innerHTML = "";
      (a.params||[]).forEach((p, j) => {{
        const row = document.createElement("div");
        row.className = "par";
        row.innerHTML = "<input class='pn' data-pn placeholder='param' autocomplete='off'>"
          + "<label class='check'><input type='checkbox' data-req> req</label>"
          + "<label class='check'><input type='checkbox' data-sec> secret</label>"
          + "<input data-pd placeholder='default' autocomplete='off'>"
          + "<button type='button' data-pr>×</button>";
        row.querySelector("[data-pn]").value = p.name || "";
        row.querySelector("[data-req]").checked = !!p.required;
        row.querySelector("[data-sec]").checked = !!p.secret;
        row.querySelector("[data-pd]").value = p.default || "";
        row.querySelector("[data-pn]").oninput = (e) => {{ p.name = e.target.value; preview(); }};
        row.querySelector("[data-req]").onchange = (e) => {{ p.required = e.target.checked; preview(); }};
        row.querySelector("[data-sec]").onchange = (e) => {{ p.secret = e.target.checked; if (p.secret) p.store = true; preview(); }};
        row.querySelector("[data-pd]").oninput = (e) => {{ p.default = e.target.value; preview(); }};
        row.querySelector("[data-pr]").onclick = () => {{ a.params.splice(j,1); paint(); }};
        ps.appendChild(row);
      }});
    }}
    paintP();
    ms.onchange = () => {{ a.method = ms.value; preview(); }};
    os.onchange = () => {{ a.output = os.value; preview(); }};
    el.querySelector("[data-id]").oninput = (e) => {{ a.id = e.target.value; preview(); }};
    el.querySelector("[data-url]").oninput = (e) => {{ a.url = e.target.value; preview(); }};
    el.querySelector("[data-ad]").oninput = (e) => {{ a.description = e.target.value; preview(); }};
    el.querySelector("[data-rm]").onclick = () => {{ if (ACTS.length > 1) {{ ACTS.splice(i,1); paint(); }} }};
    el.querySelector("[data-ap]").onclick = () => {{ a.params = a.params || []; a.params.push({{name:"", required:false, secret:false, default:""}}); paint(); }};
    box.appendChild(el);
  }});
  preview();
}}
function preview(){{
  document.getElementById("md").textContent = mdOf(spec());
}}
function add(kind){{
  if (kind === "set") {{
    const env = (document.getElementById("envs").value||"").trim().split(/\\s+/)[0]
      || ((document.getElementById("pid").value||"PLUGIN").toUpperCase().replace(/-/g,"_")+"_KEY");
    ACTS.push({{id:"set", description:"Store the API key in the vault", method:"", url:"", output:"raw",
      params:[{{name:env, required:true, secret:true, store:true, description:"API key"}}], keep:{{}}}});
  }} else {{
    ACTS.push({{id:"", description:"", method:kind, url:"https://", output:"interpret", params:[], keep:{{}}}});
  }}
  paint();
}}
document.getElementById("add-get").onclick = () => add("GET");
document.getElementById("add-post").onclick = () => add("POST");
document.getElementById("add-set").onclick = () => add("set");
["pid","pname","pdesc","effect","trust","auth","envs","body"].forEach((id) => {{
  document.getElementById(id).addEventListener("input", preview);
  document.getElementById(id).addEventListener("change", preview);
}});
document.getElementById("sub").addEventListener("change", preview);
function save(){{
  const s = spec();
  const err = document.getElementById("err");
  if (!s.id) {{ err.textContent = "needs an id"; return; }}
  if (!s.actions.filter(a => a.id).length) {{ err.textContent = "needs an action"; return; }}
  err.textContent = "";
  parent.postMessage({{type:"xlii-plugin-write", spec:s}}, "*");
}}
document.getElementById("save").onclick = () => save();
paint();
</script>
</body></html>"""
