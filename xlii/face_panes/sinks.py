from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from xlii.turn_events import FileOut

from .views import _MAX_INLINE_IMAGE_BYTES, _with_context

class _FailedPane:
    """A slot whose mount raised — renders as a note, accepts nothing."""

    def __init__(self, name: str, note: str) -> None:
        self.note = note
        self.address = name  # display only; _project short-circuits on type

    def render(self):  # pragma: no cover — _project short-circuits on type
        raise RuntimeError(self.note)

    def actions(self):
        return []

    def handle(self, key: str) -> bool:
        return False


# --- the face's sinks (the TUI App*Sink shapes, wire-native) -------------------

class _FaceTurnSink:
    def __init__(self, server: Any) -> None:
        self._server = server

    def submit(self, prompt: str, *, context: str = "") -> None:
        if not self._server.submit(_with_context(prompt, context)):
            self._server.send({"type": "meta_message", "level": "warn",
                               "text": "busy — the pane's turn is queued behind "
                                       "nothing; resend after this turn"})


class _FaceInputSink:
    """PREFILL → the wire event (first emitter). No claim(): CLAIM_INPUT
    raises in Dock.dispatch and surfaces as a 'not on the face yet' note.

    Special case: ``/project switch <name>`` runs as a live join on the face —
    chat posture has no ``/project`` slash, so review-before-run would fail.
    """

    def __init__(self, server: Any) -> None:
        self._server = server

    def prefill(self, text: str) -> None:
        t = (text or "").strip()
        if t.startswith("/project files "):
            addr = t[len("/project files "):].strip()
            if addr and hasattr(self._server, "bind_project_files"):
                self._server.bind_project_files(addr)
                return
        if t.startswith("/project switch "):
            rest = t[len("/project switch "):].strip()
            if rest.startswith("@"):
                target = rest[1:].strip()
                if target and hasattr(self._server, "join_project_at"):
                    self._server.join_project_at(target)
                    return
            name = rest.split(maxsplit=1)[0] if rest else ""
            if name and hasattr(self._server, "join_project"):
                self._server.join_project(name)
                return
        if t.startswith("/project rm "):
            name = t[len("/project rm "):].strip().split(maxsplit=1)
            if name and hasattr(self._server, "drop_project"):
                self._server.drop_project(name[0])
                return
        if t.startswith("/project forget "):
            path = t[len("/project forget "):].strip()
            if path and hasattr(self._server, "forget_project"):
                self._server.forget_project(path)
                return
        if t == "/project prune" or t.startswith("/project prune "):
            if hasattr(self._server, "prune_projects"):
                self._server.prune_projects()
                return
        # Plugins pane markers (chat has no /plugin slash — live wire paths).
        if t.startswith("__plugin_toggle__:"):
            pid = t.split(":", 1)[1].strip()
            if pid and hasattr(self._server, "_start_plugin_subscribe"):
                # reuse worker if present; else inline toggle
                pass
            if pid:
                self._server.send({"type": "plugin_subscribe", "plugin": pid})
                # Direct toggle path (client would send this; we run it server-side).
                try:
                    from xlii.plugin import Plugin, toggle_subscription
                    p = Plugin(id=pid)
                    if p.exists():
                        outcome, message = toggle_subscription(self._server.state, pid)
                        self._server.send({
                            "type": "meta_message",
                            "level": "info" if outcome != "error" else "error",
                            "text": message or f"plugin {pid}: {outcome}",
                        })
                        # Refresh catalog ● markers on the client.
                        try:
                            cat = self._server.plugin_catalog()
                            self._server.send(cat)
                        except Exception:  # noqa: BLE001
                            pass
                except Exception as e:  # noqa: BLE001
                    self._server.send({
                        "type": "meta_message", "level": "error",
                        "text": f"plugin toggle: {type(e).__name__}: {e}",
                    })
            return
        if t.startswith("__plugin_call__:"):
            self._seed_or_start_plugin_call(t)
            return
        from xlii.turn_events import Prefill
        from xlii.ws_protocol import serialize_event

        self._server.send(serialize_event(Prefill(text=text)))

    def _seed_or_start_plugin_call(self, text: str) -> None:
        """Run now if the action needs no holes; else seed `/plugin call … name=`."""
        from xlii.plugin import Plugin
        from xlii.plugin_call import (
            missing_required,
            parse_call_line,
            seed_call_line,
        )
        from xlii.turn_events import Prefill
        from xlii.ws_protocol import serialize_event

        try:
            parsed = parse_call_line(text)
        except ValueError as e:
            self._server.send({"type": "meta_message", "level": "error", "text": str(e)})
            return
        if parsed is None:
            return
        plugin_id, action_id, params = parsed
        p = Plugin(id=plugin_id)
        action = None
        try:
            m = p.manifest() if p.exists() else None
            action = m.get_action(action_id) if m is not None else None
        except Exception:
            action = None
        if action is None:
            self._server.send({
                "type": "meta_message", "level": "error",
                "text": f"no action {plugin_id}.{action_id}",
            })
            return
        from xlii.plugin_form import action_needs_form, auth_form_target

        setup = auth_form_target(p)
        if setup is not None:
            form_plugin, form_action = setup
            if self._server.deck.open_plugin_form(form_plugin, form_action):
                self._server.send({
                    "type": "meta_message", "level": "info",
                    "text": f"{form_plugin}.{form_action} — fill the form. "
                            "Secrets stay off the agent.",
                })
                return
        if action_needs_form(action, params):
            if self._server.deck.open_plugin_form(plugin_id, action_id, seed=params):
                self._server.send({
                    "type": "meta_message", "level": "info",
                    "text": f"{plugin_id}.{action_id} — fill the form. "
                            "Secrets stay off the agent.",
                })
                return
        holes = missing_required(action, params)
        if holes:
            seed = seed_call_line(plugin_id, action_id, action)
            descs = []
            for spec in action.params.values():
                if spec.name in holes:
                    bit = spec.name
                    if spec.description:
                        bit += f" — {spec.description}"
                    descs.append(bit)
            need = "; ".join(descs) or ", ".join(holes)
            self._server.send(serialize_event(Prefill(text=seed)))
            self._server.send({
                "type": "meta_message", "level": "info",
                "text": f"{plugin_id}.{action_id} needs {need}. "
                        "Fill the blanks in the input and send.",
            })
            return
        # Typed /plugin call already owns the face worker's _busy. Run
        # inline — _start_plugin_call would see that busy and no-op.
        exec_fn = getattr(self._server, "_exec_plugin_call", None)
        if callable(exec_fn):
            exec_fn(plugin_id, action_id, params)
            return
        if not hasattr(self._server, "_start_plugin_call"):
            return
        if not self._server._start_plugin_call(plugin_id, action_id, params):
            self._server.send({
                "type": "meta_message", "level": "warn",
                "text": "busy — try the plugin action again after this turn",
            })


