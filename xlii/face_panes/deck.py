from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Optional

from xlii.turn_events import PaneAction, PaneDeck, PaneRow, PaneState

from .sinks import (
    _FailedPane,
    _FaceInputSink,
    _FaceJobSink,
    _FaceMediaSink,
    _FaceSessionSink,
    _FaceTurnSink,
)
from .views import (
    FACE_PANE_LABELS,
    STREAM_VIEW,
    _CAPTION,
    _HTML_SLOT_PANES,
    _MAX_INLINE_IMAGE_BYTES,
    _MAX_ROWS,
    _SCHEME_TO_PANE,
    _ordered_view_ids,
    _parent_address,
    _refresh,
    _root_addresses,
    is_stream_view,
)

class FaceDeck:
    """The face's pane deck: one Dock, two *visual* slots.

    Dock slots are mounted pane types (wiki, files, …). Visual slots ``a``/``b``
    are what the workspace shows: ``stream`` or a pane id, or empty. Same view
    cannot occupy both (no mirrors). At least one slot stays occupied — closing
    the last one puts ``stream`` there (blank desk is not a layout).
    """

    def __init__(self, server: Any) -> None:
        self._server = server  # FaceServer — state, send, submit
        self._dock: Any = None
        self._mounted_for: Optional[tuple] = None  # the pane set the dock serves
        self._sent_deck = False  # a non-empty deck is on the client's screen
        self._slot_a: Optional[str] = STREAM_VIEW
        self._slot_b: Optional[str] = None
        self._slot_focus: str = "a"
        self._viewer_address: Optional[str] = None  # last PDF opened in the sticky viewer
        self._form_address: Optional[str] = None  # pluginform://plugin/action
        self._form_seed: dict = {}
        self._taskmake_address: Optional[str] = None
        self._pluginmake_address: Optional[str] = None

    # ---------------------------------------------------------------- state

    @property
    def _state(self) -> Any:
        return self._server.state

    def _is_home_surface(self) -> bool:
        st = self._state
        if getattr(st, "scratch", False):
            return True
        name = getattr(getattr(st, "project", None), "name", "") or ""
        return str(name).startswith("scratch/")

    def _declared_view_ids(self) -> list[str]:
        """Views this pack offers: declared panes + QL ``pane:`` doors + extras.

        Scratch/home always keeps ``projects`` / ``home`` so the switch door
        still mounts when the active pack is chat. ``/panel`` extras stay
        sticky until the next pack switch.
        """
        from xlii.workbench import pack_pane_ids

        ids = list(pack_pane_ids(getattr(self._state, "workbench", None)))
        if self._is_home_surface():
            for must in ("projects", "home"):
                if must not in ids:
                    ids.append(must)
        for extra in getattr(self, "_sticky_panes", ()) or ():
            if extra and extra not in ids:
                ids.append(extra)
        allow = self._phone_allow()
        if allow is not None:
            for must in allow:
                if must not in ids:
                    ids.append(must)
            ids = [pid for pid in ids if pid in allow]
        return ids

    def _workbench_panes(self) -> tuple:
        """Active pack views the dock mounts (catalog minus sticky viewers).

        PDF / makers / off-pack canvas attach after mount so adding them
        does not rebuild (and reset) explorer/git/…
        """
        from xlii.workbench import pack_pane_ids

        pack = set(pack_pane_ids(getattr(self._state, "workbench", None)))
        out = []
        for pid in self._declared_view_ids():
            if pid == "pdf" or pid in _HTML_SLOT_PANES:
                continue
            if pid == "canvas" and pid not in pack:
                continue
            out.append(pid)
        return tuple(out)

    def _roots(self) -> dict[str, str]:
        return _root_addresses(self._state)

    def sync_pack(self, *, force: bool = False) -> bool:
        """If the active pack name changed, drop sticky extras and evict slots.

        First bind only records the pack — ``ensure_pane`` extras (config,
        history, ``/panel``) must survive that. A later switch, or
        ``force=True`` from set_workbench / home / land, evicts. Returns True
        when slots/catalog should refresh.
        """
        pack = getattr(getattr(self._state, "workbench", None), "name", "") or ""
        prev = getattr(self, "_pack_key", None)
        if not force and prev == pack:
            return False
        changed = prev is not None and prev != pack
        self._pack_key = pack
        if not force and not changed:
            return False
        self._sticky_panes = []
        self._viewer_address = None
        self._form_address = None
        self._mounted_for = None
        self.constrain_slots_to_pack()
        return True

    def _phone_allow(self) -> Optional[frozenset[str]]:
        """None on the desk. On the phone glass, the D2 pane cut."""
        if getattr(self._server, "view_posture", "desk") != "phone":
            return None
        from xlii.serve_face.glass import phone_pane_allow

        preview = getattr(self._server, "glass_grant_mode", "full") == "preview"
        return phone_pane_allow(preview=preview)

    def available(self) -> bool:
        """True when the active workbench type mounts any panes at all."""
        roots = self._roots()
        return any(name in roots for name in self._workbench_panes())

    def ensure_pane(self, pane_id: str) -> bool:
        """Force *pane_id* into the next dock mount (Home join / open_pane)."""
        pid = (pane_id or "").strip()
        if pid == "pdf":
            if not self._viewer_address:
                return False
            sticky = getattr(self, "_sticky_panes", None)
            if sticky is None:
                sticky = []
                self._sticky_panes = sticky
            if "pdf" not in sticky:
                sticky.append("pdf")
            self._ensure_dock()
            return True
        if pid in _HTML_SLOT_PANES:
            sticky = getattr(self, "_sticky_panes", None)
            if sticky is None:
                sticky = []
                self._sticky_panes = sticky
            if pid not in sticky:
                sticky.append(pid)
            self._ensure_dock()
            return True
        if pid == "canvas":
            sticky = getattr(self, "_sticky_panes", None)
            if sticky is None:
                sticky = []
                self._sticky_panes = sticky
            if "canvas" not in sticky:
                sticky.append("canvas")
            self._ensure_dock()
            self._attach_canvas()
            return True
        if not pid or pid not in self._roots():
            return False
        # Invalidate mount key so _ensure_dock rebuilds with the extra slot.
        extra = list(self._workbench_panes())
        if pid not in extra:
            # Sticky extras for this face session (join even if pack is chat).
            sticky = getattr(self, "_sticky_panes", None)
            if sticky is None:
                sticky = []
                self._sticky_panes = sticky
            if pid not in sticky:
                sticky.append(pid)
                # New slot — dock must remount. Already-packed panes (home,
                # files, git, …) must not rebuild git status / vfs on every
                # /panel click.
                self._mounted_for = None
        return True

    def slot_tuple(self) -> tuple[str, str, str]:
        """``(slot_a, slot_b, focus)`` — empty string means closed."""
        return (self._slot_a or "", self._slot_b or "", self._slot_focus or "a")

    def slot_catalog(self) -> list[dict[str, str]]:
        """Dropdown: live stream + other open project tapes + Home Hub + panes.

        Home is not a stream. Open folders stay listed until that tape is closed.
        """
        self.sync_pack()
        live_label = "Home Stream"
        live_id = ""
        opener = getattr(self._server, "live_stream_row", None)
        try:
            row = opener() if callable(opener) else None
        except Exception:
            row = None
        if row:
            live_label = str(row.get("label") or row.get("name") or "stream")
            live_id = str(row.get("id") or "")
        rows = [
            {"id": STREAM_VIEW, "label": live_label},
        ]
        seen = {STREAM_VIEW}
        extra = getattr(self._server, "open_streams", None)
        try:
            others = list(extra()) if callable(extra) else []
        except Exception:
            others = []
        for s in others:
            sid = str(s.get("id") or "")
            if not sid or sid == live_id:
                continue
            vid = f"stream:{sid}"
            if vid in seen:
                continue
            rows.append({"id": vid, "label": str(s.get("label") or s.get("name") or sid)})
            seen.add(vid)
        rows.append({"id": "home", "label": FACE_PANE_LABELS["home"]})
        seen.add("home")
        roots = self._roots()
        skip_engine = {"artifacts"}  # pile is Attachments; not a user door
        for pid in _ordered_view_ids(self._declared_view_ids()):
            if pid in seen or pid in skip_engine:
                continue
            if pid not in FACE_PANE_LABELS and pid not in roots:
                continue
            rows.append({"id": pid, "label": FACE_PANE_LABELS.get(pid, pid)})
            seen.add(pid)
        return rows

    def pane_catalog(self) -> list[dict[str, str]]:
        """Keep menu — talk-side tray, not stream tapes.

        Home Stream / Home Hub live on Xlii. Project switch lives on Project.
        Lab doors (files/git/plans) live on Project; jobs/tasks/skills on Tools.
        """
        skip = {STREAM_VIEW, "home", "projects"}
        return [
            r for r in self.slot_catalog()
            if r["id"] not in skip and not str(r["id"]).startswith("stream:")
        ]

    def constrain_slots_to_pack(self) -> None:
        """Drop slot occupants the new pack does not offer. Last slot → stream."""
        allowed = {r["id"] for r in self.slot_catalog()}
        changed = False
        for slot in ("a", "b"):
            view = self._slot_get(slot)
            if view and view not in allowed and not str(view).startswith("stream:"):
                self._slot_put(slot, None)
                changed = True
        a, b = self._slot_a, self._slot_b
        if not a and not b:
            self._slot_a = STREAM_VIEW
            self._slot_focus = "a"
            changed = True
        elif not a and b:
            self._slot_focus = "b"
        elif a and not b:
            self._slot_focus = "a"
        if changed:
            self._emit_slots()

    def _slot_get(self, slot: str) -> Optional[str]:
        return self._slot_a if slot == "a" else self._slot_b

    def _slot_put(self, slot: str, view: Optional[str]) -> None:
        if slot == "a":
            self._slot_a = view
        else:
            self._slot_b = view

    def _emit_slots(self) -> None:
        a, b, focus = self.slot_tuple()
        pane = ""
        if focus == "b" and b and not is_stream_view(b):
            pane = b
        elif a and not is_stream_view(a):
            pane = a
        elif b and not is_stream_view(b):
            pane = b
        self._open_pane_id = pane or None
        try:
            self._server.send({
                "type": "slot_state",
                "a": a, "b": b, "focus": focus,
            })
            self._server.send({"type": "pane_focus", "pane": pane})
            self._server.send(self._server.chrome_state())
        except Exception:  # noqa: BLE001
            pass

    def _norm_stream(self, view: str) -> str:
        """``stream:<live-id>`` is the live tape — same occupant as ``stream``."""
        v = (view or "").strip()
        if not v.startswith("stream:"):
            return v
        live = ""
        fn = getattr(self._server, "live_stream_id", None)
        try:
            live = str(fn() or "") if callable(fn) else ""
        except Exception:
            live = ""
        if live and v == f"stream:{live}":
            return STREAM_VIEW
        return v

    def remap_streams(self, prev_id: str, new_id: str) -> None:
        """Clicking a peeked tape enters it. Menu switch does not move slots.

        The peeked slot becomes live ``stream``. The other live tape parks as
        ``stream:<prev>``. Leaving Home (no prev stream) puts the Home hub
        in that other slot — Home is not a tape you peek.
        """
        prev_id = (prev_id or "").strip()
        new_id = (new_id or "").strip()
        if not new_id or prev_id == new_id:
            return
        incoming = f"stream:{new_id}"
        target = None
        for slot in ("a", "b"):
            if (self._slot_get(slot) or "") == incoming:
                target = slot
                break
        if target is None:
            return
        for slot in ("a", "b"):
            v = self._slot_get(slot) or ""
            if slot == target:
                self._slot_put(slot, STREAM_VIEW)
            elif v == STREAM_VIEW:
                if prev_id:
                    self._slot_put(slot, f"stream:{prev_id}")
                else:
                    self.ensure_pane("home")
                    self._slot_put(slot, "home")
        self._slot_focus = target
        self._emit_slots()
        if self._slot_a == "home" or self._slot_b == "home":
            self.send_snapshot()

    def set_slot(self, slot: str, view: str) -> bool:
        """Assign *view* (``stream`` / ``stream:<id>`` / pane id / ``""``) to a|b."""
        slot = "b" if str(slot).strip().lower() == "b" else "a"
        view = self._norm_stream((view or "").strip())
        if not view:
            return self.close_slot(slot)
        peek_id = view[7:] if view.startswith("stream:") else ""
        # D2: phone glass must cut panes server-side even for hand-rolled
        # set_slot (open_pane already checks; this closes the bypass).
        allow = self._phone_allow()
        if allow is not None and not peek_id and view != STREAM_VIEW and view not in allow:
            self._server.send({
                "type": "error",
                "message": f"pane {view!r} is not on the phone glass",
            })
            return False
        if peek_id:
            pass  # occupancy only — peek tapes need no pane mount
        elif view != STREAM_VIEW:
            if not self.ensure_pane(view):
                return False
            self.send_snapshot()
        other_slot = "b" if slot == "a" else "a"
        other = self._norm_stream(self._slot_get(other_slot) or "")
        if other and view == other:
            return False  # no mirrors (including live stream vs stream:<same>)
        # Solo slot picking a *new* view opens the empty side (stream stays,
        # or files|wiki). Replace only when both slots are already occupied.
        cur = self._slot_get(slot)
        if not other and cur and cur != view:
            slot = other_slot
        # Live slot dropdown → enter that tape here. The other slot keeps
        # its peek (or parks the old live if it would become a mirror).
        if peek_id and cur == STREAM_VIEW:
            if self._enter_stream_in_slot(slot, peek_id):
                return True
        self._slot_put(slot, view)
        self._coerce_live_peeks()
        # A foreign project tape is a look. Focus stays on the live room so
        # typing does not jump. Click the peek slot to enter it.
        if not peek_id:
            self._slot_focus = slot
        self._emit_slots()
        peek_now = (self._slot_get(slot) or "")
        if peek_now.startswith("stream:"):
            emit = getattr(self._server, "emit_stream_peek", None)
            if callable(emit):
                emit(slot, peek_now.split(":", 1)[1])
        if view == "home" or self._slot_a == "home" or self._slot_b == "home":
            self.send_snapshot()
        return True

    def _enter_stream_in_slot(self, slot: str, sid: str) -> bool:
        """Make ``sid`` the live tape in *slot*. Other slot keeps its peek."""
        prev = ""
        fn = getattr(self._server, "live_stream_id", None)
        try:
            prev = str(fn() or "") if callable(fn) else ""
        except Exception:
            prev = ""
        enter = getattr(self._server, "enter_open_stream", None)
        if not callable(enter):
            return False
        # Park as a peek first so remap promotes THIS slot, not the other.
        self._slot_put(slot, f"stream:{sid}")
        if not enter(sid):
            self._slot_put(slot, STREAM_VIEW)
            return False
        self.remap_streams(prev, sid)
        self._coerce_live_peeks()
        self._slot_focus = slot
        other = "b" if slot == "a" else "a"
        ov = self._slot_get(other) or ""
        if ov.startswith("stream:"):
            emit = getattr(self._server, "emit_stream_peek", None)
            if callable(emit):
                emit(other, ov.split(":", 1)[1])
        self._emit_slots()
        return True

    def _coerce_live_peeks(self) -> None:
        """``stream:<live-id>`` is the live tape — never a second copy of it."""
        live = ""
        fn = getattr(self._server, "live_stream_id", None)
        try:
            live = str(fn() or "") if callable(fn) else ""
        except Exception:
            live = ""
        if not live:
            return
        live_peek = f"stream:{live}"
        for s in ("a", "b"):
            if (self._slot_get(s) or "") == live_peek:
                self._slot_put(s, STREAM_VIEW)
        if (self._slot_a == STREAM_VIEW and self._slot_b == STREAM_VIEW):
            keep = self._slot_focus if self._slot_focus in ("a", "b") else "a"
            other = "b" if keep == "a" else "a"
            self._slot_put(other, None)

    def swap_slots(self) -> bool:
        """Exchange slot a and b. No-op when a side is empty."""
        a, b = self._slot_a, self._slot_b
        if not a or not b:
            return False
        self._slot_a, self._slot_b = b, a
        self._slot_focus = "b" if self._slot_focus == "a" else "a"
        self._emit_slots()
        return True

    def focus_slot(self, slot: str) -> bool:
        """Click a slot. A peek tape becomes the live project (change stream)."""
        slot = "b" if str(slot).strip().lower() == "b" else "a"
        view = self._slot_get(slot) or ""
        if view.startswith("stream:"):
            sid = view.split(":", 1)[1]
            enter = getattr(self._server, "enter_open_stream", None)
            if callable(enter) and enter(sid):
                return True
        self._slot_focus = slot
        self._emit_slots()
        return True

    def close_slot(self, slot: str) -> bool:
        """Empty a visual slot. Last occupant becomes stream (no blank desk)."""
        slot = "b" if str(slot).strip().lower() == "b" else "a"
        view = self._slot_get(slot) or ""
        if view.startswith("stream:"):
            drop = getattr(self._server, "forget_open_stream", None)
            if callable(drop):
                drop(view.split(":", 1)[1])
        other = self._slot_get("b" if slot == "a" else "a")
        if not other:
            self._slot_put(slot, STREAM_VIEW)
            self._slot_focus = slot
            self._emit_slots()
            return True
        self._slot_put(slot, None)
        self._slot_focus = "b" if slot == "a" else "a"
        self._emit_slots()
        return True

    def open_pane(self, pane_id: str) -> bool:
        """Mount + snapshot + put the pane in a visual slot (empty first)."""
        pid = (pane_id or "").strip()
        allow = self._phone_allow()
        if allow is not None and pid and pid not in allow:
            try:
                self._server.send({
                    "type": "error",
                    "message": f"pane {pid!r} is not on the phone glass",
                })
            except Exception:
                pass
            return False
        if pid == "taskmake":
            self._taskmake_address = "taskmake://"
        if pid == "pluginmake":
            self._pluginmake_address = "pluginmake://"
        if pid in _HTML_SLOT_PANES:
            return self._open_html_slot(pid)
        if not self.ensure_pane(pid):
            return False
        self.send_snapshot()
        if self._slot_a == pid:
            self._slot_focus = "a"
            self._emit_slots()
            return True
        if self._slot_b == pid:
            self._slot_focus = "b"
            self._emit_slots()
            return True
        if not self._slot_b:
            return self.set_slot("b", pid)
        if not self._slot_a:
            return self.set_slot("a", pid)
        target = self._slot_focus if self._slot_focus in ("a", "b") else "b"
        if self._slot_get(target) == STREAM_VIEW:
            target = "b" if target == "a" else "a"
        return self.set_slot(target, pid)

    def close_pane(self) -> bool:
        """Close a pane-occupied visual slot (not stream)."""
        for s in (self._slot_focus, "b", "a"):
            v = self._slot_get(s)
            if v and v != STREAM_VIEW:
                return self.close_slot(s)
        return True

    def open_scheme(self, scheme: str) -> bool:
        """Open ``scheme://`` root as a face pane (TUI doorway parity)."""
        sch = (scheme or "").strip().lower().rstrip(":/")
        pid = _SCHEME_TO_PANE.get(sch)
        if not pid:
            # Unknown scheme — try same name as slot if we have a root address.
            pid = sch if sch in self._roots() else ""
        if not pid:
            return False
        return self.open_pane(pid)

    def _ensure_dock(self) -> None:
        """(Re)build the dock when the workbench's pane set — or the address a
        slot mounts at (the explorer's root follows ``shell_cwd``) — changed."""
        self.sync_pack()
        wanted = self._workbench_panes()
        roots = self._roots()
        mountable = [n for n in wanted if n in roots]
        key = tuple((n, roots[n]) for n in mountable)
        if self._dock is not None and self._mounted_for == key:
            self._attach_pdf_viewer()
            self._attach_canvas()
            self._attach_html_panes()
            return
        from xlii.panes.dock import Dock

        self._dock = Dock(slots=tuple(mountable) or ("_",))
        self._dock.set_turn_sink(_FaceTurnSink(self._server))
        self._dock.set_input_sink(_FaceInputSink(self._server))
        self._dock.set_media_sink(_FaceMediaSink(self._server))
        self._dock.set_session_sink(_FaceSessionSink(self._state, self._server))
        self._dock.set_job_sink(_FaceJobSink(self._state, self._server))
        for name in mountable:
            try:
                self._dock.open_address(roots[name], slot=name)
            except Exception as e:  # noqa: BLE001 — one bad mount must not kill the deck
                self._dock.place(name, _FailedPane(name, str(e)))
        self._mounted_for = key
        self._attach_pdf_viewer()
        self._attach_canvas()
        self._attach_html_panes()

    def _attach_pdf_viewer(self) -> None:
        """Keep the sticky PDF slot on the dock without remounting the pack.

        Re-opens only when the slot is empty or the address changed — a
        same-file remount would reset the page.
        """
        addr = (self._viewer_address or "").strip()
        if not addr or self._dock is None:
            return
        if "pdf" not in self._dock.slot_ids:
            self._dock.add_slot("pdf")
        pane = self._dock.slots.get("pdf")
        if pane is not None and not isinstance(pane, _FailedPane):
            from xlii.addressing import Address

            try:
                want = Address.parse(addr)
                have = pane.address if hasattr(pane.address, "target") else Address.parse(str(pane.address))
                if want.scheme == have.scheme and want.target == have.target:
                    return
            except Exception:
                if str(getattr(pane, "address", "")) == addr:
                    return
        try:
            self._dock.open_address(addr, slot="pdf")
        except Exception as e:  # noqa: BLE001
            self._dock.place("pdf", _FailedPane("pdf", str(e)))

    def _attach_html_panes(self) -> None:
        """Keep sticky maker slots on the dock without remounting the pack."""
        if self._dock is None:
            return
        sticky = set(getattr(self, "_sticky_panes", ()) or ())
        roots = self._roots()
        for pid in _HTML_SLOT_PANES:
            if pid not in sticky:
                continue
            addr = roots.get(pid)
            if pid == "pluginform":
                addr = (self._form_address or "").strip()
                if not addr:
                    continue
            if pid == "taskmake":
                addr = (self._taskmake_address or "").strip() or addr
            if pid == "pluginmake":
                addr = (self._pluginmake_address or "").strip() or addr
            if not addr:
                continue
            if pid not in self._dock.slot_ids:
                self._dock.add_slot(pid)
            pane = self._dock.slots.get(pid)
            if pane is not None and not isinstance(pane, _FailedPane):
                if pid == "pluginform":
                    have = str(getattr(pane, "address", "") or "")
                    if have.rstrip("/") == addr.rstrip("/"):
                        if hasattr(pane, "set_seed"):
                            pane.set_seed(getattr(self, "_form_seed", None))
                        continue
                elif pid in ("taskmake", "pluginmake"):
                    have = str(getattr(pane, "address", "") or "")
                    if have.rstrip("/") == str(addr).rstrip("/"):
                        continue
                    if hasattr(pane, "mount"):
                        try:
                            pane.mount(addr)
                            continue
                        except Exception:
                            # Mount failed — fall through to open_address,
                            # which places a _FailedPane on error.
                            pass
                else:
                    continue
            try:
                self._dock.open_address(addr, slot=pid)
            except Exception as e:  # noqa: BLE001
                self._dock.place(pid, _FailedPane(pid, str(e)))
                continue
            if pid == "pluginform":
                pane = self._dock.slots.get(pid)
                seed = getattr(self, "_form_seed", None)
                if pane is not None and hasattr(pane, "set_seed"):
                    pane.set_seed(seed)

    def _attach_canvas(self, *, select: Optional[str] = None) -> None:
        """Keep the canvas slot on the dock without remounting the pack."""
        from xlii.workbench import pack_pane_ids

        pack = set(pack_pane_ids(getattr(self._state, "workbench", None)))
        sticky = getattr(self, "_sticky_panes", ()) or ()
        if "canvas" not in pack and "canvas" not in sticky:
            return
        if self._dock is None:
            return
        if "canvas" not in self._dock.slot_ids:
            self._dock.add_slot("canvas")
        pane = self._dock.slots.get("canvas")
        if pane is None or isinstance(pane, _FailedPane):
            try:
                self._dock.open_address("canvas://", slot="canvas")
            except Exception as e:  # noqa: BLE001
                self._dock.place("canvas", _FailedPane("canvas", str(e)))
                return
            pane = self._dock.slots.get("canvas")
        if pane is None or not hasattr(pane, "mount"):
            return
        # Remount only when picking a make (reveal) or first bind. A
        # snapshot remount would reset the work to the latest make.
        if select is None and not isinstance(pane, _FailedPane):
            return
        dest = f"canvas://{select}" if select else "canvas://"
        try:
            pane.mount(dest)
        except Exception:
            # A pane that refuses the mount just leaves the canvas unattached.
            pass

    def reveal_canvas(self, path: str = "", *, keep_view: str = "") -> bool:
        """Show the canvas slot with *path* as the work (latest if empty)."""
        sticky = getattr(self, "_sticky_panes", None)
        if sticky is None:
            sticky = []
            self._sticky_panes = sticky
        if "canvas" not in sticky:
            sticky.append("canvas")
        self._ensure_dock()
        name = Path(path).name if path else None
        self._attach_canvas(select=name)
        if not name:
            pane = self._dock.slots.get("canvas") if self._dock is not None else None
            node = None
            if pane is not None and hasattr(pane, "selection"):
                try:
                    node = pane.selection().node
                except Exception:
                    node = None
            name = getattr(node, "name", None)
        if name:
            self._pin_canvas_work(str(name))
        self._reveal_view("canvas", keep_view=keep_view)
        self.send_snapshot()
        return True

    def _pin_canvas_work(self, name: str) -> None:
        """Chip + locker: the canvas make is a held attachment, not just a picture."""
        name = (name or "").strip()
        if not name:
            return
        addr = name if "://" in name else f"canvas://{name}"
        pin = getattr(self._server, "pin_canvas_work", None)
        if callable(pin):
            try:
                pin(addr)
                return
            except Exception:
                # Direct pin unavailable — fall through to the hook path below.
                pass
        hf = getattr(self._server, "handle_focus", None)
        if callable(hf):
            try:
                hf({"address": addr, "once": False, "quiet": True})
            except Exception:
                # Both pin paths failed — the work item stays unpinned.
                pass

    # ---------------------------------------------------------------- wire out

    def snapshot(self) -> PaneDeck:
        """The whole strip as one wire event (re-projection = refresh)."""
        self._ensure_dock()
        wb = getattr(self._state, "workbench", None)
        panes: list[PaneState] = []
        for slot_id, pane in self._dock.slots.items():
            if pane is None:
                continue
            panes.append(self._project(slot_id, pane))
        posture = getattr(self._server, "view_posture", "desk") or "desk"
        allow = self._phone_allow()
        if allow is not None:
            panes = [p for p in panes if p.id in allow]
            panes = [self._phone_ro(p) for p in panes]
        return PaneDeck(
            workbench=getattr(wb, "name", "chat"), panes=panes, posture=posture,
        )

    def _phone_ro(self, pane: PaneState) -> PaneState:
        if pane.id != "git":
            return pane
        from xlii.serve_face.glass import GIT_RO_ACTIONS

        acts = [a for a in (pane.actions or []) if a.name in GIT_RO_ACTIONS]
        return PaneState(
            id=pane.id, title=pane.title, rows=pane.rows, actions=acts,
            empty=pane.empty, note=pane.note,
            image_b64=pane.image_b64, image_alt=pane.image_alt, form=pane.form,
        )

    def _project(self, slot_id: str, pane: Any) -> PaneState:
        if isinstance(pane, _FailedPane):
            return PaneState(id=slot_id, title=slot_id, empty=True, note=pane.note)
        try:
            _refresh(pane)
            if hasattr(pane, "set_block"):
                other = self._other_visual_view() if slot_id == "home" else ""
                pane.set_block(other)
            rendered = pane.render()
            rows = [
                PaneRow(
                    text=r.text, address=r.address, kind=r.kind,
                    selected=r.selected, accent=r.accent, tone=r.tone,
                    choices=[
                        {"value": str(c[0]), "label": str(c[1] if len(c) > 1 else c[0])}
                        for c in (getattr(r, "choices", ()) or ())
                    ],
                    value=str(getattr(r, "value", "") or ""),
                )
                for r in rendered.rows[:_MAX_ROWS]
            ]
            actions = [PaneAction(name=a.name, label=a.label) for a in pane.actions()]
            image_b64 = ""
            image_alt = ""
            media = getattr(rendered, "media", None)
            if media is not None:
                raw_b64 = getattr(media, "b64", "") or ""
                if raw_b64 and len(raw_b64) <= (_MAX_INLINE_IMAGE_BYTES * 4) // 3 + 8:
                    image_b64 = raw_b64
                    image_alt = getattr(media, "caption", "") or ""
            form = getattr(rendered, "form", None)
            if form is not None and not isinstance(form, dict):
                form = None
            return PaneState(id=slot_id, title=rendered.title, rows=rows,
                             actions=actions, empty=rendered.empty,
                             image_b64=image_b64, image_alt=image_alt,
                             form=form)
        except Exception as e:  # noqa: BLE001 — a render hiccup degrades one pane
            return PaneState(id=slot_id, title=slot_id, empty=True,
                             note=f"{type(e).__name__}: {e}")

    def send_snapshot(self) -> None:
        """Project the deck onto the wire.

        A workbench with no panes normally sends nothing (a plain ``chat``
        session is byte-identical to today), but once a deck HAS been shown
        the client must be told to clear it — switching back to ``chat``
        otherwise leaves the previous workbench's strip on screen forever.
        So: empty deck exactly once, on the transition."""
        from xlii.ws_protocol import serialize_event

        if not self.available():
            # Switching to a pane-less type (/workbench chat) must TAKE the strip
            # down: send one empty deck, but only when a deck is up — a face that
            # connects on a chat workbench stays byte-identical to the pre-B1 one.
            if not self._sent_deck:
                return
            self._sent_deck = False
            self._dock = None
            self._mounted_for = None
            wb = getattr(self._state, "workbench", None)
            try:
                self._server.send(serialize_event(
                    PaneDeck(workbench=getattr(wb, "name", "chat"), panes=[])))
            except Exception:  # noqa: BLE001 — chrome must never kill a turn/connect
                pass
            return
        try:
            self._server.send(serialize_event(self.snapshot()))
            self._sent_deck = True
        except Exception:  # noqa: BLE001 — chrome must never kill a turn/connect
            pass

    # ---------------------------------------------------------------- wire in

    def handle(self, msg: dict[str, Any]) -> None:
        """One inbound ``pane_action``: {pane, op: select|key|action, …}."""
        if not self.available():
            self._server.send({"type": "error",
                               "message": "the active workbench has no panes"})
            return
        self._ensure_dock()
        slot = str(msg.get("pane") or "")
        op = str(msg.get("op") or "")
        allow = self._phone_allow()
        if allow is not None and slot not in allow:
            self._server.send({"type": "error",
                               "message": f"pane {slot!r} is not on the phone glass"})
            return
        if allow is not None and slot == "git" and op == "action":
            from xlii.serve_face.glass import GIT_RO_ACTIONS

            name = str(msg.get("name") or "")
            if name not in GIT_RO_ACTIONS:
                self._server.send({
                    "type": "error",
                    "message": "git is read-only on the phone glass",
                })
                return
        pane = self._dock.slots.get(slot)
        if pane is None:
            self._server.send({"type": "error", "message": f"no pane {slot!r}"})
            return
        try:
            if op == "select":
                self._op_select(pane, msg)
            elif op == "key":
                self._op_key(slot, pane, str(msg.get("key") or ""))
            elif op == "action":
                self._op_action(slot, pane, str(msg.get("name") or ""))
            elif op == "set":
                fn = getattr(pane, "apply_value", None)
                if callable(fn):
                    fn(str(msg.get("address") or ""), str(msg.get("value") or ""))
            else:
                self._server.send({"type": "error",
                                   "message": "pane_action op must be select|key|action|set"})
                return
        except NotImplementedError as e:
            self._server.send({"type": "meta_message", "level": "warn",
                               "text": f"not on the face yet: {e}"})
        except Exception as e:  # noqa: BLE001 — a bad op degrades to a meta note
            self._server.send({"type": "meta_message", "level": "error",
                               "text": f"pane {slot}: {type(e).__name__}: {e}"})
        pop = getattr(pane, "pop_wire", None)
        if callable(pop):
            ev = pop()
            if ev:
                try:
                    self._server.send(ev)
                except Exception:
                    # Best-effort — the snapshot below refreshes the client.
                    pass
        self.send_snapshot()
        # Config cycle (side/width) is often Enter, not the Cycle button.
        # Push chrome after every pane op so the dock actually moves.
        self._push_chrome()

    def _push_chrome(self) -> None:
        try:
            send = getattr(self._server, "send", None)
            chrome = getattr(self._server, "chrome_state", None)
            if callable(send) and callable(chrome):
                send(chrome())
        except Exception:
            # Chrome push is advisory: a detached client re-reads the state
            # when it reconnects.
            pass

    def _op_select(self, pane: Any, msg: dict[str, Any]) -> None:
        idx = msg.get("index")
        select_index = getattr(pane, "select_index", None)
        if select_index is not None and idx is not None:
            # `index` is the RENDERED row, but select_index() counts only
            # SELECTABLE rows — panes interleave caption dividers ("─ Staged
            # (2)"), so the raw index would pick the wrong file. Translate by
            # skipping captions, and ignore a click on a caption itself.
            rows = pane.render().rows
            i = int(idx)
            if not (0 <= i < len(rows)) or rows[i].kind == _CAPTION:
                return
            select_index(sum(1 for r in rows[:i] if r.kind != _CAPTION))
            return
        address = str(msg.get("address") or "")
        if not address and idx is not None:
            # Pane without select_index: map the row index through the
            # projection (pane-agnostic), then use the reconstruct hook.
            rows = pane.render().rows
            if 0 <= int(idx) < len(rows):
                address = rows[int(idx)].address
        if address:
            # The reconstruct hook: same mount, new selection (state-ownership).
            pane.mount(pane.address, select=address)

    def _op_key(self, slot: str, pane: Any, key: str) -> None:
        """Local nav; an Enter the pane declines runs its primary action.

        The Dock surface's rule (``tui/dock_surface.py`` ``_route``): Enter on a leaf
        is not local navigation, so it falls through to ``actions()[0]`` — otherwise
        double-clicking a file only selects it and never opens/views it."""
        if pane.handle(key):
            return
        if key == "back" and self._close_viewer_slot(slot):
            return
        if key == "back":
            parent = _parent_address(pane.address)
            if parent is not None:
                self._dock.open_address(str(parent), slot=slot, focus=True)
                return
            # Scheme root → home hub (TUI I2). Home itself already no-ops.
            if getattr(pane.address, "scheme", "") != "home":
                if self.ensure_pane("home"):
                    self._ensure_dock()
                    self._dock.open_address("home://", slot="home", focus=True)
                    if self._slot_a == slot:
                        self._slot_put("a", "home")
                    elif self._slot_b == slot:
                        self._slot_put("b", "home")
                    self._emit_slots()
                return
        if key == "enter":
            actions = pane.actions()
            if actions:
                self._dispatch(slot, actions[0].outcome)

    def _op_action(self, slot: str, pane: Any, name: str) -> None:
        if name == "apply":
            apply = getattr(pane, "apply_selection", None)
            if callable(apply) and apply():
                # Chrome is pushed once at the end of handle().
                return
        if name == "clear":
            clearer = getattr(pane, "clear_typed", None)
            if callable(clearer):
                clearer()
                send = getattr(self._server, "send", None)
                if callable(send):
                    send({"type": "clear_input_history"})
                return
        for action in pane.actions():
            if action.name == name:
                self._dispatch(slot, action.outcome)
                return
        if name == "view" and self._view_selection(slot, pane):
            return
        self._server.send({"type": "error",
                           "message": f"pane {slot}: no action {name!r} "
                                      "(selection changed?)"})

    def _view_selection(self, slot: str, pane: Any) -> bool:
        """F3: view the selected leaf even when the pane has no ``view`` action."""
        sel = pane.selection() if hasattr(pane, "selection") else None
        node = getattr(sel, "node", None) if sel is not None else None
        addr = str(getattr(node, "address", "") or "")
        if not addr:
            return False
        return self._project_leaf_to_feed(addr, keep_view=slot)

    def _other_visual_view(self) -> str:
        """The pane id (or stream) occupying the slot that is not Home."""
        if self._slot_a == "home":
            v = self._slot_b or ""
        elif self._slot_b == "home":
            v = self._slot_a or ""
        else:
            return ""
        return v

    def _open_home_here(self, address: str) -> bool:
        """Morph the visual slot that is showing Home into the hub target."""
        from xlii.home_catalog import pane_id_for_address

        addr = (address or "").strip()
        pid = pane_id_for_address(addr) if addr else ""
        if addr == STREAM_VIEW or pid == STREAM_VIEW:
            pid = STREAM_VIEW
        if not pid or pid == "home":
            return False
        if pid != STREAM_VIEW and not self.ensure_pane(pid):
            return False
        if pid != STREAM_VIEW:
            self.send_snapshot()
        for s in ("a", "b"):
            if self._slot_get(s) != "home":
                continue
            other = self._slot_get("b" if s == "a" else "a")
            if other == pid:
                return True
            self._slot_put(s, pid)
            self._slot_focus = s
            self._emit_slots()
            return True
        self._reveal_view(pid)
        self.send_snapshot()
        return True

    def _open_home_target(self, address: str) -> bool:
        """Open a hub row in the other visual slot. Home stays the launcher."""
        from xlii.home_catalog import pane_id_for_address

        addr = (address or "").strip()
        pid = pane_id_for_address(addr) if addr else ""
        if addr == STREAM_VIEW or pid == STREAM_VIEW:
            pid = STREAM_VIEW
        if not pid or pid == "home":
            return False
        if self._other_visual_view() == pid:
            return True
        if pid in _HTML_SLOT_PANES:
            return self._open_html_slot(pid, keep_view="home")
        if pid != STREAM_VIEW and not self.ensure_pane(pid):
            return False
        self._reveal_view(pid, keep_view="home")
        self.send_snapshot()
        return True

    def _dispatch(self, slot: str, outcome: Any) -> None:
        from xlii.panes.dock import NAVIGATE, PREFILL, RETARGET_SLOT

        if slot == "home" and outcome.kind == NAVIGATE:
            addr = str(getattr(outcome, "address", "") or "")
            if addr and self._open_home_here(addr):
                return
        if slot == "home" and outcome.kind == RETARGET_SLOT:
            addr = str(getattr(outcome, "address", "") or "")
            if addr and self._open_home_target(addr):
                return
        if outcome.kind == RETARGET_SLOT:
            # Canvas-worthy open → the canvas slot (the work, focused).
            # PDF → other slot (listing | PDF). Makers → other slot (listing | form).
            # Other leaves still project into the transcript feed. Containers
            # morph the side dock (browse in place).
            addr = str(getattr(outcome, "address", "") or "")
            if addr.startswith("canvas://"):
                key = addr.split("://", 1)[-1].strip()
                self.reveal_canvas(key, keep_view=slot)
                return
            if addr and self._open_pdf_if(addr, keep_view=slot):
                return
            if addr and self._open_html_if(addr, keep_view=slot):
                return
            if addr and self._project_leaf_to_feed(addr, keep_view=slot):
                return
            # Container / non-leaf: morph THIS slot (one panel per tab).
            self._dock.open_address(outcome.address, slot=slot)
            return
        if outcome.kind == PREFILL:
            addr = str(getattr(outcome, "address", "") or "")
            if addr.startswith(("canvas://", "artifacts://")):
                key = addr.split("://", 1)[-1].strip()
                if key:
                    self.reveal_canvas(key, keep_view=slot)
        self._dock.dispatch(outcome, from_slot=slot)

    def _reveal_stream(self, keep_view: str = "") -> None:
        """Make the stream occupy a visible slot so a feed_view can be seen.

        View/source is an explicit “show this in the feed” — not a talk turn.
        Prefer an empty slot. If both slots are panes, replace the one that
        is not *keep_view* (the pane they viewed from).
        """
        if self._slot_a == STREAM_VIEW or self._slot_b == STREAM_VIEW:
            return
        if not self._slot_a:
            self.set_slot("a", STREAM_VIEW)
            return
        if not self._slot_b:
            self.set_slot("b", STREAM_VIEW)
            return
        keep = (keep_view or "").strip()
        for s in ("a", "b"):
            if self._slot_get(s) != keep:
                self._slot_put(s, STREAM_VIEW)
                self._slot_focus = s
                self._emit_slots()
                return

    def _is_pdf_address(self, address: str) -> bool:
        try:
            from xlii.addressing import classify, vfs_stat

            return classify(vfs_stat(address)) == "pdf"
        except Exception:
            return str(address).lower().split("?", 1)[0].split("#", 1)[0].endswith(".pdf")

    def _open_pdf_if(self, address: str, *, keep_view: str = "") -> bool:
        if not address or not self._is_pdf_address(address):
            return False
        return self._open_pdf_slot(address, keep_view=keep_view)

    def _open_pdf_slot(self, address: str, *, keep_view: str = "") -> bool:
        """Mount the PDF in a sticky viewer slot; show it opposite the listing."""
        self._viewer_address = address
        sticky = getattr(self, "_sticky_panes", None)
        if sticky is None:
            sticky = []
            self._sticky_panes = sticky
        if "pdf" not in sticky:
            sticky.append("pdf")
        self._ensure_dock()
        if self._dock is None:
            return False
        self._attach_pdf_viewer()
        self._reveal_view("pdf", keep_view)
        return True

    def open_plugin_form(
        self, plugin_id: str, action_id: str, *, seed: Optional[dict] = None
    ) -> bool:
        """Open ``pluginform://plugin/action`` in the other visual slot."""
        pid = (plugin_id or "").strip()
        aid = (action_id or "").strip()
        if not pid or not aid:
            return False
        addr = f"pluginform://{pid}/{aid}"
        already = (
            self._form_address == addr
            and (self._slot_a == "pluginform" or self._slot_b == "pluginform")
        )
        self._form_address = addr
        if seed:
            self._form_seed = dict(seed)
        if already:
            return True
        return self._open_html_slot("pluginform")

    def close_plugin_form(self) -> bool:
        """Success: drop the form slot. Stream keeps the receipt."""
        self._form_address = None
        self._form_seed = {}
        if not self._close_viewer_slot("pluginform"):
            return False
        self.send_snapshot()
        return True

    def _open_html_if(self, address: str, *, keep_view: str = "") -> bool:
        addr = (address or "").strip()
        if addr.startswith("taskmake://"):
            self._taskmake_address = addr
            return self._open_html_slot("taskmake", keep_view=keep_view)
        if addr.startswith("pluginmake://"):
            self._pluginmake_address = addr
            return self._open_html_slot("pluginmake", keep_view=keep_view)
        if addr.startswith("pluginform://"):
            self._form_address = addr
            return self._open_html_slot("pluginform", keep_view=keep_view)
        if addr.startswith("bindmake://"):
            return self._open_html_slot("bindmake", keep_view=keep_view)
        if addr.startswith("gigmake://"):
            return self._open_html_slot("gigmake", keep_view=keep_view)
        if addr.startswith("remotemake://"):
            return self._open_html_slot("remotemake", keep_view=keep_view)
        if addr.startswith("jidmake://"):
            return self._open_html_slot("jidmake", keep_view=keep_view)
        if addr.startswith("install://"):
            return self._open_html_slot("install", keep_view=keep_view)
        return False

    def _open_html_slot(self, pane_id: str, keep_view: str = "") -> bool:
        """Mount a closed-HTML pane in the other visual slot.

        Solo stream → maker takes the large slot, stream parks skinny.
        Listing open → listing | form (stream consumed), same as PDF.
        """
        pid = (pane_id or "").strip()
        if pid not in _HTML_SLOT_PANES:
            return False
        if not self.ensure_pane(pid):
            return False
        self._ensure_dock()
        if self._dock is None:
            return False
        if self._slot_a == pid or self._slot_b == pid:
            self._reveal_view(pid, keep_view)
            self.send_snapshot()
            return True
        a, b = self._slot_a, self._slot_b
        solo_stream = (a == STREAM_VIEW or not a) and not b
        if solo_stream:
            self._slot_a = pid
            self._slot_b = STREAM_VIEW
            self._slot_focus = "a"
            self._emit_slots()
            self.send_snapshot()
            return True
        self._reveal_view(pid, keep_view)
        self.send_snapshot()
        return True

    def _reveal_view(self, pane_id: str, keep_view: str = "") -> None:
        """Show *pane_id* in a visual slot. Prefer empty, then stream, then other."""
        if self._slot_a == pane_id:
            self._slot_focus = "a"
            self._emit_slots()
            return
        if self._slot_b == pane_id:
            self._slot_focus = "b"
            self._emit_slots()
            return
        if not self._slot_a:
            self.set_slot("a", pane_id)
            return
        if not self._slot_b:
            self.set_slot("b", pane_id)
            return
        for s in ("a", "b"):
            if self._slot_get(s) == STREAM_VIEW:
                self.set_slot(s, pane_id)
                return
        keep = (keep_view or "").strip()
        for s in ("a", "b"):
            if self._slot_get(s) != keep:
                self.set_slot(s, pane_id)
                return

    def _close_viewer_slot(self, dock_slot: str) -> bool:
        """Back on PDF / maker restores stream in that visual slot."""
        if dock_slot != "pdf" and dock_slot not in _HTML_SLOT_PANES:
            return False
        for s in ("a", "b"):
            if self._slot_get(s) != dock_slot:
                continue
            other = self._slot_get("b" if s == "a" else "a")
            if other and other != STREAM_VIEW:
                self.set_slot(s, STREAM_VIEW)
            else:
                self.close_slot(s)
            return True
        return False

    def _project_leaf_to_feed(self, address: str, *, keep_view: str = "") -> bool:
        """If *address* is a readable leaf, emit ``feed_view`` on the wire.

        Returns True when projected (caller should not morph the side dock).
        Caps body size for the wire; sets ``truncated`` when clipped.
        PDF leaves open the other-slot viewer instead.
        """
        from xlii.addressing import Address, vfs_read, vfs_stat

        if self._open_pdf_if(address, keep_view=keep_view):
            return True
        try:
            node = vfs_stat(address)
        except Exception:
            return False
        if getattr(node, "kind", None) != "leaf":
            return False
        try:
            raw = vfs_read(address)
        except Exception as e:  # noqa: BLE001
            self._server.send({
                "type": "meta_message", "level": "warn",
                "text": f"view failed: {type(e).__name__}: {e}",
            })
            return True  # consumed — don't morph dock into an error view
        title = getattr(node, "name", None) or Address.parse(address).target or address
        suffix = Path(str(title)).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            b64 = ""
            if 0 < len(raw) <= _MAX_INLINE_IMAGE_BYTES:
                b64 = base64.b64encode(raw).decode("ascii")
            self._server.send({
                "type": "feed_view",
                "kind": "image",
                "address": address,
                "title": str(title),
                "text": "",
                "b64": b64,
                "truncated": False,
                "lines": 0,
            })
            self._reveal_stream(keep_view)
            return True
        # Binary / huge: still offer a short notice rather than dump garbage.
        text = raw.decode("utf-8", errors="replace")
        if "\x00" in text[:4096]:
            self._server.send({
                "type": "meta_message", "level": "info",
                "text": f"binary leaf {address} — not shown in feed",
            })
            return True
        max_chars = 120_000
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars] + "\n… [truncated]"
        scheme = ""
        try:
            scheme = Address.parse(address).scheme
        except Exception:
            scheme = ""
        kind = "task" if scheme == "tasks" else (
            "skill" if scheme == "skills" else (
                "plugin" if scheme == "plugins" else (
                    "doc" if scheme in ("docs", "wiki", "xwiki") else "file"
                )
            )
        )
        self._server.send({
            "type": "feed_view",
            "kind": kind,
            "address": address,
            "title": str(title),
            "text": text,
            "truncated": truncated,
            "lines": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
        })
        self._reveal_stream(keep_view)
        return True
