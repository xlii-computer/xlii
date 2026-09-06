// Boot: resolve the endpoint (Tauri handshake → ?token= → same-origin /face/ws),
// open the wire, fan events out to the transcript and the input chrome.

import { Wire, resolveEndpoint } from "./wire.js";
import { Transcript } from "./transcript.js?v=fileout1";
import { InputBar } from "./input.js?v=flip2";
import { PaneDeckView } from "./panes.js?v=slot2";

import { MenuBar } from "./menubar.js?v=chrome1";
import "./skins.js?v=flip2"; // face theme skins (html data-skin)
import { applyPaneChrome, installPaneResize } from "./pane_resize.js?v=split1";
import { applyPluginFormStatus } from "./maker_forms.js?v=form1";
import { applyGlass, installGlass } from "./lock.js?v=glass2";
import { installFaceHistoryGuard } from "./history_guard.js";

const transcript = new Transcript(document.getElementById("transcript"));
const peekRoot = document.getElementById("transcript-peek");
const peekTranscript = peekRoot ? new Transcript(peekRoot, { quiet: true }) : null;

const els = {
  shell: document.getElementById("shell"),
  input: document.getElementById("input"),
  send: document.getElementById("send"),
  stop: document.getElementById("stop"),
  flip: document.getElementById("flip"),
  flipName: document.getElementById("flip-name"),
  flipMark: document.getElementById("flip-mark"),
  filepick: document.getElementById("filepick"),
  confirmbar: document.getElementById("confirmbar"),
  confirmPrompt: document.getElementById("confirm-prompt"),
  approve: document.getElementById("confirm-approve"),
  deny: document.getElementById("confirm-deny"),
  modeChip: document.getElementById("mode-chip"),
  hint: document.getElementById("hint"),
  exitHint: document.getElementById("exit-hint"),
  conn: document.getElementById("conn"),
  deck: document.getElementById("panedeck"),
  tabs: document.getElementById("panetabs"),
  panel: document.getElementById("panepanel"),
  title: document.getElementById("pane-title"),
  rows: document.getElementById("pane-rows"),
  actions: document.getElementById("pane-actions"),
  hud: document.getElementById("hud"),
  hudSurface: document.getElementById("hud-surface"),
  hudModel: document.getElementById("hud-model"),
  hudProject: document.getElementById("hud-project"),
  hudCwd: document.getElementById("hud-cwd"),
  hudPersona: document.getElementById("hud-persona"),
  hudJournal: document.getElementById("hud-journal"),
  hudTrust: document.getElementById("hud-trust"),
  hudTier: document.getElementById("hud-tier"),
  hudBrowser: document.getElementById("hud-browser"),
  hudUsage: document.getElementById("hud-usage"),
  hudMeter: document.getElementById("hud-meter"),
  hudSession: document.getElementById("hud-session"),
  heartbeat: document.getElementById("heartbeat"),
  hbSpin: document.getElementById("hb-spin"),
  hbLabel: document.getElementById("hb-label"),
  completions: document.getElementById("completions"),
  queue: document.getElementById("msgqueue"),
  quickstrip: document.getElementById("quickstrip"),
  focuschip: document.getElementById("focuschip"),
  fkeybar: document.getElementById("fkeybar"),
};

// Persona name only — talk/lab lives on the flip, not a third door chip.
let lastPersona = "";
let lastPosture = "chat";

function paintPersonaChip() {
  if (!els.hudPersona) return;
  if (!lastPersona) {
    els.hudPersona.textContent = "";
    els.hudPersona.hidden = true;
    return;
  }
  els.hudPersona.hidden = false;
  els.hudPersona.textContent = lastPersona;
  els.hudPersona.title = "persona — talk vs lab";
}