class _FaceMediaSink:
    def __init__(self, server: Any) -> None:
        self._server = server

    def show(self, address: str) -> None:
        from xlii.addressing import Address
        from xlii.multimodal import classify_kind
        from xlii.ws_protocol import serialize_event

        addr = Address.parse(address)
        if addr.scheme != "file" or not addr.target:
            self._server.send({"type": "meta_message", "level": "warn",
                               "text": f"can't show {address} on the face"})
            return
        path = Path(addr.target).expanduser()
        kind = classify_kind(path)
        try:
            size = path.stat().st_size
        except OSError as e:
            self._server.send({"type": "meta_message", "level": "warn",
                               "text": f"can't show {address} on the face ({e})"})
            return
        # Only images inline (a data: URL of anything else is unusable), and
        # only after the size is known — never read a huge file into memory.
        from xlii.artifacts import locate_made_file

        root = getattr(getattr(getattr(self._server, "state", None), "project", None),
                       "project_root", None)
        durable, vfs = locate_made_file(path, root)
        b64 = ""
        if kind == "image" and size <= _MAX_INLINE_IMAGE_BYTES:
            b64 = base64.b64encode(path.read_bytes()).decode()
        self._server.send(serialize_event(FileOut(
            name=durable.name,
            kind="image" if kind == "image" else (path.suffix.lstrip(".").lower() or "file"),
            b64=b64,
            path=str(durable),
            address=vfs,
        )))


class _FaceSessionSink:
    def __init__(self, state: Any, server: Any = None) -> None:
        self._state = state
        self._server = server

    def attach(self, address: str) -> None:
        # Files / canvas / artifacts → Focus (once). Skills / docs / wiki stay riders.
        if self._server is not None and hasattr(self._server, "handle_focus"):
            self._server.handle_focus({"address": address})
            return
        from xlii.attach import attach_address, focus_address

        if focus_address(self._state, address) is None:
            attach_address(self._state, address)

    def detach(self, address: str) -> None:
        from xlii.attach import detach_address, unfocus_address

        unfocus_address(self._state, {"address": address})
        detach_address(self._state, address)


class _FaceJobSink:
    def __init__(self, state: Any, server: Any) -> None:
        self._state = state
        self._server = server

    def spawn(self, address: str, *, text: str = "") -> None:
        from xlii.addressing import Address
        from xlii.repl_cmds.tasks import spawn_saved_task

        addr = Address.parse(address)
        if addr.scheme != "tasks":
            return
        name = (text or addr.key or addr.target or "").strip()
        if not name:
            return
        job_id = spawn_saved_task(self._state, name)
        if job_id:
            self._server.send({"type": "meta_message", "level": "info",
                               "text": f"▶ background job {job_id} · {name}"})
