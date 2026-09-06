"""TUI discoverability — command palette, @-pinning, tool listing (Phase 6).

Generic ``PaletteItem`` models slash-commands and agent tools so MCP can plug in
later as another source. Fuzzy matching is intentionally simple (subsequence).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

# The matcher moved to the kernel (xlii.fuzzy, godzilla-mothra V1ab); re-exported
# here so existing `from xlii.tui.discover import fuzzy_score/_best_fuzzy` call
# sites (shortcodes, tool_drawer, tests) keep working.
from xlii.fuzzy import _best_fuzzy
from xlii.fuzzy import fuzzy_score as fuzzy_score  # re-export

# Front-end-owned slash commands surfaced in the palette (mirrors tui_textual).
_FRONT_END_SLASH = (
    ("exit", "Leave the TUI"),
    ("quit", "Leave the TUI"),
    ("clear", "Clear the transcript"),
)


@dataclass(frozen=True)
class PaletteItem:
    kind: str  # "command" | "tool"
    name: str
    description: str
    action: str  # text to run or insert


@dataclass(frozen=True)
class AtSuggestion:
    label: str
    kind: str  # "file" | "url"
    value: str  # path or url


def filter_palette(items: Iterable[PaletteItem], needle: str) -> list[PaletteItem]:
    if not needle:
        return list(items)
    scored: list[tuple[int, PaletteItem]] = []
    for item in items:
        s = _best_fuzzy(needle, item.name, item.description, f"{item.kind}:{item.name}")
        if s >= 0:
            scored.append((s, item))
    scored.sort(key=lambda t: (t[0], t[1].kind, t[1].name))
    return [item for _, item in scored]


def collect_palette_items(state: Any) -> list[PaletteItem]:
    """Slash-commands for the live scope plus extended (project) agent tools."""
    from xlii.commands import _REPL_COMMANDS
    from xlii.tool_schemas import _all_tools, load_project_tools

    scope = getattr(state, "command_scope", None) or "code"
    xli_dir = getattr(getattr(state, "project", None), "xli_dir", None)
    if xli_dir is not None:
        try:
            load_project_tools(xli_dir)
        except Exception:
            # Best-effort: a broken .xlii/tools module must not break the palette.
            pass

    seen: dict[str, PaletteItem] = {}
    for name, desc in _FRONT_END_SLASH:
        seen[name] = PaletteItem("command", name, desc, f"/{name}")
    for cmd in _REPL_COMMANDS:
        if scope not in cmd.repls:
            continue
        seen.setdefault(
            cmd.name,
            PaletteItem("command", cmd.name, cmd.description or "", f"/{cmd.name}"),
        )

    items = list(seen.values())
    # P2: explicit resume picker entry (palette → EpisodePicker / numbered list).
    if scope == "code" and "session" in seen:
        items.append(
            PaletteItem(
                "command",
                "session resume",
                "Resume a stored coding episode (picker when several exist)",
                "__session_resume__",
            )
        )
    for tool in _all_tools():
        if tool.source == "builtin":
            continue
        items.append(
            PaletteItem(
                "tool",
                tool.name,
                tool.description or "project tool",
                f"?Use the {tool.name} tool to ",
            )
        )
    items.sort(key=lambda i: (i.kind, i.name))
    return items


def list_discoverable_tools(state: Any) -> list[tuple[str, str, str]]:
    """`(name, description, source)` for the tool drawer — project tools first."""
    from xlii.tool_schemas import _all_tools, load_project_tools

    xli_dir = getattr(getattr(state, "project", None), "xli_dir", None)
    if xli_dir is not None:
        try:
            load_project_tools(xli_dir)
        except Exception:
            # Best-effort: a broken .xlii/tools module must not break the palette.
            pass
    rows: list[tuple[str, str, str, int]] = []
    for tool in _all_tools():
        order = 0 if tool.source != "builtin" else 1
        rows.append((tool.name, tool.description or "", tool.source, order))
    rows.sort(key=lambda r: (r[3], r[0]))
    return [(n, d, s) for n, d, s, _ in rows]


def _iter_project_files(project_root: Path, extra_ignores: list[str], *, limit: int = 400) -> list[str]:
    from xlii.ignore import load_ignore_spec

    root = project_root.resolve()
    spec = load_ignore_spec(root, extra_ignores)
    out: list[str] = []
    try:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if spec.match_file(rel) or spec.match_file(rel + "/"):
                continue
            out.append(rel)
            if len(out) >= limit:
                break
    except OSError:
        # Best-effort file walk — unreadable trees should not crash @-completion.
        pass
    return sorted(out)


def at_suggestions(state: Any, partial: str) -> list[AtSuggestion]:
    """File and URL candidates for an ``@`` fragment at the input tail."""
    partial = partial or ""
    low = partial.lower()
    out: list[AtSuggestion] = []

    if low.startswith("url") or low.startswith("http://") or low.startswith("https://"):
        url = partial
        if low == "url":
            out.append(AtSuggestion("url:https://…", "url", "https://"))
        elif url.startswith("http"):
            out.append(AtSuggestion(url, "url", url))
        return out

    project = getattr(state, "project", None)
    root = getattr(project, "project_root", None)
    if root is None:
        return out

    extra = list(getattr(project, "extra_ignores", None) or [])
    files = _iter_project_files(Path(root), extra)
    for rel in files:
        if not partial or partial.lower() in rel.lower():
            out.append(AtSuggestion(f"@{rel}", "file", rel))
        if len(out) >= 25:
            break
    return out


def pin_at_file(state: Any, rel_path: str) -> str:
    root = Path(state.project.project_root)
    path = (root / rel_path).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return f"outside project: {rel_path}"
    if not path.is_file():
        return f"not a file: {rel_path}"
    entry = state.attach_file(str(path))
    return f"pinned {entry['name']} ({entry.get('kind', '?')}) — shared next turn"


from xlii.url_safe import safe_urlopen as _safe_urlopen
from xlii.url_safe import validate_url_host  # re-export for tests / call sites


def pin_at_url(state: Any, url: str) -> str:
    import urllib.error

    from xlii.doc import INLINE_SOFT_CAP_BYTES

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return "url must start with http:// or https://"
    hop_err = validate_url_host(url)
    if hop_err:
        return hop_err
    try:
        with _safe_urlopen(url, timeout=15) as resp:
            data = resp.read(INLINE_SOFT_CAP_BYTES + 1)
    except urllib.error.URLError as e:
        return f"fetch failed: {e.reason}"
    except OSError as e:
        return f"fetch failed: {e}"
    if len(data) > INLINE_SOFT_CAP_BYTES:
        return (
            f"url body too large (>{INLINE_SOFT_CAP_BYTES} bytes) — "
            "save locally and /locker add"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "url returned binary — save locally and /locker add"
    host = urlparse(url).netloc or "url"
    name = f"url:{host}"
    state.attach_doc(name, text)
    return f"pinned doc {name} ({len(text):,} bytes) — inlined next turn"


def resolve_at_selection(state: Any, suggestion: AtSuggestion) -> str:
    if suggestion.kind == "file":
        return pin_at_file(state, suggestion.value)
    return pin_at_url(state, suggestion.value)
