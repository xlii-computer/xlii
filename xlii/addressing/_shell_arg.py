"""The export seam — :func:`to_shell_arg`, the inverse of :func:`~xlii.addressing.resolve`.

``resolve`` turns a token *into* an address; this seam turns an address into something a
shell command (or any foreign body) can consume. Three outcomes, chosen **by the provider**:

* ``path`` — the entity is a real file/dir; the arg is its plain path, rendered relative
  to ``cwd`` when inside it, absolute otherwise. A line anchor (``#L42`` / ``#L42-51``)
  renders as ``path:NN`` — the grep/editor dialect.
* ``address`` — the entity exists only inside xlii (a jobs root, a panel hub); the arg is
  the address itself, for consumers that speak addresses.
* ``content`` — the entity's content is real but has no file behind it (a diff, a mark's
  span, a live job report); the bytes are materialized into a temp file and the arg is
  that path. The temp file is a point-in-time snapshot the caller owns — edits never
  round-trip to the source.

**The capability is declared on the provider** (``shell_export(address) -> ShellExport``),
never inferred: a provider that doesn't declare it exports nothing (a clean error, not a
guess), mirroring how :class:`~xlii.addressing.VfsProvider` gates browsing — but by an
explicit method, since which of the three outcomes fits (and *which* path — ``conv://``'s
``Resolution.path`` is the turns dir, never the addressed leaf) is per-scheme knowledge.

Shell quoting stays owned by the task-args seam (:func:`xlii.tasks.substitute` holds the
one ``shlex.quote`` in the tree): :meth:`ShellArg.quoted` delegates to it rather than
growing a second implementation. ``ShellArg.value`` itself is always the raw, unquoted
string — what non-shell consumers (F5-copy previews, file ops) want anyway.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from xlii.addressing import Address, providers

__all__ = ["ShellArg", "ShellExport", "to_shell_arg"]

_EXPORT_KINDS = ("path", "address", "content")

# The line-anchor grammar from the address docstring (``file://notes.md#L42-51``);
# a range renders as its start line — the dialect editors and grep agree on.
_LINE_ANCHOR_RE = re.compile(r"^L(\d+)(?:-(\d+))?$")


@dataclass(frozen=True)
class ShellExport:
    """A provider's declared outcome for exporting one address — what
    ``shell_export(address)`` returns for the engine to render.

    ``kind`` is one of ``path`` (``path`` names the artifact), ``address`` (only the
    address denotes it), or ``content`` (``content`` bytes get materialized to a temp
    file with ``suffix``). Misses are raised (``FileNotFoundError`` / ``ValueError`` /
    ``NotImplementedError``), matching the VFS read-side convention.
    """

    kind: str
    path: Optional[Path] = None
    content: Optional[bytes] = None
    suffix: str = ".txt"


@dataclass(frozen=True)
class ShellArg:
    """The result of exporting one address for a shell command — mirrors
    :class:`~xlii.addressing.Resolution` (``ok``/``reason``, never raises).

    ``value`` is the raw, unquoted argument; ``kind`` says which outcome produced it;
    ``path`` carries the absolute filesystem location behind ``value`` for the ``path``
    and ``content`` kinds (``value`` may be cwd-relative and anchor-suffixed).
    """

    ok: bool
    address: Address
    value: str = ""
    kind: str = ""
    path: Optional[Path] = None
    reason: str = ""

    def quoted(self) -> str:
        """``value`` as one shell-safe token. Quoting is owned by the task-args seam
        (the single ``shlex.quote`` in the tree) — delegate, don't duplicate."""
        from xlii.tasks import KIND_SHELL, substitute

        return substitute("{{v}}", {"v": self.value}, kind=KIND_SHELL)


def to_shell_arg(
    addr: "str | Address", *, cwd: "Path | str | None" = None, default_scheme: Optional[str] = None
) -> ShellArg:
    """Export ``addr`` as a shell-consumable argument (see the module docstring).

    ``cwd`` is only the base paths RENDER relative to (pass the session's shell cwd);
    it never changes how a provider resolves its target — a relative ``file://`` target
    binds to the process cwd, exactly as ``resolve()`` does. Unknown schemes and
    providers that declare no export return ``ok=False`` with a reason — never a guess.
    """
    a = addr if isinstance(addr, Address) else Address.parse(addr, default_scheme=default_scheme)
    if not a.scheme:
        return ShellArg(ok=False, address=a, reason="no scheme and no default")
    provider = providers().get(a.scheme)
    if provider is None:
        return ShellArg(ok=False, address=a, reason=f"no provider for {a.scheme}://")
    exporter = getattr(provider, "shell_export", None)
    if exporter is None:
        return ShellArg(ok=False, address=a, reason=f"{a.scheme}:// does not export shell args")
    # One try over export AND rendering — ShellArg's never-raises contract covers the
    # whole outcome (a deleted process cwd or an unwritable tmp is a miss, not a crash).
    try:
        ex = exporter(a)
        if ex.kind == "address":
            return ShellArg(ok=True, address=a, value=str(a), kind="address")
        if ex.kind == "content":
            path = _materialize(a, ex)
            return ShellArg(ok=True, address=a, value=str(path), kind="content", path=path)
        if ex.kind == "path":
            if ex.path is None:
                return ShellArg(ok=False, address=a, reason=f"{a.scheme}:// exported no path")
            path = Path(ex.path)
            if not path.is_absolute():
                path = path.resolve()
            value = _render_path(path, cwd)
            line = _line_anchor(a.anchor)
            if line is not None:
                value = f"{value}:{line}"
            return ShellArg(ok=True, address=a, value=value, kind="path", path=path)
    except (NotImplementedError, ValueError, OSError, RuntimeError) as e:
        # The VFS miss family (FileNotFoundError/IsADirectoryError are OSError), plus
        # RuntimeError — the remote layer's connection failures (see xlii.remotefs).
        return ShellArg(ok=False, address=a, reason=str(e) or type(e).__name__)
    return ShellArg(ok=False, address=a, reason=f"{a.scheme}:// declared unknown export kind {ex.kind!r}")


def _render_path(path: Path, cwd: "Path | str | None") -> str:
    """``path`` relative to ``cwd`` when inside it, absolute otherwise (posix — shell-facing)."""
    base = Path(cwd) if cwd is not None else Path.cwd()
    try:
        base = base.resolve()
    except OSError:
        # cwd may be missing or inaccessible — keep the unresolved base for is_relative_to.
        pass
    if path.is_relative_to(base):
        return path.relative_to(base).as_posix()
    return str(path)


def _line_anchor(anchor: str) -> Optional[str]:
    """The start line of a ``L42`` / ``L42-51`` anchor, or None for no/other anchors."""
    m = _LINE_ANCHOR_RE.match(anchor.strip()) if anchor else None
    return m.group(1) if m else None


def _materialize(a: Address, ex: ShellExport) -> Path:
    """Dump ``ex.content`` to a temp file the shell can read — a snapshot the caller owns."""
    fd, name = tempfile.mkstemp(prefix=f"xlii-{a.scheme}-", suffix=ex.suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(ex.content or b"")
    return Path(name)
