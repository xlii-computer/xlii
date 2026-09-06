"""Where a desk's Files mount lives — local tree or a remote VFS address.

A project is always a **local** ``.xlii/`` (journal, turns, config). ``files_root``
is an optional VFS address (``sftp://…``) that Files / Focus / ask use instead
of ``project_root``. The shell stays on the local stub. That is the pointer
desk: Switch lands Files on the remote tree; bash does not follow.

Write policy (locked):

* ``project_root`` is never a remote path. A node cannot make the throne tree
  *be* this project's root.
* ``files_root`` is a view of **that body's** tree (browse / Focus / ask).
  It is not a tunnel that writes the throne from the couch.
* Agent file tools follow the mount when it is set: ``list_dir`` / ``read_file``
  always, and ``write_file`` / ``edit_file`` for ``kind=code`` (published apps).
  Local ``.xlii/`` metadata and plan-mode write domains stay on the stub.
* Files browse is **not** ``cd``. The input line's cwd does not follow the
  explorer, and ``!`` on a pointer desk still runs on the local stub. Walking
  the listing up stops at the Files root (same honesty as the yellow
  *outside project* warning, without mixing look into mouth).
* Stub inventory (``ls`` / ``find`` / ``tree`` / ``du`` of ``.`` or
  ``project_root``) is refused so the agent cannot conclude the app is missing.
* The only ambient way a node updates a throne copy is the project's ``repo``
  (git, later sync). Live write of the throne is remote-control of an **open
  throne session** — not this module.

Adopt folder / collection accept a remote address: mint a local stub under
``~/.xlii/links/``, register it, set ``files_root``. Same two kinds. No third
"link project" noun.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any, Optional

REMOTE_FILE_SCHEMES = frozenset({"sftp", "ftp", "ftps", "dav", "smb"})
_SEP = "://"
_LINKS_PART = "/.xlii/links/"
# ``xlii make`` publishes under ``srv/apps/<name>``; stub slugs encode that as
# ``sftp-<conn>-srv-apps-<name>``. Decode that shape first — replacing every
# hyphen would smash ``fuel.xlii-code.com``.
_MAKE_SLUG = "srv-apps-"


def is_remote_files_address(raw: str) -> bool:
    """True when *raw* is a remote VFS address Files can mount."""
    text = (raw or "").strip()
    if _SEP not in text:
        return False
    scheme = text.split(_SEP, 1)[0].lower()
    return scheme in REMOTE_FILE_SCHEMES


def normalize_files_root(raw: str) -> str:
    """Canonical ``scheme://target`` or ``""`` if unusable."""
    text = (raw or "").strip()
    if not is_remote_files_address(text):
        return ""
    try:
        from xlii.addressing import Address

        addr = Address.parse(text)
    except Exception:
        return ""
    if addr.scheme.lower() not in REMOTE_FILE_SCHEMES or not (addr.target or "").strip():
        return ""
    return f"{addr.scheme.lower()}://{addr.target.rstrip('/')}"


def files_root_of(project: Any) -> str:
    """The persisted pointer, or ``""`` when Files is the local tree."""
    raw = getattr(project, "files_root", None) or ""
    return normalize_files_root(str(raw))


def sftp_files_address(conn: str, remote_path: str) -> str:
    """``sftp://<conn>/<rel>`` or ``sftp://<conn>//<abs>`` for a node path.

    SFTP listings are relative to the login home. A leading slash must survive
    as a double-slash in the address (``sftp://box//home/u/app``), otherwise
    ``sftp://box/home/u/app`` looks for ``~/home/u/app`` and misses.
    """
    name = (conn or "").strip()
    path = (remote_path or "").strip()
    if not name or not path or path == "/":
        return ""
    if path.startswith("/"):
        return normalize_files_root(f"sftp://{name}/{path}")
    return normalize_files_root(f"sftp://{name}/{path}")


def is_pointer_stub_path(raw: str) -> bool:
    """True when *raw* is an adopted-remote stub (``~/.xlii/links/<slug>``)."""
    text = (raw or "").replace("\\", "/")
    return _LINKS_PART in (text if text.startswith("/") else f"/{text}")


def pointer_stub_slug(remote_path: str) -> str:
    """The ``sftp-<conn>-…`` folder name, or ``""``."""
    text = (remote_path or "").replace("\\", "/").rstrip("/")
    i = text.find(_LINKS_PART)
    if i < 0:
        return ""
    return text[i + len(_LINKS_PART) :].strip("/")


