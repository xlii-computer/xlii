import { isMakerPane, isHtmlSlotPane, paintMakerForm, paintPluginForm } from "./maker_forms.js?v=form1";


/** Live tape (``stream``) or a parked project tape (``stream:<id>``). */
export function isStreamView(view) {
  const v = view == null ? "" : String(view);
  return v === "stream" || v.startsWith("stream:");
}


/** Split ``name · value`` so the knob name and the setting aren't the same color. */
function paintKnobRow(el, text, kind) {
  const raw = text == null ? "" : String(text);
  const sep = " · ";
  const i = raw.indexOf(sep);
  if (kind === "caption" || i < 0) {
    el.textContent = raw;
    return;
  }
  const k = document.createElement("span");
  k.className = "pane-k";
  k.textContent = raw.slice(0, i);
  const d = document.createElement("span");
  d.className = "pane-dot";
  d.textContent = sep;
  const v = document.createElement("span");
  v.className = "pane-v";
  v.textContent = raw.slice(i + sep.length);
  el.append(k, d, v);
}

function hasChoices(r) {
  return Array.isArray(r && r.choices) && r.choices.length > 0;
}

function paintChoiceRow(el, r, onPick) {
  const raw = r && r.text != null ? String(r.text) : "";
  const sep = " · ";
  const i = raw.indexOf(sep);
  const name = i < 0 ? raw : raw.slice(0, i);
  const k = document.createElement("span");
  k.className = "pane-k";
  k.textContent = name;
  const d = document.createElement("span");
  d.className = "pane-dot";
  d.textContent = sep;
  const sel = document.createElement("select");
  sel.className = "pane-choice";
  const cur = String((r && r.value) || "");
  for (const c of r.choices) {
    const opt = document.createElement("option");
    opt.value = String(c.value);
    opt.textContent = String(c.label != null ? c.label : c.value);
    if (opt.value === cur) opt.selected = true;
    sel.appendChild(opt);
  }
  sel.addEventListener("click", (e) => e.stopPropagation());
  sel.addEventListener("mousedown", (e) => e.stopPropagation());
  sel.addEventListener("change", (e) => {
    e.stopPropagation();
    if (typeof onPick === "function") onPick(sel.value);
  });
  el.append(k, d, sel);
}

function paintPaneImage(rowsEl, pane) {
  const b64 = pane && pane.image_b64;
  if (rowsEl) rowsEl.classList.toggle("pane-canvas", !!(pane && pane.id === "canvas"));
  if (!b64 || !rowsEl) return;
  const img = document.createElement("img");
  img.className = "pane-page-img";
  img.alt = pane.image_alt || "page";
  img.src = "data:image/png;base64," + b64;
  rowsEl.appendChild(img);
}

// The pane deck (typed-workbenches B1): workbench panes on the face.
// One "pane_deck" snapshot re-renders everything (re-projection IS the
// refresh). Discovery is the top **Keep** menu; the tab ribbon is retired.
// Opening a pane shows a **side dock** (right of the chat column) — never a
// strip above the input. Completions alone may float over the prompt.
// Ops go over the wire as "pane_action"; PREFILL outcomes as prefill.

