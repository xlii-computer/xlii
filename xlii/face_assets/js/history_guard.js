// Public / phone Face: Android system Back must not reveal the spent
// pairing page. The grant cookie is HttpOnly, so "sitting live" is the
// Face exitPhase (open) — #468 linger / WS reattach still work if we stay.
//
// First Back re-pushes Face. A second Back inside a short window is the
// escape (menu Exit is the other). Desk / Tauri without data-view=phone
// does not install. Does not call the fullscreen API (#472 owns that).

export const FACE_HISTORY_MARK = "xlii-face";
export const FACE_LEAVE_FLAG = "xlii-face-leave";
export const FACE_LEAVE_WINDOW_MS = 2000;

export function shouldHoldBack({ sittingLive, leaveArmed }) {
  return !!(sittingLive && !leaveArmed);
}

export function installFaceHistoryGuard({
  history: hist,
  addEventListener,
  locationHref,
  isPhone,
  isSittingLive,
  leaveWindowMs = FACE_LEAVE_WINDOW_MS,
  now = () => Date.now(),
  sessionStorage: store,
} = {}) {
  hist = hist || globalThis.history;
  const win = globalThis.window;
  addEventListener = addEventListener || (win && win.addEventListener && win.addEventListener.bind(win));
  isPhone = isPhone || (() => {
    try { return document.documentElement.dataset.view === "phone"; }
    catch { return false; }
  });
  isSittingLive = isSittingLive || (() => true);
  store = store || (typeof sessionStorage !== "undefined" ? sessionStorage : null);
  if (locationHref == null) {
    locationHref = () => {
      try { return win.location.href; }
      catch { return ""; }
    };
  }
  const hrefOf = () => (typeof locationHref === "function" ? locationHref() : locationHref);

  if (!hist || typeof hist.pushState !== "function" || typeof hist.replaceState !== "function") {
    return { installed: false };
  }
  if (typeof isPhone === "function" ? !isPhone() : !isPhone) {
    return { installed: false };
  }
  if (!addEventListener) return { installed: false };

  try {
    const prev = hist.state && typeof hist.state === "object" ? hist.state : {};
    hist.replaceState({ ...prev, xlii: FACE_HISTORY_MARK }, "", hrefOf());
    hist.pushState({ xlii: FACE_HISTORY_MARK, sentinel: 1 }, "", hrefOf());
  } catch {
    return { installed: false };
  }

  let lastPopAt = null;
  const onPop = () => {
    const t = now();
    const leaveArmed = lastPopAt != null && (t - lastPopAt) < leaveWindowMs;
    lastPopAt = t;
    if (!shouldHoldBack({ sittingLive: isSittingLive(), leaveArmed })) {
      try { if (store) store.setItem(FACE_LEAVE_FLAG, "1"); } catch { /* ignore */ }
      return;
    }
    try {
      hist.pushState({ xlii: FACE_HISTORY_MARK, sentinel: 1 }, "", hrefOf());
    } catch { /* ignore */ }
  };
  addEventListener("popstate", onPop);
  return { installed: true, onPop };
}
