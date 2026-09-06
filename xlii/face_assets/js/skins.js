// Face skins — CSS variable packs on <html data-skin="…">.
// Textual themes do not apply here; these are web skins (local only).
// Persist in localStorage. Picker opens as a side-dock panel (like history).
// Graphical packs (pack:<name>) inject /skins/<name>/skin.css; compiled
// built-ins live in face.css. Catalog merge: FACE_SKIN_META + GET /skins/catalog.

const SKIN_KEY = "xlii-face-skin";
const PACK_LINK_ID = "xlii-pack-css";
const PACK_ID_RE = /^pack:[a-z][a-z0-9_-]{0,31}$/;

/** Ordered catalog: id + human label + blurb for the picker panel. */
export const FACE_SKIN_META = [
  { id: "dark", label: "Dark", blurb: "default night desk" },
  { id: "light", label: "Light", blurb: "day desk · high contrast" },
  { id: "slate", label: "Slate", blurb: "cool blue-gray" },
  { id: "mojo", label: "Mojo", blurb: "companion purple lift" },
];

export const FACE_SKINS = FACE_SKIN_META.map((s) => s.id);

/** Extra packs from GET /skins/catalog (null = not fetched yet). */
let _packMeta = [];
let _catalogFetched = false;

function _allMeta() {
  const seen = new Set(FACE_SKIN_META.map((s) => s.id));
  const extra = [];
  for (const row of _packMeta) {
    if (!row || !row.id || seen.has(row.id)) continue;
    seen.add(row.id);
    extra.push(row);
  }
  return FACE_SKIN_META.concat(extra);
}

function _knownIds() {
  return _allMeta().map((s) => s.id);
}

function _looksLikePack(id) {
  return typeof id === "string" && PACK_ID_RE.test(id);
}

function _packName(id) {
  return _looksLikePack(id) ? id.slice(5) : "";
}

export async function refreshSkinCatalog() {
  try {
    const r = await fetch("/skins/catalog");
    if (!r.ok) {
      _catalogFetched = true;
      return false;
    }
    const data = await r.json();
    const rows = (data && Array.isArray(data.skins)) ? data.skins : [];
    _packMeta = rows.filter((s) => s && _looksLikePack(s.id));
    _catalogFetched = true;
    return true;
  } catch {
    _catalogFetched = true;
    return false;
  }
}

export function currentSkin() {
  const known = _knownIds();
  const d = document.documentElement.getAttribute("data-skin");
  if (d && (known.includes(d) || _looksLikePack(d))) {
    if (!_looksLikePack(d) || known.includes(d) || !_catalogFetched) return d;
  }
  try {
    const s = localStorage.getItem(SKIN_KEY);
    if (s && (known.includes(s) || _looksLikePack(s))) {
      if (!_looksLikePack(s) || known.includes(s) || !_catalogFetched) return s;
    }
  } catch { /* private mode */ }
  return "dark";
}

const _SKIN_VARS = [
  "--bg", "--bg-raised", "--bg-input", "--fg", "--fg-dim",
  "--edge", "--edge-btn", "--mojo", "--code", "--warn", "--error", "--mono",
  "--font-ui", "--font-mono",
  "--chrome-menubar-img", "--chrome-pane-img",
  "--chrome-btn-img", "--chrome-btn-hover-img", "--chrome-btn-active-img",
  "--chrome-input-img", "--chrome-inputbar-img", "--chrome-status-img",
  "--chrome-slot-img", "--chrome-win-img", "--chrome-win-close-img",
  "--texture-bg", "--texture-transcript",
  "--slice-menubar", "--slice-pane", "--slice-dock", "--slice-btn", "--slice-flip", "--slice-scroll",
];

/** CSS the sandboxed form iframes cannot inherit — copy live Face vars in. */
export function embedCssForSkin() {
  const skin = currentSkin();
  const scheme = (skin === "light" || _schemeOf(skin) === "light") ? "light" : "dark";
  let decls = "";
  try {
    const st = getComputedStyle(document.documentElement);
    decls = _SKIN_VARS.map((k) => `${k}: ${st.getPropertyValue(k).trim() || "inherit"};`).join(" ");
  } catch { /* tests / no document */ }
  const css = `:root { color-scheme: ${scheme}; ${decls} }
html, body { color: var(--fg); background: transparent; }
input, select, textarea, button {
  color: var(--fg);
  background: var(--bg-input);
  border-color: var(--edge);
}
input:focus, select:focus, textarea:focus { outline: 1px solid var(--mojo); }
button:hover { background: var(--bg-raised); }`;
  return { skin, scheme, css };
}

function _schemeOf(id) {
  const row = _allMeta().find((s) => s.id === id);
  return row && row.scheme ? row.scheme : "";
}

