"""Closed HTML picker for task chrome binds.

Shows available tasks, F-keys (commander default vs bound), and menus.
Submit seeds ``/bind`` — it does not write the file.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from xlii.binds import (
    COMMANDER_FKEY_LABELS,
    MENUS,
    FKEY_MAX,
    fkey_bind,
    load_binds,
)


def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def task_choices(xli_dir: Path | str | None) -> list[dict[str, str]]:
    from xlii import tasks as T

    if not xli_dir:
        return []
    rows = []
    for name, origin in T.list_pipeline_entries(xli_dir):
        klass = T.peek_task_class(T.pipeline_file(xli_dir, name))
        badge = T.listing_badge(origin, klass)
        rows.append({"id": name, "origin": origin, "class": klass, "badge": badge})
    return rows


def fkey_choices(xli_dir: Path | str | None) -> list[dict[str, str]]:
    binds = load_binds(xli_dir)
    defaults = dict(COMMANDER_FKEY_LABELS)
    rows = []
    for n in range(1, FKEY_MAX + 1):
        key = f"f{n}"
        hit = fkey_bind(binds, key)
        default = defaults.get(key, "")
        rows.append({
            "key": key,
            "default": default or "(free)",
            "bound": hit.task if hit else "",
            "label": hit.display() if hit else (default or "(free)"),
        })
    return rows


def form_spec(xli_dir: Path | str | None = None) -> dict[str, Any]:
    binds = [
        {
            "task": b.task,
            "menu": b.menu,
            "fkey": b.fkey,
            "label": b.display(),
            "origin": b.origin,
        }
        for b in load_binds(xli_dir)
    ]
    spec = {
        "plugin": "bind",
        "action": "chrome",
        "title": "Bind chrome",
        "lead": "Pick a task and a button. Seed reviews /bind — send writes it.",
        "tasks": task_choices(xli_dir),
        "fkeys": fkey_choices(xli_dir),
        "menus": sorted(MENUS),
        "binds": binds,
        "rev": ",".join(f"{b['task']}:{b.get('menu','')}:{b.get('fkey','')}" for b in binds) or "empty",
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
form { display: flex; flex-direction: column; gap: 12px; max-width: 36rem; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; opacity: .85; }
select, input {
  font: 14px/1.3 inherit; color: inherit;
  background: color-mix(in srgb, CanvasText 6%, transparent);
  border: 1px solid color-mix(in srgb, CanvasText 22%, transparent);
  border-radius: 6px; padding: 8px 10px;
}
select:focus, input:focus { outline: 1px solid color-mix(in srgb, CanvasText 45%, transparent); }
.go { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
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
.empty { opacity: .55; font-size: 13px; }
"""


def render_form_html(spec: dict[str, Any]) -> str:
    tasks = spec.get("tasks") or []
    fkeys = spec.get("fkeys") or []
    menus = spec.get("menus") or []
    binds = spec.get("binds") or []
    task_opts = ['<option value="">— pick a task —</option>']
    for t in tasks:
        badge = t.get("badge") or t.get("origin") or ""
        label = t["id"] + (f" · {badge}" if badge else "")
        task_opts.append(f'<option value="{_esc(t["id"])}">{_esc(label)}</option>')
    menu_opts = ['<option value="">(none)</option>']
    for m in menus:
        menu_opts.append(f'<option value="{_esc(m)}">{_esc(m)}</option>')
    fkey_opts = ['<option value="">(none)</option>']
    for fk in fkeys:
        key = fk["key"]
        if fk.get("bound"):
            shown = f"{key.upper()} · {fk['bound']}"
        elif fk.get("default") and fk["default"] != "(free)":
            shown = f"{key.upper()} · {fk['default']} (default)"
        else:
            shown = f"{key.upper()} · free"
        fkey_opts.append(f'<option value="{_esc(key)}">{_esc(shown)}</option>')
    cur = []
    for b in binds:
        bits = [b.get("task") or ""]
        if b.get("menu"):
            bits.append(f"menu={b['menu']}")
        if b.get("fkey"):
            bits.append(b["fkey"])
        cur.append(
            "<li><span>" + _esc(" · ".join(bits)) + "</span>"
            f"<button type=\"button\" data-rm=\"{_esc(b.get('task') or '')}\">clear</button></li>"
        )
    cur_html = (
        f'<ul class="cur">{"".join(cur)}</ul>'
        if cur else '<p class="empty">nothing pinned yet</p>'
    )
    payload = json.dumps({"tasks": [t["id"] for t in tasks]}, ensure_ascii=False)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{_CSS}</style></head><body>
<h1>{_esc(spec.get("title") or "Bind chrome")}</h1>
<p class="lead">{_esc(spec.get("lead") or "")}</p>
<h2>now</h2>
{cur_html}
<h2>pin</h2>
<form id="f">
<label>task<select name="task">{"".join(task_opts)}</select></label>
<label>menu<select name="menu">{"".join(menu_opts)}</select></label>
<label>F-key<select name="fkey">{"".join(fkey_opts)}</select></label>
<label>label (optional)<input name="label" autocomplete="off" spellcheck="false"></label>
<div class="go">
  <button type="button" id="seed">Seed bind</button>
</div>
</form>
<script>
const META = {payload};
function seed(text){{ parent.postMessage({{type:"xlii-prefill", text:text}}, "*"); }}
const f = document.getElementById("f");
document.getElementById("seed").addEventListener("click", () => {{
  const fd = new FormData(f);
  const task = (fd.get("task")||"").trim();
  if (!task) return;
  let line = "/bind " + task;
  const menu = (fd.get("menu")||"").trim();
  const fkey = (fd.get("fkey")||"").trim();
  const label = (fd.get("label")||"").trim();
  if (menu) line += " menu=" + menu;
  if (fkey) line += " fkey=" + fkey;
  if (label) line += " label=" + label;
  seed(line);
}});
document.querySelectorAll("[data-rm]").forEach((btn) => {{
  btn.addEventListener("click", () => {{
    const t = btn.getAttribute("data-rm") || "";
    if (t) seed("/bind rm " + t);
  }});
}});
</script>
</body></html>"""
