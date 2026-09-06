"""Session attachment by address — the inverse of a provider.

A provider turns ``scheme://target`` into something you can *browse/read*; this turns the same
address into something that *rides the turn*. Given an addressable content item and the live
``REPLState``, attach it to (or detach it from) the session so its body inlines into the next
turn's context — the exact thing ``/skill <name>`` and ``/doc <name>`` do, reached from a pane
button instead of the command line.

Dispatched by scheme, mirroring the provider registry:

* ``skills://<name>`` — the skill's steps ride the ``/doc`` channel under ``skill:`` (client #1).
* ``docs://<name>``   — the doc's body rides the ``/doc`` channel directly.
* ``mark://<name>``   — the mark's recall span rides under ``mark:``.
* ``wiki://<name>``   — the page rides under ``wiki:``, carrying its provenance and (when
  unverified) a loud verify-before-trust banner.
* ``ftp://<name>/<path>`` (and every remote scheme — ``sftp://`` ``dav://`` ``smb://``) — the
  **live remote file** rides under ``ftp:``, so a turn can discuss/fix the actually-deployed
  page (text files only).

``locker://`` detach drops the file; new schemes slot in the same way (each a small ``elif``). Everything is
duck-typed over ``state`` and defensive: an unknown scheme / missing item / stateless context
returns ``False`` rather than raising, so a button never crashes the surface.

The three verbs — :func:`attach_address`, :func:`detach_address`, :func:`is_attached` — are what
the Dock's :class:`~xlii.panes.SessionSink` calls and what a pane reads to green-dot the rows that
are already riding.
"""

from __future__ import annotations

from typing import Any

from xlii.addressing import Address

# A mark's recall span rides the /doc channel under this prefix (distinct from a real doc name
# and from the skill:/wiki: prefixes), so attaching a mark and a same-named doc don't collide.
# Reinstated by campaign Decision #1 (bookmarks = BOTH verbs): attach rides the span on the next
# turn like a skill; recall-paste stays the review-before-run path via /recall.
MARK_ATTACH_PREFIX = "mark:"

# A wiki page rides the /doc channel under this prefix (so a page and a same-named doc don't collide).
WIKI_ATTACH_PREFIX = "wiki:"
XWIKI_ATTACH_PREFIX = "xwiki:"  # the vendor tier's channel — distinct so detach stays symmetric

# A live remote file (any remote scheme — ftp:// sftp:// dav:// smb://) rides the /doc channel
# under this prefix — the payoff of the remote-fs provider: "fix the nav on my site" reads the
# *actually-deployed* page. The prefix keys by addr.target (name/path, scheme-less), so the same
# remote file attached via any scheme is one rider; the historical "ftp:" spelling is kept —
# changing it would orphan attachments saved before the multi-backend round.
FTP_ATTACH_PREFIX = "ftp:"

# Every scheme that names a remote connection (one source of truth in remotefs).
from xlii.remotefs import REMOTE_SCHEMES as _REMOTE_SCHEMES  # noqa: E402

# Schemes with an attach handler below — the panes gate their attach/detach buttons on this.
_ATTACHABLE_SCHEMES = frozenset({"skills", "docs", "mark", "wiki", "xwiki"} | set(_REMOTE_SCHEMES))


def attachable(address: "str | Address") -> bool:
    """Whether ``address``'s scheme can be attached to the session (gates the attach/detach buttons
    + a/d keys on a pane's leaf). Unknown schemes → False."""
    return _addr(address).scheme in _ATTACHABLE_SCHEMES


# Leaf schemes that resolve to a local file the locker can stage. Face F4 /
# feed-view "Focus" rides these as ``attach_file(..., once=True)`` — next turn
# only, not a rewind.
_FOCUS_FILE_SCHEMES = frozenset({"file", "project", "artifacts", "canvas", "locker"})


def _is_unbound_desk(state: Any) -> bool:
    """Scratch / project-less desk — no bound tree, so a previewed file is fair game.

    Bound code projects keep the path jail. Scratch is roam: the user already
    pointed at the leaf by viewing it; Focus just pins that path for one turn.
    """
    if bool(getattr(state, "scratch", False)):
        return True
    name = getattr(getattr(state, "project", None), "name", "") or ""
    return str(name).startswith("scratch/")