function applyChrome(ev) {
  els.hud.hidden = false;
  // No Home/Chat/Project door. Flip is the only switch. Folder name only
  // when a real project is bound (not scratch roam).
  if (els.hudSurface) els.hudSurface.hidden = true;
  els.hudModel.textContent = ev.model || "";
  els.hudModel.hidden = !ev.model;
  const surface = ev.surface || "code";
  const isHome =
    surface === "scratch" ||
    !ev.project ||
    String(ev.project).startsWith("scratch/");
  if (els.hudProject) {
    if (isHome) {
      els.hudProject.textContent = "";
      els.hudProject.hidden = true;
      els.hudProject.title = "no folder yet — drop files in a directory to keep them";
    } else {
      els.hudProject.hidden = false;
      els.hudProject.textContent = ev.project || "";
      els.hudProject.title = ev.workbench
        ? `folder · pack: ${ev.workbench}`
        : "folder";
    }
  }
  if (els.hudCwd) {
    const cwd = ev.cwd || "";
    els.hudCwd.textContent = cwd;
    els.hudCwd.hidden = !cwd;
    els.hudCwd.title = cwd ? "live desk — cd/ls/! and /sh share this" : "";
  }
  if (els.hudJournal) {
    const on = ev.journal === "on";
    els.hudJournal.hidden = !on;
    els.hudJournal.textContent = on ? "jrnl" : "";
    els.hudJournal.title = on ? "project journal is recording" : "";
  }
  if (els.hudTrust) {
    const trust = ev.trust === "yolo" || ev.trust === "freeball" ? ev.trust : "safe";
    els.hudTrust.hidden = false;
    els.hudTrust.dataset.trust = trust;
    els.hudTrust.textContent = trust;
    els.hudTrust.title = trust === "safe"
      ? "bash gate · safe — config pane cycles"
      : `bash gate · ${trust} — config pane cycles`;
  }
  if (els.hudTier) {
    const tier = String(ev.chat_tier || "off");
    const on = tier && tier !== "off";
    els.hudTier.hidden = !on;
    els.hudTier.textContent = on ? tier : "";
    els.hudTier.title = on ? `chat tier · ${tier}` : "";
  }
  if (els.hudBrowser) {
    const mode = ev.browser || "";
    const live = mode === "window" || mode === "hidden";
    els.hudBrowser.hidden = !live;
    els.hudBrowser.dataset.mode = mode;
    els.hudBrowser.textContent = live ? "browse" : "";
    const where = ev.browser_url || "";
    els.hudBrowser.title = !live
      ? "agent browser"
      : (mode === "hidden"
        ? `agent browser · hidden${where ? " · " + where : ""} — click to show`
        : `agent browser · window${where ? " · " + where : ""}`);
  }
  if (ev.persona != null && ev.persona !== "") lastPersona = ev.persona;
  if (ev.posture === "chat" || ev.posture === "code") lastPosture = ev.posture;
  paintPersonaChip();
  const meter = ev.meter || "";
  const sess = ev.session || "";
  if (els.hudMeter) {
    els.hudMeter.textContent = meter;
    els.hudMeter.hidden = !meter;
    els.hudMeter.title = meter ? "context window" : "";
  }
  if (els.hudSession) {
    els.hudSession.textContent = sess;
    els.hudSession.hidden = !sess;
    els.hudSession.title = sess ? "this session" : "";
  }
  applyPaneChrome({
    side: ev.pane_side,
    widthPct: ev.pane_width_pct,
  });
  if (els.hudUsage) els.hudUsage.hidden = !meter && !sess;
  if (ev.face_skin && typeof window.__xliiApplyFaceSkin === "function") {
    window.__xliiApplyFaceSkin(ev.face_skin);
  }
  if (ev.face_fkeys != null && typeof window.__xliiSetFkeysVisible === "function") {
    window.__xliiSetFkeysVisible(!!ev.face_fkeys, false);
  }
  if (ev.face_bold != null && typeof window.__xliiSetBoldType === "function") {
    window.__xliiSetBoldType(!!ev.face_bold, false);
  }
  paintStatusRail(ev);
  window.__xliiBinds = Array.isArray(ev && ev.binds) ? ev.binds : [];
  paintFkeyBar();
  if (deck && typeof deck.applySlots === "function") {
    deck.applySlots({
      a: ev.slot_a,
      b: ev.slot_b,
      focus: ev.slot_focus,
      catalog: ev.slot_catalog,
      likely: ev.quick_launch,
      cwd: ev.cwd,
    });
  }
  if (menu && typeof menu.setChrome === "function") menu.setChrome(ev);
  applyViewPosture(ev);
  applyGlass(ev);
}