def decode_stub_slug(slug: str) -> str:
    """Reverse :func:`stub_root_for_remote` for the published-app shape.

    ``sftp-appbox-srv-apps-fuel.xlii-code.com`` →
    ``sftp://appbox/srv/apps/fuel.xlii-code.com``. Other slugs fall back to
    hyphen-as-slash, which is lossy but better than listing the empty stub.
    """
    text = (slug or "").strip().strip("/")
    if not text:
        return ""
    scheme = ""
    rest = text
    for name in ("sftp", "ftps", "ftp", "dav", "smb"):
        prefix = f"{name}-"
        if text.startswith(prefix):
            scheme = name
            rest = text[len(prefix) :]
            break
    if not scheme:
        return ""
    host, sep, tail = rest.partition("-")
    if not host:
        return ""
    if not sep:
        return normalize_files_root(f"{scheme}://{host}")
    if tail.startswith(_MAKE_SLUG):
        folder = tail[len(_MAKE_SLUG) :]
        if folder:
            return normalize_files_root(f"{scheme}://{host}/srv/apps/{folder}")
    return normalize_files_root(f"{scheme}://{host}/{tail.replace('-', '/')}")


def fabric_conn_name(node: str) -> str:
    """ftp_connections name that reaches *node*, or *node* itself."""
    name = (node or "").strip()
    if not name:
        return ""
    try:
        from xlii.config import GlobalConfig

        spec = (GlobalConfig.load().fabric_nodes or {}).get(name) or {}
        remote = (spec.get("remote") or "").strip()
        if remote:
            return remote
    except Exception:
        # Documented fallback: an unreadable config means the node name is
        # already the connection name.
        pass
    return name


def fabric_node_of(project: Any) -> str:
    """Roster body this pointer belongs to, from the object or the registry."""
    node = (getattr(project, "node", None) or "").strip()
    rpath = (getattr(project, "remote_path", None) or "").strip()
    root = getattr(project, "project_root", None)
    if (not node or not rpath) and root is not None:
        try:
            from xlii.registry import Registry

            entry = Registry.load().find_by_path(root)
        except Exception:
            entry = None
        if entry is not None:
            node = node or (getattr(entry, "node", "") or "").strip()
            rpath = rpath or (getattr(entry, "remote_path", "") or "").strip()
    if not node and rpath:
        # Still try node from registry-only callers.
        pass
    return node


def _pointer_coords(project: Any) -> tuple[str, str]:
    node = (getattr(project, "node", None) or "").strip()
    rpath = (getattr(project, "remote_path", None) or "").strip()
    root = getattr(project, "project_root", None)
    if (not node or not rpath) and root is not None:
        try:
            from xlii.registry import Registry

            entry = Registry.load().find_by_path(root)
        except Exception:
            entry = None
        if entry is not None:
            node = node or (getattr(entry, "node", "") or "").strip()
            rpath = rpath or (getattr(entry, "remote_path", "") or "").strip()
    return node, rpath


def reachable_files_address(addr: str, project: Any = None) -> str:
    """Wrap an unknown remote in ``via://<node>/…`` so Files hops that body.

    A node announcing ``sftp://appbox/…`` does not mint ``appbox`` here (the
    address book stays this desk's). If this box already has the connection,
    the inner address is used directly. If not, the fabric node that adopted
    it still can — ``via://`` execs the node's VFS.
    """
    text = (addr or "").strip()
    if not text:
        return ""
    if text.startswith("via://"):
        return text
    try:
        from xlii.addressing import Address
        from xlii.address_book import is_known_connection

        parsed = Address.parse(text)
    except Exception:
        return text
    if parsed.scheme.lower() not in REMOTE_FILE_SCHEMES:
        return text
    if is_known_connection(parsed.key):
        return text
    hop = fabric_conn_name(fabric_node_of(project) if project is not None else "")
    if hop and hop != parsed.key and is_known_connection(hop):
        return f"via://{hop}/{text}"
    return text


_INVENTORY_CMDS = frozenset({"ls", "find", "tree", "du"})
_REMOTE_SHELL_TOOLS = frozenset({"sftp", "scp", "ssh", "rsync", "lftp"})
_WRAPPER_CMDS = frozenset({"sudo", "command", "time", "nice", "nohup", "env"})
_INVENTORY_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")


