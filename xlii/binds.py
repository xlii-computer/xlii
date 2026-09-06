"""Task chrome binds — menu rows and F-keys that run a saved task.

A bind is a symlink: the recipe stays ``/tasks run <name>``. The menu or F-key
only starts it. User file ``~/.config/xlii/binds.toml`` is personal chrome;
a project may add rows in ``.xlii/binds.toml``. Same F-key: user wins.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from xlii.atomicio import write_text_atomic

BINDS_FILENAME = "binds.toml"
MENUS = frozenset({"project", "tools", "xlii"})
FKEY_MIN, FKEY_MAX = 1, 12


@dataclass(frozen=True)
class Bind:
    task: str
    menu: str = ""
    fkey: str = ""
    label: str = ""
    origin: str = "user"  # user | project

    def display(self) -> str:
        return (self.label or self.task).strip() or self.task


class BindError(ValueError):
    """Bad bind file or invocation."""


def user_binds_path() -> Path:
    from xlii.config import global_config_dir

    return global_config_dir() / BINDS_FILENAME


def project_binds_path(xli_dir: Path | str | None) -> Optional[Path]:
    if not xli_dir:
        return None
    return Path(xli_dir) / BINDS_FILENAME


def parse_fkey(raw: str) -> str:
    text = (raw or "").strip().lower()
    if text.startswith("f") and text[1:].isdigit():
        n = int(text[1:])
        if FKEY_MIN <= n <= FKEY_MAX:
            return f"f{n}"
    raise BindError(f"fkey must be f{FKEY_MIN}–f{FKEY_MAX} (got {raw!r})")


def parse_menu(raw: str) -> str:
    name = (raw or "").strip().lower()
    if name not in MENUS:
        raise BindError(f"menu must be project, tools, or xlii (got {raw!r})")
    return name


def _read_file(path: Path, *, origin: str) -> list[Bind]:
    import tomllib

    if path is None or not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise BindError(f"binds file corrupt ({path.name}): {e}") from e
    except OSError as e:
        raise BindError(f"binds file unreadable ({path.name}): {e}") from e
    raw = data.get("bind")
    if not isinstance(raw, list):
        return []
    out: list[Bind] = []
    for blk in raw:
        if not isinstance(blk, dict):
            continue
        task = str(blk.get("task", "") or "").strip()
        if not task:
            continue
        menu = str(blk.get("menu", "") or "").strip().lower()
        if menu and menu not in MENUS:
            menu = ""
        fkey = str(blk.get("fkey", "") or "").strip().lower()
        if fkey:
            try:
                fkey = parse_fkey(fkey)
            except BindError:
                fkey = ""
        if not menu and not fkey:
            continue
        label = str(blk.get("label", "") or "").strip()
        out.append(Bind(task=task, menu=menu, fkey=fkey, label=label, origin=origin))
    return out


def _read_file_or_empty(path: Path, *, origin: str) -> list[Bind]:
    """Best-effort read for display/merge; corrupt files behave as empty."""
    try:
        return _read_file(path, origin=origin)
    except BindError:
        return []


def load_binds(xli_dir: Path | str | None = None) -> list[Bind]:
    """User binds first, then project binds that don't steal a user F-key."""
    user = _read_file_or_empty(user_binds_path(), origin="user")
    taken = {b.fkey for b in user if b.fkey}
    extra: list[Bind] = []
    p = project_binds_path(xli_dir)
    if p is not None:
        for b in _read_file_or_empty(p, origin="project"):
            if b.fkey and b.fkey in taken:
                extra.append(Bind(task=b.task, menu=b.menu, fkey="", label=b.label,
                                  origin="project"))
            else:
                extra.append(b)
                if b.fkey:
                    taken.add(b.fkey)
    return user + extra


def _toml_str(value: str) -> str:
    """Emit a TOML basic string with proper escaping (labels may contain quotes)."""
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def _dump(entries: Iterable[Bind]) -> str:
    lines: list[str] = []
    for b in entries:
        lines.append("[[bind]]")
        lines.append(f"task = {_toml_str(b.task)}")
        if b.menu:
            lines.append(f"menu = {_toml_str(b.menu)}")
        if b.fkey:
            lines.append(f"fkey = {_toml_str(b.fkey)}")
        if b.label:
            lines.append(f"label = {_toml_str(b.label)}")
        lines.append("")
    return "\n".join(lines)


def save_user_binds(entries: list[Bind]) -> Path:
    path = user_binds_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, _dump(entries), mode=0o644)
    return path


def save_project_binds(xli_dir: Path | str, entries: list[Bind]) -> Path:
    path = project_binds_path(xli_dir)
    if path is None:
        raise BindError("need a project to save project binds")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, _dump(entries), mode=0o644)
    return path