let phoneDualBooted = false;
let phoneChromeArmed = false;
function phoneVisualViewport() {
  try {
    if (window !== window.top && window.parent && window.parent.visualViewport) {
      return window.parent.visualViewport;
    }
  } catch {
    /* cross-origin frame — use our own */
  }
  return window.visualViewport;
}
function measurePhoneBottomChrome() {
  const thumb = document.getElementById("thumbbar");
  const bar = document.getElementById("inputbar");
  const status = document.getElementById("statusbar");
  let h = 0;
  for (const el of [thumb, bar, status]) {
    if (!el || el.hidden) continue;
    const r = el.getBoundingClientRect();
    if (r.height > 0) h += r.height;
  }
  return Math.max(0, Math.round(h));
}
function fitPhoneViewport() {
  const vv = phoneVisualViewport();
  const inFrame = window !== window.top;
  const root = document.documentElement;
  const composing = root.dataset.compose === "1";
  const h = vv ? vv.height : window.innerHeight;
  const t = inFrame ? 0 : (composing && vv ? Math.max(0, vv.offsetTop) : 0);
  root.style.setProperty("--vvh", `${Math.round(h)}px`);
  root.style.setProperty("--vvt", `${Math.round(t)}px`);
  if (root.dataset.view === "phone") {
    root.style.setProperty("--phone-bottom-chrome", `${measurePhoneBottomChrome()}px`);
  }
}
function setPhoneCompose(on) {
  const root = document.documentElement;
  if (on) root.dataset.compose = "1";
  else delete root.dataset.compose;
  fitPhoneViewport();
  if (window.__xliiBar && typeof window.__xliiBar._autosize === "function") {
    window.__xliiBar._autosize();
  }
}
function requestPhoneFullscreen() {
  const el = document.documentElement;
  if (document.fullscreenElement || document.webkitFullscreenElement) return;
  const req = el.requestFullscreen || el.webkitRequestFullscreen;
  if (!req) return;
  try {
    const p = req.call(el, { navigationUI: "hide" });
    if (p && p.catch) p.catch(() => {});
  } catch {
    /* gesture required — next pointerdown retries */
  }
}
// Chrome treats requestFullscreen as needing a user activation. focus is not
// one; pointerdown is. Skip the menu chrome so a tap on File/Edit still opens
// the dropdown instead of racing a fullscreen relayout. #inputbar is skipped
// because focus/mic already request fullscreen from a real gesture there.
const PHONE_FS_SKIP = "#menubar, .menu-drop, .menu-title, .menu-item, #inputbar";
function phoneFullscreenFromGesture(e) {
  const t = e && e.target;
  if (t && typeof t.closest === "function" && t.closest(PHONE_FS_SKIP)) return;
  requestPhoneFullscreen();
}
function armPhoneChrome() {
  fitPhoneViewport();
  if (phoneChromeArmed) return;
  phoneChromeArmed = true;
  // Public / phone Face: first Android Back stays on /face/ while the
  // sitting is live. Second Back (or menu Exit) is the escape. Does not
  // call requestFullscreen — #472 owns that gesture.
  installFaceHistoryGuard({
    isSittingLive: () => exitPhase === "open",
  });
  const vv = phoneVisualViewport();
  if (vv) {
    vv.addEventListener("resize", fitPhoneViewport);
    vv.addEventListener("scroll", fitPhoneViewport);
  }
  window.addEventListener("resize", fitPhoneViewport);
  // Bubble phase only — do not capture every tap (that stole menu titles).
  document.addEventListener("pointerdown", phoneFullscreenFromGesture);
  const input = document.getElementById("input");
  const bar = document.getElementById("inputbar");
  const onKb = () => {
    requestPhoneFullscreen();
    setPhoneCompose(true);
    [50, 250, 450].forEach((ms) => setTimeout(fitPhoneViewport, ms));
  };
  if (input) input.addEventListener("focus", onKb);
  if (bar) bar.addEventListener("focusin", onKb);
  if (input) {
    input.addEventListener("blur", () => {
      setTimeout(() => {
        const a = document.activeElement;
        if (a && bar && bar.contains(a)) return;
        setPhoneCompose(false);
      }, 80);
    });
  }
  if (window.ResizeObserver) {
    const ro = new ResizeObserver(() => fitPhoneViewport());
    for (const id of ["thumbbar", "inputbar", "statusbar"]) {
      const el = document.getElementById(id);
      if (el) ro.observe(el);
    }
  }
  installPhoneVoice();
}
function installPhoneVoice() {
  const btn = document.getElementById("voice");
  const input = document.getElementById("input");
  if (!btn || !input) return;
  btn.hidden = false;
  const SpeechCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null;
  let listening = false;
  let micWarmed = false;
  let warmPending = null;
  const stop = () => {
    listening = false;
    btn.dataset.listen = "0";
    btn.textContent = "mic";
    try { if (rec) rec.stop(); } catch { /* already stopped */ }
  };
  const note = (msg, level) => {
    if (window.__xliiBar && typeof window.__xliiBar._localMeta === "function") {
      window.__xliiBar._localMeta(msg, level || "warn");
    }
  };
  const noteMicBlocked = () => {
    note("mic blocked — allow microphone in browser settings, then tap mic again", "warn");
  };
  const noteVoiceError = (err) => {
    const e = String(err || "").toLowerCase();
    if (e === "not-allowed" || e === "service-not-allowed") {
      noteMicBlocked();
      return;
    }
    if (e === "aborted" || e === "no-speech") return;
    note(`voice: ${err}`, "warn");
  };
  const ensureRec = () => {
    if (!SpeechCtor) return null;
    if (!rec) {
      rec = new SpeechCtor();
      rec.lang = navigator.language || "en-US";
      rec.interimResults = true;
      rec.continuous = false;
      rec.onresult = (ev) => {
        let said = "";
        for (let i = ev.resultIndex; i < ev.results.length; i++) {
          if (ev.results[i].isFinal) said += ev.results[i][0].transcript;
        }
        said = said.trim();
        if (!said) return;
        const cur = input.value;
        input.value = cur && !/\s$/.test(cur) ? `${cur} ${said}` : `${cur}${said}`;
        input.focus();
        if (window.__xliiBar) window.__xliiBar._autosize();
      };
      rec.onerror = (ev) => {
        listening = false;
        btn.dataset.listen = "0";
        btn.textContent = "mic";
        noteVoiceError((ev && ev.error) || "failed");
      };
      rec.onend = () => {
        listening = false;
        btn.dataset.listen = "0";
        btn.textContent = "mic";
      };
    }
    return rec;
  };
  const warmMicOnce = () => {
    if (micWarmed) return Promise.resolve();
    if (warmPending) return warmPending;
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
      micWarmed = true;
      return Promise.resolve();
    }
    warmPending = navigator.mediaDevices.getUserMedia({ audio: true })
      .then((stream) => {
        stream.getTracks().forEach((track) => track.stop());
        micWarmed = true;
      })
      .catch((e) => {
        const name = String((e && e.name) || "");
        if (name === "NotAllowedError" || name === "PermissionDeniedError") noteMicBlocked();
        micWarmed = true;
      })
      .finally(() => { warmPending = null; });
    return warmPending;
  };
  const startListening = async () => {
    if (!window.isSecureContext) {
      note("voice needs https — open this glass over https (or tailscale serve)", "warn");
      return;
    }
    if (!SpeechCtor) {
      note("this browser has no speech recognition — type instead", "warn");
      return;
    }
    const r = ensureRec();
    if (!r) return;
    try {
      await warmMicOnce();
      r.start();
      listening = true;
      btn.dataset.listen = "1";
      btn.textContent = "stop";
    } catch (e) {
      listening = false;
      btn.dataset.listen = "0";
      btn.textContent = "mic";
      const name = String((e && e.name) || "");
      const msg = String((e && e.message) || e || "");
      if (name === "InvalidStateError") {
        try { r.stop(); } catch { /* mid-flight */ }
        try {
          await warmMicOnce();
          r.start();
          listening = true;
          btn.dataset.listen = "1";
          btn.textContent = "stop";
          return;
        } catch (e2) {
          const msg2 = String((e2 && e2.message) || e2 || "");
          if (/not-allowed|permission/i.test(msg2)) noteMicBlocked();
          else note(`voice could not start — ${msg2 || "tap mic again"}`, "warn");
          return;
        }
      }
      if (/not-allowed|permission/i.test(name) || /not-allowed|permission/i.test(msg)) {
        noteMicBlocked();
      } else {
        note(`voice could not start — ${msg || "tap mic again"}`, "warn");
      }
    }
  };
  btn.addEventListener("mousedown", (e) => e.preventDefault());
  btn.addEventListener("click", () => {
    requestPhoneFullscreen();
    if (listening) { stop(); return; }
    startListening();
  });
}
function applyViewPosture(ev) {
  const v = String((ev && ev.view_posture) || "");
  if (v === "phone") {
    document.documentElement.dataset.view = "phone";
    const barEl = document.getElementById("thumbbar");
    if (barEl) barEl.hidden = false;
    const lockEl = document.getElementById("thumb-lock");
    if (lockEl) {
      const tailnetGlass = !!(ev && ev.tailnet_glass);
      lockEl.hidden = !tailnetGlass;
      lockEl.disabled = !tailnetGlass;
    }
    armPhoneChrome();
    if (!phoneDualBooted) {
      phoneDualBooted = true;
      if (!(ev && ev.slot_b) && wire) {
        wire.send({ type: "open_pane", pane: "bookmarks" });
      }
    }
  } else if (document.documentElement.dataset.view === "phone" && ev && ev.type === "hello") {
    delete document.documentElement.dataset.view;
  }
}