def _path_in_scope(state: Any, path) -> bool:
    """True when *path* is under the project root or ``.xlii/`` (uploads, artifacts)."""
    from pathlib import Path

    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return False
    root = _project_root(state)
    if root is None:
        return False
    try:
        root = Path(root).resolve()
    except OSError:
        return False
    if resolved == root or resolved.is_relative_to(root):
        return True
    xli = getattr(getattr(state, "project", None), "xli_dir", None)
    if xli:
        try:
            xd = Path(xli).resolve()
        except OSError:
            xd = None
        if xd is not None and (resolved == xd or resolved.is_relative_to(xd)):
            return True
    return False


def _local_file_path(address: "str | Address"):
    """Resolve a file-like address to an existing local Path, or None."""
    from pathlib import Path

    from xlii.addressing import resolve

    addr = _addr(address)
    if addr.scheme not in _FOCUS_FILE_SCHEMES:
        return None
    try:
        res = resolve(addr)
    except Exception:
        res = None
    candidates = []
    if res is not None:
        p = getattr(res, "path", None)
        if p is not None:
            candidates.append(Path(p))
    if addr.scheme == "file" and addr.target:
        candidates.append(Path(addr.target).expanduser())
    try:
        from xlii.addressing import vfs_stat

        stat = vfs_stat(addr)
    except Exception:
        stat = None  # VFS metadata is optional — ignore lookup failures
    extra = getattr(stat, "extra", None) or {}
    extra_path = extra.get("path")
    if extra_path:
        candidates.append(Path(str(extra_path)))
    for cand in candidates:
        try:
            rp = cand.resolve()
        except OSError:
            rp = cand
        if rp.is_file():
            return rp
    return None


def focus_address(state: Any, address: "str | Address", *, once: bool = True) -> dict | None:
    """Pin *address* as next-turn context. Not a rewind — a reference.

    Files (``file://``, project leaves, artifacts, locker, canvas) stage into
    the locker. ``once=True`` (F4) rides the next turn and drops; canvas work
    uses ``once=False`` so it stays held until the work changes. Skills / docs
    / wiki / marks / remotes use :func:`attach_address` (session riders).

    Returns a small status dict, or ``None`` if the address cannot be focused.
    """
    if state is None:
        return None
    addr = _addr(address)
    raw = str(address) if not isinstance(address, str) else address

    if addr.scheme in _FOCUS_FILE_SCHEMES:
        path = _local_file_path(addr)
        if path is None or not path.is_file():
            return None
        if not _is_unbound_desk(state) and not _path_in_scope(state, path):
            return None
        fn = getattr(state, "attach_file", None)
        if not callable(fn):
            return None
        entry = None
        try:
            entry = fn(str(path), once=once)
        except TypeError:
            entry = fn(str(path))
        if isinstance(entry, dict) and addr.scheme == "canvas":
            entry["role"] = "canvas"
        item = {
            "address": raw,
            "title": path.name,
            "kind": "image" if path.suffix.lower() in {
                ".png", ".jpg", ".jpeg", ".gif", ".webp",
            } else "file",
            "path": str(path),
            "once": bool(once),
            "role": "canvas" if addr.scheme == "canvas" else "",
        }
        _remember_focus(state, item)
        return item

    if attachable(addr) and attach_address(state, addr):
        title = (addr.key or addr.target or raw).strip() or raw
        item = {
            "address": raw,
            "title": title,
            "kind": addr.scheme,
            "path": "",
            "once": False,
        }
        _remember_focus(state, item)
        return item
    return None


def _remember_focus(state: Any, item: dict) -> None:
    """Last Focus pin — ``/send focus`` and ``/send last`` read this."""
    try:
        state.last_focus = dict(item)
    except Exception:
        # Best-effort cache only: failures here must not interrupt focus/attach flow.
        pass


def unfocus_address(state: Any, item: dict) -> bool:
    """Drop a previously focused item. Once-files disable; riders detach."""
    if state is None or not item:
        return False
    path = str(item.get("path") or "")
    if path and callable(getattr(state, "set_file_enabled", None)):
        return bool(state.set_file_enabled(path, False))
    address = item.get("address")
    if address:
        return bool(detach_address(state, address))
    return False


def _addr(address: "str | Address") -> Address:
    return address if isinstance(address, Address) else Address.parse(address)


def _project_root(state: Any):
    root = getattr(getattr(state, "project", None), "project_root", None)
    if not root:
        return None
    from pathlib import Path

    return Path(str(root))


