"""Closed HTML form for Tools → Gigwork.

Seeds ``/gigwork add`` (preset or custom API brain) and ``/jam add``.
Does not write config itself.
"""

from __future__ import annotations

import html
import json
from typing import Any


def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


# Worker tool palettes. Same three /gigwork --kit and /jam member kits.
KITS: tuple[tuple[str, str], ...] = (
    ("explore", "search / read — no shell"),
    ("bash", "can run shell"),
    ("general", "full worker tools"),
)


def form_spec() -> dict[str, Any]:
    from xlii.addressing.builtins.gigwork import ambient_gig_cfg
    from xlii.chat_backend import GIG_PRESETS, GigError, gig_providers
    from xlii.jam import jam_specs

    cfg = ambient_gig_cfg()
    presets = []
    for name in sorted(GIG_PRESETS):
        p = GIG_PRESETS[name]
        presets.append({
            "id": name,
            "model": p.get("model") or "",
            "env": p.get("api_key_env") or "",
            "note": p.get("note") or "",
        })
    providers = []
    jams = []
    err = ""
    try:
        for name, p in sorted(gig_providers(cfg).items()):
            providers.append({
                "id": name,
                "model": p.model,
                "ready": bool(p.key_set or p.key_optional),
            })
        for name, s in sorted(jam_specs(cfg).items()):
            jams.append({
                "id": name,
                "members": " ".join(m.token for m in s.members),
                "merge": s.merge,
            })
    except GigError as e:
        err = str(e)
    brains = ["xai", "gig"] + [p["id"] for p in providers if p["id"] not in ("xai", "gig")]
    spec = {
        "plugin": "gig",
        "action": "make",
        "title": "Gigwork",
        "lead": "Add an API brain (not a harness) or a premade jam. Seed reviews the slash.",
        "presets": presets,
        "providers": providers,
        "jams": jams,
        "kits": [{"id": k, "help": h} for k, h in KITS],
        "brains": brains,
        "error": err,
        "rev": ",".join(p["id"] for p in providers) + "|" + ",".join(g["id"] for g in jams) + "|kits2",
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
.cur li { display: flex; justify-content: space-between; gap: 8px; padding: 4px 0; }
.cur button { padding: 2px 8px; font-size: 12px; }
.empty, .err, .note { opacity: .7; font-size: 13px; }
.err { color: #c66; }
.kits { margin: 0 0 12px; padding: 0; list-style: none; font-size: 13px; }
.kits li { padding: 2px 0; }
.kits code { font-size: 12px; }
.mem { border: 1px solid color-mix(in srgb, CanvasText 16%, transparent); border-radius: 8px; padding: 8px; margin: 0 0 8px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.mem select { min-width: 7rem; }
.mem input { flex: 1 1 8rem; min-width: 7rem; }
.mem button { padding: 4px 8px; font-size: 12px; }
"""


def render_form_html(spec: dict[str, Any]) -> str:
    presets = spec.get("presets") or []
    providers = spec.get("providers") or []
    jams = spec.get("jams") or []
    err = spec.get("error") or ""
    preset_opts = ['<option value="">— preset —</option>']
    for p in presets:
        label = f"{p['id']} · {p.get('model') or ''}"
        preset_opts.append(f'<option value="{_esc(p["id"])}">{_esc(label)}</option>')
    prov_li = []
    for p in providers:
        mark = "ready" if p.get("ready") else "needs key"
        prov_li.append(
            f'<li><span>{_esc(p["id"])} · {_esc(p.get("model") or "")} · {mark}</span>'
            f'<button type="button" data-rm-p="{_esc(p["id"])}">remove</button></li>'
        )
    gag_li = []
    for g in jams:
        gag_li.append(
            f'<li><span>{_esc(g["id"])} · {_esc(g.get("members") or "")}</span>'
            f'<button type="button" data-rm-g="{_esc(g["id"])}">remove</button></li>'
        )
    prov_html = (
        f'<ul class="cur">{"".join(prov_li)}</ul>'
        if prov_li else '<p class="empty">no providers yet</p>'
    )
    gag_html = (
        f'<ul class="cur">{"".join(gag_li)}</ul>'
        if gag_li else '<p class="empty">stock jams only — add one to pin a crew</p>'
    )
    err_html = f'<p class="err">{_esc(err)}</p>' if err else ""
    kits = spec.get("kits") or [{"id": k, "help": h} for k, h in KITS]
    brains = spec.get("brains") or ["xai", "gig"]
    kit_legend = "".join(
        f"<li><code>{_esc(k['id'])}</code> — {_esc(k.get('help') or '')}</li>"
        for k in kits
    )
    payload = json.dumps({
        "kits": [k["id"] for k in kits],
        "brains": brains,
    }, ensure_ascii=False)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{_CSS}</style></head><body>
<h1>{_esc(spec.get("title") or "Gigwork")}</h1>
<p class="lead">{_esc(spec.get("lead") or "")}</p>
{err_html}
<h2>kits (every hire / jam member picks one)</h2>
<ul class="kits">{kit_legend}</ul>
<p class="note">Default is <code>explore</code>. Hire a brain with <code>/gigwork NAME --kit bash …</code>.</p>
<h2>providers now</h2>
{prov_html}
<h2>add a brain (preset)</h2>
<form id="preset">
<label>preset<select name="preset">{"".join(preset_opts)}</select></label>
<label>as (optional)<input name="as" placeholder="leave blank to use preset name" autocomplete="off" spellcheck="false"></label>
<label>model override (optional)<input name="model" placeholder="preset default" autocomplete="off" spellcheck="false"></label>
<div class="go"><button type="button" id="seed-preset">Seed /gigwork add</button></div>
</form>
<h2>add a custom API</h2>
<form id="custom">
<label>name<input name="name" placeholder="my-lab" autocomplete="off" spellcheck="false"></label>
<label>base url<input name="url" placeholder="https://api.example.com/v1" autocomplete="off" spellcheck="false"></label>
<label>api key env<input name="env" placeholder="MY_API_KEY" autocomplete="off" spellcheck="false"></label>
<label>model<input name="model" placeholder="model-id" autocomplete="off" spellcheck="false"></label>
<div class="go"><button type="button" id="seed-custom">Seed /gigwork add --custom</button></div>
</form>
<h2>jams now</h2>
{gag_html}
<h2>premade jam</h2>
<p class="note">Add members one at a time — not a comma list. Each row is one brain + one kit. Seed becomes <code>/jam add name xai:explore kimi:bash</code> (spaces).</p>
<form id="jam">
<label>name<input name="name" placeholder="trio" autocomplete="off" spellcheck="false"></label>
<div id="members"></div>
<div class="go"><button type="button" id="add-mem">+ member</button></div>
<label>merge<select name="merge">
  <option value="synth_conflicts">synth_conflicts — home model judges the answers</option>
  <option value="concat_digest">concat_digest — just stack the answers</option>
</select></label>
<label>cap (0 = all at once)<input name="cap" value="0" autocomplete="off"></label>
<div class="go"><button type="button" id="seed-jam">Seed /jam add</button></div>
</form>
<script>
const META = {payload};
function seed(text){{ parent.postMessage({{type:"xlii-prefill", text:text}}, "*"); }}
document.getElementById("seed-preset").onclick = () => {{
  const fd = new FormData(document.getElementById("preset"));
  const p = (fd.get("preset")||"").trim();
  if (!p) return;
  let line = "/gigwork add " + p;
  const as = (fd.get("as")||"").trim();
  const model = (fd.get("model")||"").trim();
  if (as) line += " --as " + as;
  if (model) line += " --model " + model;
  seed(line);
}};
document.getElementById("seed-custom").onclick = () => {{
  const fd = new FormData(document.getElementById("custom"));
  const name = (fd.get("name")||"").trim();
  const url = (fd.get("url")||"").trim();
  const env = (fd.get("env")||"").trim();
  const model = (fd.get("model")||"").trim();
  if (!name || !url || !env || !model) return;
  seed("/gigwork add --custom " + name + " " + url + " " + env + " " + model);
}};
const MEMBERS = [];
function optHtml(list, cur){{
  return list.map((v) => "<option value='"+v+"'"+(v===cur?" selected":"")+">"+v+"</option>").join("");
}}
function paintMembers(){{
  const box = document.getElementById("members");
  box.innerHTML = "";
  const brains = META.brains || ["xai","gig"];
  const kits = META.kits || ["explore","bash","general"];
  if (!MEMBERS.length) {{
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "no members yet — + member";
    box.appendChild(p);
    return;
  }}
  MEMBERS.forEach((m, i) => {{
    const row = document.createElement("div");
    row.className = "mem";
    row.innerHTML =
      "<select data-b></select>"
      + "<select data-k></select>"
      + "<input data-m placeholder='model (optional)' autocomplete='off' spellcheck='false'>"
      + "<button type='button' data-rm>×</button>";
    const sb = row.querySelector("[data-b]");
    sb.innerHTML = optHtml(brains, m.backend);
    const sk = row.querySelector("[data-k]");
    sk.innerHTML = optHtml(kits, m.kit);
    row.querySelector("[data-m]").value = m.model || "";
    sb.onchange = () => {{ MEMBERS[i].backend = sb.value; }};
    sk.onchange = () => {{ MEMBERS[i].kit = sk.value; }};
    row.querySelector("[data-m]").oninput = (e) => {{ MEMBERS[i].model = e.target.value; }};
    row.querySelector("[data-rm]").onclick = () => {{ MEMBERS.splice(i, 1); paintMembers(); }};
    box.appendChild(row);
  }});
}}
function token(m){{
  let t = m.backend || "xai";
  if (m.kit && m.kit !== "explore") t += ":" + m.kit;
  if ((m.model||"").trim()) t += "@" + m.model.trim();
  return t;
}}
document.getElementById("add-mem").onclick = () => {{
  if (MEMBERS.length >= 6) return;
  MEMBERS.push({{backend: (META.brains||["xai"])[0], kit: "explore", model: ""}});
  paintMembers();
}};
document.getElementById("seed-jam").onclick = () => {{
  const fd = new FormData(document.getElementById("jam"));
  const name = (fd.get("name")||"").trim();
  const toks = MEMBERS.map(token).filter(Boolean);
  if (!name || !toks.length) return;
  let line = "/jam add " + name + " " + toks.join(" ");
  const merge = (fd.get("merge")||"").trim();
  const cap = (fd.get("cap")||"").trim();
  if (merge) line += " --merge " + merge;
  if (cap && cap !== "0") line += " --cap " + cap;
  seed(line);
}};
paintMembers();
document.querySelectorAll("[data-rm-p]").forEach((btn) => {{
  btn.onclick = () => {{
    const t = btn.getAttribute("data-rm-p") || "";
    if (t) seed("/gigwork rm " + t);
  }};
}});
document.querySelectorAll("[data-rm-g]").forEach((btn) => {{
  btn.onclick = () => {{
    const t = btn.getAttribute("data-rm-g") || "";
    if (t) seed("/jam rm " + t);
  }};
}});
</script>
</body></html>"""