function installThumbBar() {
  const mark = document.getElementById("thumb-mark");
  const panes = document.getElementById("thumb-panes");
  const lock = document.getElementById("thumb-lock");
  if (mark) {
    mark.addEventListener("click", () => wire.send({ type: "mark_last" }));
  }
  if (panes) {
    panes.addEventListener("click", () => {
      const b = deck && deck.slots && deck.slots.b;
      if (b && b !== "stream" && b !== "") {
        wire.send({ type: "set_slot", slot: "b", view: "" });
        return;
      }
      wire.send({ type: "open_pane", pane: "bookmarks" });
    });
  }
  if (lock) {
    lock.addEventListener("click", () => wire.send({ type: "remote_lock" }));
  }
}

window.__xliiBinds = window.__xliiBinds || [];
const FKEY_PREF = "xlii.face.fkeys";

function fkeysVisible() {
  try {
    const v = localStorage.getItem(FKEY_PREF);
    if (v === "0") return false;
    if (v === "1") return true;
  } catch { /* private mode */ }
  return true;
}

function setFkeysVisible(on, persist) {
  try { localStorage.setItem(FKEY_PREF, on ? "1" : "0"); } catch { /* ignore */ }
  paintFkeyBar();
  if (persist !== false && window.__xliiWire && typeof window.__xliiWire.send === "function") {
    window.__xliiWire.send({ type: "set_face_fkeys", visible: !!on });
  }
}

