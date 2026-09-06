/** 16×16 overlay marks for the flip chip. Words stay on title/aria only. */

const _svg = (body) =>
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" ` +
  `fill="none" stroke="currentColor" stroke-width="1.5" ` +
  `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

const MARKS = {
  howto: _svg(
    `<circle cx="8" cy="8" r="6"/>` +
    `<path d="M6.2 6.1a2 2 0 1 1 2.3 2.1c-.6.3-1 .8-1 1.5"/>` +
    `<path d="M8 12.1v.2"/>`,
  ),
  ops: _svg(
    `<path d="M3 5h10M3 11h10"/>` +
    `<circle cx="6.2" cy="5" r="1.5" fill="currentColor" stroke="none"/>` +
    `<circle cx="10.2" cy="11" r="1.5" fill="currentColor" stroke="none"/>`,
  ),
  plan: _svg(
    `<rect x="3.5" y="2.5" width="9" height="11" rx="1"/>` +
    `<path d="M6 6h4.2M6 8.5h4.2M6 11h2.4"/>`,
  ),
  rail: _svg(
    `<path d="M3 4.5h10M3 11.5h10"/>` +
    `<path d="M5 4.5v7M8 4.5v7M11 4.5v7"/>`,
  ),
  debug: _svg(
    `<ellipse cx="8" cy="8.6" rx="3" ry="3.4"/>` +
    `<path d="M8 5.2V3.4M5.2 7H3.4M12.8 7H11M5.2 10H3.4M12.8 10H11M5.8 12.4 4.4 14M10.2 12.4l1.4 1.6"/>`,
  ),
  disc: _svg(
    `<circle cx="7" cy="7" r="3.6"/>` +
    `<path d="M9.8 9.8 13 13"/>`,
  ),
  discovery: _svg(
    `<circle cx="7" cy="7" r="3.6"/>` +
    `<path d="M9.8 9.8 13 13"/>`,
  ),
  cursor: _svg(
    `<path d="M4 2.4 12 8.2l-3.2.5 1.5 4-1.8.7-1.5-4L4 12.6z" fill="currentColor" stroke="none"/>`,
  ),
  claude: _svg(
    `<path d="M8 2.4 9.2 6.6 13.6 8 9.2 9.4 8 13.6 6.8 9.4 2.4 8l4.4-1.4z" fill="currentColor" stroke="none"/>`,
  ),
  grok: _svg(`<path d="M4 4l8 8M12 4 4 12"/>`),
  codex: _svg(`<path d="M6 3.6 3 8l3 4.4M10 3.6 13 8l-3 4.4"/>`),
  _: _svg(
    `<path d="M8 2.8 13.5 6 8 9.2 2.5 6 8 2.8z"/>` +
    `<path d="M3 9.4 8 12.2 13 9.4"/>` +
    `<path d="M3 12 8 14.6 13 12"/>`,
  ),
};

export function overlayMark(name) {
  const key = String(name || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "");
  return MARKS[key] || MARKS._;
}
