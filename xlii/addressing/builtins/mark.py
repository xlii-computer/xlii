"""MarkProvider provider."""
from __future__ import annotations


from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)


class MarkProvider:
    """``mark://`` — the conversation's marks (bookmarked turn spans) as a browseable list.

    ``mark://`` lists every mark of the active session (newest turn first); ``mark://<name>`` reads
    that mark's recall span — the marked turn plus its stored window — rendered as markdown. Read-only:
    marks are created via ``/mark`` and drop with their turn. Reaches the live turn store through the
    ambient session (:mod:`xlii.active_session`), since a chip/F-key doorway resolves it with no
    session in hand; outside a session it lists empty rather than raising. Nodes carry ``type=mark`` so
    they bucket under the ``[marks]`` chip.
    """

    scheme = "mark"

    def resolve(self, address: Address) -> Resolution:
        name = address.key.strip()
        if not name:
            return Resolution(ok=True, address=address, kind="mark")  # the browseable root
        ok = name in self._mark_names()
        return Resolution(ok=ok, address=address, kind="mark",
                          reason="" if ok else f"no mark named {name!r}")

    def stat(self, address: Address) -> Node:
        name = address.key.strip()
        return Node(address=str(address), name=name or "marks",
                    kind="container" if not name else "leaf", extra={"type": "mark"})

    def list(self, address: Address) -> "list[Node]":
        if address.key.strip():
            return []
        from xlii.active_session import active_turns_dir
        from xlii.transcript import list_marks

        td = active_turns_dir()
        if td is None:
            return []
        seen: set[str] = set()
        out: list[Node] = []
        for name, ts in list_marks(td):  # newest turn first
            if name in seen:
                continue
            seen.add(name)
            out.append(Node(address=f"mark://{name}", name=name, kind="leaf",
                            extra={"type": "mark", "ts": ts}))
        return out

    def read(self, address: Address) -> bytes:
        name = address.key.strip()
        if not name:
            raise IsADirectoryError("mark://: a marks root — use ls")
        from xlii.transcript import get_marked_span

        td, mark = _resolve_mark_store(name)
        span = get_marked_span(td, mark) if td is not None else None
        if not span:
            raise FileNotFoundError(
                f"mark://{name}: no such mark — persona deleted or the turn is missing"
            )
        return _render_mark_span(mark, span).encode()

    def exists(self, address: Address) -> bool:
        name = address.key.strip()
        if not name:
            return True
        td, mark = _resolve_mark_store(name)
        if td is None:
            return False
        from xlii.transcript import get_marked_span

        return get_marked_span(td, mark) is not None

    def shell_export(self, address: Address) -> ShellExport:
        if not address.key.strip():
            return ShellExport(kind="address")  # the marks root — a live list, no file behind it
        # A mark's span exists only in the turn store — snapshot the rendered markdown.
        return ShellExport(kind="content", content=self.read(address), suffix=".md")

    @staticmethod
    def _mark_names() -> "set[str]":
        from xlii.active_session import active_session, active_turns_dir
        from xlii.transcript import list_marks

        names: set[str] = set()
        td = active_turns_dir()
        if td is not None:
            names.update(n for n, _ in list_marks(td))
        state = active_session()
        if state is None:
            return names
        try:
            from xlii.repl_cmds.chat import _all_mark_stores

            for _label, store, _addr in _all_mark_stores(state):
                names.update(n for n, _ in list_marks(store))
        except Exception:
            # Unreadable mark stores contribute no names; whatever was collected so far is returned.
            pass
        return names


def _resolve_mark_store(target: str):
    """``(turns_dir, mark_name)`` or ``(None, name)`` — quiet global lookup."""
    from xlii.active_session import active_session, active_turns_dir
    from xlii.transcript import list_marks

    name = (target or "").strip()
    state = active_session()
    if ":" in name:
        left, _, right = name.partition(":")
        left, right = left.strip(), right.strip()
        if left and right:
            try:
                from xlii.persona import Persona
                from xlii.repl_cmds.chat import _all_mark_stores

                if state is not None:
                    for label, td, _addr in _all_mark_stores(state):
                        if label == left and right in {n for n, _ in list_marks(td)}:
                            return td, right
                if Persona(left).exists():
                    td = Persona(left).turns_dir
                    if right in {n for n, _ in list_marks(td)}:
                        return td, right
            except Exception:
                # Quiet lookup by contract: an unreadable store falls through
                # to the bare mark name below.
                pass
            return None, right
    td = active_turns_dir()
    if td is not None and name in {n for n, _ in list_marks(td)}:
        return td, name
    if state is not None:
        try:
            from xlii.repl_cmds.chat import _all_mark_stores

            hits = []
            for _label, store, _addr in _all_mark_stores(state):
                if name in {n for n, _ in list_marks(store)}:
                    hits.append(store)
            if len(hits) == 1:
                return hits[0], name
        except Exception:
            # Quiet lookup by contract: an unreadable store means no match.
            pass
    return None, name


def _render_mark_span(name: str, span) -> str:
    """Render a mark's recall span (a list of ``Turn``) as readable markdown for the view pane."""
    lines = [f"# mark: {name}", ""]
    for t in span:
        lines.append(f"## {t.timestamp}")
        lines.append("")
        lines.append(f"**you:** {t.user}".rstrip())
        lines.append("")
        lines.append(f"**xlii:** {t.assistant}".rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


