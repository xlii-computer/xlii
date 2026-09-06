"""Desk app preferences — editor / image editor / browser / terminal.

Config stores a command line (or empty = auto). The face Config pane cycles
through whatever is actually on PATH. Resolvers never invent a binary: empty
means "use the existing auto path" ($EDITOR, xdg-open, webbrowser, …).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Optional, Sequence

from xlii.project_paths import user_home

# Common Linux / desktop binaries. Cycle rings only include what's on PATH.
EDITOR_CANDIDATES: tuple[str, ...] = (
    "nano", "vim", "nvim", "vi", "micro", "emacs", "hx", "helix",
    "pluma", "gedit", "kate", "mousepad", "xed", "geany",
    "code", "codium", "code -w",
)
IMAGE_EDITOR_CANDIDATES: tuple[str, ...] = (
    "gimp", "krita", "pinta", "inkscape", "darktable",
    "eog", "eom", "gwenview", "nsxiv", "feh",
)
BROWSER_CANDIDATES: tuple[str, ...] = (
    "firefox", "firefox-esr", "chromium", "google-chrome",
    "google-chrome-stable", "brave-browser", "vivaldi",
    "epiphany", "falkon", "qutebrowser",
)
TERMINAL_CANDIDATES: tuple[str, ...] = (
    "kitty", "alacritty", "wezterm", "gnome-terminal",
    "konsole", "xfce4-terminal", "x-terminal-emulator", "xterm",
)

PANEL_WIDTH_RING: tuple[int, ...] = (0, 25, 33, 50, 60, 70)

TOOL_ITER_RING: tuple[int, ...] = (8, 12, 20, 40, 80)
CHAT_ITER_RING: tuple[int, ...] = (4, 8, 12, 20)
WORKER_ITER_RING: tuple[int, ...] = (5, 10, 20)

# Tools → New terminal cwd. ``project`` is the live desk; ``home`` is ~;
# ``root`` is the filesystem root; ``custom`` uses ``tui_terminal_cwd_path``.
TERM_CWD_PROJECT = "project"
TERM_CWD_HOME = "home"
TERM_CWD_ROOT = "root"
TERM_CWD_CUSTOM = "custom"
TERM_CWD_RING: tuple[str, ...] = (
    TERM_CWD_PROJECT, TERM_CWD_HOME, TERM_CWD_ROOT, TERM_CWD_CUSTOM,
)
TERM_CWD_LABELS: dict[str, str] = {
    TERM_CWD_PROJECT: "this project",
    TERM_CWD_HOME: "home folder",
    TERM_CWD_ROOT: "root",
    TERM_CWD_CUSTOM: "custom",
}


def installed(candidates: Sequence[str]) -> list[str]:
    """Candidates whose first token is on PATH, in the given order."""
    out: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        line = (c or "").strip()
        if not line or line in seen:
            continue
        try:
            parts = shlex.split(line)
        except ValueError:
            continue
        if parts and shutil.which(parts[0]):
            out.append(line)
            seen.add(line)
    return out


def cycle_pref(current: str, candidates: Sequence[str]) -> str:
    """Advance ``current`` through ``[''] + candidates`` (empty = auto)."""
    cur = (current or "").strip()
    ring = [""]
    for c in candidates:
        line = (c or "").strip()
        if line and line not in ring:
            ring.append(line)
    if cur and cur not in ring:
        ring.insert(1, cur)
    try:
        i = ring.index(cur)
    except ValueError:
        return ring[0]
    return ring[(i + 1) % len(ring)]


def pref_label(current: str, *, auto: str = "auto") -> str:
    return (current or "").strip() or auto


def compact_home_path(path: str) -> str:
    """``/home/you/foo`` → ``~/foo``. Already-short paths pass through."""
    raw = (path or "").strip()
    if not raw:
        return ""
    try:
        home = str(user_home())
    except Exception:
        return raw
    if raw == home:
        return "~"
    prefix = home.rstrip("/") + "/"
    if raw.startswith(prefix):
        return "~/" + raw[len(prefix):]
    return raw


def normalize_term_cwd(raw: str | None) -> str:
    v = (raw or "").strip().lower()
    return v if v in TERM_CWD_RING else TERM_CWD_PROJECT


def term_cwd_label(cfg=None, *, path: str | None = None) -> str:
    """Menu / config hint: this project · home folder · root · ~/foo."""
    mode = normalize_term_cwd(getattr(cfg, "tui_terminal_cwd", "") if cfg else "")
    if mode != TERM_CWD_CUSTOM:
        return TERM_CWD_LABELS[mode]
    custom = path
    if custom is None:
        custom = str(getattr(cfg, "tui_terminal_cwd_path", "") or "").strip() if cfg else ""
    custom = custom.strip()
    return compact_home_path(custom) if custom else "custom"


def cycle_term_cwd(cfg) -> str:
    """Advance ``tui_terminal_cwd`` through the ring. Does not persist."""
    cur = normalize_term_cwd(getattr(cfg, "tui_terminal_cwd", ""))
    nxt = TERM_CWD_RING[(TERM_CWD_RING.index(cur) + 1) % len(TERM_CWD_RING)]
    cfg.tui_terminal_cwd = nxt
    return nxt


def apply_terminal_cwd_path(cfg, raw: str) -> tuple[bool, str]:
    """Set (or clear) the custom New-terminal folder. Persists when ``save`` exists."""
    if cfg is None:
        return False, "no config"
    text = (raw or "").strip()
    if not text or text.lower() in ("clear", "off", "none", "auto", "project"):
        cfg.tui_terminal_cwd = TERM_CWD_PROJECT
        cfg.tui_terminal_cwd_path = ""
        _save_cfg(cfg)
        return True, "new terminal · this project"
    p = Path(text).expanduser()
    if not p.is_absolute():
        p = user_home() / p
    cfg.tui_terminal_cwd = TERM_CWD_CUSTOM
    cfg.tui_terminal_cwd_path = str(p)
    _save_cfg(cfg)
    return True, f"new terminal · {compact_home_path(str(p))}"


def _save_cfg(cfg) -> None:
    save = getattr(cfg, "save", None)
    if callable(save):
        try:
            save()
        except Exception:
            # Config save is best-effort; callers must not fail on persistence errors.
            pass


def _project_cwd(state) -> str:
    return str(
        getattr(state, "shell_cwd", None)
        or getattr(getattr(state, "project", None), "project_root", None)
        or os.getcwd()
    )


def resolve_terminal_cwd(state=None, cfg=None) -> str:
    """Directory Tools → New terminal should open.

    ``project`` (default) is the live desk. ``home`` is ``~``. ``root`` is
    ``/``. ``custom`` uses the stored path when that directory exists;
    otherwise the live desk (so a stale custom never bricks the menu).
    """
    if cfg is None and state is not None:
        cfg = getattr(state, "cfg", None)
    mode = normalize_term_cwd(getattr(cfg, "tui_terminal_cwd", "") if cfg else "")
    if mode == TERM_CWD_HOME:
        return str(user_home())
    if mode == TERM_CWD_ROOT:
        return os.sep
    if mode == TERM_CWD_CUSTOM:
        raw = str(getattr(cfg, "tui_terminal_cwd_path", "") or "").strip() if cfg else ""
        if raw:
            try:
                p = Path(raw).expanduser()
                if not p.is_absolute():
                    p = user_home() / p
                if p.is_dir():
                    return str(p.resolve())
            except OSError:
                # Unresolvable custom path — fall back to the project cwd.
                pass
    return _project_cwd(state)


def _line_if_on_path(line: str) -> Optional[str]:
    raw = (line or "").strip()
    if not raw:
        return None
    try:
        parts = shlex.split(raw)
    except ValueError:
        return None
    if parts and shutil.which(parts[0]):
        return raw
    return None


def resolve_image_editor(cfg=None) -> Optional[str]:
    """Configured image editor when the binary is on PATH; else None (OS opener)."""
    if cfg is None:
        try:
            from xlii.active_session import active_cfg

            cfg = active_cfg()
        except Exception:
            cfg = None
    return _line_if_on_path(str(getattr(cfg, "image_editor", "") or ""))


def downloads_dir() -> Path:
    """The user's drop zone. Installers land here, not in the project cwd."""
    xdg = os.environ.get("XDG_DOWNLOAD_DIR", "").strip()
    if xdg:
        p = Path(xdg).expanduser()
        if p.is_dir():
            return p
    return user_home() / "Downloads"


