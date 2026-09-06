// Dual-slot split: one gutter in the center of #workspace.
// Drag follows the mouse (divider = cursor). Visual-left width is stored
// as a percent of #workspace so swapping sides or switching panels
// does not jump. Config "panel width · N%" still applies when the knob
// changes; a remembered drag wins on boot.

const STORE_SPLIT = "xlii-face-split-left";
const STORE_W = "xlii-face-pane-w";
const STORE_UNIT = "xlii-face-pane-w-unit";
const STORE_SIDE = "xlii-face-pane-side";
const MIN = 160;

function workspace() {
  return document.getElementById("workspace");
}

function hostWidth() {
  const el = workspace();
  return (el && el.clientWidth) || window.innerWidth || 800;
}

function paneSide() {
  const raw = (document.documentElement.dataset.paneSide
    || (workspace() && workspace().dataset.paneSide)
    || "right");
  return raw === "left" ? "left" : "right";
}

function clampLeft(px) {
  const host = hostWidth();
  const max = Math.max(MIN, host - MIN);
  return Math.max(MIN, Math.min(max, Math.round(px)));
}

function applySplitPx(px) {
  const w = `${clampLeft(px)}px`;
  const ws = workspace();
  if (ws) ws.style.setProperty("--split-left", w);
}

function persistLeftPx(px) {
  const host = hostWidth();
  if (host <= 0) return;
  const pct = Math.round((clampLeft(px) / host) * 100);
  try {
    localStorage.setItem(STORE_SPLIT, String(Math.max(1, Math.min(99, pct))));
  } catch { /* private */ }
}

function hasStoredSplit() {
  try {
    const n = parseInt(localStorage.getItem(STORE_SPLIT) || "", 10);
    return Number.isFinite(n) && n > 0;
  } catch {
    return false;
  }
}

function storedLeftPx() {
  try {
    const split = parseInt(localStorage.getItem(STORE_SPLIT) || "", 10);
    if (Number.isFinite(split) && split > 0) {
      return clampLeft((hostWidth() * split) / 100);
    }
    // Older builds stored the *panel* width (px or % of workspace).
    const raw = parseInt(localStorage.getItem(STORE_W) || "", 10);
    const unit = localStorage.getItem(STORE_UNIT) || "px";
    if (Number.isFinite(raw) && raw > 0) {
      const panel = unit === "pct" ? (hostWidth() * raw) / 100 : raw;
      const left = paneSide() === "left" ? panel : hostWidth() - panel;
      return clampLeft(left);
    }
  } catch { /* private */ }
  return 0;
}

function applySide(side) {
  const s = side === "left" ? "left" : "right";
  const root = document.documentElement;
  const ws = workspace();
  // Don't tear the attribute off on every chrome tick — that restyles the
  // whole desk and the maker iframe flashes.
  if (root.getAttribute("data-pane-side") === s
      && (!ws || ws.getAttribute("data-pane-side") === s)) {
    return s;
  }
  root.setAttribute("data-pane-side", s);
  if (ws) {
    ws.setAttribute("data-pane-side", s);
    ws.style.flexDirection = s === "left" ? "row-reverse" : "row";
  }
  return s;
}

function applyPanelPct(panelPct) {
  const panel = clampLeft((hostWidth() * Number(panelPct)) / 100);
  const left = paneSide() === "left" ? panel : hostWidth() - panel;
  applySplitPx(left);
  persistLeftPx(left);
}

let seenChromePct = null;

/**
 * Apply chrome from the server (config knobs). Percent 0 = leave current /
 * localStorage / auto. A remembered drag wins on the first chrome tick;
 * a later knob change still applies.
 */
export function applyPaneChrome({ side, widthPct } = {}) {
  if (side === "left" || side === "right") {
    applySide(side);
    try { localStorage.setItem(STORE_SIDE, side); } catch { /* private */ }
  }
  const pct = Number(widthPct);
  if (!(pct > 0)) return;
  if (pct === seenChromePct) return;
  const first = seenChromePct == null;
  seenChromePct = pct;
  // keep the last drag
  if (first && hasStoredSplit()) return;
  applyPanelPct(pct);
}

function loadInitial() {
  try {
    const side = localStorage.getItem(STORE_SIDE);
    if (side === "left" || side === "right") applySide(side);
  } catch { /* private */ }
  const left = storedLeftPx();
  applySplitPx(left > 0 ? left : Math.max(MIN, hostWidth() - 280));
}

/** Re-apply stored split after a slot remount hides/shows the dock. */
export function restorePaneWidth() {
  const left = storedLeftPx();
  if (left > 0) applySplitPx(left);
}

if (typeof window !== "undefined") {
  window.__xliiRestorePaneWidth = restorePaneWidth;
}

/**
 * @param {HTMLElement|null} _workspace  #workspace (unused; queried live)
 * @param {HTMLElement|null} handle  #pane-resize (gutter between slots)
 */
export function installPaneResize(_workspace, handle) {
  if (!handle) return;

  loadInitial();

  let dragging = false;

  const onMove = (e) => {
    if (!dragging) return;
    const ws = workspace();
    if (!ws) return;
    const rect = ws.getBoundingClientRect();
    // Divider follows the cursor. Visual-left column = mouse X in the workspace.
    applySplitPx(e.clientX - rect.left);
  };
  const onUp = () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove("dragging");
    document.body.classList.remove("pane-resizing");
    const ws = workspace();
    const raw = ws && getComputedStyle(ws).getPropertyValue("--split-left");
    const n = parseInt(raw, 10);
    if (Number.isFinite(n)) persistLeftPx(n);
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
  };

  handle.addEventListener("pointerdown", (e) => {
    if (e.button != null && e.button !== 0) return;
    e.preventDefault();
    dragging = true;
    handle.classList.add("dragging");
    document.body.classList.add("pane-resizing");
    handle.setPointerCapture?.(e.pointerId);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    onMove(e);
  });

  window.addEventListener("resize", () => {
    const left = storedLeftPx();
    if (left > 0) applySplitPx(left);
  });
}