def writes_follow_files_mount(project: Any) -> bool:
    """True when agent write/edit of app files should hit the Files mount.

    Pointer + ``kind=code`` (published apps, unstamped labs). Collections stay
    local. ``project_root`` is never rewritten to the remote address.
    """
    if project is None:
        return False
    try:
        from xlii.config import PROJECT_KIND_CODE, project_kind

        if project_kind(project) != PROJECT_KIND_CODE:
            return False
    except Exception:
        return False
    return bool(files_mount_address(project))


def is_xlii_relpath(relpath: str) -> bool:
    """True when *relpath* names local ``.xlii/`` metadata, not the app tree."""
    parts = Path((relpath or "").replace("\\", "/")).parts
    return ".xlii" in parts


def files_mount_prompt_addendum(project: Any) -> str:
    """``[FILES]`` block for pointer desks, or ``""`` when Files is local."""
    try:
        mount = files_mount_address(project)
    except Exception:
        return ""
    if not mount:
        return ""
    return (
        f"[FILES] This desk's program lives at {mount}. "
        "That remote Files mount is the application tree "
        "(HTML, JS, CSS, backend — whatever the published app is). "
        "The local project folder is only a stub (.xlii metadata). "
        "It is NOT the codebase. An empty stub listing does not mean "
        "there is no application code — never rebuild from scratch on that basis. "
        "Inspect and edit the app with list_dir / read_file / glob / grep "
        "(and write_file / edit_file for kind=code). "
        "Do not use bash ls/find/tree/du on the project root or `.` to inventory "
        "the program; those stay on the stub and will look empty. "
        "Bash is for intentional remote ops (sftp, publish scripts), not file inspection."
    )


def is_stub_inventory_command(command: str, project_root: Any = None) -> bool:
    """True when *command* is ls/find/tree/du of `.` or *project_root*.

    Remote tooling (sftp/ssh/scp/rsync) and non-inventory commands are left
    alone — publish scripts and explicit paths like ``.xlii`` still run.
    """
    text = (command or "").strip()
    if not text:
        return False
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()
    names = {t.rsplit("/", 1)[-1] for t in tokens if t and not t.startswith("-")}
    if names & _REMOTE_SHELL_TOOLS:
        return False
    parts = [p for p in _INVENTORY_SPLIT.split(text) if p.strip()]
    return bool(parts) and _part_is_stub_inventory(parts[0], project_root)


def _part_is_stub_inventory(part: str, project_root: Any) -> bool:
    try:
        tokens = shlex.split(part)
    except ValueError:
        tokens = part.split()
    while tokens and (
        re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0])
        or tokens[0].rsplit("/", 1)[-1] in _WRAPPER_CMDS
    ):
        tokens = tokens[1:]
    if not tokens:
        return False
    cmd = tokens[0].rsplit("/", 1)[-1]
    if cmd not in _INVENTORY_CMDS:
        return False
    operands = [t for t in tokens[1:] if t != "--" and not t.startswith("-")]
    if not operands:
        return True
    return any(_is_stub_inventory_path(op, project_root) for op in operands)


def _is_stub_inventory_path(token: str, project_root: Any) -> bool:
    if token in (".", "./", ".\\"):
        return True
    if project_root is None:
        return False
    try:
        p = Path(token).expanduser()
        root = Path(str(project_root)).expanduser()
        try:
            p = p.resolve()
            root = root.resolve()
        except OSError:
            # resolve() can fail on missing/broken paths — compare expanduser()
            # results instead of treating the token as non-root.
            pass
        return p == root
    except Exception:
        return False


def stub_inventory_refusal(mount: str) -> str:
    """Model-facing error when bash inventories the empty stub."""
    where = (mount or "").strip() or "the Files mount"
    return (
        f"bash refused: this desk's program lives on the Files mount ({where}). "
        "The local folder is only a stub (.xlii metadata) — "
        "ls/find/tree/du of the project root will look empty and is not the codebase. "
        'Use list_dir(path=".") or read_file on the Files mount to inspect the app.'
    )


def files_mount_address(project: Any) -> str:
    """Remote Files mount for tools and sync, or ``""`` if this desk is local.

    Unlike :func:`files_address`, this never falls through to ``file://`` —
    callers that need a VFS mount get a remote address or nothing.
    """
    if project is None:
        return ""
    pointed = files_root_of(project)
    if pointed and not is_pointer_stub_path(pointed):
        return reachable_files_address(pointed, project)
    inferred = infer_fabric_files_address(project)
    if inferred and inferred != pointed and not is_pointer_stub_path(inferred):
        _node, rpath = _pointer_coords(project)
        _heal_pointer_card(project, inferred, code=is_pointer_stub_path(rpath))
        pointed = inferred
    elif inferred:
        pointed = inferred
    return reachable_files_address(pointed, project) if pointed else ""


