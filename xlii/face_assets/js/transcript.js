// Transcript: typed events → DOM. One function per event kind; state is one
// streaming buffer + the newest unfinished tool chip. Dry strings only.

import { renderMarkdown } from "./markdown.js";

function _looksMarkdown(text) {
  return /\*\*|^\s*#{1,3}\s|^\s*[-*]\s/m.test(text || "");
}

export class Transcript {
  constructor(root, opts) {
    this.root = root;
    this._stream = null;        // live assistant_chunk buffer element
    this._openTools = new Map(); // name → <details> awaiting tool_finished
    this._selection = null;     // {kind, address, title}
    this._onSelect = null;
    this._focused = new Set();  // addresses currently pinned for next turn
    this._unseen = 0;
    this._quiet = !!(opts && opts.quiet);
    this._jump = this._quiet ? null : document.getElementById("jump-latest");
    if (this._jump) {
      this._jump.addEventListener("click", () => this.jumpToLatest());
    }
    this.root.addEventListener("scroll", () => this._syncJump(), { passive: true });
    if (!this._quiet) {
      document.addEventListener("keydown", (e) => {
        if (e.key !== "End") return;
        const t = e.target;
        if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.isContentEditable)) return;
        if (t && t.closest && t.closest(".pane-rows, #pane-rows, #panepanel")) return;
        e.preventDefault();
        this.jumpToLatest();
      });
    }
  }

  clear() {
    this.root.textContent = "";
    this._stream = null;
    this._openTools.clear();
    this._unseen = 0;
    this._syncJump();
    this._selection = null;
    if (this._onSelect) this._onSelect(null);
  }

  /** Replace the feed with this project's tape (stream === project history). */
  sync(turns) {
    this.clear();
    const rows = Array.isArray(turns) ? turns : [];
    for (const t of rows) {
      const role = t && t.role;
      const text = (t && t.text) || "";
      if (role === "user") this.handle({ type: "user_turn", text });
      else if (role === "assistant") this.handle({ type: "assistant_answer", markdown: text });
    }
    this.jumpToLatest();
  }

  getSelection() {
    return this._selection;
  }

  setOnSelect(fn) {
    this._onSelect = fn;
  }

  markFocused(addresses) {
    this._focused = new Set((addresses || []).filter(Boolean));
    this.root.querySelectorAll(".feed-view, .fileout").forEach((art) => {
      art.classList.toggle("focused", this._focused.has(art.dataset.address || ""));
    });
  }

  _atBottom() {
    return this.root.scrollTop + this.root.clientHeight >= this.root.scrollHeight - 40;
  }

  jumpToLatest() {
    this.root.scrollTop = this.root.scrollHeight;
    this._unseen = 0;
    this._syncJump();
  }

  _syncJump() {
    const btn = this._jump;
    if (!btn) return;
    const host = this.root.parentElement;
    if (host && btn.parentElement !== host && host.id !== "stream-park") {
      host.appendChild(btn);
    }
    if (this._atBottom()) this._unseen = 0;
    const away = !this._atBottom() && !this.root.hidden;
    btn.hidden = !away;
    btn.textContent = this._unseen > 0 ? "↓ catch up" : "↓ latest";
  }

  _append(el) {
    const stick = this._atBottom();
    this.root.appendChild(el);
    if (stick) this.root.scrollTop = this.root.scrollHeight;
    else this._unseen += 1;
    this._syncJump();
  }

  handle(ev) {
    const fn = this[`_on_${ev.type}`];
    if (fn) fn.call(this, ev);
  }

  // ------------------------------------------------------------ streaming

  _ensureStream() {
    if (!this._stream) {
      this._stream = document.createElement("div");
      this._stream.className = "stream";
      this._append(this._stream);
    }
    return this._stream;
  }

  _settleStream(asInterim) {
    if (!this._stream) return;
    if (asInterim && this._stream.textContent.trim()) {
      this._stream.className = "meta interim";
    } else {
      this._stream.remove();
    }
    this._stream = null;
  }

  _on_assistant_chunk(ev) {
    this._ensureStream().textContent += ev.text;
    if (this._atBottom()) this.root.scrollTop = this.root.scrollHeight;
    else this._unseen += 1;
    this._syncJump();
  }

  // --------------------------------------------------------------- turns

  _on_user_turn(ev) {
    this._settleStream(false);
    const el = document.createElement("div");
    el.className = "turn-user";
    const kind = ev.kind || "";
    if (kind) el.dataset.kind = kind;
    el.textContent = ev.text;
    this._append(el);
  }

  _on_assistant_answer(ev) {
    this._settleStream(false);
    const el = document.createElement("div");
    el.className = "answer";
    el.dataset.mode = ev.mode || "";
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.textContent = [ev.mode, ev.role].filter(Boolean).join(" · ");
    if (chip.textContent) el.appendChild(chip);
    el.appendChild(renderMarkdown(ev.markdown));
    this._append(el);
  }

  _on_turn_done(_ev) {
    this._settleStream(false);
    this._openTools.clear();
  }

  // --------------------------------------------------------------- tools

  _on_tool_started(ev) {
    // Narration streamed before a tool call is interim thinking, not the
    // final answer — settle it dim so the buffer restarts clean after.
    this._settleStream(true);
    const d = document.createElement("details");
    d.className = "tool";
    const s = document.createElement("summary");
    s.textContent = `→ ${ev.name} ${ev.args_preview || ""}`.trimEnd();
    d.appendChild(s);
    this._append(d);
    this._openTools.set(ev.name, d);
  }

  _on_tool_finished(ev) {
    let d = this._openTools.get(ev.name);
    this._openTools.delete(ev.name);
    if (!d) {
      d = document.createElement("details");
      d.className = "tool";
      const s = document.createElement("summary");
      d.appendChild(s);
      this._append(d);
    }
    const s = d.querySelector("summary");
    const secs = ev.duration_s != null ? ` · ${ev.duration_s.toFixed(1)}s` : "";
    s.textContent = `${ev.is_error ? "✗" : "✓"} ${ev.name} ${ev.args_preview || ""}${secs}`;
    if (ev.is_error) d.dataset.error = "1";
    if (ev.content) {
      const pre = document.createElement("pre");
      pre.textContent = ev.content;
      d.appendChild(pre);
    }
  }

  _on_shell_ran(ev) {
    this._settleStream(true);
    const d = document.createElement("details");
    d.className = "shell";
    const s = document.createElement("summary");
    const rcBad = ev.returncode !== 0;
    s.innerHTML = "";
    s.textContent = `$ ${ev.command}`;
    if (rcBad) {
      const rc = document.createElement("span");
      rc.className = "rc-bad";
      rc.textContent = ` · rc ${ev.returncode}`;
      s.appendChild(rc);
    }
    d.appendChild(s);
    const body = [ev.stdout, ev.stderr].filter(Boolean).join("\n");
    if (body) {
      const pre = document.createElement("pre");
      pre.textContent = body;
      d.appendChild(pre);
    }
    // YOUR commands arrive open — you asked for that output; only the
    // agent's own bash rides folded (expand on demand, like tool chips).
    d.open = ev.source !== "agent_bash" && Boolean(body);
    this._append(d);
  }

  // ---------------------------------------------------------------- misc

  _on_meta_message(ev) {
    const el = document.createElement("div");
    el.className = "meta";
    el.dataset.level = ev.level || "info";
    const text = ev.text || "";
    if (_looksMarkdown(text)) {
      el.classList.add("prose");
      el.appendChild(renderMarkdown(text));
    } else {
      el.textContent = text;
    }
    this._append(el);
  }

  _on_error(ev) {
    this._on_meta_message({ text: ev.message, level: "error" });
  }

  _on_file_out(ev) {
    const el = document.createElement("article");
    el.className = "fileout";
    const address = ev.address || (ev.path ? `file://${ev.path}` : "");
    el.dataset.address = address;
    el.dataset.kind = ev.kind || "file";
    el.tabIndex = 0;
    if (this._focused.has(address)) el.classList.add("focused");

    if (ev.kind === "image" && ev.b64) {
      const img = document.createElement("img");
      const ext = (ev.name.split(".").pop() || "png").toLowerCase();
      const mime = ext === "jpg" ? "jpeg" : ext;
      img.src = `data:image/${mime};base64,${ev.b64}`;
      img.alt = ev.name;
      el.appendChild(img);
    }

    const name = document.createElement("div");
    name.className = "name";
    name.textContent = ev.name || "file";
    el.appendChild(name);

    const loc = this._storeLabel(ev.path || "");
    if (loc || ev.path) {
      const pathEl = document.createElement("div");
      pathEl.className = "fileout-path";
      pathEl.textContent = loc ? `saved · ${loc}` : (ev.path || "");
      pathEl.title = ev.path || loc;
      el.appendChild(pathEl);
    }

    const tools = document.createElement("div");
    tools.className = "fileout-tools";
    const titleText = ev.name || address || "file";

    const focusBtn = document.createElement("button");
    focusBtn.type = "button";
    focusBtn.textContent = "Focus";
    focusBtn.title = "Pin this for the next turn (edit / talk about this one)";
    focusBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      this._selectCard(el, titleText);
      if (address && typeof window.__xliiFocus === "function") {
        window.__xliiFocus(address);
      }
    });
    tools.appendChild(focusBtn);

    if ((ev.kind || "") === "image") {
      const canvasBtn = document.createElement("button");
      canvasBtn.type = "button";
      canvasBtn.textContent = "On canvas";
      canvasBtn.title = "Put this one on the pad";
      canvasBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        this._selectCard(el, titleText);
        const wire = window.__xliiWire;
        if (wire && typeof wire.send === "function") {
          wire.send({ type: "open_pane", pane: "canvas", select: ev.name || ev.path || "" });
        }
      });
      tools.appendChild(canvasBtn);

      const saveBtn = document.createElement("button");
      saveBtn.type = "button";
      saveBtn.textContent = "Save into project…";
      saveBtn.title = "Copy out of .xlii/artifacts into a folder you pick";
      saveBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        this._selectCard(el, titleText);
        const bar = window.__xliiBar;
        const destName = ev.name || "image.png";
        const line = `/imagine --from ${destName} --save assets/${destName}`;
        if (bar && typeof bar.seed === "function") bar.seed(line);
      });
      tools.appendChild(saveBtn);
    }
    el.appendChild(tools);

    el.addEventListener("click", () => this._selectCard(el, titleText));
    this._append(el);
  }

  _storeLabel(path) {
    const text = String(path || "");
    const marker = ".xlii/artifacts/";
    const i = text.indexOf(marker);
    return i >= 0 ? text.slice(i) : "";
  }

  /**
   * Pane "View" (file / task plan / skill / doc / image) → main feed card.
   * Click selects; Focus / F4 pins it as next-turn context (not a rewind).
   */
  _on_feed_view(ev) {
    const art = document.createElement("article");
    art.className = "feed-view open";
    art.dataset.kind = ev.kind || "file";
    art.dataset.address = ev.address || "";
    art.dataset.feedView = "1";
    art.tabIndex = 0;
    if (this._focused.has(art.dataset.address)) art.classList.add("focused");

    const titleText = ev.title || ev.address || "view";
    const copyText = (ev.text || ev.address || titleText || "").trim();
    art.dataset.copyText = copyText;

    const makeBar = (where) => {
      const row = document.createElement("div");
      row.className = `feed-view-bar ${where}`;

      const collapse = document.createElement("button");
      collapse.type = "button";
      collapse.className = "feed-view-collapse";
      collapse.textContent = "▾ collapse";
      collapse.title = "Collapse / expand this view";
      collapse.addEventListener("click", (e) => {
        e.stopPropagation();
        const open = art.classList.toggle("open");
        art.classList.toggle("collapsed", !open);
        const label = open ? "▾ collapse" : "▸ expand";
        art.querySelectorAll(".feed-view-collapse").forEach((btn) => {
          btn.textContent = label;
        });
      });

      const tools = document.createElement("span");
      tools.className = "feed-view-tools";

      const focusBtn = document.createElement("button");
      focusBtn.type = "button";
      focusBtn.className = "feed-view-focus";
      focusBtn.textContent = "Focus";
      focusBtn.title = "Pin this for the next turn (F4) — not a rewind";
      focusBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        this._selectCard(art, titleText);
        if (typeof window.__xliiFocus === "function") {
          window.__xliiFocus(art.dataset.address);
        }
      });

      const copyBtn = document.createElement("button");
      copyBtn.type = "button";
      copyBtn.className = "feed-view-copy";
      copyBtn.textContent = "Copy";
      copyBtn.title = "Copy this view to the clipboard";
      copyBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        this._copyView(art, copyBtn);
      });

      tools.appendChild(focusBtn);
      tools.appendChild(copyBtn);
      row.appendChild(collapse);
      row.appendChild(tools);
      return row;
    };

    art.appendChild(makeBar("top"));

    const head = document.createElement("header");
    head.className = "feed-view-head";
    const kind = document.createElement("span");
    kind.className = "feed-view-kind";
    kind.textContent = ev.kind || "file";
    const title = document.createElement("span");
    title.className = "feed-view-title";
    title.textContent = titleText;
    head.appendChild(kind);
    head.appendChild(title);
    if (ev.address) {
      const addr = document.createElement("span");
      addr.className = "feed-view-addr";
      addr.textContent = ev.address;
      head.appendChild(addr);
    }
    art.appendChild(head);

    const isImage = (ev.kind || "") === "image" && ev.b64;
    if (isImage) {
      const img = document.createElement("img");
      img.className = "feed-view-img";
      const ext = (String(ev.title || "").split(".").pop() || "png").toLowerCase();
      const mime = ext === "jpg" ? "jpeg" : ext;
      img.src = `data:image/${mime};base64,${ev.b64}`;
      img.alt = ev.title || "image";
      art.appendChild(img);
    } else if (_looksMarkdown(ev.text || "") || ev.kind === "plugin") {
      const box = document.createElement("div");
      box.className = "feed-view-body prose";
      box.appendChild(renderMarkdown(ev.text || ""));
      art.appendChild(box);
    } else {
      const pre = document.createElement("pre");
      pre.className = "feed-view-body";
      pre.textContent = ev.text || "";
      art.appendChild(pre);
    }

    if (ev.truncated) {
      const note = document.createElement("div");
      note.className = "feed-view-note";
      note.textContent = "truncated for the wire — full file is still on disk";
      art.appendChild(note);
    }

    art.appendChild(makeBar("bottom"));

    const select = () => this._selectCard(art, titleText);
    art.addEventListener("click", select);
    art.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        select();
      }
    });

    this._append(art);
    if (typeof art.scrollIntoView === "function") {
      art.scrollIntoView({ block: "nearest" });
    }
  }

  _selectCard(art, titleText) {
    this.root.querySelectorAll(".feed-view.selected, .fileout.selected").forEach((n) => {
      n.classList.remove("selected");
    });
    art.classList.add("selected");
    this._selection = {
      kind: art.dataset.kind,
      address: art.dataset.address,
      title: titleText,
    };
    window.__xliiFeedSelection = this._selection;
    if (this._onSelect) this._onSelect(this._selection);
  }

  _copyView(art, btn) {
    const text = art.dataset.copyText || art.dataset.address || "";
    const done = () => {
      const prev = btn.textContent;
      btn.textContent = "Copied";
      setTimeout(() => { btn.textContent = prev; }, 1200);
    };
    const clip = navigator.clipboard;
    if (clip && typeof clip.writeText === "function") {
      clip.writeText(text).then(done).catch(() => this._copyFallback(text, done));
      return;
    }
    this._copyFallback(text, done);
  }

  _copyFallback(text, done) {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
      done();
    } catch {
      /* clipboard blocked */
    }
  }
}