def bind_run_line(task: str, xli_dir: Path | str | None = None) -> str:
    """The line a bind submits. Seeds (trailing space) when required params need values."""
    name = (task or "").strip()
    if not name:
        raise BindError("need a task name")
    if xli_dir:
        try:
            from xlii import tasks as T

            pipe = T.load_pipeline(xli_dir, name)
            needs = any(p.required and not p.default for p in pipe.params)
            if needs:
                return f"/tasks run {name} "
        except Exception:
            return f"/tasks run {name} "
    return f"/tasks run {name} --yes"


def upsert_bind(
    entries: list[Bind],
    *,
    task: str,
    menu: str = "",
    fkey: str = "",
    label: str = "",
    origin: str = "user",
) -> list[Bind]:
    """Add or merge a bind for *task*. A new F-key is taken off any other row."""
    task = (task or "").strip()
    if not task:
        raise BindError("need a task name")
    menu = parse_menu(menu) if menu else ""
    fkey = parse_fkey(fkey) if fkey else ""
    label = (label or "").strip()
    if not menu and not fkey:
        menu = "project"
    out: list[Bind] = []
    found = False
    for b in entries:
        if b.fkey and fkey and b.fkey == fkey and b.task != task:
            out.append(Bind(task=b.task, menu=b.menu, fkey="", label=b.label,
                            origin=b.origin))
            continue
        if b.task == task and b.origin == origin:
            out.append(Bind(
                task=task,
                menu=menu or b.menu,
                fkey=fkey or b.fkey,
                label=label or b.label,
                origin=origin,
            ))
            found = True
        else:
            out.append(b)
    if not found:
        out.append(Bind(task=task, menu=menu, fkey=fkey, label=label, origin=origin))
    return [b for b in out if b.menu or b.fkey]


def remove_binds(entries: list[Bind], token: str) -> tuple[list[Bind], int]:
    """Drop binds by task, label, ``f11``, or ``menu:project``.

    Empty *token* removes the only bind (fails closed if there are several).
    """
    raw = (token or "").strip()
    if not raw:
        if len(entries) == 1:
            return [], 1
        return list(entries), 0
    key = raw.lower()
    kept: list[Bind] = []
    n = 0
    fkey = ""
    try:
        fkey = parse_fkey(raw)
    except BindError:
        fkey = ""
    menu = ""
    if key.startswith("menu:"):
        try:
            menu = parse_menu(key.split(":", 1)[1])
        except BindError:
            menu = ""
    for b in entries:
        drop = (
            b.task.lower() == key
            or (b.label or "").lower() == key
            or (fkey and b.fkey == fkey)
            or (menu and b.menu == menu)
        )
        if drop:
            n += 1
            continue
        kept.append(b)
    return kept, n


def menu_binds(binds: Iterable[Bind], menu: str) -> list[Bind]:
    want = (menu or "").strip().lower()
    return [b for b in binds if b.menu == want]


def fkey_bind(binds: Iterable[Bind], key: str) -> Optional[Bind]:
    try:
        fk = parse_fkey(key)
    except BindError:
        return None
    for b in binds:
        if b.fkey == fk:
            return b
    return None


# Default commander labels (same row as the TUI/face bar). Binds overlay these.
COMMANDER_FKEY_LABELS: tuple[tuple[str, str], ...] = (
    ("f1", "help"), ("f2", "Home Hub"), ("f3", "view"), ("f4", "edit"),
    ("f5", "copy"), ("f6", "detach"), ("f7", "add"), ("f8", "rem"),
    ("f9", "jobs"), ("f10", "tasks"),
)


def fkey_hints(binds: Iterable[Bind] | None = None) -> list[tuple[str, str]]:
    """Commander F1–F10 labels, overlayed by binds; extra F11/F12 appended."""
    labels = {k: lab for k, lab in COMMANDER_FKEY_LABELS}
    extra: list[tuple[str, str]] = []
    for b in binds or ():
        if not b.fkey:
            continue
        lab = b.display()[:16]
        n = int(b.fkey[1:])
        if n <= 10:
            labels[b.fkey] = lab
        else:
            extra.append((b.fkey.upper(), lab))
    rows = [(f"F{i}", labels[f"f{i}"]) for i in range(1, 11)]
    return rows + extra


def chrome_rows(xli_dir: Path | str | None = None) -> list[dict[str, Any]]:
    """Face/TUI chrome payload: one dict per bind."""
    rows = []
    binds = load_binds(xli_dir)
    for b in binds:
        try:
            line = bind_run_line(b.task, xli_dir)
        except BindError:
            line = f"/tasks run {b.task} "
        rows.append({
            "task": b.task,
            "menu": b.menu,
            "fkey": b.fkey,
            "label": b.display(),
            "line": line,
            "origin": b.origin,
        })
    return rows