_DESK_LIST = frozenset({"ls", "ll", "pwd", "dir"})
_DESK_NAV_CHAIN = re.compile(r"[&;|]")


def is_desk_nav(line: str) -> bool:
    """True for a short desk-navigation line: ``cd``, ``ls``/``ll``, ``pwd``.

    Talk-primary still talks. These are the commands a user types to *be*
    somewhere — they must move the live cwd even when the prompt is [M].
    Chained forms (``cd a && ls``) and prose (``cd to the downloads folder``)
    are not desk-nav.
    """
    s = (line or "").strip()
    if not s or _DESK_NAV_CHAIN.search(s):
        return False
    try:
        parts = shlex.split(s)
    except ValueError:
        parts = s.split()
    if not parts:
        return False
    cmd = parts[0]
    if cmd == "cd":
        return len(parts) <= 2
    if cmd == "pwd":
        return len(parts) == 1
    if cmd in ("ls", "ll"):
        paths = [p for p in parts[1:] if not p.startswith("-")]
        return len(paths) <= 1
    return False


def listing_from_shell(ev) -> str:
    """Compact last-``ls`` note for ``/sh``, or '' if *ev* is not a listing."""
    if ev is None:
        return ""
    cmd = (getattr(ev, "command", "") or "").strip()
    first = cmd.split()[0] if cmd else ""
    if first not in _DESK_LIST:
        return ""
    text = (getattr(ev, "stdout", "") or "").strip()
    if not text:
        return ""
    tail = text.splitlines()[:40]
    return f"Last listing ($ {cmd}):\n" + "\n".join(f"  {ln}" for ln in tail)


