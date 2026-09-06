/** Closed HTML slot body for taskmake / pluginmake.

The form is a sandboxed srcdoc (no open web). Seed still reviews the slash
in the parent input. Occupancy is the other visual slot (listing | form).
Srcdoc cannot inherit Face ``data-skin`` vars — ``themedSrcdoc`` copies them in.
*/

import { embedCssForSkin } from "./skins.js?v=flip2";

const TASK_SHAPES = ["linear", "params", "verdict", "rc", "split"];
const TASK_PARAMS = ["base", "path", "query", "ref", "topic"];
const TASK_BRANCHES = ["yes,no", "clean,dirty", "ok,fail", "ship,hold"];
const TASK_ARMS = ["a,b", "lint,types", "unit,integ"];
const TASK_POLICY = ["all", "any", "first_ok"];

const PLUGIN_EFFECT = ["read-only", "external-write", "local-system", "destructive"];
const PLUGIN_TRUST = ["subscription", "always-confirm"];
const PLUGIN_AUTH = ["none", "query", "header"];
const PLUGIN_OUTPUT = ["schema", "raw", "interpret"];

const _CSS = `
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
form { display: flex; flex-direction: column; gap: 12px; max-width: 36rem; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; opacity: .85; }
input, select {
  font: 14px/1.3 inherit; color: inherit;
  background: color-mix(in srgb, CanvasText 6%, transparent);
  border: 1px solid color-mix(in srgb, CanvasText 22%, transparent);
  border-radius: 6px; padding: 8px 10px;
}
input:focus, select:focus { outline: 1px solid color-mix(in srgb, CanvasText 45%, transparent); }
.check { flex-direction: row; align-items: center; gap: 8px; font-size: 14px; opacity: 1; }
.go { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
button {
  font: 13px inherit; color: inherit; cursor: pointer;
  background: color-mix(in srgb, CanvasText 10%, transparent);
  border: 1px solid color-mix(in srgb, CanvasText 28%, transparent);
  border-radius: 6px; padding: 8px 12px;
}
button:hover { background: color-mix(in srgb, CanvasText 16%, transparent); }
.extras { display: flex; flex-direction: column; gap: 12px; }
`;

function _opts(list, cur) {
  return list.map((o) => `<option value="${o}"${o === cur ? " selected" : ""}>${o}</option>`).join("");
}

function _field(lab, control) {
  return `<label>${lab}${control}</label>`;
}

function _quoteJs() {
  return `function q(s){return '"'+String(s).replace(/\\\\/g,'\\\\\\\\').replace(/"/g,'\\\\"')+'"';}`;
}

