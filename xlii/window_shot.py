"""Grab pixels of the face window (Options → Save screenshot).

A DOM snapshot (SVG + foreignObject) opens as a blank sheet in viewers,
and WebKit taints a canvas after drawing one. The face shot is an OS
window grab — X11 ``import``/``xwd``, then GNOME's focused-window call.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Optional

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# WM_CLASS / title tokens that mean the face, not a terminal sitting in this repo.
_FACE_CLASS = ("xlii-desktop", "xlii.xlii", "life.n3r4.xlii")
_SKIP_CLASS = ("kitty", "gnome-terminal", "xfce4-terminal", "alacritty",
               "wezterm", "konsole", "xterm", "mate-terminal")


def is_png(path: Path) -> bool:
    try:
        data = path.read_bytes()[:8]
    except OSError:
        return False
    return data == PNG_MAGIC and path.stat().st_size > 32


def pick_face_id(rows: Iterable[tuple[str, str, str]]) -> Optional[str]:
    """Pick a wmctrl/xdotool row ``(id, wm_class, title)`` that is the face."""
    scored: list[tuple[int, str]] = []
    for wid, cls, title in rows:
        cl = (cls or "").lower()
        tl = (title or "").strip().lower()
        if any(s in cl for s in _SKIP_CLASS):
            continue
        score = 0
        if any(tok in cl for tok in _FACE_CLASS) or cl.startswith("xlii"):
            score += 10
        if tl == "xlii":
            score += 6
        elif tl.startswith("xlii"):
            score += 3
        if score:
            scored.append((score, str(wid)))
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1] if scored else None


def _run(cmd: list[str], timeout: float = 6) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False,
    )


def _wmctrl_rows() -> list[tuple[str, str, str]]:
    if not shutil.which("wmctrl"):
        return []
    try:
        out = _run(["wmctrl", "-lx"]).stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows: list[tuple[str, str, str]] = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 3:
            continue
        wid, _desk, cls = parts[0], parts[1], parts[2]
        title = parts[4] if len(parts) > 4 else ""
        rows.append((wid, cls, title))
    return rows


def _xdotool_ids(kind: str, value: str) -> list[str]:
    if not shutil.which("xdotool"):
        return []
    try:
        out = _run(["xdotool", "search", f"--{kind}", value]).stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _active_id() -> str:
    if not shutil.which("xdotool"):
        return ""
    try:
        return (_run(["xdotool", "getactivewindow"]).stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _candidate_ids() -> list[str]:
    found: list[str] = []
    picked = pick_face_id(_wmctrl_rows())
    if picked:
        found.append(picked)
    for kind, value in (
        ("class", "xlii-desktop"),
        ("class", "xlii"),
    ):
        found.extend(_xdotool_ids(kind, value))
    active = _active_id()
    if active:
        found.append(active)
    seen: set[str] = set()
    out: list[str] = []
    for wid in found:
        if wid not in seen:
            seen.add(wid)
            out.append(wid)
    return out


def _import_window(wid: str, dest: Path) -> bool:
    if not shutil.which("import"):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    for extra in (["-frame"], []):
        try:
            r = _run(["import", "-silent", *extra, "-window", wid, str(dest)], timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0 and is_png(dest):
            return True
    return False


def _xwd_window(wid: str, dest: Path) -> bool:
    if not shutil.which("xwd") or not shutil.which("convert"):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    xwd = dest.with_suffix(".xwd")
    try:
        r = _run(["xwd", "-silent", "-id", wid, "-out", str(xwd)], timeout=8)
        if r.returncode != 0 or not xwd.is_file():
            return False
        r2 = _run(["convert", str(xwd), str(dest)], timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        try:
            xwd.unlink(missing_ok=True)
        except OSError:
            # Temp capture cleanup in a finally: the return value above is already decided.
            pass
    return r2.returncode == 0 and is_png(dest)


def _gnome_focused(dest: Path) -> bool:
    if not shutil.which("gdbus"):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = _run([
            "gdbus", "call", "--session",
            "--dest", "org.gnome.Shell.Screenshot",
            "--object-path", "/org/gnome/Shell/Screenshot",
            "--method", "org.gnome.Shell.Screenshot.ScreenshotWindow",
            "true", "false", "false", str(dest.resolve()),
        ], timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and is_png(dest)


def grab_face_window(dest: Path) -> bool:
    """Write a PNG of the face window to *dest*. False if nothing usable."""
    dest = Path(dest)
    for wid in _candidate_ids():
        if _import_window(wid, dest) or _xwd_window(wid, dest):
            return True
    return _gnome_focused(dest)
