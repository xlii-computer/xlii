// Face glass overlay — occupancy lock / mouth. Unlock is this overlay, not /unlock in a dead box.

export function applyGlass(ev) {
  const glass = document.getElementById("glass");
  if (!glass) return;
  const g = String((ev && ev.glass) || "");
  const mouth = String((ev && ev.mouth) || "desk");
  const via = String((ev && ev.mouth_via) || "");
  const posture = String(
    (ev && ev.view_posture) || document.documentElement.dataset.view || ""
  );
  // Phone glass IS the mouth. The occupancy overlay is the desk banner;
  // showing it here covers the only input, and the phone cannot unlock.
  if (posture === "phone") {
    glass.hidden = true;
    glass.dataset.mode = "";
    document.documentElement.dataset.glass = "";
    return;
  }
  let mode = "";
  if (g === "black") mode = "black";
  else if (g === "locked") mode = "locked";
  else if (mouth && mouth !== "desk") mode = "away";
  glass.hidden = !mode;
  glass.dataset.mode = mode;
  document.documentElement.dataset.glass = mode;
  const title = document.getElementById("glass-title");
  const copy = document.getElementById("glass-copy");
  const code = document.getElementById("glass-code");
  if (title) {
    title.textContent = mode === "away"
      ? (via ? `me@ has the mouth on ${via}` : "me@ has the mouth")
      : "locked";
  }
  if (copy) {
    copy.textContent = mode === "away"
      ? (via
          ? `one me — this glass waits while ${via} talks`
          : "this glass is lock / unlock only")
      : "unlock on this desk — the phone cannot hostage it";
  }
  if (code) {
    code.hidden = !(mode === "locked" || mode === "black");
  }
}

export function installGlass(wire) {
  const lockBtn = document.getElementById("glass-lock");
  const unlockBtn = document.getElementById("glass-unlock");
  const code = document.getElementById("glass-code");
  if (lockBtn) {
    lockBtn.addEventListener("click", () => {
      wire.send({ type: "lock" });
    });
  }
  if (unlockBtn) {
    unlockBtn.addEventListener("click", () => {
      wire.send({ type: "unlock", code: (code && code.value) || "" });
      if (code) code.value = "";
    });
  }
  if (code) {
    code.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        unlockBtn && unlockBtn.click();
      }
    });
  }
}