def _mark_bare_name(key: str) -> str:
    """The /doc-channel name for a mark address key. ``<persona>:<mark>`` carries no persona
    baggage into the context — but only when the left side is a REAL persona, so a bare mark
    named "ratio 3:1" stays whole (the resolve_mark_global rule)."""
    if ":" in key:
        left, _, right = key.partition(":")
        left, right = left.strip(), right.strip()
        if left and right:
            from xlii.persona import Persona, is_valid_name

            if is_valid_name(left) and Persona(left).exists():
                return right
    return key.strip()


def _resolve_mark_store(state: Any, key: str):
    """Resolve a mark key against the GLOBAL bookmark namespace — the headless twin of
    ``repl_cmds/chat.resolve_mark_global``'s rule (that one prints; this returns ``None``):

    * ``<persona>:<mark>`` — pin that one persona's store;
    * ``<mark>`` — searched across the active session's store + every persona's; the
      unique match wins, zero or several matches return ``None`` (qualify instead).

    Returns ``(turns_dir, bare_mark)`` or ``None``.
    """
    from xlii.persona import Persona, is_valid_name, list_personas
    from xlii.transcript import list_marks

    if ":" in key:
        left, _, right = key.partition(":")
        left, right = left.strip(), right.strip()
        if left and right and is_valid_name(left) and Persona(left).exists():
            td = Persona(left).turns_dir
            return (td, right) if right in {n for n, _ in list_marks(td)} else None
        # Not a persona qualifier — fall through to bare-name resolution of the whole key.

    name = key.strip()
    if not name:
        return None
    stores = [p.turns_dir for p in list_personas()]
    mem = getattr(getattr(state, "profile", None), "memory", None)
    active_td = getattr(mem, "turns_dir", None) or getattr(
        getattr(state, "persona", None), "turns_dir", None)
    if active_td is not None and all(str(active_td) != str(td) for td in stores):
        stores.append(active_td)
    matches = [td for td in stores if name in {n for n, _ in list_marks(td)}]
    if len(matches) != 1:
        return None
    return matches[0], name


def attach_address(state: Any, address: "str | Address") -> bool:
    """Attach ``address`` to ``state`` so it rides the next turn. Returns ``True`` on success,
    ``False`` for an unknown scheme / missing item / non-session context. Idempotent — re-attaching
    an already-riding item just refreshes its body."""
    if state is None or not hasattr(state, "attach_doc"):
        return False
    addr = _addr(address)

    if addr.scheme == "skills":
        from xlii.skills import SKILL_ATTACH_PREFIX, load_skills, render_skill

        name = addr.key.strip()
        sk = load_skills(_project_root(state)).get(name)
        if sk is None:
            return False
        state.attach_doc(SKILL_ATTACH_PREFIX + sk.name, render_skill(sk))
        return True

    if addr.scheme == "docs":
        from xlii.doc import Doc

        name = addr.key.strip()
        d = Doc(name)
        if not d.exists():
            return False
        try:
            state.attach_doc(name, d.read())
        except OSError:
            return False
        return True

    if addr.scheme == "mark":
        # The mark's recall span rides as a `mark:` doc (Decision #1: bookmarks = both verbs).
        # Resolved against the GLOBAL namespace — the pane lists every persona's marks.
        from xlii.transcript import get_marked_span

        resolved = _resolve_mark_store(state, addr.key.strip())
        if resolved is None:
            return False
        turns_dir, name = resolved
        span = get_marked_span(turns_dir, name)
        if not span:
            return False
        from xlii.addressing.builtins.mark import _render_mark_span

        state.attach_doc(MARK_ATTACH_PREFIX + name, _render_mark_span(name, span))
        return True

    if addr.scheme == "xwiki":
        from xlii.selfwiki import selfwiki_root
        from xlii.wiki import page_exists, read_page

        name = addr.key.strip()
        d = selfwiki_root()
        if d is None or not page_exists(d, name):
            return False
        try:
            page = read_page(d, name)
        except OSError:
            return False
        # Vendor tier: shipped truth, version-locked — no trust warning (the ✓/?
        # ladder is the project tier's), but provenance still rides.
        header = ["> xlii's shipped self-doc (xwiki — read-only, matches this build)."]
        if page.sources:
            header.append("> sources: " + ", ".join(page.sources))
        state.attach_doc(XWIKI_ATTACH_PREFIX + name, "\n".join(header) + "\n\n" + page.body)
        return True

    if addr.scheme == "wiki":
        from xlii.active_session import xli_dir_of
        from xlii.wiki import page_exists, read_page

        name = addr.key.strip()
        d = xli_dir_of(state)
        if d is None or not page_exists(d, name):
            return False
        try:
            page = read_page(d, name)
        except OSError:
            return False
        # The verify-before-trust flag travels WITH the attachment: an unverified page rides
        # wearing its warning, and provenance rides alongside so claims stay checkable.
        header = []
        if not page.verified:
            header.append("> ⚠ UNVERIFIED wiki page — check claims against its sources "
                          "before trusting them as fact.")
        if page.sources:
            header.append("> sources: " + ", ".join(page.sources))
        body = ("\n".join(header) + "\n\n" if header else "") + page.body
        state.attach_doc(WIKI_ATTACH_PREFIX + name, body)
        return True

    if addr.scheme in _REMOTE_SCHEMES:
        from xlii.addressing import vfs_read

        if not addr.key.strip() or not addr.subpath:
            return False  # the server picker / a host home isn't a leaf
        try:
            data = vfs_read(addr)
        except (OSError, RuntimeError, NotImplementedError):
            return False
        try:
            body = data.decode("utf-8")
        except UnicodeDecodeError:
            return False  # binary remote files don't ride the /doc channel
        # Keyed by the full target (name/path) so two hosts' index.html don't collide.
        state.attach_doc(FTP_ATTACH_PREFIX + addr.target, body)
        return True

    return False