const BOLD_PREF = "xlii.face.bold";

function boldTypeOn() {
  try {
    const v = localStorage.getItem(BOLD_PREF);
    if (v === "1") return true;
    if (v === "0") return false;
  } catch { /* private mode */ }
  return false;
}

function applyBoldType() {
  document.documentElement.setAttribute("data-bold", boldTypeOn() ? "1" : "0");
}

function setBoldType(on, persist) {
  try { localStorage.setItem(BOLD_PREF, on ? "1" : "0"); } catch { /* ignore */ }
  applyBoldType();
  if (persist !== false && window.__xliiWire && typeof window.__xliiWire.send === "function") {
    window.__xliiWire.send({ type: "set_face_bold", on: !!on });
  }
}

applyBoldType();

// V2b commander verbs — same row as the TUI. Binds overlay labels / add F11–F12.
const COMMANDER_FKEYS = [
  { key: "F1", label: "help" },
  { key: "F2", label: "Home Hub" },
  { key: "F3", label: "view" },
  { key: "F4", label: "edit" },
  { key: "F5", label: "copy" },
  { key: "F6", label: "detach" },
  { key: "F7", label: "add" },
  { key: "F8", label: "rem" },
  { key: "F9", label: "jobs" },
  { key: "F10", label: "tasks" },
];

function fkeyRows() {
  const binds = Array.isArray(window.__xliiBinds) ? window.__xliiBinds : [];
  const byKey = {};
  for (const b of binds) {
    const fk = String(b.fkey || "").toLowerCase();
    if (fk) byKey[fk] = String(b.label || b.task || fk);
  }
  const rows = COMMANDER_FKEYS.map((row) => {
    const overlay = byKey[row.key.toLowerCase()];
    return overlay ? { key: row.key, label: overlay } : row;
  });
  for (const b of binds) {
    const fk = String(b.fkey || "").toLowerCase();
    const n = Number(fk.slice(1));
    if (n >= 11 && n <= 12) {
      rows.push({ key: fk.toUpperCase(), label: String(b.label || b.task || fk) });
    }
  }
  return rows;
}

function paintFkeyBar() {
  const bar = els.fkeybar;
  if (!bar) return;
  bar.textContent = "";
  if (!fkeysVisible()) {
    bar.hidden = true;
    return;
  }
  bar.hidden = false;
  fkeyRows().forEach((row, i) => {
    if (i) {
      const sep = document.createElement("span");
      sep.className = "fkey-sep";
      sep.textContent = "│";
      bar.appendChild(sep);
    }
    const b = document.createElement("button");
    b.type = "button";
    b.className = "fkey";
    b.dataset.key = row.key.toLowerCase();
    const kbd = document.createElement("kbd");
    kbd.textContent = row.key;
    b.appendChild(kbd);
    b.appendChild(document.createTextNode(` ${row.label}`));
    b.title = row.label;
    b.addEventListener("click", () => handleFkey(row.key.toLowerCase()));
    bar.appendChild(b);
  });
}

function paintStatusRail(ev) {
  const strip = els.quickstrip;
  if (!strip) return;
  strip.textContent = "";
  strip.setAttribute("aria-label", "session flags");
  const pills = Array.isArray(ev && ev.jobs) ? ev.jobs : [];
  const unseen = Number(ev && ev.jobs_unseen) || 0;
  const active = Number(ev && ev.jobs_active) || 0;
  // One pill per job (running + finished-unseen). Journal was the only
  // thing that lingered as a count chip; task jobs vanished when they
  // finished. Click opens the jobs board and marks that pill seen.
  if (!pills.length && unseen <= 0 && active <= 0) {
    strip.hidden = true;
    return;
  }
  strip.hidden = false;
  for (const it of pills) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "ql-btn flag" + (it.unseen ? " job-done" : " job-live");
    b.textContent = it.label || it.id || "job";
    b.title = it.unseen ? "open · mark seen" : "open jobs";
    b.addEventListener("click", () => {
      wire.send({ type: "jobs_open", id: String(it.id || "") });
    });
    strip.appendChild(b);
  }
  if (unseen > 0 || pills.some((p) => p.unseen)) {
    const clr = document.createElement("button");
    clr.type = "button";
    clr.className = "ql-btn flag job-clear";
    clr.textContent = "clear done";
    clr.title = "drop finished jobs and pills";
    clr.addEventListener("click", () => wire.send({ type: "jobs_clear" }));
    strip.appendChild(clr);
  }
}