function pushSkinToForms() {
  const pack = embedCssForSkin();
  document.querySelectorAll("iframe.slot-html-frame").forEach((f) => {
    try {
      f.style.colorScheme = pack.scheme;
      f.contentWindow.postMessage({ type: "xlii-skin", ...pack }, "*");
    } catch { /* sandbox without a window yet */ }
  });
}

function _setPackStylesheet(skin) {
  const pack = _packName(skin);
  const existing = document.getElementById(PACK_LINK_ID);
  if (!pack) {
    if (existing) existing.remove();
    return;
  }
  const href = `/skins/${pack}/skin.css`;
  if (existing) {
    if (existing.getAttribute("href") !== href) existing.setAttribute("href", href);
    return;
  }
  const el = document.createElement("link");
  el.id = PACK_LINK_ID;
  el.rel = "stylesheet";
  el.href = href;
  document.head.appendChild(el);
}

export function applySkin(name, persist) {
  const known = _knownIds();
  let skin = (typeof name === "string") ? name : "dark";
  if (known.includes(skin)) {
    /* keep */
  } else if (_looksLikePack(skin) && !_catalogFetched) {
    /* catalog still loading — apply tentatively so a reload keeps the pack */
  } else if (FACE_SKINS.includes(skin)) {
    /* keep */
  } else {
    skin = "dark";
  }
  // chrome_state re-applies the configured skin on every HUD refresh (each
  // posture flip included). Same skin already on <html> → nothing to do:
  // the root attribute write, the localStorage write and the
  // getComputedStyle pass for the form iframes all forced style work for
  // no visual change.
  if (persist === false && document.documentElement.getAttribute("data-skin") === skin) {
    return skin;
  }
  document.documentElement.setAttribute("data-skin", skin);
  _setPackStylesheet(skin);
  try { localStorage.setItem(SKIN_KEY, skin); } catch { /* ignore */ }
  // Server config survives a Tauri relaunch; localStorage often does not.
  if (persist !== false && window.__xliiWire && typeof window.__xliiWire.send === "function") {
    window.__xliiWire.send({ type: "set_face_skin", skin });
  }
  pushSkinToForms();
  return skin;
}

export function cycleSkin() {
  const ids = _knownIds();
  const cur = currentSkin();
  const i = ids.indexOf(cur);
  const next = ids[(i + 1) % ids.length];
  return applySkin(next);
}

/**
 * Build a local pane-deck snapshot for PaneDeckView.openLocal().
 * Click applies the skin and re-renders with ✓ on the active row.
 */
export function skinPickerPanel() {
  const cur = currentSkin();
  const rows = _allMeta().map((s) => ({
    text: `${s.id === cur ? "✓ " : "  "}${s.label}  · ${s.blurb}`,
    address: `skin:${s.id}`,
    kind: "leaf",
    selected: s.id === cur,
    accent: s.id === cur,
    tone: "",
  }));
  return {
    id: "skins",
    title: "skins:// — face theme",
    note: "click a row to apply · stays until you change it · make your own: selfwiki skin-packs · xlii skin check",
    empty: false,
    rows,
    actions: [
      { name: "cycle", label: "Cycle next" },
    ],
  };
}

/** Wire a PaneDeckView to host the skin picker (local panel, no server). */
export function openSkinPicker(deck, { onApplied } = {}) {
  if (!deck || typeof deck.openLocal !== "function") return;

  const paint = () => {
    const panel = skinPickerPanel();
    deck.openLocal(panel, {
      onRow(row) {
        const id = (row.address || "").startsWith("skin:")
          ? row.address.slice(5)
          : "";
        if (!id) return;
        if (!_knownIds().includes(id) && !_looksLikePack(id) && !FACE_SKINS.includes(id)) return;
        const applied = applySkin(id);
        if (typeof onApplied === "function") onApplied(applied);
        paint(); // refresh ✓
      },
      onAction(name) {
        if (name === "cycle") {
          const applied = cycleSkin();
          if (typeof onApplied === "function") onApplied(applied);
          paint();
        }
      },
    });
  };
  paint();
  refreshSkinCatalog().then((ok) => {
    if (ok) {
      const cur = document.documentElement.getAttribute("data-skin") || currentSkin();
      if (_looksLikePack(cur) && !_knownIds().includes(cur)) {
        applySkin("dark");
      }
    }
    paint();
  });
}

function _boot() {
  applySkin(currentSkin(), false);
  refreshSkinCatalog().then((ok) => {
    const cur = document.documentElement.getAttribute("data-skin") || currentSkin();
    if (ok && _looksLikePack(cur) && !_knownIds().includes(cur)) {
      applySkin("dark");
      return;
    }
    applySkin(cur, false);
  });
}

_boot();

// Globals for menubar (avoid import cycles)
window.__xliiFaceSkin = currentSkin;
window.__xliiApplyFaceSkin = (name) => applySkin(name, false);
window.__xliiCycleFaceSkin = cycleSkin;
window.__xliiOpenSkinPicker = openSkinPicker;
window.__xliiRefreshSkinCatalog = refreshSkinCatalog;