_DROP_HINT_RE = re.compile(
    r"download|install|appimage|\.deb|\.dmg|\.rpm|newest|latest|upgrade",
    re.I,
)
_DROP_STOP = {
    "the", "this", "that", "from", "folder", "directory", "file", "files",
    "install", "installed", "newest", "latest", "version", "and", "for",
    "with", "into", "onto", "please", "just", "download", "downloads",
    "upgrade", "update", "run", "open", "the",
}


def _nl_tokens(nl: str) -> list[str]:
    return [
        t.lower() for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]{2,}", nl or "")
        if t.lower() not in _DROP_STOP
    ]


def name_matches(nl: str, directory: Path, *, limit: int = 8) -> list[Path]:
    """Newest files in *directory* whose names hit tokens from *nl* (case-fold)."""
    if not directory or not Path(directory).is_dir():
        return []
    tokens = _nl_tokens(nl)
    if not tokens:
        return []
    hits: list[Path] = []
    try:
        kids = [p for p in Path(directory).iterdir() if p.is_file()]
    except OSError:
        return []
    kids.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for p in kids:
        name = p.name.lower()
        if any(tok in name for tok in tokens):
            hits.append(p)
            if len(hits) >= limit:
                break
    return hits


def drop_matches(nl: str, *, limit: int = 8) -> list[Path]:
    """Newest files in the drop zone whose names hit tokens from *nl*."""
    return name_matches(nl, downloads_dir(), limit=limit)


def _file_size_label(p: Path) -> str:
    try:
        n = p.stat().st_size
    except OSError:
        return "?"
    return f"{n / 1_048_576:.0f}M" if n >= 1_048_576 else f"{n}B"


def desk_context(
    nl: str = "",
    *,
    cwd: "os.PathLike[str] | str | None" = None,
    last_listing: str = "",
) -> str:
    """Facts ``/sh`` and auto-fix need so they don't invent macOS or search cwd."""
    home = user_home()
    down = downloads_dir()
    cwd_path = Path(cwd).expanduser() if cwd else None
    cwd_s = str(cwd_path) if cwd_path else ""
    lines = [
        f"Home: {home}",
        f"Drop zone: {down}" + (" (exists)" if down.is_dir() else " (missing)"),
        (
            "Bare globs search CWD only — not the drop zone. Use ~/Downloads/… for "
            "something the user downloaded. Globs are case-sensitive; prefer the "
            "absolute paths listed below."
        ),
        (
            "Linux installers: .AppImage → chmod +x and run that path; "
            ".deb → sudo dpkg -i; never dpkg an AppImage; never assume .dmg."
        ),
        (
            "If the user already cd'd / ls'd, CWD and Last listing are the truth. "
            "Do not search a different directory."
        ),
    ]
    if cwd_s:
        lines.append(f"Working directory (CWD): {cwd_s}")
    if last_listing:
        lines.append(last_listing.rstrip())
    hint = bool(_DROP_HINT_RE.search(nl or ""))
    drop_hits = drop_matches(nl) if (hint or _nl_tokens(nl)) else []
    cwd_hits: list[Path] = []
    if cwd_path is not None:
        try:
            same = cwd_path.resolve() == down.resolve()
        except OSError:
            same = False
        if not same:
            cwd_hits = name_matches(nl, cwd_path)
    if cwd_hits:
        lines.append("Matching files in CWD (newest first):")
        for p in cwd_hits:
            lines.append(f"  {p}  ({_file_size_label(p)})")
    if drop_hits:
        lines.append("Matching files in drop zone (newest first):")
        for p in drop_hits:
            lines.append(f"  {p}  ({_file_size_label(p)})")
    elif hint and not cwd_hits:
        lines.append("No matching files in the drop zone for this task.")
    return "\n".join(lines)


def resolve_browser(cfg=None) -> Optional[str]:
    """Configured browser when the binary is on PATH; else None (webbrowser/OS)."""
    if cfg is None:
        try:
            from xlii.active_session import active_cfg

            cfg = active_cfg()
        except Exception:
            cfg = None
    return _line_if_on_path(str(getattr(cfg, "browser", "") or ""))
