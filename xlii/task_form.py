"""Task maker — a real pipe: bash, slash, ask. Save writes the TOML.

New and edit are the same form. ``Draft with xlii`` is not this: that seeded
``/tasks new --from`` so the *agent* invented a file. Here the steps *are* the
file.
"""

from __future__ import annotations

import html
import json
from typing import Any


def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def empty_spec(name: str = "") -> dict[str, Any]:
    return {
        "name": name or "",
        "description": "",
        "class": "",
        "steps": [{"kind": "shell", "id": "", "body": "echo hello"}],
        "params": [],
        "edges": [],
        "error": "",
        "exists": False,
    }


def form_spec(xli_dir, name: str = "") -> dict[str, Any]:
    from xlii import tasks as T

    name = (name or "").strip()
    data = empty_spec(name)
    err = ""
    if name and xli_dir:
        try:
            pipe = T.load_pipeline(xli_dir, name)
            data.update(T.pipeline_as_spec(pipe))
            data["exists"] = True
        except T.TaskNotFound:
            data["name"] = name
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    data["error"] = err
    spec = {
        "plugin": "task",
        "action": "compose",
        "title": f"Task · {data['name']}" if data.get("name") else "Task maker",
        "lead": "Each row is a step. + bash · + slash · + ask. Save writes the .toml.",
        "name": data.get("name") or "",
        "description": data.get("description") or "",
        "class": data.get("class") or "",
        "steps": data.get("steps") or [],
        "params": data.get("params") or [],
        "edges": data.get("edges") or [],
        "exists": bool(data.get("exists")),
        "error": err,
    }
    spec["rev"] = str(hash(json.dumps({
        "n": spec["name"], "s": spec["steps"], "p": spec["params"], "e": spec["edges"],
    }, sort_keys=True, default=str)))
    spec["html"] = render_form_html(spec)
    return spec


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
input, select, textarea {
  font: 13px/1.35 inherit; color: inherit;
  background: color-mix(in srgb, CanvasText 6%, transparent);
  border: 1px solid color-mix(in srgb, CanvasText 22%, transparent);
  border-radius: 6px; padding: 7px 9px;
}
textarea { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; min-height: 2.6em; resize: vertical; }
.step {
  border: 1px solid color-mix(in srgb, CanvasText 16%, transparent);
  border-radius: 8px; padding: 8px; margin: 0 0 8px;
}
.step-h { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.step-h select { width: 6.5rem; }
.step-h input.id { width: 7rem; }
.step-h button { padding: 4px 8px; font-size: 12px; }
.go { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
button {
  font: 13px inherit; color: inherit; cursor: pointer;
  background: color-mix(in srgb, CanvasText 10%, transparent);
  border: 1px solid color-mix(in srgb, CanvasText 28%, transparent);
  border-radius: 6px; padding: 8px 12px;
}
button:hover { background: color-mix(in srgb, CanvasText 16%, transparent); }
pre#toml {
  font: 11px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  white-space: pre-wrap;
  background: color-mix(in srgb, CanvasText 6%, transparent);
  border: 1px solid color-mix(in srgb, CanvasText 18%, transparent);
  border-radius: 6px; padding: 10px 12px;
  max-height: 12rem; overflow: auto;
}
.err { color: #c66; font-size: 13px; min-height: 1.2em; }
.note { font-size: 12px; opacity: .65; }
"""


def render_form_html(spec: dict[str, Any]) -> str:
    payload = json.dumps({
        "name": spec.get("name") or "",
        "description": spec.get("description") or "",
        "class": spec.get("class") or "",
        "steps": spec.get("steps") or [],
        "params": spec.get("params") or [],
        "edges": spec.get("edges") or [],
        "exists": bool(spec.get("exists")),
    }, ensure_ascii=False)
    err = spec.get("error") or ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{_CSS}</style></head><body>
<h1>{_esc(spec.get("title") or "Task maker")}</h1>
<p class="lead">{_esc(spec.get("lead") or "")}</p>
<p class="err" id="err">{_esc(err)}</p>
<label class="top">name<input id="name" autocomplete="off" spellcheck="false"></label>
<label class="top">description<input id="desc" autocomplete="off"></label>
<label class="top">class (optional)<input id="klass" placeholder="system" autocomplete="off" spellcheck="false"></label>
<h2>steps</h2>
<div id="steps"></div>
<div class="go">
  <button type="button" id="add-bash">+ bash</button>
  <button type="button" id="add-slash">+ slash</button>
  <button type="button" id="add-ask">+ ask</button>
</div>
<h2>toml this will write</h2>
<pre id="toml"></pre>
<div class="go">
  <button type="button" id="save">Save .toml</button>
  <button type="button" id="run">Save + seed run</button>
</div>
<p class="note">Save writes <code>.xlii/tasks/&lt;name&gt;.toml</code>. It does not ask the agent to invent a pipe.</p>
<script>
const INIT = {payload};
let STEPS = (INIT.steps && INIT.steps.length) ? INIT.steps.map(s => Object.assign({{kind:"shell", id:"", body:""}}, s)) : [{{kind:"shell", id:"", body:"echo hello"}}];
const EDGES = INIT.edges || [];
const PARAMS = INIT.params || [];
document.getElementById("name").value = INIT.name || "";
document.getElementById("desc").value = INIT.description || "";
document.getElementById("klass").value = INIT.class || "";

function q(s){{ return JSON.stringify(String(s == null ? "" : s)); }}
function spec(){{
  return {{
    name: (document.getElementById("name").value||"").trim(),
    description: document.getElementById("desc").value||"",
    class: (document.getElementById("klass").value||"").trim(),
    steps: STEPS,
    params: PARAMS,
    edges: EDGES,
  }};
}}
function tomlOf(s){{
  const name = (s.name||"").trim() || "untitled";
  let out = "name = " + q(name) + "\\n";
  if (s.class) out += "class = " + q(s.class) + "\\n";
  if (s.description) out += "description = " + q(s.description) + "\\n";
  out += "\\n";
  (s.params||[]).forEach((p) => {{
    if (!p.name) return;
    out += "[params." + p.name + "]\\n";
    if (p.required) out += "required = true\\n";
    if (p.default) out += "default = " + q(p.default) + "\\n";
    if (p.help) out += "help = " + q(p.help) + "\\n";
    out += "\\n";
  }});
  (s.steps||[]).forEach((st) => {{
    out += "[[step]]\\n";
    if (st.id) out += "id = " + q(st.id) + "\\n";
    if (st.kind === "split") {{
      const br = (st.split||[]).map(q).join(", ");
      out += "split = [" + br + "]\\n";
      if (st.join) out += "join = " + q(st.join) + "\\n";
      if (st.policy) out += "policy = " + q(st.policy) + "\\n";
    }} else {{
      const key = st.kind === "slash" ? "slash" : (st.kind === "agent" ? "ask" : "run");
      out += key + " = " + q(st.body||"") + "\\n";
      if (st.on_success) out += "on_success = " + q(st.on_success) + "\\n";
      if (st.on_failure) out += "on_failure = " + q(st.on_failure) + "\\n";
    }}
    out += "\\n";
  }});
  (s.edges||[]).forEach((e) => {{
    if (!e.from || !e.to) return;
    out += "[[edge]]\\nfrom = " + q(e.from) + "\\n";
    if (e.branch) out += "when = {{ branch = " + q(e.branch) + " }}\\n";
    out += "to = " + q(e.to) + "\\n\\n";
  }});
  return out;
}}
function paint(){{
  const box = document.getElementById("steps");
  box.innerHTML = "";
  STEPS.forEach((st, i) => {{
    const el = document.createElement("div");
    el.className = "step";
    if (st.kind === "split") {{
      el.innerHTML = "<div class='step-h'><strong>split</strong> "+(st.split||[]).join(", ")+" → "+(st.join||"?")+" <span class='note'>Task+ fan-out (kept)</span></div>";
      box.appendChild(el);
      return;
    }}
    el.innerHTML =
      "<div class='step-h'>"
      + "<select data-k></select>"
      + "<input class='id' data-id placeholder='id' autocomplete='off' spellcheck='false'>"
      + "<button type='button' data-up>↑</button>"
      + "<button type='button' data-dn>↓</button>"
      + "<button type='button' data-rm>×</button>"
      + "</div>"
      + "<textarea data-body rows='2' placeholder='command'></textarea>";
    const sel = el.querySelector("[data-k]");
    [["shell","bash"],["slash","slash /"],["agent","ask"]].forEach(([v,l]) => {{
      const o = document.createElement("option");
      o.value = v; o.textContent = l;
      if (st.kind === v) o.selected = true;
      sel.appendChild(o);
    }});
    el.querySelector("[data-id]").value = st.id || "";
    const ta = el.querySelector("[data-body]");
    ta.value = st.body || "";
    ta.placeholder = st.kind === "slash" ? "/command {{prev}}" : (st.kind === "agent" ? "what should xlii do with {{prev}}?" : "bash — echo, git, xlii new…");
    sel.onchange = () => {{ STEPS[i].kind = sel.value; paint(); }};
    el.querySelector("[data-id]").oninput = (e) => {{ STEPS[i].id = e.target.value; preview(); }};
    ta.oninput = (e) => {{ STEPS[i].body = e.target.value; preview(); }};
    el.querySelector("[data-rm]").onclick = () => {{ if (STEPS.length > 1) {{ STEPS.splice(i,1); paint(); }} }};
    el.querySelector("[data-up]").onclick = () => {{ if (i) {{ const t = STEPS[i-1]; STEPS[i-1] = STEPS[i]; STEPS[i] = t; paint(); }} }};
    el.querySelector("[data-dn]").onclick = () => {{ if (i < STEPS.length-1) {{ const t = STEPS[i+1]; STEPS[i+1] = STEPS[i]; STEPS[i] = t; paint(); }} }};
    box.appendChild(el);
  }});
  preview();
}}
function preview(){{
  document.getElementById("toml").textContent = tomlOf(spec());
}}
function add(kind){{
  const body = kind === "slash" ? "/" : (kind === "agent" ? "" : "");
  STEPS.push({{kind: kind, id: "", body: body}});
  paint();
}}
document.getElementById("add-bash").onclick = () => add("shell");
document.getElementById("add-slash").onclick = () => add("slash");
document.getElementById("add-ask").onclick = () => add("agent");
["name","desc","klass"].forEach((id) => document.getElementById(id).addEventListener("input", preview));
function save(thenRun){{
  const s = spec();
  const err = document.getElementById("err");
  if (!s.name) {{ err.textContent = "needs a name"; return; }}
  if (!s.steps.length) {{ err.textContent = "needs a step"; return; }}
  err.textContent = "";
  parent.postMessage({{type:"xlii-task-write", spec:s, run: !!thenRun}}, "*");
}}
document.getElementById("save").onclick = () => save(false);
document.getElementById("run").onclick = () => save(true);
paint();
</script>
</body></html>"""