function runBindLine(line) {
  const raw = String(line || "");
  if (!raw.trim()) return false;
  if (raw.endsWith(" ")) {
    if (bar && typeof bar.seed === "function") bar.seed(raw);
    return true;
  }
  if (bar && bar.posture !== "code") {
    wire.send({ type: "set_posture", posture: "code" });
  }
  wire.send({ type: "input", text: raw.trim() });
  return true;
}

function handleFkey(key) {
  const k = String(key || "").toLowerCase();
  const binds = Array.isArray(window.__xliiBinds) ? window.__xliiBinds : [];
  const hit = binds.find((b) => String(b.fkey || "").toLowerCase() === k);
  if (hit && runBindLine(hit.line || `/tasks run ${hit.task} `)) return;
  if (k === "f1") {
    runQuickAction("cmd:help");
    return;
  }
  if (k === "f2") {
    runQuickAction("pane:home");
    return;
  }
  if (k === "f3") {
    if (deck && deck.openPane) {
      wire.send({ type: "pane_action", pane: deck.openPane, op: "action", name: "view" });
    }
    return;
  }
  if (k === "f4") {
    seedEdit();
    return;
  }
  if (k === "f5") {
    const sel = transcript.getSelection && transcript.getSelection();
    const text = (sel && (sel.text || sel.address)) || "";
    if (text && navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(String(text)).catch(() => {});
    }
    return;
  }
  if (k === "f6") {
    clearFocus();
    return;
  }
  if (k === "f9") {
    runQuickAction("pane:jobs");
    return;
  }
  if (k === "f10") {
    runQuickAction("pane:tasks");
  }
}

function editArg(address) {
  const raw = String(address || "");
  const i = raw.indexOf("://");
  if (i < 0) return "";
  const scheme = raw.slice(0, i);
  const target = raw.slice(i + 3);
  if (scheme === "file") return `--file ${target}`;
  if (scheme === "docs") return `--doc ${target.split("/")[0]}`;
  if (scheme === "persona") return `--id ${target.split("/")[0]}`;
  if (scheme === "skills" && target) return "";
  return "";
}

function seedEdit() {
  const feed = transcript.getSelection && transcript.getSelection();
  const addr = (feed && feed.address) || (deck && deck.lastAddress) || "";
  const arg = editArg(addr);
  if (bar && typeof bar.seed === "function") {
    bar.seed(arg ? `/edit ${arg}` : "/edit ");
  }
}

function focusAddress(address) {
  if (!address) return;
  wire.send({ type: "focus", address });
}

function clearFocus() {
  wire.send({ type: "focus", clear: true });
}

function paintFocusChip(items) {
  const chip = els.focuschip;
  if (!chip) return;
  chip.textContent = "";
  const list = Array.isArray(items) ? items : [];
  transcript.markFocused(list.map((i) => i.address));
  if (!list.length) {
    chip.hidden = true;
    return;
  }
  chip.hidden = false;
  for (const it of list) {
    const pill = document.createElement("span");
    pill.className = "focus-pill";
    const addr = String(it.address || "");
    const label = addr.startsWith("canvas://") ? "canvas" : "focus";
    pill.textContent = `${label}: ${it.title || it.address || "item"}`;
    const x = document.createElement("button");
    x.type = "button";
    x.textContent = "✕";
    x.title = "clear focus";
    x.addEventListener("click", () => clearFocus());
    pill.appendChild(x);
    chip.appendChild(pill);
  }
}

function runQuickAction(action) {
  if (!action) return;
  if (action.startsWith("pane:")) {
    deck.togglePane(action.slice(5));
    return;
  }
  if (action.startsWith("seed:")) {
    bar.seed(action.slice(5));
    return;
  }
  if (action === "attach:files") {
    els.filepick.click();
    return;
  }
  if (action === "browser:open") {
    // Research pack tool door — agent Chromium (CDP) via face server, not a mode.
    wire.send({ type: "open_browser" });
    return;
  }
  if (action === "research:kg" || action === "research:canvas") {
    // KG / canvas doors — open interim panes + teach (three-faces R1).
    wire.send({ type: "research_tool", tool: action.slice("research:".length) });
    return;
  }
  if (action === "menu:plugins" || action === "pane:plugins") {
    // Plugins live under Tools (plugins://), not a top-level menubar dropdown.
    if (menu && typeof menu.openPlugins === "function") {
      menu.openPlugins();
    } else {
      deck.togglePane("plugins");
    }
    return;
  }
  if (action === "cmd:help") {
    if (bar && bar.posture === "code") {
      bar.seed("/help");
    } else if (deck) {
      deck.togglePane("menu");
    }
  }
}