def infer_fabric_files_address(project: Any) -> str:
    """Where the program actually lives for a fabric pointer.

    A node tree is ``sftp://<conn>//<abs-path>``. A node that itself only
    holds an adopted-remote stub (``~/.xlii/links/sftp-appbox-…``) is not the
    program — Files must follow the inner ``sftp://appbox/srv/apps/…``.
    """
    node, rpath = _pointer_coords(project)
    if not node or not rpath:
        return ""
    if is_pointer_stub_path(rpath):
        inner = decode_stub_slug(pointer_stub_slug(rpath))
        if inner:
            return inner
    conn = fabric_conn_name(node) or node
    return sftp_files_address(conn, rpath)


def _heal_pointer_card(project: Any, address: str, *, code: bool = False) -> None:
    """Persist the inner Files mount (and code kind for published apps)."""
    if not address or address.startswith("via://"):
        return
    try:
        from xlii.config import PROJECT_KIND_CODE, normalize_project_kind

        if files_root_of(project) != address:
            bind_files_root(project, address)
        if code and normalize_project_kind(getattr(project, "kind", None)) != PROJECT_KIND_CODE:
            # Published-app stubs are labs, not piles. Absorb used to drop kind.
            project.kind = PROJECT_KIND_CODE
            save = getattr(project, "save", None)
            if callable(save):
                save()
    except Exception:
        # Healing is opportunistic -- a card that can't be rebound is left as-is rather than failing the caller.
        pass


def repo_of(project: Any) -> str:
    return (getattr(project, "repo", None) or "").strip()


def files_address(project: Any = None, *, shell_cwd: Any = None) -> str:
    """Address Files should mount for this desk.

    Pointer wins (hopped when this box cannot reach the named connection).
    Else a fabric node's remote_path via the roster SFTP.
    Else live ``shell_cwd`` when it is a real local dir, else
    ``file://project_root``, else process cwd. Shell cwd never overrides a
    pointer — bash stays local; Files does not follow it onto the stub.
    """
    import os

    mount = files_mount_address(project) if project is not None else ""
    if mount:
        return mount
    if shell_cwd and Path(str(shell_cwd)).expanduser().is_dir():
        return f"file://{Path(str(shell_cwd)).expanduser()}"
    root = getattr(project, "project_root", None) if project is not None else None
    if root and Path(str(root)).expanduser().is_dir():
        return f"file://{Path(str(root)).expanduser()}"
    return f"file://{os.getcwd()}"


def files_browse_fence(project: Any = None) -> str:
    """Directory Files must not walk above for this desk.

    Remote ``files_root`` / fabric path when set; the home desk is ``~``;
    else the local ``project_root``. Empty when there is no project — no fence.

    Not ``shell_cwd``: browsing up should stop at the project, not at wherever
    bash happened to be when Files opened.
    """
    if project is None:
        try:
            from xlii.active_session import active_session

            sess = active_session()
            project = getattr(sess, "project", None) if sess is not None else None
        except Exception:
            project = None
    if project is None:
        return ""
    pointed = files_root_of(project)
    if pointed and not is_pointer_stub_path(pointed):
        return pointed
    inferred = infer_fabric_files_address(project)
    if inferred and not is_pointer_stub_path(inferred):
        return inferred
    try:
        from xlii.project_paths import is_home_desk_project, user_home

        if is_home_desk_project(project):
            return f"file://{user_home()}"
    except Exception:
        # Can't tell whether this is the home desk -- fall through to the root handling below.
        pass
    root = getattr(project, "project_root", None)
    if root:
        p = Path(str(root)).expanduser()
        try:
            p = p.resolve()
        except OSError:
            # Keep the unresolved expanduser() path from above.
            pass
        return f"file://{p}"
    return ""


def _as_address(raw: Any):
    if raw is None or raw == "":
        return None
    from xlii.addressing import Address

    if isinstance(raw, Address):
        return raw
    try:
        return Address.parse(str(raw))
    except Exception:
        return None


def _unwrap_via(addr: Any):
    """Peel ``via://node/<inner>`` so a hopped listing compares as the inner tree."""
    cur = addr
    for _ in range(4):
        if getattr(cur, "scheme", "") != "via" or not getattr(cur, "subpath", ""):
            return cur
        inner = _as_address(cur.subpath)
        if inner is None:
            return cur
        cur = inner
    return cur


