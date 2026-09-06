"""Panel-home catalog — shared by ``home://`` VFS + HomePane (Track I).

The hub lists every real panel (plus stream). Sections keep it from reading
as a dump. ``xlii://`` stays the kernel index. ``file://.`` means the live
file root (resolved at navigate time via ``file_dock_root_address``).
``stream`` is the talk feed — not a VFS scheme; the face opens it as a slot.

User words, not engine words: Attachments (on or off), Canvas (the pad).
``locker://`` / ``artifacts://`` stay the addresses.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HomeEntry:
    """One hub row: display label, slug under ``home://``, navigate target,
    a section heading, the face pane id (occupancy), and a one-line hint."""

    label: str
    slug: str
    target: str
    hint: str = ""
    section: str = ""
    pane: str = ""


# Slugs are home:// ADDRESSES and stay stable. Locker's slug is ``images``
# (historical). Stream is a slot view, not a dock scheme.
HOME_CATALOG: tuple[HomeEntry, ...] = (
    # desk — this glass
    HomeEntry("Home Stream", "stream", "stream", "the talk", "desk", "stream"),
    HomeEntry("Projects / switch", "projects", "projects://", "change folder", "desk", "projects"),
    HomeEntry("Remotes", "remote", "remote://", "other desks", "desk", "remote"),
    HomeEntry("Files", "files", "file://.", "this folder", "desk", "explorer"),
    HomeEntry("Commands", "menu", "menu://", "slash + shortcuts", "desk", "menu"),
    HomeEntry("Config / session", "config", "faceconfig://", "side, width, how it behaves", "desk", "config"),
    # now
    HomeEntry("Plans", "plan", "plan://", "the current plan", "now", "plan"),
    HomeEntry("Jobs", "jobs", "jobs://", "work running in the background", "now", "jobs"),
    HomeEntry("Gigwork", "gigwork", "gigwork://", "other models you can hire", "now", "gigwork"),
    # project
    HomeEntry("Git", "git", "git://", "what changed", "project", "git"),
    HomeEntry("Docs", "docs", "docs://", "rules it always reads", "project", "docs"),
    HomeEntry("Wiki", "wiki", "wiki://", "notes for this folder", "project", "wiki"),
    HomeEntry("Research cards", "sources", "sources://", "starting points", "project", "sources"),
    HomeEntry("Research results", "results", "results://", "saved lookups", "project", "results"),
    # saved
    HomeEntry("Artifacts", "artifacts", "artifacts://", "what it made", "saved", "artifacts"),
    HomeEntry("Canvas", "canvas", "canvas://", "the pad", "saved", "canvas"),
    HomeEntry("Attachments", "images", "locker://", "on or off", "saved", "locker"),
    HomeEntry("Bookmarks", "bookmarks", "mark://", "turns you marked", "saved", "bookmarks"),
    HomeEntry("Typed history", "history", "history://", "lines you typed — fill, don't run", "saved", "history"),
    # tools
    HomeEntry("Install node", "install", "install://", "stamp a limb", "tools", "install"),
    HomeEntry("XMPP addresses", "jids", "jidmake://", "mint JIDs", "tools", "jidmake"),
    HomeEntry("Tasks", "tasks", "tasks://", "saved pipelines", "tools", "tasks"),
    HomeEntry("Skills", "skills", "skills://", "extra abilities", "tools", "skills"),
    HomeEntry("Plugins", "plugins", "plugins://", "catalog", "tools", "plugins"),
)


def pane_id_for_address(address: str) -> str:
    """Map a navigate target / slot view to a face pane id (occupancy)."""
    raw = (address or "").strip()
    if not raw:
        return ""
    if raw == "stream" or raw.startswith("stream://"):
        return "stream"
    sch = raw.split("://", 1)[0].lower()
    aliases = {"file": "explorer", "mark": "bookmarks", "faceconfig": "config"}
    if sch in aliases:
        return aliases[sch]
    for entry in HOME_CATALOG:
        tsch = entry.target.split("://", 1)[0]
        if tsch == sch or entry.pane == sch or entry.slug == sch:
            return entry.pane
    return ""
