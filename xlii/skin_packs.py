"""Face skin packs — Winamp-grade graphical skins as plain files.

A pack is a folder with ``skin.toml`` + ``skin.css`` + images/fonts. Built-in
packs ship under ``xlii/face_assets/skins/``; user packs live in
``~/.config/xlii/skins/<name>/``. Pack ids are namespaced ``pack:<name>`` so
they can never shadow the four compiled skins (dark/light/slate/mojo).

The sidecar serves packs read-only at ``GET /skins/<name>/<file>`` with the
same resolve+``relative_to`` traversal guard as static assets, plus an
extension allowlist and size caps. See ``docs/selfwiki/skin-packs.md``.
"""
from __future__ import annotations

import re
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

# Compiled color skins — packs must never reuse these as folder names.
BUILTIN_FACE_SKINS = frozenset({"dark", "light", "slate", "mojo"})

BUILTIN_FACE_SKIN_META: tuple[dict[str, str], ...] = (
    {"id": "dark", "label": "Dark", "blurb": "default night desk", "scheme": "dark"},
    {"id": "light", "label": "Light", "blurb": "day desk · high contrast", "scheme": "light"},
    {"id": "slate", "label": "Slate", "blurb": "cool blue-gray", "scheme": "dark"},
    {"id": "mojo", "label": "Mojo", "blurb": "companion purple lift", "scheme": "dark"},
)

PACK_ID_PREFIX = "pack:"
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
ALLOWED_EXT = frozenset({
    ".css", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".woff2", ".toml",
})
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_PACK_BYTES = 8 * 1024 * 1024
_ALLOWED_SCHEME = frozenset({"dark", "light"})

_URL_RE = re.compile(r"url\(\s*(['\"]?)([^)'\"]*?)\1\s*\)", re.I)
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)


def builtin_skins_dir() -> Path:
    """Shipped packs (the y2k showcase lives here)."""
    return Path(__file__).resolve().parent / "face_assets" / "skins"


def user_skins_dir() -> Path:
    """Operator packs. Reads ``GLOBAL_CONFIG_DIR`` live so tests can patch it."""
    from xlii import config

    return Path(config.GLOBAL_CONFIG_DIR) / "skins"


def is_valid_pack_name(name: str) -> bool:
    return bool(NAME_RE.fullmatch(name or "")) and name not in BUILTIN_FACE_SKINS


def pack_skin_id(name: str) -> str:
    return f"{PACK_ID_PREFIX}{name}"


def parse_pack_skin_id(skin: str) -> Optional[str]:
    """Return the pack folder name if *skin* is ``pack:<name>``, else None."""
    raw = (skin or "").strip().lower()
    if not raw.startswith(PACK_ID_PREFIX):
        return None
    name = raw[len(PACK_ID_PREFIX):]
    return name if is_valid_pack_name(name) else None


@dataclass(frozen=True)
class SkinPack:
    name: str
    label: str
    blurb: str
    scheme: str
    root: Path
    origin: str  # "builtin" | "user"

    @property
    def skin_id(self) -> str:
        return pack_skin_id(self.name)

    @property
    def css_href(self) -> str:
        return f"/skins/{self.name}/skin.css"

    def as_catalog(self) -> dict[str, str]:
        return {
            "id": self.skin_id,
            "label": self.label,
            "blurb": self.blurb,
            "scheme": self.scheme,
            "kind": "pack",
            "css": self.css_href,
        }


def catalog_entries(packs: Optional[Iterable[SkinPack]] = None) -> list[dict[str, str]]:
    """Merged picker catalog: compiled built-ins first, then discovered packs."""
    rows: list[dict[str, str]] = [
        {**meta, "kind": "builtin"} for meta in BUILTIN_FACE_SKIN_META
    ]
    seen = set(BUILTIN_FACE_SKINS)
    for pack in (packs if packs is not None else discover_packs()):
        if pack.name in seen:
            continue
        seen.add(pack.name)
        rows.append(pack.as_catalog())
    return rows


def is_known_skin(name: str) -> bool:
    skin = (name or "").strip().lower()
    if skin in BUILTIN_FACE_SKINS:
        return True
    pack_name = parse_pack_skin_id(skin)
    if pack_name is None:
        return False
    return find_pack(pack_name) is not None


def discover_packs() -> list[SkinPack]:
    """Builtin packs first, then user packs. Duplicate names: builtin wins."""
    found: list[SkinPack] = []
    seen: set[str] = set()
    for origin, root in (("builtin", builtin_skins_dir()), ("user", user_skins_dir())):
        if not root.is_dir():
            continue
        for child in sorted(p for p in root.iterdir() if p.is_dir()):
            pack = _load_pack(child, origin=origin)
            if pack is None or pack.name in seen:
                continue
            seen.add(pack.name)
            found.append(pack)
    return found


def find_pack(name: str) -> Optional[SkinPack]:
    name = (name or "").strip().lower()
    if not is_valid_pack_name(name):
        return None
    for pack in discover_packs():
        if pack.name == name:
            return pack
    return None


