"""Uniform addressing for xlii — ``scheme://target``, one resolver, schemes are providers.

Kernel-rebuild Vectors V1 (resolver core) + V2 (VFS surface) + V3 (write side). The
load-bearing idea: **a scheme is a provider**. :func:`resolve` parses an address and
dispatches to the provider registered for its scheme. A provider may *also* implement
:class:`VfsProvider` (``stat``/``list``/``read``/``exists``) so its addresses are browseable,
and :class:`WritableVfs` (``write``) so leaves can be written — that's what panes mount and
what the fs tools (``cp``/``mv``) drive.

Address grammar::

    scheme://target[?k=v&...]

* ``scheme`` — the namespace / provider (``file``, ``project``, ``conv``, ``persona``, …)
* ``target`` — everything after ``://``; interpreted by the provider (a path, a name,
  a ``name/subpath``, ``.``, an id, …)

A scheme-less token follows the bare-token rule (*path-like → file, else the surface's
default_scheme*). Commands that mean a specific namespace pass an explicit scheme.

:func:`to_shell_arg` is the seam's inverse — an address out to a shell-consumable
argument, per the provider's declared ``shell_export`` capability (see :mod:`._shell_arg`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

__all__ = [
    "Address",
    "Resolution",
    "Provider",
    "register",
    "providers",
    "resolve",
    "ShellArg",
    "ShellExport",
    "to_shell_arg",
    "Node",
    "VfsProvider",
    "WritableVfs",
    "supports_vfs",
    "supports_write",
    "vfs_stat",
    "vfs_list",
    "vfs_read",
    "vfs_exists",
    "vfs_write",
    "vfs_delete",
    "vfs_mkdir",
    "classify",
    "register_content_type",
    "unregister_content_type",
    "content_types",
]

_SEP = "://"


def _looks_like_path(token: str) -> bool:
    """The shared bare-token sniff: does a scheme-less token denote a filesystem path?"""
    if not token:
        return False
    if "/" in token or token.startswith((".", "~")):
        return True
    try:
        return Path(token).expanduser().exists()
    except OSError:
        return False


@dataclass(frozen=True)
class Address:
    """A parsed ``scheme://target[?query][#anchor]`` address.

    ``anchor`` is the URI-style fragment after ``#`` — a span *within* the target
    (``conv://nick/turn#mark:thesis``, ``file://notes.md#L42-51``, ``wiki://page#section``).
    Providers that understand spans honour it (``vfs_read`` returns just that span); providers
    that don't simply ignore it. It is the primitive that precise marks are built on.
    """

    scheme: str
    target: str = ""
    query: Mapping[str, str] = field(default_factory=dict)
    raw: str = ""
    anchor: str = ""

    @classmethod
    def parse(cls, s: str, *, default_scheme: Optional[str] = None) -> "Address":
        raw = s or ""
        body = raw.strip()
        if _SEP in body:
            scheme, rest = body.split(_SEP, 1)
        else:
            scheme = "file" if _looks_like_path(body) else (default_scheme or "")
            rest = body
        # Fragment (#anchor) is everything after the first '#' — a span within the target.
        rest, _, anchor = rest.partition("#")
        query: dict[str, str] = {}
        if "?" in rest:
            rest, qs = rest.split("?", 1)
            for part in filter(None, qs.split("&")):
                k, _, v = part.partition("=")
                query[k] = v
        return cls(scheme=scheme, target=rest, query=query, anchor=anchor, raw=raw)

    @property
    def key(self) -> str:
        """First segment of ``target`` — for name-keyed schemes (project, persona, …)."""
        return self.target.split("/", 1)[0]

    @property
    def subpath(self) -> str:
        """Target remainder after ``key`` — the path *inside* the entity."""
        parts = self.target.split("/", 1)
        return parts[1] if len(parts) > 1 else ""

    def __str__(self) -> str:
        s = f"{self.scheme}{_SEP}{self.target}"
        if self.query:
            s += "?" + "&".join(f"{k}={v}" for k, v in self.query.items())
        if self.anchor:
            s += f"#{self.anchor}"
        return s


@dataclass(frozen=True)
class Resolution:
    """The result of resolving one address — generalized from
    ``project_resolver.ProjectResolution``.

    ``handle`` is the primary domain object (``ProjectConfig`` / ``Persona`` /
    ``Path`` / …); ``path`` is the filesystem location when the entity has one;
    ``matches`` carries ambiguous candidates; ``reason`` explains a miss/ambiguity for
    the caller's message; ``detail`` carries provider-specific extra (e.g. the original
    ``ProjectResolution``) so existing print helpers keep working during migration.
    """

    ok: bool
    address: Address
    handle: Any = None
    kind: Optional[str] = None
    path: Optional[Path] = None
    matches: tuple = ()
    reason: str = ""
    detail: Any = None

    @property
    def ambiguous(self) -> bool:
        return len(self.matches) > 1


@runtime_checkable
class Provider(Protocol):
    """A scheme handler. Every provider answers ``resolve``; browseable ones also
    implement :class:`VfsProvider`, writable ones :class:`WritableVfs`."""

    scheme: str

    def resolve(self, address: Address) -> Resolution: ...


_PROVIDERS: dict[str, Provider] = {}


def register(provider: Provider) -> None:
    """Register (or replace) the provider for ``provider.scheme``."""
    _PROVIDERS[provider.scheme] = provider


def providers() -> Mapping[str, Provider]:
    """Snapshot of the registered providers, keyed by scheme."""
    return dict(_PROVIDERS)


def resolve(s: "str | Address", *, default_scheme: Optional[str] = None) -> Resolution:
    """Parse ``s`` (if a string) and dispatch to its scheme's provider."""
    addr = s if isinstance(s, Address) else Address.parse(s, default_scheme=default_scheme)
    if not addr.scheme:
        return Resolution(ok=False, address=addr, reason="no scheme and no default")
    provider = _PROVIDERS.get(addr.scheme)
    if provider is None:
        return Resolution(ok=False, address=addr, reason=f"no provider for {addr.scheme}{_SEP}")
    return provider.resolve(addr)


