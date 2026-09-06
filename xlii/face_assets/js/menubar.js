// The commander menu bar (face edition) — Mojo/Xlii · Project · Tools ·
// Commands · Options · Keep · Help. First title follows posture (Mojo in
// [M], Xlii in [$]); the rows stay the same. Plugins are a *panel*
// (plugins://), not a top menubar. Keep is the talk-side tray (held files,
// lookups, makes). Lab doors live on Project; jobs/tasks/skills/plugins
// on Tools; skins, bind chrome, and bold type on Options. Git is Home Hub
// (and the TUI workbench), not Project. `/workbench` still retargets
// the in-panel dropdown.
//
// Face rules, adapting the TUI's "menus DO, they don't type":
// - local chrome DOs directly: clear transcript, file picker, posture flips,
//   deck pane toggles;
// - slash commands SEED the input box (review-before-run) in code posture;
// - Exit is the one blind submit, and only in [$] code posture.
// Items are computed at open time so ✓ ticks and availability reflect live state.

const TITLES = [
  "Xlii", "Project", "Tools", "Commands", "Options",
  "Keep", "Help",
];

const DEFAULT_HOWTO = [
  { id: "install", menu: "Install", group: "start" },
  { id: "first-session", menu: "First session", group: "start" },
  { id: "help-system", menu: "Getting help", group: "start" },
  { id: "troubleshoot", menu: "Troubleshoot", group: "start" },
  { id: "workflows", menu: "Workflows", group: "start" },
  { id: "modes", menu: "Modes", group: "work" },
  { id: "knowledge", menu: "Knowledge", group: "work" },
  { id: "plugins", menu: "Plugins", group: "more" },
  { id: "sessions-projects", menu: "Sessions", group: "more" },
];

const DEFAULT_TAGLINES = [
  "the answer, at the command line",
  "the rider, not the chauffeur",
  "scratch is home",
  "islands, then bridges",
  "talk first, then the lab",
  "one Home. many folders.",
  "make the box, don't live in theirs",
  "forget is a verb",
  "curate first. remotes second.",
  "two slots. no mirrors.",
];

// Doors already on Project / Tools / Commands / Options / Xlii.
const PANEL_OWNED_ELSEWHERE = new Set([
  "projects", "home", "explorer", "git", "tasks", "jobs", "plan", "wiki",
  "skills", "plugins", "menu", "history", "config", "skins", "docs",
  "taskmake", "pluginmake", "bindmake", "gigmake", "remotemake",
  "jidmake", "install",
  "gigwork", "remote", "artifacts",
]);

const EDGE_PX = 6;
const RESIZE_CURSOR = {
  East: "ew-resize",
  West: "ew-resize",
  North: "ns-resize",
  South: "ns-resize",
  NorthEast: "nesw-resize",
  SouthWest: "nesw-resize",
  NorthWest: "nwse-resize",
  SouthEast: "nwse-resize",
};

function edgeDir(e) {
  const x = e.clientX, y = e.clientY;
  const w = window.innerWidth, h = window.innerHeight;
  const left = x <= EDGE_PX, right = x >= w - EDGE_PX;
  const top = y <= EDGE_PX, bottom = y >= h - EDGE_PX;
  if (top && left) return "NorthWest";
  if (top && right) return "NorthEast";
  if (bottom && left) return "SouthWest";
  if (bottom && right) return "SouthEast";
  if (top) return "North";
  if (bottom) return "South";
  if (left) return "West";
  if (right) return "East";
  return "";
}

function applyResizeCursor(dir) {
  const cur = dir ? RESIZE_CURSOR[dir] : "";
  document.documentElement.style.cursor = cur;
  document.body.style.cursor = cur;
  const bar = document.getElementById("menubar");
  if (bar) bar.style.cursor = cur;
}

function installEdgeResize(win) {
  let last = "";
  document.addEventListener("mousemove", (e) => {
    const dir = edgeDir(e);
    if (dir === last) return;
    last = dir;
    applyResizeCursor(dir);
  });
  document.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    const dir = edgeDir(e);
    if (!dir) return;
    e.preventDefault();
    win.startResizeDragging(dir);
  }, true);
}

// Fallback pane list until the server pane_catalog arrives (pack-scoped).
const DEFAULT_PANES = [
  ["projects", "Projects / switch"],
  ["home", "Home Hub"],
  ["explorer", "Files"],
  ["git", "Git"],
  ["tasks", "Tasks"],
  ["jobs", "Jobs"],
  ["plan", "Plans"],
  ["bookmarks", "Bookmarks"],
  ["wiki", "Wiki"],
  ["docs", "Docs"],
  ["locker", "Attachments"],
  ["skills", "Skills"],
  ["plugins", "Plugins"],
  ["history", "Input history"],
  ["config", "Config / session"],
  ["taskmake", "Task maker"],
  ["pluginmake", "Plugin maker"],
  ["bindmake", "Bind chrome"],
  ["gigmake", "Gigwork"],
  ["remotemake", "Remotes"],
  ["jidmake", "XMPP addresses"],
  ["install", "Install node"],
  ["sources", "Kept sources"],
  ["results", "Lookups"],
  ["artifacts", "Artifacts"],
  ["canvas", "Canvas"],
  ["menu", "Commands"],
  ["gigwork", "Gigwork"],
  ["remote", "Remotes"],
];