let bar; // assigned below; the wire handlers close over it
let deck; // the pane strip (B1)
let menu; // MenuBar — plugins catalog
let lastDetail = "";
/** open → exiting (sent /exit) → ended (session_end received). */
let exitPhase = "open";


/** Close the Tauri host after a clean session_end (typed /exit · menu Exit). */
function closeHostWindow() {
  try {
    const t = window.__TAURI__;
    const winApi = t && t.window;
    const core = t && t.core;
    const doClose = () => {
      if (winApi && typeof winApi.getCurrentWindow === "function") {
        winApi.getCurrentWindow().close();
        return true;
      }
      return false;
    };
    // Arm Rust `closing` first — otherwise CloseRequested re-enters
    // __xliiRequestExit and the window sticks on the bye line.
    if (core && typeof core.invoke === "function") {
      Promise.resolve(core.invoke("prepare_close"))
        .catch(() => {})
        .then(() => { doClose(); });
      return true;
    }
    return doClose();
  } catch { /* browser / no host */ }
  return false;
}

/** Ask the server to perform the same graceful exit as the menu's Exit item. */
function requestGracefulExit() {
  if (exitPhase === "ended") return closeHostWindow();
  if (exitPhase === "exiting") return true;
  exitPhase = "exiting";
  try {
    if (wire && wire.send({ type: "input", text: "/exit" })) return true;
  } catch { /* fall through to direct close below */ }
  return closeHostWindow();
}