function _taskSrc() {
  return `<!doctype html><html><head><meta charset="utf-8"><style>${_CSS}</style></head><body>
<h1>Task maker</h1>
<p class="lead">Name the pipe, pick a shape. Seed reviews the slash — send writes it.</p>
<form id="f">
${_field("name", `<input name="name" value="linear" placeholder="task name" autocomplete="off" spellcheck="false">`)}
${_field("start from (optional)", `<input name="clone" placeholder="stock name" autocomplete="off" spellcheck="false">`)}
${_field("shape", `<select name="shape">${_opts(TASK_SHAPES, "linear")}</select>`)}
<div class="extras" id="extras"></div>
<div class="go">
  <button type="button" id="scaf">Seed scaffold</button>
  <button type="button" id="draft">Draft with xlii</button>
</div>
</form>
<script>
${_quoteJs()}
const SHAPES = ${JSON.stringify(TASK_SHAPES)};
const PARAMS = ${JSON.stringify(TASK_PARAMS)};
const BRANCHES = ${JSON.stringify(TASK_BRANCHES)};
const ARMS = ${JSON.stringify(TASK_ARMS)};
const POLICY = ${JSON.stringify(TASK_POLICY)};
const f = document.getElementById("f");
const extras = document.getElementById("extras");
function seed(text){ parent.postMessage({type:"xlii-prefill", text:text}, "*"); }
function cmd(kind){
  const fd = new FormData(f);
  const name = (fd.get("name")||"").trim() || "linear";
  const clone = (fd.get("clone")||"").trim();
  if (clone) return "/tasks new "+name+" --clone "+clone;
  const shape = fd.get("shape") || "linear";
  let extra = "";
  if (shape === "params") extra = " --param "+(fd.get("param")||"base");
  else if (shape === "verdict") extra = " --branches "+(fd.get("branches")||"yes,no");
  else if (shape === "split") extra = " --arms "+(fd.get("arms")||"a,b")+" --policy "+(fd.get("policy")||"all");
  if (kind === "draft") {
    const intent = {
      params: "a pipeline that takes a "+(fd.get("param")||"base")+" parameter and uses it",
      verdict: "classify the situation as "+String(fd.get("branches")||"yes,no").replace(/,/g," / ")+" and branch on that verdict",
      rc: "run a check; on failure triage, otherwise note success",
      split: "run "+String(fd.get("arms")||"a,b").replace(/,/g," and ")+" in parallel ("+(fd.get("policy")||"all")+"), then summarize",
    }[shape] || "a short linear pipe: gather something, then ask the agent to summarize";
    return "/tasks new "+name+" --from "+q(intent);
  }
  return "/tasks new "+name+" --shape "+shape+extra;
}
function sel(name, opts, cur){
  return "<label>"+name+"<select name='"+name+"'>"+opts.map(o=>"<option"+(o===cur?" selected":"")+">"+o+"</option>").join("")+"</select></label>";
}
function sync(){
  const clone = (f.clone.value||"").trim();
  f.shape.disabled = !!clone;
  extras.innerHTML = "";
  if (clone) return;
  const s = f.shape.value;
  if (s === "params") extras.innerHTML = sel("param", PARAMS, "base");
  else if (s === "verdict") extras.innerHTML = sel("branches", BRANCHES, "yes,no");
  else if (s === "split") extras.innerHTML = sel("arms", ARMS, "a,b")+sel("policy", POLICY, "all");
}
f.shape.addEventListener("change", sync);
f.clone.addEventListener("input", sync);
document.getElementById("scaf").onclick = () => seed(cmd("scaffold"));
document.getElementById("draft").onclick = () => seed(cmd("draft"));
f.addEventListener("submit", (e) => { e.preventDefault(); seed(cmd("scaffold")); });
sync();
</script>
</body></html>`;
}

function _pluginSrc() {
  return `<!doctype html><html><head><meta charset="utf-8"><style>${_CSS}</style></head><body>
<h1>Plugin maker</h1>
<p class="lead">Id + badges. Seed reviews /plugin new — send writes the stub.</p>
<form id="f">
${_field("id", `<input name="id" value="my-api" placeholder="plugin id" autocomplete="off" spellcheck="false">`)}
${_field("effect", `<select name="effect">${_opts(PLUGIN_EFFECT, "read-only")}</select>`)}
${_field("trust", `<select name="trust">${_opts(PLUGIN_TRUST, "subscription")}</select>`)}
${_field("auth", `<select name="auth">${_opts(PLUGIN_AUTH, "none")}</select>`)}
${_field("output", `<select name="output">${_opts(PLUGIN_OUTPUT, "interpret")}</select>`)}
<label class="check"><input type="checkbox" name="subscribe" checked> subscribe here</label>
<div class="go"><button type="button" id="go">Seed /plugin new</button></div>
</form>
<script>
function seed(text){ parent.postMessage({type:"xlii-prefill", text:text}, "*"); }
function cmd(){
  const fd = new FormData(document.getElementById("f"));
  const id = (fd.get("id")||"").trim() || "my-api";
  let c = "/plugin new "+id
    +" --effect "+(fd.get("effect")||"read-only")
    +" --trust "+(fd.get("trust")||"subscription")
    +" --auth "+(fd.get("auth")||"none")
    +" --output "+(fd.get("output")||"interpret");
  if (fd.get("subscribe")) c += " --subscribe";
  return c;
}
document.getElementById("go").onclick = () => seed(cmd());
document.getElementById("f").addEventListener("submit", (e) => { e.preventDefault(); seed(cmd()); });
</script>
</body></html>`;
}