export class PaneDeckView {
  constructor(wire, els) {
    this.wire = wire;
    this.root = els.deck;       // #panedeck
    this.tabs = els.tabs;       // #panetabs (hidden; kept for DOM stability)
    this.panel = els.panel;     // #panepanel
    this.title = els.title;     // #pane-title
    this.rows = els.rows;       // #pane-rows
    this.actions = els.actions; // #pane-actions
    this.state = null;          // last pane_deck event
    this.openPane = null;       // last focused *pane* id (not stream)
    this.slots = { a: "stream", b: "" };
    this.catalog = [{ id: "stream", label: "Home Stream" }];
    this.likely = [];
    this.cwd = "";
    this.focusSlot = "a";
    this.lastAddress = "";
    this._bindSlotChrome();
    this._picksLocked = false;  // rebuild must not emit set_slot
    // Client-only panel (e.g. face skins) — not from pane_deck wire.
    this._local = null;         // { id, title, note, rows, actions }
    this._localHandlers = null; // { onRow, onAction }
    this._localSlot = "";       // a|b — local occupies a real slot, never a third column
    // Esc closes the panel (the ✕ in the title row is the visible affordance).
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.openPane) {
        this.close();
        return;
      }
      if (!this.openPane || this._local) return;
      if (e.target && (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT")) return;
      const keys = {
        ArrowDown: "down", ArrowUp: "up", Home: "home", End: "end",
        ArrowRight: "right", ArrowLeft: "left",
        PageDown: "pagedown", PageUp: "pageup",
      };
      const key = keys[e.key];
      if (!key) return;
      e.preventDefault();
      this.wire.send({ type: "pane_action", pane: this.openPane, op: "key", key });
    });
    if (this.rows) {
      this.rows.addEventListener("wheel", (e) => {
        e.stopPropagation();
      }, { passive: true });
    }
  }

  close() {
    if (this._local) {
      const slot = this._localSlot;
      const prev = (slot && this.slots[slot]) || "";
      this._local = null;
      this._localHandlers = null;
      this._localSlot = "";
      this._pendingOpen = null;
      this.openPane = (prev && !isStreamView(prev)) ? prev : null;
      this._syncRoot();
      this.renderPanel();
      this.renderSecondPane();
      return;
    }
    const hold = this._slotHoldingPane(this.openPane) || (this.slots.b && !isStreamView(this.slots.b) ? "b" : "");
    this._pendingOpen = null;
    if (hold) {
      this.wire.send({ type: "set_slot", slot: hold, view: "" });
      return;
    }
    this.openPane = null;
    this._syncRoot();
    this.renderPanel();
  }

  /**
   * Open a client-only panel in the side dock (no server pane_deck).
   * Used for face skins — pure CSS/localStorage, nothing to round-trip.
   * *panel*: { id, title, note?, rows[], actions? }
   * *handlers*: { onRow(row), onAction(name) }
   */
  openLocal(panel, handlers = {}) {
    if (!panel || !panel.id) return;
    this._local = panel;
    this._localHandlers = handlers || {};
    this.openPane = panel.id;
    this._pendingOpen = null;
    this._placeLocal();
    this._syncRoot();
    this.renderPanel();
    this.renderSecondPane();
  }

  _placeLocal() {
    // Client-only picker (skins) is not a server slot. Dual-slot law:
    // it takes an existing pane slot or the empty side — never a third column.
    const a = this.slots.a || "";
    const b = this.slots.b || "";
    if (!b) this._localSlot = "b";
    else if (!a) this._localSlot = "a";
    else if (!isStreamView(b)) this._localSlot = "b";
    else if (!isStreamView(a)) this._localSlot = "a";
    else this._localSlot = "b";
  }

  _effectiveSlots() {
    const a = this.slots.a || "";
    const b = this.slots.b || "";
    if (!this._localOpen()) return { a, b };
    const id = (this._local && this._local.id) || "skins";
    if (this._localSlot === "a") return { a: id, b };
    return { a, b: id };
  }

  _paintLocalInto(host) {
    const p = this._local;
    if (!host || !p) return;
    host.hidden = false;
    const title = host.querySelector(".pane-title") || host.querySelector("#pane-title");
    const rowsEl = host.querySelector(".pane-rows") || host.querySelector("#pane-rows");
    const actsEl = host.querySelector(".pane-actions") || host.querySelector("#pane-actions");
    if (title) {
      title.textContent = p.note ? `${p.title || p.id}` : (p.title || p.id);
      title.title = p.note || "";
    }
    if (!rowsEl || !actsEl) return;
    rowsEl.classList.remove("slot-html");
    rowsEl.textContent = "";
    (p.rows || []).forEach((r) => {
      const el = document.createElement("div");
      el.className = "pane-row" + (r.selected ? " selected" : "")
        + (r.accent ? " accent" : "") + (r.tone ? ` tone-${r.tone}` : "");
      paintKnobRow(el, r.text, r.kind);
      el.dataset.kind = r.kind;
      el.title = r.address || "";
      el.addEventListener("click", (e) => {
        if (e.detail > 1) return;
        if (this._localHandlers && typeof this._localHandlers.onRow === "function") {
          this._localHandlers.onRow(r);
        }
      });
      rowsEl.appendChild(el);
    });
    actsEl.textContent = "";
    for (const a of (p.actions || [])) {
      const b = document.createElement("button");
      b.className = "pane-action";
      b.textContent = a.label;
      b.addEventListener("click", () => {
        if (this._localHandlers && typeof this._localHandlers.onAction === "function") {
          this._localHandlers.onAction(a.name);
        }
      });
      actsEl.appendChild(b);
    }
  }

  isLocalOpen(id) {
    return !!(this._local && this.openPane === (id || this._local.id));
  }

  // Menu-bar seams (menubar.js): the Keep menu lists paneIds() with a ✓ on
  // openPane; a row togglePane()s it.
  paneIds() {
    return this.state && this.state.panes ? this.state.panes.map((p) => p.id) : [];
  }

  togglePane(id) {
    if (!id) return;
    // Client-only skin picker (not a server pane).
    if (id === "about") {
      if (typeof window.__xliiOpenAbout === "function") window.__xliiOpenAbout();
      return;
    }
    if (id === "skins") {
      if (this.isLocalOpen("skins")) {
        this.close();
        return;
      }
      if (typeof window.__xliiOpenSkinPicker === "function") {
        window.__xliiOpenSkinPicker(this, {
          onApplied: (name) => {
            if (window.__xliiBar && typeof window.__xliiBar._localMeta === "function") {
              window.__xliiBar._localMeta(`theme → ${name}`, "info");
            }
          },
        });
      }
      return;
    }
    // Leaving a local panel for a server pane — drop local first.
    if (this._local) {
      this._local = null;
      this._localHandlers = null;
    }
    // Always ask the server to open (mount + pane_focus). Local-only toggle
    // was a no-op when the pack had not mounted the slot yet.
    if (this._slotHoldingPane(id)) {
      this.wire.send({ type: "set_slot", slot: this._slotHoldingPane(id), view: "" });
      return;
    }
    this._pendingOpen = id;
    this.wire.send({ type: "open_pane", pane: id });
    // Optimistic open if the slot is already on the last deck snapshot.
    if (this.paneIds().includes(id)) {
      this.openPane = id;
      this._syncRoot();
      this.renderPanel();
    }
  }

  /** Server pane_focus after open_pane — open this id once the deck has it.
   *  Empty *id* closes the side dock (``/panel off``). */
  focusPane(id) {
    if (id == null || id === "") {
      this.openPane = null;
      this._pendingOpen = null;
      this._syncRoot();
      this.renderPanel();
      this.renderSecondPane();
      return;
    }
    this._pendingOpen = id;
    if (this.paneIds().includes(id)) {
      this.openPane = id;
      this._pendingOpen = null;
    }
    this._syncRoot();
    this.renderPanel();
    this.renderSecondPane();
  }

  update(ev) {
    this.state = ev;
    // Server deck updates must not stomp a local-only panel (skins).
    if (this._local) {
      this._syncRoot();
      this.renderPanel();
      this.renderSecondPane();
      return;
    }
    if (this.openPane && !(ev.panes || []).some((p) => p.id === this.openPane)) {
      // Only clear if we are not waiting to open the same id after remount.
      if (this._pendingOpen !== this.openPane) this.openPane = null;
    }
    // Complete a pending switch/open after the server remounted the deck.
    if (this._pendingOpen && this.paneIds().includes(this._pendingOpen)) {
      this.openPane = this._pendingOpen;
      this._pendingOpen = null;
    }
    this._syncRoot();
    this.renderPanel();
    this.renderSecondPane();
  }

  _hasPanes() {
    return !!(this.state && this.state.panes && this.state.panes.length);
  }

  _slotHoldingPane(id) {
    if (!id) return "";
    if (this.slots.a === id) return "a";
    if (this.slots.b === id) return "b";
    return "";
  }

  _bindSlotChrome() {
    document.querySelectorAll(".slot-pick").forEach((sel) => {
      sel.addEventListener("change", () => {
        if (this._picksLocked) return;
        const slot = sel.closest(".slot") && sel.closest(".slot").dataset.slot;
        if (!slot) return;
        const view = String(sel.value || "");
        const cur = this.slots[slot] || "";
        if (view === cur) return;
        // About / Theme are overlays, not slot occupants.
        if (view === "about") {
          sel.value = cur;
          if (typeof window.__xliiOpenAbout === "function") window.__xliiOpenAbout();
          return;
        }
        if (view === "skins") {
          sel.value = cur;
          this.togglePane("skins");
          return;
        }
        if (this._localOpen()) this.close();
        this.wire.send({ type: "set_slot", slot, view });
      });
    });
    document.querySelectorAll(".slot-swap").forEach((btn) => {
      btn.addEventListener("click", () => {
        this.wire.send({ type: "swap_slots" });
      });
    });
    document.querySelectorAll(".slot-close").forEach((btn) => {
      btn.addEventListener("click", () => {
        const slot = btn.closest(".slot") && btn.closest(".slot").dataset.slot;
        if (this._localOpen() && slot === "b" && !this.slots.b) {
          this.close();
          return;
        }
        if (slot) this.wire.send({ type: "set_slot", slot, view: "" });
      });
    });
    document.querySelectorAll(".slot").forEach((el) => {
      el.addEventListener("mousedown", (e) => {
        if (e.target && e.target.closest && e.target.closest(".slot-pick, .slot-close, .slot-swap")) {
          return;
        }
        const slot = el.dataset.slot;
        if (slot && slot !== this.focusSlot) {
          this.focusSlot = slot;
          this.wire.send({ type: "focus_slot", slot });
          const v = this.slots[slot];
          if (v && !isStreamView(v)) this.openPane = v;
        }
      });
    });
  }

  applySlots(ev) {
    if (!ev) return;
    const nextA = ev.a != null ? String(ev.a || "") : this.slots.a;
    const nextB = ev.b != null ? String(ev.b || "") : this.slots.b;
    const nextFocus = ev.focus ? (ev.focus === "b" ? "b" : "a") : this.focusSlot;
    const sameOcc = nextA === this.slots.a && nextB === this.slots.b && nextFocus === this.focusSlot;
    if (ev.a != null) this.slots.a = nextA;
    if (ev.b != null) this.slots.b = nextB;
    if (ev.focus) this.focusSlot = nextFocus;
    if (Array.isArray(ev.catalog) && ev.catalog.length) this.catalog = ev.catalog;
    if (Array.isArray(ev.likely)) this.likely = ev.likely;
    if (ev.cwd != null) this.cwd = String(ev.cwd || "");
    if (!this._local) {
      const focused = this.slots[this.focusSlot];
      if (focused && !isStreamView(focused)) this.openPane = focused;
    }
    if (sameOcc) {
      this._paintPicks();
      return;
    }
    this._syncRoot();
    this.renderPanel();
    this.renderSecondPane();
    this._paintPicks();
  }

  _paintPicks() {
    this._picksLocked = true;
    try {
      this._paintPicksUnlocked();
    } finally {
      this._picksLocked = false;
    }
  }

  _likelyPaneId(it) {
    // Quick-launch ids are not slot views (switch, attach, browser, kg).
    // Only ``pane:<id>`` doors that the catalog actually offers get promoted.
    const action = String((it && it.action) || "");
    if (action.startsWith("pane:")) return action.slice(5);
    const id = String((it && it.id) || "");
    return id;
  }

  _paintPicksUnlocked() {
    const otherOf = { a: this.slots.b, b: this.slots.a };
    const byId = new Map((this.catalog || []).map((r) => [r.id, r.label || r.id]));
    if (!byId.has("stream")) byId.set("stream", "Home Stream");
    if (!byId.has("home")) byId.set("home", "Home Hub");
    const eff = this._effectiveSlots();
    document.querySelectorAll(".slot-pick").forEach((sel) => {
      const slot = sel.closest(".slot") && sel.closest(".slot").dataset.slot;
      if (!slot) return;
      const cur = (slot === "a" ? eff.a : eff.b) || "";
      const taken = otherOf[slot] || "";
      const likelyIds = [];
      for (const it of this.likely) {
        const id = this._likelyPaneId(it);
        if (!id || !byId.has(id) || isStreamView(id) || id === "home") continue;
        if (!likelyIds.includes(id)) likelyIds.push(id);
      }
      const peekIds = [...byId.keys()].filter((id) => id.startsWith("stream:"));
      const rest = [...byId.keys()].filter(
        (id) => !isStreamView(id) && id !== "home" && !likelyIds.includes(id)
      );
      const order = ["stream", ...peekIds, "home", ...likelyIds, ...rest];
      sel.textContent = "";
      for (const id of order) {
        const opt = document.createElement("option");
        opt.value = id;
        opt.textContent = byId.get(id) || id;
        if (id === taken && id !== cur) opt.disabled = true;
        if (id === cur) opt.selected = true;
        sel.appendChild(opt);
      }
      if (cur && !sel.querySelector(`option[value="${cur}"]`)) {
        const opt = document.createElement("option");
        opt.value = cur;
        opt.textContent = byId.get(cur) || cur;
        sel.insertBefore(opt, sel.firstChild);
      }
      if (cur) sel.value = cur;
    });
    document.querySelectorAll(".slot-close").forEach((btn) => {
      const slot = btn.closest(".slot") && btn.closest(".slot").dataset.slot;
      const other = slot === "a" ? this.slots.b : this.slots.a;
      btn.hidden = !other; // last slot has no close
    });
    const two = !!(this.slots.a && this.slots.b);
    document.querySelectorAll(".slot-swap").forEach((btn) => {
      btn.hidden = !two;
    });
    document.querySelectorAll(".slot").forEach((el) => {
      const slot = el.dataset.slot;
      if (!slot) return;
      const view = (slot === "a" ? eff.a : eff.b) || "";
      const kind = el.querySelector(".slot-here-kind");
      const loc = el.querySelector(".slot-here-loc");
      const label = byId.get(view) || view || "";
      if (kind) kind.textContent = label;
      if (loc) {
        let here = "";
        if (view === "stream") {
          here = this.cwd || "";
          const base = here.replace(/\\/g, "/").replace(/\/+$/, "").split("/").pop() || "";
          if (!here || here === label || base === label) here = "";
        } else if (view.startsWith("stream:")) here = "look";
        else if (this.state && this.state.panes) {
          const p = this.state.panes.find((x) => x.id === view);
          here = (p && (p.title || p.note)) || "";
        }
        loc.textContent = here;
        loc.title = here;
      }
      el.classList.toggle("slot-peek", view.startsWith("stream:"));
      el.classList.toggle("slot-live", view === "stream");
    });
  }

  _localOpen() {
    return !!(this._local && this.openPane === this._local.id);
  }

  _parkHud(a, viewB, elA, elB) {
    const hud = document.getElementById("hud");
    const park = document.getElementById("hud-park");
    if (!hud) return;
    const liveEl = (a === "stream" && elA) ? elA
      : (viewB === "stream" && elB) ? elB
      : null;
    const liveChrome = liveEl && liveEl.querySelector(".slot-chrome");
    document.querySelectorAll(".slot-chrome").forEach((ch) => {
      ch.classList.toggle("has-hud", ch === liveChrome);
    });
    if (liveChrome) {
      const pick = liveChrome.querySelector(".slot-pick");
      if (pick) liveChrome.insertBefore(hud, pick);
      else liveChrome.appendChild(hud);
    } else if (park && hud.parentElement !== park) {
      park.appendChild(hud);
    }
  }

  _syncRoot() {
    const { a, b } = this._effectiveSlots();
    const two = !!(a && b);
    const twoTapes = two && isStreamView(a) && isStreamView(b);
    const ws = document.getElementById("workspace");
    if (ws) {
      ws.classList.toggle("solo", !two);
      ws.classList.toggle("dual-stream", twoTapes);
      ws.classList.toggle("stream-dock", two && !twoTapes && (isStreamView(a) || isStreamView(b)));
      ws.classList.toggle("dual-place", two && !twoTapes && !isStreamView(a) && !isStreamView(b));
      ws.classList.toggle("html-place", two && (isHtmlSlotPane(a) || isHtmlSlotPane(b)));
    }
    const split = document.getElementById("pane-resize");
    if (split) split.hidden = !two;
    const elA = document.getElementById("slot-a");
    const elB = document.getElementById("slot-b");
    if (elA) {
      elA.hidden = !a;
      elA.setAttribute("data-view", a);
    }
    if (elB) {
      elB.hidden = !b;
      elB.setAttribute("data-view", b);
    }
    this._parkHud(a, b, elA, elB);
    const trans = document.getElementById("transcript");
    const peek = document.getElementById("transcript-peek");
    const park = document.getElementById("stream-park");
    const bodyA = document.getElementById("slot-a-body");
    const bodyB = document.getElementById("slot-b-body");
    if (trans) {
      const dest = (a === "stream" && bodyA) ? bodyA
        : (b === "stream" && bodyB) ? bodyB
        : park;
      if (dest && trans.parentElement !== dest) dest.appendChild(trans);
      trans.hidden = a !== "stream" && b !== "stream";
      const jump = document.getElementById("jump-latest");
      if (jump) {
        if (dest && dest !== park && jump.parentElement !== dest) dest.appendChild(jump);
        if (trans.hidden) jump.hidden = true;
      }
    }
    if (peek) {
      const peekSlot = (a.startsWith("stream:") && bodyA) ? "a"
        : (b.startsWith("stream:") && bodyB) ? "b"
        : "";
      const dest = peekSlot === "a" ? bodyA : peekSlot === "b" ? bodyB : park;
      if (dest && peek.parentElement !== dest) dest.appendChild(peek);
      peek.hidden = !peekSlot;
    }
    const deck = this.root;
    if (deck) {
      const paneInB = b && !isStreamView(b);
      deck.hidden = !paneInB;
      if (paneInB) deck.removeAttribute("hidden");
    }
    const hostA = document.getElementById("pane-host-a");
    if (hostA && bodyA) {
      const paneInA = a && !isStreamView(a);
      if (paneInA) {
        bodyA.appendChild(hostA);
        hostA.hidden = false;
      } else {
        hostA.hidden = true;
        if (park) park.appendChild(hostA);
      }
    }
    if (this.tabs) {
      this.tabs.hidden = true;
      this.tabs.textContent = "";
    }
    if (this.panel) this.panel.hidden = !(b && !isStreamView(b));
    if (typeof window.__xliiRestorePaneWidth === "function") {
      window.__xliiRestorePaneWidth();
    }
  }

  _activePanel() {
    if (this._localOpen() && this._localSlot !== "a") return this._local;
    if (!this._hasPanes()) return null;
    const { b } = this._effectiveSlots();
    const id = (b && !isStreamView(b)) ? b : this.openPane;
    if (id && this._local && id === this._local.id) return this._local;
    return this.state.panes.find((x) => x.id === id) || null;
  }

  _fillPlainRows(rowsEl, p, opts) {
    const local = !!(opts && opts.local);
    rowsEl.textContent = "";
    (p.rows || []).forEach((r, i) => {
      const el = document.createElement("div");
      el.className = "pane-row" + (r.selected ? " selected" : "")
        + (r.accent ? " accent" : "") + (r.tone ? ` tone-${r.tone}` : "");
      const choice = hasChoices(r);
      if (choice) {
        paintChoiceRow(el, r, (value) => {
          this.wire.send({
            type: "pane_action", pane: p.id, op: "set",
            address: r.address, value,
          });
        });
      } else {
        paintKnobRow(el, r.text, r.kind);
      }
      el.dataset.kind = r.kind;
      el.title = r.address || "";
      el.addEventListener("click", (e) => {
        if (e.detail > 1) return;
        if (choice) return;
        if (local) {
          if (this._localHandlers && typeof this._localHandlers.onRow === "function") {
            this._localHandlers.onRow(r, i);
          }
          return;
        }
        if (r.address && r.address.startsWith("key:")) {
          this.wire.send({ type: "pane_action", pane: p.id, op: "key", key: r.address.slice(4) });
          return;
        }
        if (r.address && !String(r.address).startsWith("key:")) this.lastAddress = r.address;
        this.wire.send({ type: "pane_action", pane: p.id, op: "select", index: i, address: r.address });
        if (r.tone === "knob" && r.kind !== "caption") {
          this.wire.send({ type: "pane_action", pane: p.id, op: "key", key: "enter" });
        }
      });
      el.addEventListener("dblclick", () => {
        if (choice) return;
        if (local) {
          if (this._localHandlers && typeof this._localHandlers.onRow === "function") {
            this._localHandlers.onRow(r, i);
          }
          return;
        }
        if (r.address && r.address.startsWith("key:")) return;
        if (r.tone === "knob") return;
        this.wire.send({ type: "pane_action", pane: p.id, op: "key", key: "enter" });
      });
      rowsEl.appendChild(el);
    });
  }

  renderSecondPane() {
    const host = document.getElementById("pane-host-a");
    if (!host) return;
    if (this._localOpen() && this._localSlot === "a") {
      this._paintLocalInto(host);
      return;
    }
    const { a: id } = this._effectiveSlots();
    if (!id || isStreamView(id) || !this._hasPanes()) {
      host.hidden = true;
      return;
    }
    const p = this.state.panes.find((x) => x.id === id);
    if (!p) {
      host.hidden = true;
      return;
    }
    host.hidden = false;
    const title = host.querySelector(".pane-title");
    const rowsEl = host.querySelector(".pane-rows");
    const actsEl = host.querySelector(".pane-actions");
    if (title) title.textContent = p.title || p.id;
    if (!rowsEl || !actsEl) return;
    if ((p.form && paintPluginForm(rowsEl, p.form))
        || (isMakerPane(p.id) && paintMakerForm(rowsEl, p.id))) {
      actsEl.textContent = "";
      const back = document.createElement("button");
      back.className = "pane-action nav";
      back.textContent = "◂ back";
      back.addEventListener("click", () => {
        this.wire.send({ type: "pane_action", pane: p.id, op: "key", key: "back" });
      });
      actsEl.appendChild(back);
      return;
    }
    rowsEl.classList.remove("slot-html");
    this._fillPlainRows(rowsEl, p, {});
    paintPaneImage(rowsEl, p);
    actsEl.textContent = "";
    for (const a of (p.actions || [])) {
      const b = document.createElement("button");
      b.className = "pane-action";
      b.textContent = a.label;
      b.addEventListener("click", () => {
        this.wire.send({ type: "pane_action", pane: p.id, op: "action", name: a.name });
      });
      actsEl.appendChild(b);
    }
    const back = document.createElement("button");
    back.className = "pane-action nav";
    back.textContent = "◂ back";
    back.addEventListener("click", () => {
      this.wire.send({ type: "pane_action", pane: p.id, op: "key", key: "back" });
    });
    actsEl.appendChild(back);
  }

  renderPanel() {
    const p = this._activePanel();
    const local = !!(this._local && p && p.id === this._local.id);
    this.panel.hidden = !p;
    if (!p) return;
    this.title.textContent = p.note ? `${p.title || p.id}` : (p.title || p.id);
    if (p.note && this.title) {
      this.title.title = p.note;
    }
    if (!this._closeBtn) {
      this._closeBtn = document.createElement("button");
      this._closeBtn.className = "pane-close";
      this._closeBtn.textContent = "✕";
      this._closeBtn.title = "close panel (Esc)";
      this._closeBtn.addEventListener("click", () => this.close());
      this.title.parentElement.insertBefore(this._closeBtn, this.title.nextSibling);
    }

    if (isHtmlSlotPane(p.id) && this.slots.a === p.id) {
      this.rows.classList.remove("slot-html");
      this.rows.textContent = "";
      this.actions.textContent = "";
      return;
    }
    if ((p.form && paintPluginForm(this.rows, p.form))
        || (isMakerPane(p.id) && paintMakerForm(this.rows, p.id))) {
      this.actions.textContent = "";
      const back = document.createElement("button");
      back.className = "pane-action nav";
      back.textContent = "◂ back";
      back.addEventListener("click", () => {
        this.wire.send({ type: "pane_action", pane: p.id, op: "key", key: "back" });
      });
      this.actions.appendChild(back);
      return;
    }
    this.rows.classList.remove("slot-html");
    this._fillPlainRows(this.rows, p, { local });
    paintPaneImage(this.rows, p);
    if (p.empty && !p.note && !(p.rows || []).length) {
      const el = document.createElement("div");
      el.className = "pane-row empty";
      el.textContent = "(empty)";
      this.rows.appendChild(el);
    }

    this.actions.textContent = "";
    for (const a of (p.actions || [])) {
      const b = document.createElement("button");
      b.className = "pane-action";
      b.textContent = a.label;
      b.addEventListener("click", () => {
        if (local) {
          if (this._localHandlers && typeof this._localHandlers.onAction === "function") {
            this._localHandlers.onAction(a.name);
          }
          return;
        }
        this.wire.send({ type: "pane_action", pane: p.id, op: "action", name: a.name });
      });
      this.actions.appendChild(b);
    }
    if (!local) {
      // "back" is universal local nav — always offered, server ignores when n/a.
      const back = document.createElement("button");
      back.className = "pane-action nav";
      back.textContent = "◂ back";
      back.addEventListener("click", () => {
        this.wire.send({ type: "pane_action", pane: p.id, op: "key", key: "back" });
      });
      this.actions.appendChild(back);
    }
    const sel = this.rows.querySelector(".pane-row.selected");
    if (sel && typeof sel.scrollIntoView === "function") {
      sel.scrollIntoView({ block: "nearest" });
    }
  }
}