def resolve_pack_file(name: str, rel: str) -> Optional[Path]:
    """Return the real file for ``/skins/<name>/<rel>``, or None if refused.

    Traversal, missing packs, disallowed extensions, and oversize files all
    yield None (the HTTP layer maps that to 404).
    """
    pack = find_pack(name)
    if pack is None:
        return None
    return _safe_file(pack.root, rel)


def check_pack(root: Path) -> list[str]:
    """Authoring lint. Empty list = the pack is installable / loadable."""
    errors: list[str] = []
    root = Path(root)
    if not root.is_dir():
        return [f"{root}: not a directory"]
    name = root.name.lower()
    if not NAME_RE.fullmatch(name):
        errors.append(
            f"pack folder {root.name!r} must match {NAME_RE.pattern} "
            "(lowercase, start with a letter, ≤32 chars)"
        )
    if name in BUILTIN_FACE_SKINS:
        errors.append(f"{name!r} is a compiled skin id — pick another folder name")
    toml_path = root / "skin.toml"
    css_path = root / "skin.css"
    if not toml_path.is_file():
        errors.append("missing skin.toml")
    if not css_path.is_file():
        errors.append("missing skin.css")
    meta, meta_errs = _read_meta(toml_path, folder_name=name)
    errors.extend(meta_errs)
    size_errs, files = _walk_pack_files(root)
    errors.extend(size_errs)
    if css_path.is_file():
        try:
            css = css_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            errors.append(f"skin.css: {e}")
            css = ""
        if css:
            errors.extend(_lint_css(css, name=name, root=root))
    for path in files:
        if path.suffix.lower() not in ALLOWED_EXT:
            errors.append(f"disallowed extension: {path.relative_to(root).as_posix()}")
    return errors


def install_pack(src: Path, *, dest_parent: Optional[Path] = None) -> tuple[bool, str]:
    """Copy a local pack directory into the user skins dir. No network fetch."""
    src = Path(src).expanduser()
    try:
        src = src.resolve()
    except OSError as e:
        return False, f"cannot resolve {src}: {e}"
    if not src.is_dir():
        return False, f"{src}: not a directory"
    errors = check_pack(src)
    if errors:
        return False, "check failed:\n  " + "\n  ".join(errors)
    name = src.name.lower()
    if find_pack(name) is not None and (builtin_skins_dir() / name).is_dir():
        return False, f"{name!r} is a built-in pack — choose another name"
    dest_root = Path(dest_parent) if dest_parent is not None else user_skins_dir()
    dest = dest_root / name
    if dest.exists():
        return False, f"already installed: {dest}"
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
    except OSError as e:
        return False, f"copy failed: {e}"
    return True, f"installed pack:{name} → {dest}"


def _load_pack(root: Path, *, origin: str) -> Optional[SkinPack]:
    if check_pack(root):
        return None
    meta, errs = _read_meta(root / "skin.toml", folder_name=root.name.lower())
    if errs or meta is None:
        return None
    return SkinPack(
        name=root.name.lower(),
        label=meta["label"],
        blurb=meta["blurb"],
        scheme=meta["scheme"],
        root=root.resolve(),
        origin=origin,
    )


def _read_meta(toml_path: Path, *, folder_name: str) -> tuple[Optional[dict[str, str]], list[str]]:
    if not toml_path.is_file():
        return None, []
    try:
        raw = toml_path.read_bytes()
    except OSError as e:
        return None, [f"skin.toml: {e}"]
    if len(raw) > MAX_FILE_BYTES:
        return None, [f"skin.toml exceeds {MAX_FILE_BYTES} bytes"]
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        return None, [f"skin.toml: {e}"]
    if not isinstance(data, dict):
        return None, ["skin.toml: expected a table"]
    errors: list[str] = []
    declared = str(data.get("name") or "").strip().lower()
    if declared and declared != folder_name:
        errors.append(f"skin.toml name {declared!r} does not match folder {folder_name!r}")
    label = str(data.get("label") or "").strip()
    if not label:
        errors.append("skin.toml: label is required")
    blurb = str(data.get("blurb") or "").strip()
    scheme = str(data.get("scheme") or "dark").strip().lower()
    if scheme not in _ALLOWED_SCHEME:
        errors.append("skin.toml: scheme must be 'dark' or 'light'")
        scheme = "dark"
    if errors:
        return None, errors
    return {"label": label, "blurb": blurb, "scheme": scheme}, []


def _walk_pack_files(root: Path) -> tuple[list[str], list[Path]]:
    errors: list[str] = []
    files: list[Path] = []
    total = 0
    try:
        walker = root.rglob("*")
    except OSError as e:
        return [f"{root}: {e}"], []
    for path in walker:
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        files.append(path)
        try:
            size = path.stat().st_size
        except OSError as e:
            errors.append(f"{path.relative_to(root).as_posix()}: {e}")
            continue
        if size > MAX_FILE_BYTES:
            errors.append(
                f"{path.relative_to(root).as_posix()} is {size} bytes "
                f"(cap {MAX_FILE_BYTES})"
            )
        total += size
    if total > MAX_PACK_BYTES:
        errors.append(f"pack is {total} bytes (cap {MAX_PACK_BYTES})")
    return errors, files