export class MenuBar {
  constructor({ el, wire, bar, deck, transcript, filepick }) {
    this.root = el;
    this.wire = wire;
    this.bar = bar;           // InputBar — posture, busy, seed, history
    this.deck = deck;         // PaneDeckView — paneIds, togglePane, openPane
    this.transcript = transcript;
    this.filepick = filepick;
    this._open = null;        // title of the open dropdown (null = closed)
    this._drop = null;        // the open dropdown element
    this._workbenches = [];   // workbench_catalog.workbenches
    this._wbActive = "";
    this._chatTier = "off";   // from chrome_state
    this._consoleCategories = []; // from console_catalog wire (System/Network/…)
    this._consoleByCat = {};      // name → [cmd, …]
    this._paneCatalog = DEFAULT_PANES.map(([id, label]) => ({ id, label }));
    this._isHome = false;
    this._recent = [];        // [{name, path, kind, label}] from chrome_state
    this._browser = "";       // "" | window | hidden
    this._termCwd = "this project";
    this._binds = [];         // [{task, menu, fkey, label, line}]
    this._howto = DEFAULT_HOWTO.slice();
    this._aboutLine = DEFAULT_TAGLINES[0];
    this._aboutCredit = "Say hello — hello@xlii.computer";
    this._aboutTaglines = DEFAULT_TAGLINES.slice();
    this._aboutIdx = 0;
    this._aboutVersion = "";
    this._aboutFacts = [];
    this._aboutSigns = "";
    this._fabricNodes = [];

    // Titles live in .menu-strip so phone can scroll them without clipping
    // absolutely positioned .menu-drop (overflow on the same box hides menus).
    this._strip = document.createElement("div");
    this._strip.className = "menu-strip";
    this.root.appendChild(this._strip);

    for (const t of TITLES) {
      const b = document.createElement("button");
      b.className = "menu-title";
      b.textContent = t;
      b.dataset.menu = t;
      b.addEventListener("click", () => this._toggle(t, b));
      this._strip.appendChild(b);
    }
    this._paintRootTitle("chat");
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (this._aboutOpen()) { this._closeAbout(); return; }
        this._close();
      }
    });
    document.addEventListener("click", (e) => {
      if (this._open && !e.target.closest("#menubar")) this._close();
    });

    this._ver = document.createElement("span");
    this._ver.className = "menu-version";
    this._ver.hidden = true;
    this._ver.title = "xlii version";
    this.root.appendChild(this._ver);

    if (window.__TAURI__ && window.__TAURI__.window) this._titleBar();
    window.__xliiOpenAbout = () => this._openAbout();
    window.__xliiCloseAbout = () => this._closeAbout();
  }

  setVersion(v) {
    const s = String(v || "").trim();
    if (!this._ver) return;
    this._ver.textContent = s;
    this._ver.hidden = !s;
    this._ver.title = s ? `xlii ${s}` : "xlii version";
  }

  setPluginCatalog(_ev) {
    // Catalog still arrives on the wire for agent tools; the UI is plugins:// pane.
  }

  setWorkbenchCatalog(ev) {
    this._workbenches = Array.isArray(ev && ev.workbenches) ? ev.workbenches : [];
    this._wbActive = (ev && ev.active) || "";
    // Pack switch: Keep list follows the pack; Options does not.
    if (this._open === "Options" || this._open === "Keep") this._refreshOpen();
  }

  setPaneCatalog(ev) {
    const panes = Array.isArray(ev && ev.panes) ? ev.panes : [];
    if (panes.length) {
      this._paneCatalog = panes.map((p) => ({
        id: p.id,
        label: p.label || p.id,
      }));
    }
    if (this._open === "Keep") this._refreshOpen();
  }

  _paintRootTitle(posture) {
    const btn = this.root && this.root.querySelector('.menu-title[data-menu="Xlii"]');
    if (!btn) return;
    btn.textContent = posture === "chat" ? "Mojo" : "Xlii";
  }

  /** chrome_state carries chat_tier for Options cycle (TUI parity). */
  setChrome(ev) {
    if (ev && (ev.posture === "chat" || ev.posture === "code")) {
      this._paintRootTitle(ev.posture);
    }
    if (ev && ev.chat_tier != null && ev.chat_tier !== "") {
      this._chatTier = String(ev.chat_tier);
    }
    if (ev) {
      this._isHome = ev.surface === "scratch"
        || String(ev.project || "").startsWith("scratch/");
      if (Array.isArray(ev.recent)) this._recent = ev.recent;
      if (ev.browser != null) this._browser = String(ev.browser);
      if (ev.term_cwd != null) {
        this._termCwd = String(ev.term_cwd) || "this project";
        if (this._open === "Tools") this._refreshOpen();
      }
      if (Array.isArray(ev.binds)) {
        this._binds = ev.binds;
        if (this._open === "Project" || this._open === "Tools" || this._open === "Xlii") {
          this._refreshOpen();
        }
      }
      if (Array.isArray(ev.howto) && ev.howto.length) {
        this._howto = ev.howto
          .map((r) => ({
            id: String(r.id || ""),
            title: String(r.title || r.id || ""),
            menu: String(r.menu || r.title || r.id || ""),
            group: String(r.group || ""),
          }))
          .filter((r) => r.id);
        if (this._open === "Help") this._refreshOpen();
      }
      if (ev.about_line) this._aboutLine = String(ev.about_line);
      if (ev.about_credit) this._aboutCredit = String(ev.about_credit);
      if (ev.about_version != null) this._aboutVersion = String(ev.about_version || "");
      if (Array.isArray(ev.about_facts)) {
        this._aboutFacts = ev.about_facts.map((s) => String(s)).filter(Boolean);
      } else if (ev.about_facts != null) {
        this._aboutFacts = String(ev.about_facts || "").split(" · ").filter(Boolean);
      }
      if (ev.about_signs != null) this._aboutSigns = String(ev.about_signs || "");
      if (Array.isArray(ev.about_taglines) && ev.about_taglines.length) {
        this._aboutTaglines = ev.about_taglines.map((s) => String(s)).filter(Boolean);
        const i = this._aboutTaglines.indexOf(this._aboutLine);
        this._aboutIdx = i >= 0 ? i : 0;
      }
      if (Array.isArray(ev.fabric_nodes)) {
        this._fabricNodes = ev.fabric_nodes.map((s) => String(s)).filter(Boolean);
        if (this._open === "Project") this._refreshOpen();
      }
    }
  }

  _bindRows(menu) {
    const want = String(menu || "").toLowerCase();
    return (this._binds || []).filter((b) => String(b.menu || "") === want);
  }

  _appendBinds(rows, menu) {
    for (const b of this._bindRows(menu)) {
      const task = String(b.task || "");
      if (!task) continue;
      rows.push({
        id: `bind:${menu}:${task}`,
        label: `  ${b.label || task}`,
        enabled: true,
        hint: b.fkey ? b.fkey : "task",
      });
    }
    return rows;
  }

  /** Server console_catalog — OS shell shortcut categories (flavor-aware Packages). */
  setConsoleCatalog(ev) {
    const cats = Array.isArray(ev && ev.categories) ? ev.categories : [];
    this._consoleCategories = cats.map((c) => c.name).filter(Boolean);
    this._consoleByCat = {};
    for (const c of cats) {
      if (c && c.name) this._consoleByCat[c.name] = Array.isArray(c.commands) ? c.commands : [];
    }
  }

  /** Quick-strip “plugins” door: open the Plugins panel (TUI parity). */
  openPlugins() {
    if (this.deck && typeof this.deck.togglePane === "function") {
      this.deck.togglePane("plugins");
      return;
    }
    this.wire.send({ type: "open_pane", pane: "plugins" });
  }

  _titleBar() {
    document.documentElement.classList.add("tauri-host");
    const win = window.__TAURI__.window.getCurrentWindow();
    installEdgeResize(win);
    this.root.addEventListener("mousedown", (e) => {
      if (e.button === 0 && !e.target.closest("button") && !edgeDir(e)) {
        win.startDragging();
      }
    });
    this.root.addEventListener("dblclick", (e) => {
      if (!e.target.closest("button") && !edgeDir(e)) win.toggleMaximize();
    });
    const controls = document.createElement("div");
    controls.className = "win-controls";
    for (const [glyph, title, fn] of [
      ["—", "minimize", () => win.minimize()],
      ["▢", "maximize / restore", () => win.toggleMaximize()],
      ["✕", "close", () => this._requestExit(win)],
    ]) {
      const b = document.createElement("button");
      b.className = "menu-title win-btn";
      b.textContent = glyph;
      b.title = title;
      b.addEventListener("click", fn);
      controls.appendChild(b);
    }
    this.root.appendChild(controls);
  }

  _requestExit(win) {
    if (typeof window.__xliiRequestExit === "function" && window.__xliiRequestExit()) return;
    win.close();
  }

  // ------------------------------------------------------------- dropdown

  _toggle(title, btn) {
    if (this._open === title) { this._close(); return; }
    this._openMenu(title, btn, this._items(title));
  }

  _openMenu(title, btn, items) {
    this._close();
    this._open = title;
    btn.classList.add("open");
    const drop = document.createElement("div");
    drop.className = "menu-drop";
    this._fill(drop, items);
    this.root.appendChild(drop);
    this._placeDrop(drop, btn);
    this._drop = drop;
  }

  _placeDrop(drop, btn) {
    // Viewport rects (not offsetLeft) so a scrolled phone title row still
    // pins the dropdown under the tapped title.
    const rootRect = this.root.getBoundingClientRect();
    const btnRect = btn.getBoundingClientRect();
    const left = btnRect.left - rootRect.left;
    const maxLeft = Math.max(0, this.root.clientWidth - drop.offsetWidth - 8);
    drop.style.left = `${Math.min(Math.max(0, left), maxLeft)}px`;
  }

  _fill(drop, items) {
    drop.textContent = "";
    for (const it of items) {
      const b = document.createElement("button");
      b.className = "menu-item" + (it.kind === "caption" ? " caption" : "");
      b.disabled = !it.enabled;
      const label = document.createElement("span");
      label.textContent = it.label;
      b.appendChild(label);
      if (it.hint) {
        const hint = document.createElement("span");
        hint.className = "hint";
        hint.textContent = it.hint;
        b.appendChild(hint);
      }
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        this._run(it.id);
      });
      drop.appendChild(b);
    }
  }

  _close() {
    if (this._drop) this._drop.remove();
    this._drop = null;
    this._open = null;
    for (const b of this.root.querySelectorAll(".menu-title.open")) {
      b.classList.remove("open");
    }
  }

  // ---------------------------------------------------------------- items

  _items(title) {
    const posture = this.bar.posture;
    // Hard busy or in-flight bg agent — don't flip posture / exit mid-turn.
    const busy = this.bar.busy || this.bar.agentBusy;
    const panes = this.deck.paneIds();
    const has = (id) => panes.includes(id);
    const nopane = (ok) => (ok ? "" : "no pane");
    const tick = (on) => (on ? "✓ " : "  ");

    switch (title) {
      case "Xlii": {
        // Flip is talk/lab. Recents sit under Home — the way back in.
        const hubOpen = !!(this.deck && typeof this.deck._slotHoldingPane === "function"
          && this.deck._slotHoldingPane("home"));
        const rows = [
          { id: "xlii:clear", label: "  Clear transcript", enabled: true },
          { id: "door:home", label: `${tick(this._isHome)}Home Stream`, enabled: !busy,
            hint: "scratch tape" },
          { id: "pane:home", label: `${tick(hubOpen)}Home Hub`, enabled: true,
            hint: "panel hopper" },
        ];
        for (const d of (this._recent || [])) {
          const path = String(d.path || "");
          if (!path) continue;
          const kind = d.kind === "talk" ? "talk" : "lab";
          const label = String(d.label || d.name || path);
          rows.push({
            id: `join:${path}`,
            label: `${tick(!!d.current)}${label}`,
            enabled: !busy,
            hint: kind,
          });
        }
        rows.push(
          { id: "door:chat", label: `${tick(posture === "chat")}Talk`, enabled: !busy,
            hint: "?" },
          { id: "door:project", label: `${tick(posture === "code")}Lab`, enabled: !busy,
            hint: "$" },
        );
        this._appendBinds(rows, "xlii");
        rows.push(
          { id: "pane:install", label: "  Install node…", enabled: true,
            hint: "stamp a limb" },
          { id: "pane:jidmake", label: "  XMPP addresses…", enabled: true,
            hint: "mint JIDs" },
        );
        rows.push(
          { id: "xlii:exit", label: "  Exit", enabled: true,
            hint: "" },
        );
        return rows;
      }
      case "Project": {
        // Desk switch first, then create / adopt / doors, then user binds.
        const rows = [
          { id: "pane:projects", label: "  Projects…", enabled: true,
            hint: "switch desk" },
          { id: "proj:newfile", label: "  New project file…", enabled: !busy },
          { id: "proj:newfolder", label: "  New project folder…", enabled: !busy,
            hint: "task" },
          { id: "proj:newcollection", label: "  New collection…", enabled: !busy,
            hint: "task" },
          { id: "proj:create", label: "  Adopt folder…", enabled: !busy,
            hint: "dir or remote · lab" },
          { id: "proj:collection", label: "  Adopt collection…", enabled: !busy,
            hint: "dir or remote · talk" },
        ];
        const nodes = this._fabricNodes || [];
        if (nodes.length) {
          rows.push({ id: "proj:fabricsync", label: "  Sync fabric projects…",
            enabled: !busy, hint: "all nodes" });
          for (const n of nodes) {
            rows.push({
              id: `proj:remote:${n}`,
              label: `  New on ${n}…`,
              enabled: !busy,
              hint: "collection",
            });
          }
        }
        this._appendBinds(rows, "project");
        rows.push(
          { id: "pane:explorer", label: "  Files", enabled: true,
            hint: "panel" },
          { id: "pane:wiki", label: "  Wiki", enabled: true,
            hint: "panel" },
          { id: "pane:plan", label: "  Plans", enabled: true,
            hint: "panel" },
          { id: "seed:/sync", label: "  Sync", enabled: true,
            hint: "panel" },
        );
        return rows;
      }
      case "Tools": {
        const br = this._browser === "window" || this._browser === "hidden"
          ? this._browser
          : "open";
        const rows = [
          { id: "tools:jobs", label: "  Jobs", enabled: true,
            hint: "panel" },
          { id: "tools:tasks", label: "  Run task…", enabled: true,
            hint: "panel" },
          { id: "tools:skills", label: "  Skills", enabled: true,
            hint: "panel" },
          { id: "pane:plugins", label: "  Plugins", enabled: true,
            hint: "catalog" },
          { id: "tools:term", label: "  New terminal", enabled: true,
            hint: this._termCwd || "this project" },
          { id: "tools:browser", label: "  Browser", enabled: true,
            hint: br },
          { id: "pane:canvas", label: "  Canvas", enabled: true,
            hint: "the pad" },
          { id: "pane:taskmake", label: "  Task maker", enabled: true,
            hint: "form" },
          { id: "pane:pluginmake", label: "  Plugin maker", enabled: true,
            hint: "form" },
          { id: "pane:gigmake", label: "  Gigwork", enabled: true,
            hint: "form" },
          { id: "pane:remotemake", label: "  Remotes", enabled: true,
            hint: "form" },
        ];
        this._appendBinds(rows, "tools");
        return rows;
      }
      case "Commands": {
        // Attach verbs + slash/OS catalog. One menu; Attach is not a family.
        const sel = this.transcript && this.transcript.getSelection
          ? this.transcript.getSelection()
          : null;
        const cats = (this._consoleCategories && this._consoleCategories.length)
          ? this._consoleCategories
          : ["System", "Network", "Packages", "Searches"];
        const rows = [
          { id: "attach:files", label: "  Attach files…", enabled: true },
          {
            id: "attach:focus",
            label: "  Focus",
            enabled: !!(sel && sel.address),
            hint: sel ? "next turn" : "select a viewed file",
          },
          { id: "seed:/attachments", label: "  Attachments", enabled: true },
          { id: "seed:/detach ", label: "  Detach…", enabled: true },
          { id: "cmd:xlii", label: "  Xlii…", enabled: true, hint: "slash menu" },
        ];
        for (const name of cats) {
          rows.push({
            id: `cmd:cat:${name}`,
            label: `  ${name}…`,
            enabled: true,
            hint: "shell shortcuts",
          });
        }
        rows.push({
          id: "cmd:history", label: "  Input history…", enabled: true, hint: "panel",
        });
        return rows;
      }
      case "Options": {
        // Sticky-friendly rows: tier cycle · theme · config.
        const tiers = ["auto", "fast", "expert", "heavy", "off"];
        const cur = tiers.includes(this._chatTier) ? this._chatTier : "off";
        const nxt = tiers[(tiers.indexOf(cur) + 1) % tiers.length];
        const skin = (typeof window.__xliiFaceSkin === "function")
          ? window.__xliiFaceSkin()
          : "dark";
        const fkeysOn = (typeof window.__xliiFkeysVisible === "function")
          ? window.__xliiFkeysVisible()
          : true;
        const boldOn = (typeof window.__xliiBoldType === "function")
          ? window.__xliiBoldType()
          : false;
        return [
          {
            id: `opt:tier:${nxt}`,
            label: `  Chat tier: ${cur}`,
            enabled: !busy,
            hint: `click → ${nxt}`,
          },
          {
            id: "opt:fkeys",
            label: `  F-keys: ${fkeysOn ? "visible" : "hidden"}`,
            enabled: true,
            hint: "click to toggle",
          },
          {
            id: "opt:bold",
            label: `  Bold type: ${boldOn ? "on" : "off"}`,
            enabled: true,
            hint: "terminal-weight type",
          },
          {
            id: "opt:theme",
            label: `  Theme: ${skin}…`,
            enabled: true,
            hint: "skin picker panel",
          },
          {
            id: "opt:config",
            label: "  Config…",
            enabled: true,
            hint: "session knobs panel",
          },
          {
            id: "pane:bindmake",
            label: "  Bind chrome…",
            enabled: true,
            hint: "form",
          },
          {
            id: "opt:screenshot",
            label: "  Save screenshot",
            enabled: true,
            hint: "Desktop",
          },
        ];
      }
      case "Keep": {
        // Talk-side tray only. Lab doors are Project; jobs/tasks/skills/plugins
        // are Tools; skins are Options. `/workbench` still retargets the dock list.
        const items = [];
        for (const p of this._paneCatalog) {
          if (PANEL_OWNED_ELSEWHERE.has(p.id)) continue;
          const open = this.deck.openPane === p.id;
          const mounted = has(p.id);
          items.push({
            id: `pane:${p.id}`,
            label: `${tick(open)}${p.label || p.id}`,
            enabled: true,
            hint: open ? "open" : (mounted ? "" : "open"),
          });
        }
        items.push({ id: "panel:close", label: "  Close panel", enabled: !!this.deck.openPane });
        return items;
      }
      case "Help": {
        const seen = new Set();
        const groups = [];
        for (const t of (this._howto || DEFAULT_HOWTO)) {
          const g = String(t.group || "").trim();
          if (!g || seen.has(g)) continue;
          seen.add(g);
          groups.push(g);
        }
        const labels = { start: "Start…", work: "Work…", more: "More…" };
        const rows = [
          { id: "howto:", label: "  How to use xlii", enabled: true },
        ];
        for (const g of groups) {
          rows.push({
            id: `help:g:${g}`,
            label: `  ${labels[g] || `${g}…`}`,
            enabled: true,
          });
        }
        rows.push({ id: "help:about", label: "  About", enabled: true });
        return rows;
      }
      default:
        return [];
    }
  }

  // -------------------------------------------------------------- actions

  _runBind(id) {
    const parts = String(id || "").split(":");
    const menu = parts[1] || "";
    const task = parts.slice(2).join(":");
    const row = this._bindRows(menu).find((b) => b.task === task)
      || (this._binds || []).find((b) => b.task === task);
    const line = row && row.line
      ? String(row.line)
      : (task ? `/tasks run ${task} ` : "");
    if (!line) return;
    if (line.endsWith(" ")) {
      if (this.bar && typeof this.bar.seed === "function") this.bar.seed(line);
      return;
    }
    if (this.bar && this.bar.posture !== "code") {
      this.wire.send({ type: "set_posture", posture: "code" });
    }
    this.wire.send({ type: "input", text: line.trim() });
  }

  _run(id) {
    if (String(id || "").startsWith("bind:")) {
      this._runBind(id);
      this._close();
      return;
    }
    if (id === "noop") return;
    if (id.startsWith("seed:")) {
      this.bar.seed(id.slice(5));
      this._close();
      return;
    }
    if (id.startsWith("pane:")) {
      this.deck.togglePane(id.slice(5));
      this._close();
      return;
    }
    if (id.startsWith("join:")) {
      const path = id.slice("join:".length);
      if (path) this.wire.send({ type: "join_project", name: `@${path}` });
      this._close();
      return;
    }
    // Chat tier: cycle in place — keep Options open.
    // Dismiss is Esc / click outside (same as leaving the menu alone).
    if (id.startsWith("opt:tier:")) {
      const next = id.slice("opt:tier:".length);
      this._chatTier = next; // optimistic label until chrome_state returns
      this.wire.send({ type: "input", text: `/tier ${next}` });
      this._refreshOpen();
      return;
    }
    if (id === "opt:fkeys") {
      const on = typeof window.__xliiFkeysVisible === "function"
        ? window.__xliiFkeysVisible()
        : true;
      if (typeof window.__xliiSetFkeysVisible === "function") {
        window.__xliiSetFkeysVisible(!on);
      }
      this._refreshOpen();
      return;
    }
    if (id === "opt:bold") {
      const on = typeof window.__xliiBoldType === "function"
        ? window.__xliiBoldType()
        : false;
      if (typeof window.__xliiSetBoldType === "function") {
        window.__xliiSetBoldType(!on);
      }
      this._refreshOpen();
      return;
    }
    switch (id) {
      case "xlii:clear":
        this.transcript.clear();
        this.wire.send({ type: "input", text: "/cls" });
        break;
      case "tools:term":
      case "xlii:term":
        this.wire.send({ type: "open_terminal" });
        break;
      case "tools:browser":
        // Same Chromium the agent drives. Headed. Chip + hint follow live state.
        this.wire.send({ type: "open_browser" });
        break;

      case "door:home":
        // Leave the bound folder. Same flip. Scratch = home = blank slate.
        this.wire.send({ type: "go_home" });
        break;
      case "door:chat":
      case "xlii:chat":
        this.wire.send({ type: "set_posture", posture: "chat" });
        break;
      case "door:project":
      case "xlii:code":
        this.wire.send({ type: "set_posture", posture: "code" });
        break;
      case "xlii:exit":
        // Server runs graceful exit + emits session_end; app.js closes Tauri.
        // Do not race-close here — that left a half-saved session + orphan window.
        this.wire.send({ type: "input", text: "/exit" });
        break;
      case "attach:files":
        this.filepick.click();
        break;
      case "attach:focus": {
        const sel = this.transcript && this.transcript.getSelection
          ? this.transcript.getSelection()
          : null;
        if (sel && sel.address && typeof window.__xliiFocus === "function") {
          window.__xliiFocus(sel.address);
        }
        break;
      }
      case "tools:jobs":
        this.deck.togglePane("jobs");
        break;
      case "tools:tasks":
        this.deck.togglePane("tasks");
        break;
      case "tools:skills":
        this.deck.togglePane("skills");
        break;
      case "cmd:xlii":
        this.deck.togglePane("menu");
        break;
      case "cmd:history":
        // Open history:// as a face panel (parity with TUI HistoryPanel).
        this.deck.togglePane("history");
        break;
      case "proj:newfile":
        this._claimCreate("file");
        break;
      case "proj:newfolder":
        this._claimNewFolder("code");
        break;
      case "proj:newcollection":
        this._claimNewFolder("collection");
        break;
      case "proj:create":
        this._claimCreateProject("code");
        break;
      case "proj:collection":
        this._claimCreateProject("collection");
        break;
      case "proj:fabricsync":
        this.wire.send({ type: "fabric_sync_projects" });
        break;
      case "opt:config":
        this.deck.togglePane("config");
        break;
      case "opt:theme":
        // Side-dock skin picker (not a Textual theme list — face CSS skins).
        this.deck.togglePane("skins");
        break;
      case "opt:screenshot":
        this._saveScreenshot();
        break;
      case "panel:close":
        this.deck.close();
        break;
      case "help:about":
        this._close();
        requestAnimationFrame(() => this._openAbout());
        return;
      default:
        if (id === "help:back") {
          this._fill(this._drop, this._items("Help"));
          return;
        }
        if (id.startsWith("help:g:")) {
          this._openHelpGroup(id.slice("help:g:".length));
          return;
        }
        if (id === "howto:" || id.startsWith("howto:")) {
          const topic = id.slice("howto:".length).trim();
          this.wire.send({ type: "input", text: topic ? `/howto ${topic}` : "/howto" });
          this._close();
          return;
        }
        if (id.startsWith("cmd:cat:")) {
          this._openConsoleCategory(id.slice("cmd:cat:".length));
          return; // nested dropdown stays open under Commands title
        }
        if (id.startsWith("csh:")) {
          // csh:Category:index — run or seed shell shortcut
          this._runConsoleShortcut(id);
          break;
        }
        if (id.startsWith("proj:remote:")) {
          this._claimFabricNew(id.slice("proj:remote:".length));
          break;
        }
        break;
    }
    this._close();
  }

  _aboutRoot() {
    return document.getElementById("about-card");
  }

  _aboutOpen() {
    const el = this._aboutRoot();
    return !!(el && !el.hidden);
  }

  _closeAbout() {
    const el = this._aboutRoot();
    if (el) el.hidden = true;
  }

  _openAbout() {
    const root = this._aboutRoot();
    if (!root) return;
    if (root.parentElement !== document.body) {
      document.body.appendChild(root);
    }
    const lines = this._aboutTaglines.length ? this._aboutTaglines : DEFAULT_TAGLINES;
    const credit = root.querySelector(".about-credit");
    const verEl = root.querySelector(".about-version");
    const factsEl = root.querySelector(".about-facts");
    const signsEl = root.querySelector(".about-signs");
    const lineEl = root.querySelector(".about-line");
    const product = root.querySelector(".about-product");
    if (product) product.textContent = "xlii — a personal AI substrate";
    if (credit) credit.textContent = this._aboutCredit || "Say hello — hello@xlii.computer";
    if (verEl) {
      verEl.textContent = this._aboutVersion || "";
      verEl.hidden = !this._aboutVersion;
    }
    if (factsEl) {
      const rows = Array.isArray(this._aboutFacts) ? this._aboutFacts : [];
      factsEl.textContent = "";
      for (const row of rows) {
        const d = document.createElement("div");
        d.textContent = row;
        factsEl.appendChild(d);
      }
      factsEl.hidden = !rows.length;
    }
    if (signsEl) {
      signsEl.textContent = this._aboutSigns || "";
      signsEl.hidden = !this._aboutSigns;
    }
    const paint = () => {
      const cur = lines[((this._aboutIdx % lines.length) + lines.length) % lines.length]
        || this._aboutLine || DEFAULT_TAGLINES[0];
      if (lineEl) lineEl.textContent = cur;
    };
    if (!this._aboutWired) {
      this._aboutWired = true;
      root.addEventListener("click", (e) => {
        if (e.target === root) this._closeAbout();
      });
      const close = root.querySelector(".about-close");
      if (close) close.addEventListener("click", () => this._closeAbout());
      const next = root.querySelector(".about-next");
      if (next) {
        next.addEventListener("click", (e) => {
          e.stopPropagation();
          this._aboutIdx = (this._aboutIdx + 1) % lines.length;
          paint();
        });
      }
    }
    paint();
    root.hidden = false;
  }

  _claimCreate(kind) {
    const label = kind === "folder"
      ? "new folder — enter a name (Esc cancels):"
      : "new file — enter a name (Esc cancels):";
    this.bar.claimLine({
      label,
      initial: "",
      onSubmit: (name) => {
        this.wire.send({ type: "create_in_project", kind, name });
      },
    });
  }

  _claimFabricNew(node) {
    const host = String(node || "").trim();
    if (!host) return;
    this.bar.claimLine({
      label: `new on ${host} — enter a name (Esc cancels):`,
      initial: "",
      onSubmit: (name) => {
        const n = String(name || "").trim();
        if (!/^[A-Za-z0-9._-]+$/.test(n) || n === "." || n === "..") {
          this.bar._localMeta("need a simple name (letters, digits, . _ -)", "warn");
          return;
        }
        this.wire.send({ type: "fabric_new", node: host, name: n });
      },
    });
  }

  _claimNewFolder(kind) {
    const pile = kind === "collection";
    this.bar.claimLine({
      label: pile
        ? "new collection — enter a name (Esc cancels):"
        : "new project folder — enter a name (Esc cancels):",
      initial: "",
      onSubmit: (name) => {
        const n = String(name || "").trim();
        if (!/^[A-Za-z0-9._-]+$/.test(n) || n === "." || n === "..") {
          this.bar._localMeta("need a simple name (letters, digits, . _ -)", "warn");
          return;
        }
        if (this.bar.posture !== "code") {
          this.wire.send({ type: "set_posture", posture: "code" });
        }
        const extra = pile ? " kind=collection" : "";
        this.wire.send({
          type: "input",
          text: `/tasks run new-folder ${n}${extra} --yes`,
        });
      },
    });
  }

  _saveScreenshot() {
    // Let the Options drop unpaint, then the server grabs the window.
    // DOM→SVG is a blank sheet; WebKit cannot rasterize it.
    window.setTimeout(() => {
      this.wire.send({ type: "save_screenshot" });
    }, 60);
  }

  _folderHint() {
    const addr = (this.deck && this.deck.lastAddress) || "";
    if (addr.startsWith("file://")) return addr.slice("file://".length);
    const panes = this.deck && this.deck.state && this.deck.state.panes;
    if (Array.isArray(panes)) {
      const ex = panes.find((p) => p.id === "explorer");
      const title = ex && String(ex.title || "");
      if (title.startsWith("file://")) return title.slice("file://".length);
    }
    return "";
  }

  _claimCreateProject(kind) {
    const pile = kind === "collection";
    const hint = this._folderHint();
    this.bar.claimLine({
      label: pile
        ? "adopt folder as collection (Enter confirms, Esc cancels):"
        : "adopt folder as project (Enter confirms, Esc cancels):",
      initial: hint,
      onSubmit: (path) => {
        this.wire.send({ type: "create_project", path, kind: pile ? "collection" : "code" });
      },
    });
  }

  _openHelpGroup(group) {
    if (!this._drop) return;
    const want = String(group || "").trim();
    const items = [
      { id: "help:back", label: "  ◂ Help", enabled: true },
    ];
    for (const t of (this._howto || DEFAULT_HOWTO)) {
      if (String(t.group || "").trim() !== want) continue;
      const id = String(t.id || "").trim();
      if (!id) continue;
      items.push({
        id: `howto:${id}`,
        label: `  ${t.menu || t.title || id}`,
        enabled: true,
      });
    }
    this._fill(this._drop, items);
  }

  _openConsoleCategory(name) {
    const cmds = this._consoleByCat[name] || [];
    if (!this._drop) return;
    const items = cmds.map((c, i) => ({
      id: `csh:${name}:${i}`,
      label: `  ! ${c}`,
      enabled: true,
      hint: c.includes("<") ? "fill placeholders" : "run",
    }));
    if (!items.length) {
      items.push({ id: "noop", label: "  (empty)", enabled: false });
    }
    // Keep parent title as Commands; refill with category recipes.
    this._fill(this._drop, items);
  }

  _runConsoleShortcut(id) {
    // id = csh:Category:index
    const parts = id.split(":");
    if (parts.length < 3) return;
    const cat = parts[1];
    const idx = parseInt(parts[2], 10);
    const cmds = this._consoleByCat[cat] || [];
    const cmd = cmds[idx];
    if (!cmd) return;
    const line = `!${cmd}`;
    if (cmd.includes("<")) {
      // Face has no claim-line for mid-template edit of arbitrary markers —
      // seed for review-before-run (user fills <…> then Enter).
      this.bar.seed(line);
      this.bar._localMeta("edit <placeholders>, then Enter to run", "info");
    } else {
      // Ready recipe — flip to code if needed and run.
      if (this.bar.posture !== "code") {
        this.wire.send({ type: "set_posture", posture: "code" });
      }
      this.wire.send({ type: "input", text: line });
    }
  }

  /** Rebuild the open dropdown so cycle labels update in place. */
  _refreshOpen() {
    if (!this._drop || !this._open) return;
    this._fill(this._drop, this._items(this._open));
  }

  _paneOrSeed(paneId, command) {
    if (this.deck.paneIds().includes(paneId)) {
      this.deck.togglePane(paneId);
    } else {
      this.bar.seed(command);
    }
  }

}
