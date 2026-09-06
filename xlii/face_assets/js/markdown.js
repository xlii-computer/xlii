// Markdown → safe DOM. Vendored marked (MIT) with raw HTML ESCAPED — the
// transcript renders the model's markdown, never its HTML (no sanitizer
// dependency; if inline HTML is ever wanted, vendor DOMPurify then).

import { Marked } from "../vendor/marked.esm.js";

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const marked = new Marked({
  gfm: true,
  breaks: false,
  renderer: {
    html(token) {
      const raw = typeof token === "string" ? token : (token && token.text) || "";
      return escapeHtml(raw);
    },
  },
});

export function renderMarkdown(md) {
  const div = document.createElement("div");
  div.className = "md";
  div.innerHTML = marked.parse(md || "");
  // Belt over braces: never let a crafted link run script or hijack the shell.
  for (const a of div.querySelectorAll("a[href]")) {
    const href = a.getAttribute("href") || "";
    if (/^\s*javascript:/i.test(href)) a.removeAttribute("href");
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
  }
  return div;
}