export function isMakerPane(id) {
  return id === "taskmake" || id === "pluginmake";
}

export function isHtmlSlotPane(id) {
  return id === "taskmake" || id === "pluginmake" || id === "pluginform"
    || id === "bindmake" || id === "gigmake" || id === "remotemake";
}

const _formDrafts = new Map();

function _formKey(plugin, action, name) {
  const extra = name ? `/${name}` : "";
  return `${plugin || ""}/${action || ""}${extra}`;
}

/** True only when `source` is one of this page's own sandboxed form frames.
 * e.origin is useless here — sandboxed srcdoc frames are "null", and so is
 * any attacker's sandboxed frame. e.source cannot be forged cross-origin,
 * so a page holding Face as window.opener cannot impersonate a form frame. */
function _isOwnSlotFrame(source) {
  if (!source) return false;
  const frames = document.querySelectorAll("iframe.slot-html-frame");
  for (const frame of frames) {
    if (frame.contentWindow === source) return true;
  }
  return false;
}

function _listenPluginCall() {
  if (window.__xliiPluginCallMsg) return;
  window.__xliiPluginCallMsg = true;
  window.addEventListener("message", (e) => {
    if (!_isOwnSlotFrame(e.source)) return;
    const d = e.data;
    if (!d) return;
    if (d.type === "xlii-plugin-draft" && d.plugin && d.action) {
      _formDrafts.set(_formKey(d.plugin, d.action), d.params && typeof d.params === "object" ? d.params : {});
      return;
    }
    if (d.type !== "xlii-plugin-call") return;
    if (typeof d.plugin !== "string" || typeof d.action !== "string") return;
    const w = window.__xliiWire;
    if (w && typeof w.send === "function") {
      w.send({
        type: "plugin_call",
        plugin: d.plugin,
        action: d.action,
        params: d.params && typeof d.params === "object" ? d.params : {},
      });
    }
  });
}

function _restoreDraft(frame, key) {
  const draft = _formDrafts.get(key);
  if (!draft || !frame || !frame.contentWindow) return;
  const send = () => {
    try {
      frame.contentWindow.postMessage({ type: "xlii-plugin-draft-restore", params: draft }, "*");
    } catch {
      /* sandboxed frame not ready */
    }
  };
  if (frame.contentDocument && frame.contentDocument.readyState === "complete") send();
  else frame.addEventListener("load", send, { once: true });
}

/** Forward a wire result into the open plugin form iframe. Success closes. */
export function applyPluginFormStatus(ev) {
  if (!ev) return;
  if (ev.ok) {
    _formDrafts.delete(_formKey(ev.plugin, ev.action));
  }
  if (ev.close && ev.ok) {
    return;
  }
  const frame = document.querySelector("iframe.slot-html-frame[data-pane=pluginform]");
  if (!frame || !frame.contentWindow) return;
  frame.contentWindow.postMessage({
    type: "xlii-plugin-result",
    ok: !!ev.ok,
    text: ev.text || "",
  }, "*");
}

/** Paint a plugin action form (server-generated srcdoc) into the slot. */
export function paintPluginForm(rowsEl, form) {
  if (!rowsEl || !form) return false;
  _listenPrefill();
  _listenPluginCall();
  const key = _formKey(form.plugin, form.action, form.id || form.name);
  const rev = String(form.rev || (form.html && form.html.length) || "");
  const existing = rowsEl.querySelector("iframe.slot-html-frame");
  // Same form key = the live editor. Do not remount on a chrome/snapshot
  // tick (new rev after save, or hash jitter) — that is the blink.
  if (existing && existing.dataset.form === key) {
    if (rev) existing.dataset.rev = rev;
    _restoreDraft(existing, key);
    return true;
  }
  if (!form.html) return false;
  rowsEl.textContent = "";
  rowsEl.classList.add("slot-html");
  const frame = document.createElement("iframe");
  frame.className = "slot-html-frame";
  frame.dataset.pane = form.plugin === "bind" ? "bindmake"
    : form.plugin === "task" ? "taskmake"
    : form.plugin === "pluginmake" ? "pluginmake"
    : form.plugin === "gig" ? "gigmake"
    : form.plugin === "remote" ? "remotemake"
    : "pluginform";
  frame.dataset.form = key;
  frame.dataset.rev = rev;
  frame.setAttribute("sandbox", "allow-forms allow-scripts");
  frame.setAttribute("title", form.title || "plugin form");
  frame.srcdoc = themedSrcdoc(form.html);
  frame.addEventListener("load", () => _restoreDraft(frame, key), { once: true });
  rowsEl.appendChild(frame);
  return true;
}