const wire = new Wire(resolveEndpoint, {
  onEvent(ev) {
    switch (ev.type) {
      case "hello":
        if (menu && ev.version) menu.setVersion(ev.version);
        applyViewPosture(ev);
        break;
      case "mode_state":
        bar.applyModeState(ev);
        // Flip button updates here — keep persona pill prefix in lockstep.
        if (ev.posture === "chat" || ev.posture === "code") {
          lastPosture = ev.posture;
          paintPersonaChip();
        }
        break;
      case "chrome_state":
        applyChrome(ev);
        break;
      case "door": {
        // Mojo-keeper K6: pane:* opens here; land:* is server-owned (queued
        // while _agent_running, flushed after the turn — do not echo
        // join_project/go_home or Face hits the busy gate mid-oneshot).
        const action = String(ev.action || "");
        if (action.startsWith("pane:")) {
          const pane = action.slice(5).trim();
          if (pane) wire.send({ type: "open_pane", pane });
        }
        break;
      }
      case "stream_sync":
        transcript.sync(ev.turns || []);
        break;
      case "clear_transcript":
        transcript.clear();
        break;
      case "stream_peek":
        if (peekTranscript) peekTranscript.sync(ev.turns || []);
        if (deck && typeof deck._syncRoot === "function") deck._syncRoot();
        break;
      case "confirm_request":
        bar.showConfirm(ev);
        break;
      case "session_end":
        // Server finished save/sync — drop the native window. Browser face
        // just shows the bye line and sits (no host to close).
        exitPhase = "ended";
        if (ev.message) {
          transcript.handle({
            type: "meta_message",
            text: String(ev.message),
            level: "info",
          });
        }
        bar.setBusy(false);
        bar.agentBusy = false;
        bar._paintBusy();
        bar.setConn("closed");
        closeHostWindow();
        break;
      case "turn_done":
        bar.setBusy(false);
        // Agent may still be running — busy_state owns agentBusy.
        if (!bar.agentBusy) bar._paintBusy();
        if (typeof bar.drainQueue === "function") bar.drainQueue();
        transcript.handle(ev);
        break;
      case "busy_state":
        bar.applyBusyState(ev);
        break;
      case "error":
        // Failed submit (busy / agent gate) — don't leave the bar hard-busy.
        // Do NOT close the host here: /exit is handled immediately server-side
        // and never returns these busy errors; an unrelated in-flight refusal
        // while exitPhase==="exiting" would kill the window mid-save.
        if (ev.message && /busy|agent working/i.test(ev.message)) {
          bar.setBusy(false);
        }
        transcript.handle(ev);
        break;
      case "pong":
        break;
      case "prefill":
        els.input.value = ev.text;
        els.input.focus();
        break;
      case "claim_line": {
        const submit = String(ev.submit || "input");
        bar.claimLine({
          label: ev.prompt || "…",
          initial: ev.initial || "",
          onSubmit: (v) => {
            if (submit === "set_terminal_cwd_path") {
              wire.send({ type: "set_terminal_cwd_path", path: v });
              return;
            }
            wire.send({ type: "input", text: v });
          },
        });
        break;
      }
      case "pane_deck":
        deck.update(ev);
        break;
      case "pane_focus":
        // Always apply — empty string closes; missing pane waits for next deck.
        deck.focusPane(ev.pane != null ? String(ev.pane) : "");
        break;
      case "slot_state":
        deck.applySlots({
          a: ev.a, b: ev.b, focus: ev.focus,
          catalog: ev.catalog, likely: ev.likely,
        });
        break;
      case "command_catalog":
        bar.setCommandCatalog(ev);
        break;
      case "plugin_catalog":
        menu.setPluginCatalog(ev);
        break;
      case "plugin_form_status":
        applyPluginFormStatus(ev);
        break;
      case "workbench_catalog":
        menu.setWorkbenchCatalog(ev);
        break;
      case "console_catalog":
        if (menu && typeof menu.setConsoleCatalog === "function") {
          menu.setConsoleCatalog(ev);
        }
        break;
      case "pane_catalog":
        menu.setPaneCatalog(ev);
        break;
      case "feed_view":
        // File/task/skill/image "View" → main transcript (not only the side dock).
        transcript.handle(ev);
        break;
      case "focus_state":
        paintFocusChip(ev.items || []);
        break;
      case "clear_input_history":
        if (bar && typeof bar.clearTypedHistory === "function") {
          bar.clearTypedHistory();
        }
        break;
      default:
        transcript.handle(ev);
    }
  },
  onState(state, detail) {
    bar.setConn(state);
    if (state === "open") {
      els.input.placeholder = "…";
      els.input.focus();
      lastDetail = "";
    }
    if (state === "session_ended") {
      bar.setConn("closed");
      const el = document.createElement("div");
      el.className = "meta";
      el.dataset.level = "warn";
      const msg = detail || "session expired — pair again";
      el.innerHTML = `${msg} — <a href="/">pair again</a>`;
      document.getElementById("transcript").appendChild(el);
      return;
    }
    // Dedupe: the reconnect loop re-reports the same reason every retry
    // (e.g. "sidecar starting" while the backend boots) — say it once.
    if (detail && detail !== lastDetail) {
      lastDetail = detail;
      const el = document.createElement("div");
      el.className = "meta";
      el.dataset.level = "warn";
      el.textContent = detail;
      document.getElementById("transcript").appendChild(el);
    }
  },
});

if (els.hudBrowser) {
  els.hudBrowser.addEventListener("click", () => {
    // Hidden session → show the window. Live window → same door (reuse).
    wire.send({ type: "open_browser" });
  });
}
function openConfigPane() {
  wire.send({ type: "open_pane", pane: "config" });
}
if (els.hudTrust) els.hudTrust.addEventListener("click", openConfigPane);
if (els.hudTier) els.hudTier.addEventListener("click", openConfigPane);

window.__xliiRequestExit = requestGracefulExit;
window.__xliiFocus = focusAddress;
window.__xliiClearFocus = clearFocus;
window.__xliiFkeysVisible = fkeysVisible;
window.__xliiSetFkeysVisible = setFkeysVisible;
window.__xliiBoldType = boldTypeOn;
window.__xliiSetBoldType = setBoldType;

bar = new InputBar(wire, els);
deck = new PaneDeckView(wire, els);
// So /panel off can close the dock from InputBar without a circular import.
window.__xliiDeck = deck;
window.__xliiBar = bar;
window.__xliiWire = wire;
installPaneResize(document.getElementById("workspace"), document.getElementById("pane-resize"));
menu = new MenuBar({
  el: document.getElementById("menubar"),
  wire,
  bar,
  deck,
  transcript,
  filepick: els.filepick,
});
installGlass(wire);
installThumbBar();
if (document.documentElement.dataset.view === "phone") armPhoneChrome();
wire.start();
paintFkeyBar();
applyBoldType();

document.addEventListener("keydown", (e) => {
  const m = /^F(\d{1,2})$/.exec(e.key);
  if (!m) return;
  const n = Number(m[1]);
  if (n < 1 || n > 12) return;
  e.preventDefault();
  handleFkey(`f${n}`);
});

// Keepalive: a quiet loopback socket has nothing to time out, but the public
// proxy path rides real infrastructure — a light ping keeps it warm.
setInterval(() => wire.send({ type: "ping" }), 30000);