def address_inside_root(child: Any, root: Any) -> bool:
    """True when *child* is *root* or a descendant (same scheme + host + path)."""
    c = _unwrap_via(_as_address(child))
    r = _unwrap_via(_as_address(root))
    if c is None or r is None:
        return False
    if c.scheme != r.scheme:
        return False
    if c.scheme == "file":
        try:
            cp = Path(c.target).expanduser().resolve()
            rp = Path(r.target).expanduser().resolve()
        except (OSError, RuntimeError):
            cp = Path(c.target)
            rp = Path(r.target)
        return cp == rp or rp in cp.parents
    if c.key != r.key:
        return False
    cs = (c.subpath or "").rstrip("/")
    rs = (r.subpath or "").rstrip("/")
    if cs == rs:
        return True
    if not rs:
        # Fence is the host root (``sftp://box``) — everything on that host.
        return True
    return cs.startswith(rs + "/")


def parent_inside_files_fence(addr: Any, parent: Any, *, fence: str = "") -> Any:
    """Drop *parent* when walking it would leave the session Files root.

    Applied only when *addr* itself is inside the fence. Remotes host browse
    (a listing that is not under this desk's Files root) stays unfenced.
    Empty / missing fence is a no-op.
    """
    if parent is None:
        return None
    bound = (fence or "").strip() or files_browse_fence()
    if not bound:
        return parent
    try:
        if address_inside_root(addr, bound) and not address_inside_root(parent, bound):
            return None
    except Exception:
        return parent
    return parent


def pointer_store_root() -> Path:
    """Local homes for adopted remotes: ``~/.xlii/links``."""
    from xlii.project_paths import xlii_user_root

    return xlii_user_root() / "links"


def stub_root_for_remote(address: str) -> Path:
    """Stable local directory that will hold ``.xlii/`` for *address*."""
    canon = normalize_files_root(address)
    if not canon:
        raise ValueError(f"not a remote Files address: {address!r}")
    from xlii.addressing import Address

    addr = Address.parse(canon)
    host = (addr.key or addr.target.split("/", 1)[0] or addr.scheme).strip()
    tail = (addr.subpath or "").strip("/")
    tail = re.sub(r"[^A-Za-z0-9._-]+", "-", tail).strip("-.")[:80]
    slug = f"{addr.scheme}-{host}"
    if tail:
        slug = f"{slug}-{tail}"
    slug = slug[:120] or addr.scheme
    return pointer_store_root() / slug


def bind_files_root(project: Any, address: str) -> str:
    """Point this project's Files at *address*. Local ``.xlii`` stays put.

    Returns the canonical address. Raises ``ValueError`` if the address is not
    a remote Files mount. Does not move ``project_root``.
    """
    canon = normalize_files_root(address)
    if not canon:
        raise ValueError(f"need a remote Files address (sftp://…), not {address!r}")
    project.files_root = canon
    save = getattr(project, "save", None)
    if callable(save):
        save()
    return canon


def adopt_remote(address: str, *, kind: Optional[str] = None, name: str = "") -> Any:
    """Mint a local stub, register it, point Files at *address*.

    ``kind`` is ``code`` / ``collection`` (same two kinds). The remote tree is
    not a Collection — stubs are local-only.
    """
    from xlii.config import PROJECT_KIND_COLLECTION, ProjectConfig, normalize_project_kind
    from xlii.sync import init_project

    canon = normalize_files_root(address)
    if not canon:
        raise ValueError(f"need a remote Files address (sftp://…), not {address!r}")
    existing = ProjectConfig.load(stub_root_for_remote(canon))
    if existing is not None:
        bind_files_root(existing, canon)
        stamped = normalize_project_kind(kind)
        if stamped is not None and existing.kind != stamped:
            existing.kind = stamped
            if stamped == PROJECT_KIND_COLLECTION:
                existing.local_only = True
            existing.save()
        return existing

    stub = stub_root_for_remote(canon)
    stub.mkdir(parents=True, exist_ok=True)
    label = (name or "").strip() or _default_remote_name(canon)
    project = init_project(None, stub, name=label, local_only=True, kind=kind)
    bind_files_root(project, canon)
    return project


def _default_remote_name(address: str) -> str:
    from xlii.addressing import Address

    addr = Address.parse(address)
    tail = (addr.subpath or addr.target).rstrip("/").split("/")[-1]
    return tail or addr.key or addr.scheme