/** Stamp the live Face skin into a closed srcdoc (iframes do not inherit). */
export function themedSrcdoc(html) {
  const pack = embedCssForSkin();
  const snippet = `<style id="xlii-skin">${pack.css}</style>
<meta name="color-scheme" content="${pack.scheme}">
<script>
(function(){
  document.documentElement.setAttribute("data-skin", ${JSON.stringify(pack.skin)});
  window.addEventListener("message", function(e){
    if (e.source !== window.parent) return;
    var d = e.data;
    if (!d || d.type !== "xlii-skin" || typeof d.css !== "string") return;
    var el = document.getElementById("xlii-skin");
    if (el) el.textContent = d.css;
    if (d.skin) document.documentElement.setAttribute("data-skin", d.skin);
  });
})();
</script>`;
  const raw = String(html || "");
  if (/<\/head>/i.test(raw)) return raw.replace(/<\/head>/i, `${snippet}</head>`);
  return `<!doctype html><html><head>${snippet}</head><body>${raw}</body></html>`;
}

export function makerSrcdoc(paneId) {
  if (paneId === "taskmake") return _taskSrc();
  if (paneId === "pluginmake") return _pluginSrc();
  return "";
}

function _listenPrefill() {
  if (window.__xliiMakerMsg) return;
  window.__xliiMakerMsg = true;
  window.addEventListener("message", (e) => {
    if (!_isOwnSlotFrame(e.source)) return;
    const d = e.data;
    if (!d) return;
    if (d.type === "xlii-task-write" && d.spec && typeof d.spec === "object") {
      const w = window.__xliiWire;
      if (w && typeof w.send === "function") {
        w.send({ type: "task_write", spec: d.spec, run: !!d.run });
      }
      return;
    }
    if (d.type === "xlii-plugin-write" && d.spec && typeof d.spec === "object") {
      const w = window.__xliiWire;
      if (w && typeof w.send === "function") {
        w.send({ type: "plugin_write", spec: d.spec });
      }
      return;
    }
    if (d.type !== "xlii-prefill" || typeof d.text !== "string") return;
    if (window.__xliiBar && typeof window.__xliiBar.seed === "function") {
      window.__xliiBar.seed(d.text);
    }
  });
}

/** Paint a closed HTML document into the slot host (iframe srcdoc). */
export function paintMakerForm(rowsEl, paneId) {
  if (!rowsEl || !isMakerPane(paneId)) return false;
  _listenPrefill();
  const existing = rowsEl.querySelector("iframe.slot-html-frame");
  if (existing && existing.dataset.pane === paneId) return true;
  rowsEl.textContent = "";
  rowsEl.classList.add("slot-html");
  const frame = document.createElement("iframe");
  frame.className = "slot-html-frame";
  frame.dataset.pane = paneId;
  frame.setAttribute("sandbox", "allow-forms allow-scripts");
  frame.setAttribute("title", paneId === "taskmake" ? "task maker" : "plugin maker");
  frame.srcdoc = themedSrcdoc(makerSrcdoc(paneId));
  rowsEl.appendChild(frame);
  return true;
}

export const _test = { _taskCmd: null, _pluginCmd: null, TASK_SHAPES, PLUGIN_EFFECT, makerSrcdoc };
