// The input bar: submit, the talk/lab flip (M-brain), mode chrome, the
// confirm bar, cancel, uploads (drag-drop + picker), busy heartbeat, and
// slash completions. Catalog is chat-safe in [M], full lab in [$].

const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
const HISTORY_KEY = "xlii-face-history";
const HISTORY_MAX = 200;
const QUEUE_MAX = 16;
const SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏";
import { overlayMark } from "./overlay_marks.js?v=flip1";

// Talk = hollow TL+BR steel 42; lab = mirrored diagonal (hollow↔solid).
const FLIP_MARK = {
  chat: {
    src: "img/stamp-steel.png",
    srcset: "img/stamp-steel.png 1x, img/stamp-steel@2x.png 2x",
    alt: "talk",
  },
  code: {
    src: "img/stamp-steel-mirror.png",
    srcset: "img/stamp-steel-mirror.png 1x, img/stamp-steel-mirror@2x.png 2x",
    alt: "lab",
  },
};

// Quiet surfaces — exclusive modes (plan/rail/…) get the loud chip.
const QUIET_MODES = new Set(["mojo", "code", "chat", "scratch", ""]);

export class InputBar {
  constructor(wire, els) {
    this.wire = wire;
    this.els = els; // {input, send, stop, flip, flipName, flipMark, filepick, shell,
                    //  confirmbar, confirmPrompt, approve, deny,
                    //  modeChip, hint, exitHint, conn, heartbeat, hbSpin,
                    //  hbLabel, completions}
    this.busy = false;       // hard busy — submit blocked
    this.agentBusy = false;  // bg agent (M2.2) — submit free for allow-list
    this.posture = "chat";
    this.overlay = "";
    this._confirmId = null;
    this._history = this._loadHistory();
    this._histIdx = null;   // null = editing the live draft
    this._histDraft = "";
    this._catalog = [];     // [{name, description}, …] from command_catalog
    this._compMatches = [];
    this._compIndex = 0;
    this._hbTimer = null;
    this._hbStarted = 0;
    this._spinI = 0;
    this._slashNudgeShown = false; // unused; chat now has slash like the Commands panel
    this._claim = null; // { onSubmit(value), label } — Project create / new name
    this._queue = [];   // [{id, text}] stacked while a turn is in flight
    this._qid = 0;
    this._bind();
  }

  // ------------------------------------------------------------- wiring

  _bind() {
    const { input, send, stop, flip, filepick, approve, deny, shell } = this.els;

    input.addEventListener("keydown", (e) => {
      if (this._claim && e.key === "Escape") {
        e.preventDefault();
        this._claim = null;
        input.value = "";
        this._autosize();
        this._localMeta("cancelled", "info");
        return;
      }
      if (this._compOpen()) {
        if (e.key === "ArrowDown") {
          e.preventDefault();
          this._compMove(1);
          return;
        }
        if (e.key === "ArrowUp") {
          e.preventDefault();
          this._compMove(-1);
          return;
        }
        if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
          e.preventDefault();
          this._compAccept();
          return;
        }
        if (e.key === "Escape") {
          e.preventDefault();
          this._compHide();
          return;
        }
      }
      if (e.key === ";" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        this._queueNow(0);
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        this.submit();
      } else if (e.key === "ArrowUp" && this._atHistoryBoundary(input, "start")) {
        e.preventDefault();
        this._historyStep(1);
      } else if (e.key === "ArrowDown" && this._atHistoryBoundary(input, "end")) {
        e.preventDefault();
        this._historyStep(-1);
      } else if (e.key !== "ArrowUp" && e.key !== "ArrowDown") {
        this._histIdx = null; // any real typing breaks the history walk
      }
    });
    input.addEventListener("input", () => {
      this._autosize();
      this._onInputValue();
    });
    send.addEventListener("click", () => this.submit());
    // Stop cancels the turn AND denies any pending gate (matches heartbeat copy).
    stop.addEventListener("click", () => {
      if (this._confirmId != null) this._answerConfirm(false);
      this.wire.send({ type: "cancel" });
    });
    if (flip) flip.addEventListener("click", () => this._flip());
    approve.addEventListener("click", () => this._answerConfirm(true));
    deny.addEventListener("click", () => this._answerConfirm(false));