def _safe_file(root: Path, rel: str) -> Optional[Path]:
    rel = (rel or "").lstrip("/")
    if not rel or rel.endswith("/"):
        return None
    suffix = Path(rel).suffix.lower()
    if suffix not in ALLOWED_EXT:
        return None
    try:
        target = (root / rel).resolve()
        target.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if not target.is_file():
        return None
    try:
        if target.stat().st_size > MAX_FILE_BYTES:
            return None
    except OSError:
        return None
    return target


def _lint_css(css: str, *, name: str, root: Path) -> list[str]:
    errors: list[str] = []
    lower = css.lower()
    if "@import" in lower:
        errors.append("skin.css: @import is forbidden")
    if "javascript:" in lower or "expression(" in lower:
        errors.append("skin.css: script-like constructs are forbidden")
    stripped = _CSS_COMMENT_RE.sub("", css)
    pack_id = pack_skin_id(name)
    for prelude, body, kind in _css_blocks(stripped):
        if kind == "media":
            for inner_pre, inner_body, inner_kind in _css_blocks(body):
                if inner_kind != "rule":
                    errors.append(
                        f"skin.css: only style rules inside @media "
                        f"(got {inner_pre.strip()[:40]!r})"
                    )
                    continue
                errors.extend(_lint_selector(inner_pre, pack_id))
                errors.extend(_lint_urls(inner_body, name=name, root=root))
            continue
        if kind == "font-face":
            errors.extend(_lint_urls(body, name=name, root=root))
            continue
        if kind == "other-at":
            errors.append(f"skin.css: unsupported at-rule {prelude.strip()[:48]!r}")
            continue
        errors.extend(_lint_selector(prelude, pack_id))
        errors.extend(_lint_urls(body, name=name, root=root))
    return _unique(errors)


def _lint_selector(prelude: str, pack_id: str) -> list[str]:
    prefix_dq = f'html[data-skin="{pack_id}"]'
    prefix_sq = f"html[data-skin='{pack_id}']"
    bad: list[str] = []
    for sel in prelude.split(","):
        s = re.sub(r"\s+", " ", sel).strip()
        if not s:
            continue
        if not (s.startswith(prefix_dq) or s.startswith(prefix_sq)):
            bad.append(
                f"skin.css: selector must live under {prefix_dq} "
                f"(got {s[:80]!r})"
            )
    return bad


def _lint_urls(css: str, *, name: str, root: Path) -> list[str]:
    errors: list[str] = []
    allowed = f"/skins/{name}/"
    for m in _URL_RE.finditer(css):
        raw = m.group(2).strip()
        if not raw or raw.lower() == "none":
            continue
        path = raw.split("#", 1)[0].split("?", 1)[0]
        if not path.startswith(allowed) or ".." in path:
            errors.append(
                f"skin.css: url() must be an absolute {allowed}… path "
                f"(got {raw[:80]!r})"
            )
            continue
        rel = path[len(allowed):]
        if not rel:
            errors.append(f"skin.css: empty url() path {raw!r}")
            continue
        target = _safe_file(root, rel)
        if target is None:
            errors.append(f"skin.css: missing or refused asset {rel!r}")
    return errors


def _css_blocks(css: str) -> list[tuple[str, str, str]]:
    """Split *css* into (prelude, body, kind) at brace depth 0."""
    blocks: list[tuple[str, str, str]] = []
    i = 0
    n = len(css)
    while i < n:
        while i < n and css[i].isspace():
            i += 1
        if i >= n:
            break
        start = i
        while i < n and css[i] != "{":
            if css[i] in ("'", '"'):
                q = css[i]
                i += 1
                while i < n and css[i] != q:
                    if css[i] == "\\":
                        i += 2
                        continue
                    i += 1
            i += 1
        if i >= n:
            break
        prelude = css[start:i].strip()
        i += 1  # skip '{'
        depth = 1
        body_start = i
        while i < n and depth:
            ch = css[i]
            if ch in ("'", '"'):
                q = ch
                i += 1
                while i < n and css[i] != q:
                    if css[i] == "\\":
                        i += 2
                        continue
                    i += 1
                i += 1
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        body = css[body_start:i - 1] if depth == 0 else css[body_start:]
        kind = _prelude_kind(prelude)
        blocks.append((prelude, body, kind))
    return blocks


def _prelude_kind(prelude: str) -> str:
    p = prelude.lstrip().lower()
    if p.startswith("@media") or p.startswith("@supports"):
        return "media"
    if p.startswith("@font-face"):
        return "font-face"
    if p.startswith("@"):
        return "other-at"
    return "rule"


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
