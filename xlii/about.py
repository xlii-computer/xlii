"""About + rotating tagline — Help menu credits.

Contact line + a day-rotating tagline so the About pane is not a tombstone.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional

from rich.text import Text

PRODUCT = "xlii"
CONTACT = "Say hello — hello@xlii.computer"

# Dry, short, product-voice. Index is day-of-year so the line moves
# without being random-on-every-open.
TAGLINES: tuple[str, ...] = (
    "the answer, at the command line",
    "the rider, not the chauffeur",
    "scratch is home",
    "islands, then bridges",
    "talk first, then the lab",
    "one Home. many folders.",
    "make the box, don't live in theirs",
    "forget is a verb",
    "curate first. remotes second.",
    "two slots. no mirrors.",
    "keep your mojo",
)


def tagline(*, when: Optional[date] = None) -> str:
    """Today's line. ``when`` is for tests."""
    day = when or date.today()
    idx = int(day.strftime("%j")) - 1
    return TAGLINES[idx % len(TAGLINES)]


_MODULE_N: Optional[int] = None


def _module_count() -> int:
    """Python files under ``xlii/`` (the body, not the tests). Cached."""
    global _MODULE_N
    if _MODULE_N is not None:
        return _MODULE_N
    root = Path(__file__).resolve().parent
    _MODULE_N = sum(
        1 for p in root.rglob("*.py") if "__pycache__" not in p.parts
    )
    return _MODULE_N


def about_receipt(*, version: Optional[str] = None) -> dict:
    """Version receipt: stamp, meaning, counts, both zodiacs."""
    from xlii import __version__, version_season_line, version_stamp

    v = (version or "").strip() or __version__
    stamp = version_stamp(v)
    born = ""
    channel = ""
    tests = 0
    facts: list[str] = []
    if stamp is not None:
        major, day, tests = stamp
        born = f"{day.day} {day.strftime('%b')} {day.year}"
        channel = "alpha" if major == 0 else "public"
        facts.append(f"release date {born}")
        facts.append(f"{tests} tests green")
    mods = _module_count()
    if mods:
        facts.append(f"{mods} modules")
    # Version + channel share one line in About (Face + CLI).
    version_line = f"{v} · {channel}" if channel else v
    return {
        "version": version_line,
        "facts": facts,
        "signs": version_season_line(v),
        "tests": tests,
        "modules": mods,
        "born": born,
        "channel": channel,
    }


def about_lines(*, when: Optional[date] = None) -> list[str]:
    receipt = about_receipt()
    lines = [
        f"{PRODUCT} — a personal AI substrate",
        CONTACT,
    ]
    if receipt.get("version"):
        lines.append(str(receipt["version"]))
    for item in receipt.get("facts") or []:
        if item:
            lines.append(str(item))
    if receipt.get("signs"):
        lines.append(str(receipt["signs"]))
    lines.append(tagline(when=when))
    return lines


def about_plain(*, when: Optional[date] = None) -> str:
    return "\n".join(about_lines(when=when))


def about_renderable(*, when: Optional[date] = None) -> Text:
    lines = about_lines(when=when)
    t = Text()
    if not lines:
        return t
    t.append(lines[0], style="bold")
    if len(lines) > 1:
        t.append("\n")
        t.append(lines[1], style="dim")
    for line in lines[2:-1]:
        t.append("\n")
        t.append(line, style="cyan")
    if len(lines) > 2:
        t.append("\n")
        t.append(lines[-1], style="italic")
    return t


def howto_menu_rows() -> list[dict[str, str]]:
    """Help-bar rows: short ``menu`` label + ``group``, skip the index shard.

    Group order is start → work → more (then anything else). The long
    ``title`` stays on the topic page; the bar never paints it.
    """
    try:
        from xlii.help_corpus import HELP_MENU_GROUPS, load_manifest

        manifest = load_manifest()
    except Exception:
        return [dict(row) for row in _FALLBACK_HOWTO]
    raw: list[dict[str, str]] = []
    for tid, topic in manifest.topics.items():
        if tid == "index":
            continue
        menu = (topic.menu or _short_title(topic.title) or tid).strip()
        raw.append({
            "id": tid,
            "title": topic.title,
            "menu": menu,
            "group": topic.group or "",
        })
    if not raw:
        return [dict(row) for row in _FALLBACK_HOWTO]
    rank = {name: i for i, name in enumerate(HELP_MENU_GROUPS)}
    raw.sort(key=lambda r: (rank.get(r["group"], 99), r["menu"].lower()))
    return raw


def _short_title(title: str) -> str:
    text = (title or "").strip()
    for sep in (" — ", " – ", ": ", " - "):
        if sep in text:
            return text.split(sep, 1)[0].strip()
    return text


def about_payload(*, when: Optional[datetime] = None) -> dict:
    """Face chrome slice: current line + the full rotating set + receipt."""
    day = (when.date() if isinstance(when, datetime) else when) or date.today()
    receipt = about_receipt()
    return {
        "line": tagline(when=day),
        "taglines": list(TAGLINES),
        "credit": CONTACT,
        "product": PRODUCT,
        "version": receipt["version"],
        "facts": receipt["facts"],
        "signs": receipt["signs"],
    }


_FALLBACK_HOWTO: tuple[dict[str, str], ...] = (
    {"id": "install", "title": "Install & first-time setup", "menu": "Install", "group": "start"},
    {"id": "first-session", "title": "First session", "menu": "First session", "group": "start"},
    {"id": "help-system", "title": "Getting help", "menu": "Getting help", "group": "start"},
    {"id": "troubleshoot", "title": "Troubleshooting", "menu": "Troubleshoot", "group": "start"},
    {"id": "workflows", "title": "Workflow recipes", "menu": "Workflows", "group": "start"},
    {"id": "modes", "title": "Modes & gates", "menu": "Modes", "group": "work"},
    {"id": "knowledge", "title": "Knowledge & context", "menu": "Knowledge", "group": "work"},
    {"id": "plugins", "title": "Plugins", "menu": "Plugins", "group": "more"},
    {"id": "sessions-projects", "title": "Sessions & projects", "menu": "Sessions", "group": "more"},
)
