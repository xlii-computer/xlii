"""External artifact preview — ``python -m xlii.artifact_preview <path>`` (media M3).

The desktop complement to the inline terminal preview (R3/M1): hand the
artifact to the OS opener (``xdg-open`` / ``open`` / ``start``) so it appears
in the user's real image viewer. Deliberately boring and robust:

- **Default: OS opener.** No custom windowing to maintain; every desktop has
  one; remote/odd setups degrade cleanly.
- **Headless: print the path and exit 0.** On SSH the inline terminal preview
  is the primary channel; this module refusing loudly would just be noise.
- Complements, never replaces, ``maybe_preview`` — ``/imagine --no-preview``
  skips inline only; callers can still launch this.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

_IMAGE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".bmp", ".tif", ".tiff", ".xcf", ".ico",
}


def _image_editor_argv() -> Optional[list[str]]:
    """Configured image editor argv, or None to fall through to the OS opener."""
    try:
        from xlii.desk import resolve_image_editor
    except Exception:
        return None
    line = resolve_image_editor()
    if not line:
        return None
    try:
        parts = shlex.split(line)
    except ValueError:
        return None
    return parts or None


def _opener_command() -> Optional[list[str]]:
    """The platform's file-opener argv prefix, or None when there isn't one."""
    if sys.platform.startswith("darwin"):
        return ["open"]
    if os.name == "nt":
        # `start` is a cmd builtin; the empty title arg keeps paths with
        # spaces from being eaten as the window title.
        return ["cmd", "/c", "start", ""]
    if shutil.which("xdg-open"):
        return ["xdg-open"]
    return None


def _has_display() -> bool:
    """A GUI session we could actually open a window in (POSIX heuristics;
    Windows/macOS launchers handle their own headless cases)."""
    if os.name == "nt" or sys.platform.startswith("darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def open_artifact(path: str | Path, *, console=None) -> bool:
    """Open *path* in the OS viewer. Returns True when a viewer was launched;
    False (after printing the path) when headless or no opener exists —
    which is a SUCCESS for callers: the path is the fallback preview."""
    p = Path(path)
    printer = console.print if console is not None else print
    if not p.exists():
        printer(f"artifact not found: {p}")
        return False
    opener = None
    if p.suffix.lower() in _IMAGE_SUFFIXES:
        opener = _image_editor_argv()
    if opener is None:
        opener = _opener_command()
    if opener is None or not _has_display():
        printer(str(p))                    # headless: the path IS the preview
        return False
    try:
        subprocess.Popen(
            [*opener, str(p)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except OSError:
        printer(str(p))
        return False


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print("usage: python -m xlii.artifact_preview <path> [<path> …]")
        return 0
    rc = 0
    for a in args:
        if not Path(a).exists():
            print(f"artifact not found: {a}", file=sys.stderr)
            rc = 1
            continue
        open_artifact(a)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