def detach_address(state: Any, address: "str | Address") -> bool:
    """Detach ``address`` from ``state`` (the inverse of :func:`attach_address`). Returns ``True``
    if something was removed, ``False`` if it wasn't attached / unknown scheme / no session."""
    if state is None or not hasattr(state, "detach_doc"):
        return False
    addr = _addr(address)

    if addr.scheme == "skills":
        from xlii.skills import SKILL_ATTACH_PREFIX

        return bool(state.detach_doc(SKILL_ATTACH_PREFIX + addr.key.strip()))

    if addr.scheme == "docs":
        return bool(state.detach_doc(addr.key.strip()))

    if addr.scheme == "mark":
        return bool(state.detach_doc(MARK_ATTACH_PREFIX + _mark_bare_name(addr.key)))

    if addr.scheme == "xwiki":
        return bool(state.detach_doc(XWIKI_ATTACH_PREFIX + addr.key.strip()))

    if addr.scheme == "wiki":
        return bool(state.detach_doc(WIKI_ATTACH_PREFIX + addr.key.strip()))

    if addr.scheme in _REMOTE_SCHEMES:
        return bool(state.detach_doc(FTP_ATTACH_PREFIX + addr.target))

    if addr.scheme == "locker" and hasattr(state, "remove_file"):
        return bool(state.remove_file(addr.key.strip()))  # drop the file from the locker entirely

    return False


def is_attached(state: Any, address: "str | Address") -> bool:
    """Whether ``address`` is currently riding ``state`` — for the green-dot rider indicator and to
    gate the attach/detach buttons."""
    if state is None:
        return False
    addr = _addr(address)
    attached = getattr(state, "attached_docs", None) or []

    if addr.scheme == "skills":
        from xlii.skills import active_skill_names

        return addr.key.strip() in active_skill_names(attached)

    if addr.scheme == "docs":
        return any(n == addr.key.strip() for n, _ in attached)

    if addr.scheme == "mark":
        return any(n == MARK_ATTACH_PREFIX + _mark_bare_name(addr.key) for n, _ in attached)

    if addr.scheme == "xwiki":
        return any(n == XWIKI_ATTACH_PREFIX + addr.key.strip() for n, _ in attached)

    if addr.scheme == "wiki":
        return any(n == WIKI_ATTACH_PREFIX + addr.key.strip() for n, _ in attached)

    if addr.scheme in _REMOTE_SCHEMES:
        return any(n == FTP_ATTACH_PREFIX + addr.target for n, _ in attached)

    if addr.scheme == "locker":
        files = getattr(state, "attached_files", None) or []
        return any(isinstance(e, dict) and e.get("name") == addr.key.strip() for e in files)

    return False