    // Modal-style y/n/Escape while a gate is open — Textual ConfirmModal parity.
    // The prompt text still says [y/N]; without this, typing y only dirties the
    // input bar and never resolves the wire confirm.
    document.addEventListener("keydown", (e) => {
      if (this._confirmId == null) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target;
      if (target === this.els.input || (target && target.isContentEditable)) return;
      const k = e.key;
      if (k === "y" || k === "Y") {
        e.preventDefault();
        this._answerConfirm(true);
      } else if (k === "n" || k === "N" || k === "Escape") {
        e.preventDefault();
        this._answerConfirm(false);
      }
    });

    filepick.addEventListener("change", () => {
      for (const f of filepick.files) this._upload(f);
      filepick.value = "";
    });
    shell.addEventListener("dragover", (e) => {
      e.preventDefault();
      shell.classList.add("dragging");
    });
    shell.addEventListener("dragleave", () => shell.classList.remove("dragging"));
    shell.addEventListener("drop", (e) => {
      e.preventDefault();
      shell.classList.remove("dragging");
      for (const f of e.dataTransfer.files) this._upload(f);
    });
  }

  // ------------------------------------------------------------- history

  _loadHistory() {
    try {
      const raw = localStorage.getItem(HISTORY_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr.slice(-HISTORY_MAX) : [];
    } catch { return []; }
  }

  _saveHistory() {
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(this._history.slice(-HISTORY_MAX))); }
    catch { /* storage full/blocked — history is a nicety */ }
  }

  _atHistoryBoundary(input, which) {
    // Shell rule: ↑ works when the cursor is on the FIRST line, ↓ on the last.
    const before = input.value.slice(0, input.selectionStart);
    const after = input.value.slice(input.selectionEnd);
    return which === "start" ? !before.includes("\n") : !after.includes("\n");
  }

  _historyStep(dir) {
    const input = this.els.input;
    if (this._histIdx === null) {
      if (dir !== 1 || !this._history.length) return;
      this._histDraft = input.value;
      this._histIdx = this._history.length;
    }
    const next = this._histIdx - dir; // dir=1 → older, dir=-1 → newer
    if (next >= this._history.length) {
      this._histIdx = null;
      input.value = this._histDraft;
    } else if (next < 0) {
      this._histIdx = 0;
      input.value = this._history[0];
    } else {
      this._histIdx = next;
      input.value = this._history[next];
    }
    input.selectionStart = input.selectionEnd = input.value.length;
    this._autosize();
    this._compHide();
  }

  _pushHistory(text) {
    if (this._history[this._history.length - 1] !== text) {
      this._history.push(text);
      this._saveHistory();
    }
    this._histIdx = null;
    this._histDraft = "";
  }

  /** History pane “Clear typed lines” — ↑/↓ only, not working talk. */
  clearTypedHistory() {
    this._history = [];
    this._histIdx = null;
    this._histDraft = "";
    try { localStorage.removeItem(HISTORY_KEY); }
    catch { /* nicety */ }
  }

  _autosize() {
    const el = this.els.input;
    const compose = document.documentElement.dataset.compose === "1";
    el.style.height = "auto";
    const cap = compose ? 120 : 180;
    const min = compose ? 72 : 0;
    el.style.height = `${Math.max(min, Math.min(el.scrollHeight, cap))}px`;
  }

  // ------------------------------------------------------------- catalog

  setCommandCatalog(ev) {
    const cmds = ev && ev.commands;
    this._catalog = Array.isArray(cmds) ? cmds : [];
  }

  // ------------------------------------------------------------- submit

  // Menu-bar seams (menubar.js): history() feeds Commands → Input history…;
  // seed() is the face's review-before-run rule — a menu row stages the line
  // in the box, Enter (the user's) is what fires it.
  history() {
    return [...this._history];
  }

  seed(text) {
    this._claim = null; // a normal seed cancels any claimed line
    const input = this.els.input;
    input.value = text;
    input.selectionStart = input.selectionEnd = text.length;
    input.focus();
    this._autosize();
    this._onInputValue();
  }

  /**
   * Claim the input for a short form (TUI minibuffer parity).
   * Enter → onSubmit(value); Esc clears the claim.
   * Used for Project → Adopt folder / New file / New folder names.
   */
  claimLine({ label, initial = "", onSubmit }) {
    this._claim = typeof onSubmit === "function" ? { onSubmit, label: label || "" } : null;
    const input = this.els.input;
    const text = initial == null ? "" : String(initial);
    input.value = text;
    input.placeholder = label || "…";
    input.selectionStart = 0;
    input.selectionEnd = text.length;
    input.focus();
    this._autosize();
    this._compHide();
    if (label) this._localMeta(label, "info");
  }

  clearClaim() {
    this._claim = null;
  }

  submit() {
    const text = this.els.input.value.trim();

    // Claimed line (create project / new file name) — Enter runs the callback.
    if (this._claim) {
      const claim = this._claim;
      this._claim = null;
      this.els.input.value = "";
      this._autosize();
      this._compHide();
      if (text) claim.onSubmit(text);
      else this._localMeta("cancelled — empty name", "info");
      return;
    }

    if (!text) return;

    // /panel and /home are chrome — never block on hard/agent busy.
    if (/^\/panel(?:\s|$)/i.test(text) || /^\/home(?:\s|$)/i.test(text)) {
      this._compHide();
      this._submitPanel(text);
      return;
    }

    // /exit·/quit must reach the server even mid-turn (OS close / typed quit).
    if (/^\/(?:exit|quit)(?:\s|$)/i.test(text)) {
      this._compHide();
      if (this.wire.send({ type: "input", text })) {
        this._pushHistory(text);
        this.els.input.value = "";
        this._autosize();
      }
      return;
    }

    // Pending approve/deny gate: y/n in the box answers it (not a new shell line).
    if (this._confirmId != null) {
      const ans = text.toLowerCase();
      if (ans === "y" || ans === "yes") {
        this.els.input.value = "";
        this._autosize();
        this._answerConfirm(true);
        return;
      }
      if (ans === "n" || ans === "no") {
        this.els.input.value = "";
        this._autosize();
        this._answerConfirm(false);
        return;
      }
      this._localMeta(
        "approve or deny the pending gate first (y / n, or the buttons)",
        "warn",
      );
      return;
    }
    if (this.busy) {
      this._enqueue(text);
      return;
    }
    if (this.agentBusy && !this._allowedWhileAgent(text)) {
      this._enqueue(text);
      return;
    }
    this._compHide();
    if (this.wire.send({ type: "input", text })) {
      this._pushHistory(text);
      this.els.input.value = "";
      this._autosize();
      // Hard-busy until busy_state frees us (bg agent) or turn_done.
      this.setBusy(true);
    }
  }

  /** Side panels: control-plane wire (open_pane), not the turn input queue. */
  _submitPanel(line) {
    const parts = line.trim().split(/\s+/);
    const verb = (parts[0] || "").toLowerCase();
    // /home → /panel home (extra words, including off, still apply).
    if (verb === "/home") {
      parts[0] = "/panel";
      if (!parts[1]) parts.push("home");
    }
    const arg = (parts[1] || "").toLowerCase();
    this._pushHistory(line);
    this.els.input.value = "";
    this._autosize();

    if (!arg || arg === "?" || arg === "help" || arg === "list") {
      this._localMeta(
        "panel: home|projects|files|git|tasks|jobs|plan|bookmarks|wiki|docs|"
        + "locker|skills|sources|results|artifacts|menu|gigwork · /panel off",
        "info",
      );
      if (arg === "?" || arg === "help" || arg === "list") return;
    }
    if (arg === "off" || arg === "close" || arg === "hide") {
      this.wire.send({ type: "open_pane", pane: "" }); // server closes
      // local close immediately
      if (window.__xliiDeck) window.__xliiDeck.close();
      return;
    }
    // Map common /panel words → deck slot ids (server also maps).
    const map = {
      files: "explorer", file: "explorer", explorer: "explorer", vfs: "explorer",
      images: "locker", image: "locker", gallery: "locker",
      bookmarks: "bookmarks", bookmark: "bookmarks", marks: "bookmarks", mark: "bookmarks",
      join: "projects", switch: "projects",
      project: "projects", projects: "projects",
      history: "history", hist: "history",
      assets: "artifacts",
      commands: "menu", cmds: "menu",
      // panel + scheme are skills (plural); slash attach verb is /skill
      skill: "skills", skills: "skills",
    };
    const pane = map[arg] || arg || "home";
    this.wire.send({ type: "open_pane", pane });
    this._localMeta(`opening panel · ${pane}…`, "info");
  }

  _allowedWhileAgent(text) {
    const t = text.trim();
    if (t.startsWith("!") || t.startsWith("?>")) return true;
    if (/^\/panel(?:\s|$)/i.test(t)) return true;
    if (t.startsWith("?")) return false;
    if (t.startsWith("/")) {
      const token = t.slice(1).split(/\s+/, 1)[0].toLowerCase();
      return token === "btw" || token === "jobs" || token === "panel"
        || token === "home"
        || token === "exit" || token === "quit"
        || token === "clear" || token === "cls" || token === "clear-screen";
    }
    // Bare shell only in code posture (server re-checks shell-primary).
    return this.posture === "code";
  }

  _flip() {
    if (this.overlay) {
      this._exitOverlay();
      return;
    }
    this._flipTo(this.posture === "chat" ? "code" : "chat");
  }

  _exitOverlay() {
    if (this.busy || this.agentBusy) return;
    this.wire.send({ type: "exit_overlay" });
  }

  _flipTo(posture) {
    if (this.busy || this.agentBusy) return;
    if (document.documentElement.dataset.view === "phone") return;
    if (posture !== "chat" && posture !== "code") return;
    if (posture === this.posture) return;
    // Paint the flip on the click, not on the round trip. The server's
    // mode_state is authoritative (it re-sends one on every refusal).
    this._paintFlip(posture, "");
    this.wire.send({ type: "set_posture", posture });
  }

  /** The flip chip: overlay mark when a mode is on, else the talk/lab stamp. */
  _paintFlip(posture, overlay) {
    const flip = this.els.flip;
    if (!flip) return;
    if (flip.dataset.posture !== posture) flip.dataset.posture = posture;
    if (overlay) {
      if (flip.dataset.overlay !== overlay) {
        flip.dataset.overlay = overlay;
        if (this.els.flipName) this.els.flipName.innerHTML = overlayMark(overlay);
      }
      if (this.els.flipName) this.els.flipName.hidden = false;
      flip.title = `${overlay} on — click to leave`;
      flip.setAttribute("aria-label", `${overlay} on, click off`);
      return;
    }
    if (flip.dataset.overlay != null) {
      delete flip.dataset.overlay;
      if (this.els.flipName) {
        this.els.flipName.hidden = true;
        this.els.flipName.textContent = "";
      }
    }
    const mark = this.els.flipMark;
    if (mark) {
      const want = posture === "chat" ? FLIP_MARK.chat : FLIP_MARK.code;
      // Same src → no attribute churn, no image re-decode.
      if (mark.getAttribute("src") !== want.src) {
        mark.src = want.src;
        mark.srcset = want.srcset;
        mark.alt = want.alt;
      }
    }
    flip.title = posture === "chat" ? "talk — click for lab" : "lab — click for talk";
    flip.setAttribute("aria-label", "talk or lab");
  }

  setBusy(b) {
    this.busy = b;
    this._paintBusy();
  }

  /** Wire busy_state (M2.2): free the prompt while an agent turn continues. */
  applyBusyState(ev) {
    if (typeof ev.hard === "boolean") this.busy = ev.hard;
    if (typeof ev.agent === "boolean") this.agentBusy = ev.agent;
    this._paintBusy();
    this.drainQueue();
  }

  drainQueue() {
    if (this.busy) return;
    if (!this._queue.length) return;
    const item = this._queue[0];
    if (this.agentBusy && !this._allowedWhileAgent(item.text)) return;
    if (this._confirmId != null) return;
    if (!this.wire.send({ type: "input", text: item.text })) return;
    this._queue.shift();
    this.setBusy(true);
    this._paintQueue();
  }

  _enqueue(text) {
    const line = (text || "").trim();
    if (!line) return;
    if (this._queue.length >= QUEUE_MAX) {
      this._localMeta(`queue full (${QUEUE_MAX}) — send now, edit, or drop one`, "warn");
      return;
    }
    this._queue.push({ id: ++this._qid, text: line });
    this._pushHistory(line);
    this.els.input.value = "";
    this._autosize();
    this._compHide();
    this._paintQueue();
  }

  _queueNow(index) {
    if (!this._queue.length) return;
    const i = Math.max(0, Math.min(index | 0, this._queue.length - 1));
    if (i > 0) {
      const [item] = this._queue.splice(i, 1);
      this._queue.unshift(item);
      this._paintQueue();
    }
    if (this.busy) return; // next after the current turn
    this.drainQueue();
  }

  _queueEdit(index) {
    if (index < 0 || index >= this._queue.length) return;
    const [item] = this._queue.splice(index, 1);
    this._paintQueue();
    this.seed(item.text);
  }

  _queueDrop(index) {
    if (index < 0 || index >= this._queue.length) return;
    this._queue.splice(index, 1);
    this._paintQueue();
  }

  _paintQueue() {
    const host = this.els.queue;
    if (!host) return;
    host.textContent = "";
    if (!this._queue.length) {
      host.hidden = true;
      return;
    }
    host.hidden = false;
    this._queue.forEach((item, i) => {
      const row = document.createElement("div");
      row.className = "q-row";
      const n = document.createElement("span");
      n.className = "q-n";
      n.textContent = `#${i + 1}`;
      const body = document.createElement("span");
      body.className = "q-text";
      body.textContent = item.text.replace(/\s+/g, " ");
      body.title = item.text;
      const now = document.createElement("button");
      now.type = "button";
      now.className = "q-btn";
      now.textContent = "now";
      now.title = "send this next";
      now.addEventListener("click", () => this._queueNow(i));
      const ed = document.createElement("button");
      ed.type = "button";
      ed.className = "q-btn";
      ed.textContent = "edit";
      ed.addEventListener("click", () => this._queueEdit(i));
      const del = document.createElement("button");
      del.type = "button";
      del.className = "q-btn";
      del.textContent = "×";
      del.title = "drop";
      del.addEventListener("click", () => this._queueDrop(i));
      row.append(n, body, now, ed, del);
      host.appendChild(row);
    });
  }

  _paintBusy() {
    const hard = this.busy;
    const agent = this.agentBusy;
    const active = hard || agent;
    // Send stays up so Enter/click can queue while a turn runs.
    this.els.send.hidden = false;
    if (this.els.send) this.els.send.title = hard ? "queue" : "send";
    this.els.stop.hidden = !active;
    this.els.input.disabled = false;
    if (active) this._hbStart();
    else this._hbStop();
  }

  // ---------------------------------------------------------- heartbeat

  _hbStart() {
    if (!this._hbStarted || !(this.busy || this.agentBusy)) {
      this._hbStarted = performance.now();
      this._spinI = 0;
    }
    const hb = this.els.heartbeat;
    if (hb) hb.hidden = false;
    this._hbTick();
    if (this._hbTimer) clearInterval(this._hbTimer);
    this._hbTimer = setInterval(() => this._hbTick(), 100);
  }

  _hbStop() {
    if (this._hbTimer) {
      clearInterval(this._hbTimer);
      this._hbTimer = null;
    }
    if (this.els.heartbeat) this.els.heartbeat.hidden = true;
    this._hbStarted = 0;
  }

  _hbTick() {
    if (!this.busy && !this.agentBusy) return;
    const elapsed = Math.floor((performance.now() - this._hbStarted) / 1000);
    const frame = SPINNER[this._spinI % SPINNER.length];
    this._spinI += 1;
    if (this.els.hbSpin) this.els.hbSpin.textContent = frame;
    const waiting = this.els.confirmbar && !this.els.confirmbar.hidden;
    let label;
    if (waiting) {
      label = `waiting for your answer — stop = deny  ${elapsed}s`;
    } else if (this.agentBusy && !this.busy) {
      label = `agent working — input free · /btw to steer  ${elapsed}s`;
    } else {
      label = `working ${elapsed}s`;
    }
    if (this.els.hbLabel) this.els.hbLabel.textContent = label;
  }

  // ------------------------------------------------------------ confirm

  showConfirm(ev) {
    this._confirmId = ev.id;
    // Gate prompts are often "approve network command?\n  curl…\n[y/N] " —
    // keep the body readable; face actions are buttons + y/n keys.
    this.els.confirmPrompt.textContent = ev.prompt || "";
    this.els.confirmbar.hidden = false;
    // Prefer the approve button so Enter (native) isn't required; y/n still work.
    try { this.els.approve.focus(); } catch { /* headless / no focus */ }
    if (this.busy || this.agentBusy) this._hbTick();
  }

  _answerConfirm(approve) {
    if (this._confirmId == null) return;
    this.wire.send({ type: "confirm", id: this._confirmId, approve });
    this._confirmId = null;
    this.els.confirmbar.hidden = true;
    try { this.els.input.focus(); } catch { /* ignore */ }
    if (this.busy || this.agentBusy) this._hbTick();
  }

  // ----------------------------------------------------------- mode/chrome

  applyModeState(ev) {
    this.posture = ev.posture;
    const overlay = String(ev.overlay || "").trim().toLowerCase();
    this.overlay = overlay;
    this._paintFlip(ev.posture, overlay);
    const mode = (ev.mode || "").trim();
    this.els.modeChip.textContent = mode;
    this.els.modeChip.dataset.color = ev.color || "";
    const exclusive = !QUIET_MODES.has(mode.toLowerCase().split("·")[0].trim());
    this.els.modeChip.classList.toggle("mode-exclusive", exclusive);
    this.els.hint.textContent = ev.hint || "";
    this.els.exitHint.textContent = ev.exit_hint || "";
    this.els.input.placeholder = ev.posture === "chat"
      ? "talk — $ for the lab"
      : (ev.placeholder || "lab");
    this._onInputValue();
  }

  setConn(state) {
    this.els.conn.textContent = state;
    this.els.conn.dataset.state = state;
    if (state !== "open") {
      this.busy = false;
      this.agentBusy = false;
      this._paintBusy();
    }
  }

  // ---------------------------------------------------- slash completions

  _compOpen() {
    return this.els.completions && !this.els.completions.hidden;
  }

  _onInputValue() {
    const value = this.els.input.value;
    // Bare /prefix (no whitespace) → live narrow. Catalog is posture-scoped.
    const m = value.match(/^\/([^\s]*)$/);
    if (!m || !this._catalog.length) {
      this._compHide();
      return;
    }
    const prefix = m[1].toLowerCase();
    const matches = this._catalog.filter((c) => {
      const name = String(c.name || "").toLowerCase();
      return !prefix || name.startsWith(prefix);
    }).slice(0, 40);
    if (!matches.length) {
      this._compHide();
      return;
    }
    this._compMatches = matches;
    this._compIndex = 0;
    this._compRender();
  }

  _compRender() {
    const box = this.els.completions;
    if (!box) return;
    box.textContent = "";
    this._compMatches.forEach((c, i) => {
      const row = document.createElement("div");
      row.className = "comp-item" + (i === this._compIndex ? " hi" : "");
      row.setAttribute("role", "option");
      const name = document.createElement("span");
      name.className = "comp-name";
      name.textContent = `/${c.name}`;
      const desc = document.createElement("span");
      desc.className = "comp-desc";
      desc.textContent = c.description || "";
      row.appendChild(name);
      row.appendChild(desc);
      row.addEventListener("mousedown", (e) => {
        e.preventDefault(); // keep focus in input
        this._compIndex = i;
        this._compAccept();
      });
      box.appendChild(row);
    });
    box.hidden = false;
  }

  _compMove(delta) {
    const n = this._compMatches.length;
    if (!n) return;
    this._compIndex = (this._compIndex + delta + n) % n;
    this._compRender();
    const hi = this.els.completions.querySelector(".comp-item.hi");
    if (hi) hi.scrollIntoView({ block: "nearest" });
  }

  _compAccept() {
    const item = this._compMatches[this._compIndex];
    if (!item) { this._compHide(); return; }
    const text = `/${item.name} `;
    this.els.input.value = text;
    this.els.input.selectionStart = this.els.input.selectionEnd = text.length;
    this._autosize();
    this._compHide();
    this.els.input.focus();
  }

  _compHide() {
    this._compMatches = [];
    this._compIndex = 0;
    if (this.els.completions) {
      this.els.completions.hidden = true;
      this.els.completions.textContent = "";
    }
  }

  // ------------------------------------------------------------- upload

  _upload(file) {
    if (this.busy || this.agentBusy) {
      this._localMeta(`${file.name}: upload after the turn finishes`, "warn");
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      this._localMeta(`${file.name}: over ${MAX_UPLOAD_BYTES / (1024 * 1024)} MiB`, "error");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const b64 = String(reader.result).split(",", 2)[1] || "";
      this.wire.send({ type: "upload", name: file.name, b64 });
    };
    reader.readAsDataURL(file);
  }

  _localMeta(text, level) {
    const el = document.createElement("div");
    el.className = "meta";
    el.dataset.level = level;
    el.textContent = text;
    document.getElementById("transcript").appendChild(el);
  }
}