# --- VFS surface (optional capabilities) -------------------------------------
# A provider may also let its addresses be browsed/read (VfsProvider) and written
# (WritableVfs). Panes/commands check ``supports_vfs``/``supports_write`` before offering
# navigation or edits; providers that only answer ``resolve()`` remain valid.


@dataclass(frozen=True)
class Node:
    """One entry in the VFS — what an explorer pane lists and a view pane reads."""

    address: str
    name: str
    kind: str  # "container" | "leaf"
    size: Optional[int] = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class VfsProvider(Provider, Protocol):
    """A provider whose addresses can be browsed and read."""

    def stat(self, address: Address) -> Node: ...

    def list(self, address: Address) -> "list[Node]": ...

    def read(self, address: Address) -> bytes: ...

    def exists(self, address: Address) -> bool: ...


@runtime_checkable
class WritableVfs(VfsProvider, Protocol):
    """A VFS provider whose leaves can be written, and whose entries can be created/removed."""

    def write(self, address: Address, data: bytes) -> None: ...

    def delete(self, address: Address, recursive: bool = False) -> None: ...

    def mkdir(self, address: Address) -> None: ...


def supports_vfs(scheme: str) -> bool:
    """True if ``scheme``'s provider can browse/read (implements :class:`VfsProvider`)."""
    return isinstance(_PROVIDERS.get(scheme), VfsProvider)


def supports_write(scheme: str) -> bool:
    """True if ``scheme``'s provider can write (implements :class:`WritableVfs`)."""
    return isinstance(_PROVIDERS.get(scheme), WritableVfs)


def _vfs(addr: "str | Address", default_scheme: Optional[str]) -> "tuple[Address, VfsProvider]":
    a = addr if isinstance(addr, Address) else Address.parse(addr, default_scheme=default_scheme)
    p = _PROVIDERS.get(a.scheme)
    if not isinstance(p, VfsProvider):
        raise NotImplementedError(f"{a.scheme}{_SEP} is not browseable")
    return a, p


def vfs_stat(addr: "str | Address", *, default_scheme: Optional[str] = None) -> Node:
    a, p = _vfs(addr, default_scheme)
    return p.stat(a)


def vfs_list(addr: "str | Address", *, default_scheme: Optional[str] = None) -> "list[Node]":
    a, p = _vfs(addr, default_scheme)
    return p.list(a)


def vfs_read(addr: "str | Address", *, default_scheme: Optional[str] = None) -> bytes:
    a, p = _vfs(addr, default_scheme)
    return p.read(a)


def vfs_exists(addr: "str | Address", *, default_scheme: Optional[str] = None) -> bool:
    a, p = _vfs(addr, default_scheme)
    return p.exists(a)


def _writable(addr: "str | Address", default_scheme: Optional[str]) -> "tuple[Address, WritableVfs]":
    a = addr if isinstance(addr, Address) else Address.parse(addr, default_scheme=default_scheme)
    p = _PROVIDERS.get(a.scheme)
    if not isinstance(p, WritableVfs):
        raise NotImplementedError(f"{a.scheme}{_SEP} is not writable")
    return a, p


def vfs_write(addr: "str | Address", data: bytes, *, default_scheme: Optional[str] = None) -> None:
    a, p = _writable(addr, default_scheme)
    p.write(a, data)


def vfs_delete(addr: "str | Address", *, recursive: bool = False, default_scheme: Optional[str] = None) -> None:
    a, p = _writable(addr, default_scheme)
    p.delete(a, recursive=recursive)


def vfs_mkdir(addr: "str | Address", *, default_scheme: Optional[str] = None) -> None:
    a, p = _writable(addr, default_scheme)
    p.mkdir(a)


# The export seam — the inverse of resolve() (see _shell_arg). Imported before the
# built-ins so provider modules can return ShellExport from their shell_export().
from ._shell_arg import (  # noqa: E402
    ShellArg,
    ShellExport,
    to_shell_arg,
)

# Register the built-in providers (file/project/conv/persona) on package import. The
# providers import their wrapped modules lazily, so this stays cycle-free.
from . import _builtin_providers as _builtin_providers  # noqa: E402

_builtin_providers.register_builtins()

# Content-class (`type`) facet — a small registry keyed off a Node, distinct from `kind`
# (container/leaf). Imported here (after Node) so the built-in classes register on import.
from ._content_type import (  # noqa: E402
    classify,
    content_types,
    register_content_type,
    unregister_content_type,
)
