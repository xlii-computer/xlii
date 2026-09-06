"""Project create/join/forget/drop and folder landing.

Mixin extracted from :mod:`xlii.serve_face.server`.
"""
from __future__ import annotations

import base64
import re
import time
from pathlib import Path
from typing import Any


class FaceProjectsMixin:
    """Project create/join/forget/drop and folder landing."""

    def _explorer_folder(self) -> "Path | None":
        """Files pane focus: selected container, else the listing directory."""
        from pathlib import Path

        from xlii.addressing import Address

        dock = getattr(self.deck, "_dock", None)
        pane = None if dock is None else dock.slots.get("explorer")
        if pane is None:
            return None
        addr = ""
        try:
            node = pane.selection().node
        except Exception:
            node = None
        if node is not None:
            addr = str(getattr(node, "address", "") or "")
        if not addr:
            addr = str(getattr(pane, "address", "") or "")
        if not addr.startswith("file://"):
            return None
        try:
            p = Path(Address.parse(addr).target).expanduser()
        except Exception:
            return None
        if p.is_file():
            p = p.parent
        return p if p.is_dir() else None

    def create_project(self, path: str, kind: str = "") -> bool:
        """Project → Adopt folder… — stamp `.xlii` on an existing dir and switch.

        A remote VFS address (``sftp://…``) mints a local stub and points Files
        there — same two kinds, no third door.
        """
        from xlii.desk_files import adopt_remote, is_remote_files_address
        from xlii.project_paths import resolve_adopt_path

        if is_remote_files_address(path):
            try:
                project = adopt_remote(path, kind=kind)
            except Exception as e:  # noqa: BLE001
                self.send({"type": "meta_message", "level": "error",
                           "text": f"adopt failed: {type(e).__name__}: {e}"})
                return False
            try:
                from xlii.repl_cmds.switch import switch_to_code_project

                ctx = getattr(self.state, "as_context_dict", None)
                payload = (
                    ctx() if callable(ctx)
                    else {"state": self.state, "console": getattr(self.state, "console", None)}
                )
                self._desk_announce = "adopted"
                switch_to_code_project(payload, project)
            except Exception as e:  # noqa: BLE001
                self.send({"type": "meta_message", "level": "error",
                           "text": f"switch failed: {type(e).__name__}: {e}"})
                return False
            return self._land_folder(project, announce="adopted")

        root, err = resolve_adopt_path(
            path,
            shell_cwd=getattr(self.state, "shell_cwd", None),
            selected=self._explorer_folder(),
        )
        if root is None:
            self.send({"type": "meta_message", "level": "warn",
                       "text": err or "need a folder to adopt"})
            return False
        tier = (
            "freeball" if getattr(self.state, "freeball", False)
            else "yolo" if getattr(self.state, "yolo", False)
            else "safe"
        )
        try:
            from xlii.session_boot import gate_code_entry

            result = gate_code_entry(
                root, init=True, interactive=False,
                trust_tier=tier, console=getattr(self.state, "console", None),
            )
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"adopt failed: {type(e).__name__}: {e}"})
            return False
        if getattr(result, "project", None) is None:
            self.send({"type": "meta_message", "level": "warn",
                       "text": f"could not adopt {root}"})
            return False
        from xlii.config import PROJECT_KIND_COLLECTION, normalize_project_kind

        stamped = normalize_project_kind(kind)
        if stamped is not None:
            try:
                result.project.kind = stamped
                if stamped == PROJECT_KIND_COLLECTION:
                    result.project.local_only = True
                result.project.save()
            except Exception:  # noqa: BLE001
                pass
        try:
            from xlii.repl_cmds.switch import switch_to_code_project

            ctx = getattr(self.state, "as_context_dict", None)
            payload = (
                ctx() if callable(ctx)
                else {"state": self.state, "console": getattr(self.state, "console", None)}
            )
            self._desk_announce = "adopted"
            switch_to_code_project(payload, result.project)
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"switch failed: {type(e).__name__}: {e}"})
            return False
        return self._land_folder(result.project, announce="adopted")

    def bind_project_files(self, address: str) -> bool:
        """Point the current desk's Files at a remote tree (standing-on-it bind)."""
        from xlii.desk_files import bind_files_root, is_remote_files_address

        address = (address or "").strip()
        if not is_remote_files_address(address):
            self.send({"type": "meta_message", "level": "warn",
                       "text": "need a remote address (sftp://host/path)"})
            return False
        project = getattr(self.state, "project", None)
        if project is None:
            self.send({"type": "meta_message", "level": "warn",
                       "text": "no current project to bind Files on"})
            return False
        try:
            canon = bind_files_root(project, address)
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"bind failed: {type(e).__name__}: {e}"})
            return False
        try:
            if self._deck is not None:
                self._deck.sync_pack(force=True)
                self._deck.send_snapshot()
            self.send(self.chrome_state())
        except Exception:  # noqa: BLE001
            pass
        self.send({"type": "meta_message", "level": "info",
                   "text": f"Files → {canon}"})
        return True

    def create_in_project(self, kind: str, name: str) -> bool:
        """Project → New file/folder… under project root or shell cwd."""
        from pathlib import Path

        name = (name or "").strip()
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            self.send({"type": "meta_message", "level": "warn",
                       "text": "need a simple name (no path separators)"})
            return False
        project = getattr(self.state, "project", None)
        base = getattr(self.state, "shell_cwd", None) or getattr(
            project, "project_root", None
        )
        if not base:
            self.send({"type": "meta_message", "level": "warn",
                       "text": "no project/cwd to create into"})
            return False
        parent = Path(str(base)).expanduser()
        dest = parent / name
        try:
            if kind == "folder":
                dest.mkdir(parents=True, exist_ok=False)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    raise FileExistsError(str(dest))
                dest.touch()
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"create {kind} failed: {type(e).__name__}: {e}"})
            return False
        self.send({"type": "meta_message", "level": "info",
                   "text": f"created {kind} · {dest}"})
        try:
            self.deck.ensure_pane("explorer")
            self.deck.open_pane("explorer")
        except Exception:  # noqa: BLE001
            pass
        return True

    def save_screenshot(self, b64: str = "", name: str = "", kind: str = "") -> bool:
        """Options → Save screenshot — PNG of the face window, Desktop.

        The webview cannot produce a usable shot (WebKit taints canvas;
        SVG+foreignObject opens blank). A client PNG is accepted; otherwise
        the server grabs the window.
        """
        from xlii.atomicio import write_bytes_atomic
        from xlii.project_paths import user_home
        from xlii.window_shot import grab_face_window

        desktop = user_home() / "Desktop"
        dest_dir = desktop if desktop.is_dir() else user_home()
        proj = getattr(getattr(self.state, "project", None), "name", "") or "xlii"
        safe = re.sub(r"[^\w.-]+", "-", str(proj)).strip("-")[:40] or "xlii"
        fname = Path(name or "").name
        if not fname.lower().endswith(".png"):
            fname = f"xlii-{safe}-{time.strftime('%Y%m%d-%H%M%S')}.png"
        dest = dest_dir / fname

        raw = (b64 or "").strip()
        data = b""
        if raw:
            try:
                data = base64.b64decode(raw, validate=False)
            except Exception:
                data = b""
            is_png = len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n"
            if not is_png:
                self.send({"type": "meta_message", "level": "warn",
                           "text": "screenshot: not an image"})
                return False
            if len(data) > 12 * 1024 * 1024:
                self.send({"type": "meta_message", "level": "warn",
                           "text": "screenshot: too large"})
                return False
            try:
                write_bytes_atomic(dest, data, mode=0o644)
            except Exception as e:  # noqa: BLE001
                self.send({"type": "meta_message", "level": "error",
                           "text": f"screenshot failed: {type(e).__name__}: {e}"})
                return False
            self.send({"type": "meta_message", "level": "info",
                       "text": f"screenshot → {dest}"})
            return True

        if grab_face_window(dest):
            self.send({"type": "meta_message", "level": "info",
                       "text": f"screenshot → {dest}"})
            return True
        self.send({"type": "meta_message", "level": "warn",
                   "text": "screenshot: could not grab the face window"})
        return False

    def _land_folder(self, project: Any, *, announce: str = "switched") -> bool:
        """Apply kind landing: collection/persona → talk, code → lab.

        Does not decide Home (``go_home``). Flip stays user-owned after this.
        """
        from pathlib import Path as P

        from xlii.config import project_kind, project_landing_pack, project_landing_posture

        root = getattr(project, "project_root", None)
        try:
            key = str(P(root).resolve()) if root is not None else ""
        except OSError:
            key = str(root or "")
        if key and key == getattr(self, "_landed_root", ""):
            return True
        if key:
            self._landed_root = key
        from xlii.workbench import BUILTIN_WORKBENCHES, save_active_type

        kind = project_kind(project)
        posture = project_landing_posture(project)
        pack_name = project_landing_pack(project)
        xli = getattr(project, "xli_dir", None)
        if xli is not None:
            try:
                save_active_type(xli, pack_name)
            except Exception:  # noqa: BLE001
                pass
        self.state.workbench = BUILTIN_WORKBENCHES.get(pack_name)
        try:
            self.state.scratch = False
        except Exception:  # noqa: BLE001
            pass
        self._home_pack_settled = True
        self._wb_posture_applied = None
        if self._deck is not None:
            self._deck.sync_pack(force=True)
        self._set_posture(posture)
        try:
            from xlii.cmds.sessions.resolve import _lookup_persona
            from xlii.persona import persona_id_from_project_name, talk_persona_id

            if persona_id_from_project_name(getattr(project, "name", "") or ""):
                pid = talk_persona_id(state=self.state, project=project,
                                      cfg=getattr(self.state, "cfg", None))
                live = _lookup_persona(pid)
                if live is not None:
                    self.state.persona = live
        except Exception:  # noqa: BLE001
            pass
        try:
            from xlii.recent_desks import touch_desk

            touch_desk(project)
        except Exception:  # noqa: BLE001
            pass
        try:
            from xlii.face_receipt import note_project

            note_project(project, reason="alive")
        except Exception:  # noqa: BLE001
            pass
        try:
            self.send(self.mode_state())
            self.send(self.chrome_state())
            self.deck.send_snapshot()
            self.send(self.pane_catalog())
            self.send(self.plugin_catalog())
            self.send(self.workbench_catalog())
        except Exception:  # noqa: BLE001
            pass
        self._bind_open_stream(project)
        name = getattr(project, "name", "") or "folder"
        island = "talk" if self.posture == "chat" else "lab"
        self.send({"type": "meta_message", "level": "info",
                   "text": f"{announce} {name} · {kind} · {island}"})
        return True

    def _run_off_reader(self, fn, *, name: str = "face-admin") -> None:
        """Run *fn* off the websocket reader so ``confirm.ask`` can be answered.

        Pane actions land on the reader thread. Blocking there deadlocks the
        confirm bar — prune/delete then freeze Open too.
        """
        import threading

        threading.Thread(target=fn, name=name, daemon=True).start()

    def join_project(self, name: str) -> bool:
        """Live-**switch** into a registered project from Home.

        Wire/method name stays ``join_project`` for compatibility; product verb
        is *switch*. Same ``switch_to_code_project`` path as ``/project switch``,
        flips posture to code (lab), refreshes chrome/deck. Returns True on
        success.
        """
        name = (name or "").strip()
        if name.startswith("@"):
            return self.join_project_at(name[1:].strip())
        if not name:
            self.send({"type": "meta_message", "level": "warn",
                       "text": "switch needs a project name"})
            return False
        if self._turn_mutation_busy():
            self.send({"type": "meta_message", "level": "warn",
                       "text": self._turn_mutation_busy_message("switch project")})
            return False
        try:
            from xlii.project_resolver import resolve_registered_project
            from xlii.registry import Registry
            from xlii.repl_cmds.switch import switch_to_code_project
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"switch unavailable: {type(e).__name__}: {e}"})
            return False
        try:
            reg = Registry.load()
            res = resolve_registered_project(name, registry=reg)
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"switch failed: {type(e).__name__}: {e}"})
            return False
        if getattr(res, "project", None) is None:
            self.send({"type": "meta_message", "level": "warn",
                       "text": f"no registered project matching {name!r} — "
                               "xlii project list · xlii init in a tree"})
            return False
        from xlii.project_paths import is_home_desk_project

        if is_home_desk_project(res.project):
            return self.go_home()
        ctx = self.state.as_context_dict()
        try:
            switch_to_code_project(ctx, res.project, reload_surface=True)
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"switch failed: {type(e).__name__}: {e}"})
            return False
        return self._land_folder(res.project, announce="switched")

    def sync_fabric_projects(self) -> bool:
        """Throne: pull node registries, push the shared catalog. Home stays here."""
        try:
            from xlii.fabric import _default_connect, _default_require_remote
            from xlii.fabric_projects import sync_fabric_projects
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"fabric sync unavailable: {type(e).__name__}: {e}"})
            return False
        nodes = getattr(getattr(self.state, "cfg", None), "fabric_nodes", None) or {}
        if not nodes:
            self.send({"type": "meta_message", "level": "warn",
                       "text": "no fabric nodes — xlii fabric add-node"})
            return False
        result = sync_fabric_projects(
            nodes,
            connect=_default_connect,
            require_remote=_default_require_remote,
            this_node="throne",
        )
        bits = [r.summary() for r in result.nodes]
        if result.pushed:
            bits.extend(result.pushed)
        text = " · ".join(bits) if bits else "nothing to merge"
        level = "warn" if result.errors else "info"
        extra = ("; " + "; ".join(result.errors)) if result.errors else ""
        self.send({"type": "meta_message", "level": level,
                   "text": f"fabric projects: {text}{extra}"})
        self.send(self.chrome_state())
        return not result.errors

    def create_fabric_project(self, node: str, name: str) -> bool:
        """Mkdir on that node, stub here, Files at sftp. Switch onto it."""
        try:
            from xlii.fabric import _default_connect, _default_require_remote
            from xlii.fabric_projects import create_fabric_project
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"fabric new unavailable: {type(e).__name__}: {e}"})
            return False
        cfg = getattr(self.state, "cfg", None)
        roster = getattr(cfg, "fabric_nodes", None) or {}
        try:
            created = create_fabric_project(
                name, node, roster=roster,
                connect=_default_connect, require_remote=_default_require_remote,
            )
        except ValueError as e:
            self.send({"type": "meta_message", "level": "warn", "text": str(e)})
            return False
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"{type(e).__name__}: {e}"})
            return False
        note = " · on the node" if created.pushed else ""
        self.send({"type": "meta_message", "level": "info",
                   "text": f"created {created.name} · {created.node}{note} — "
                           "work it remote. /sync if you want a collection"})
        return self.join_project_at(created.path)

    def join_project_at(self, path: str) -> bool:
        """Switch by registry path — unique when several rows share a name."""
        path = (path or "").strip()
        if not path:
            self.send({"type": "meta_message", "level": "warn",
                       "text": "switch needs a path"})
            return False
        if self._turn_mutation_busy():
            self.send({"type": "meta_message", "level": "warn",
                       "text": self._turn_mutation_busy_message("switch project")})
            return False
        from pathlib import Path as P

        from xlii.config import ProjectConfig
        from xlii.project_paths import is_home_desk_project
        from xlii.project_resolver import project_is_alive
        from xlii.registry import Registry
        from xlii.repl_cmds.switch import switch_to_code_project

        entry = Registry.load().find_by_path(path)
        if entry is None:
            # Stored path may not resolve (already absolute).
            entry = next(
                (e for e in Registry.load().entries if e.path == path),
                None,
            )
        if entry is None:
            self.send({"type": "meta_message", "level": "warn",
                       "text": f"no registered project at {path}"})
            return False
        if not project_is_alive(entry):
            self.send({"type": "meta_message", "level": "warn",
                       "text": f"{entry.name} is a ghost — prune or delete the row"})
            return False
        project = ProjectConfig.load(P(entry.path).expanduser())
        if project is None:
            self.send({"type": "meta_message", "level": "warn",
                       "text": f"couldn't load {entry.name}"})
            return False
        if is_home_desk_project(project):
            return self.go_home()
        ctx = self.state.as_context_dict()
        try:
            switch_to_code_project(ctx, project, reload_surface=True)
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"switch failed: {type(e).__name__}: {e}"})
            return False
        return self._land_folder(project, announce="switched")

    def forget_project(self, path: str) -> bool:
        """Drop one registry row by path (ghosts / duplicate names)."""
        path = (path or "").strip()
        if not path:
            self.send({"type": "meta_message", "level": "warn",
                       "text": "forget needs a path"})
            return False
        from pathlib import Path as P

        from xlii.config import ProjectConfig
        from xlii.project_paths import is_home_desk_project
        from xlii.registry import Registry

        try:
            pc = ProjectConfig.load(P(path))
            if pc is not None and is_home_desk_project(pc):
                self.send({"type": "meta_message", "level": "warn",
                           "text": "won't forget the home desk"})
                return False
        except Exception:
            # An unreadable config can't be matched against the home desk; the
            # forget proceeds rather than wedging on a bad file.
            pass
        reg = Registry.load()
        if not reg.remove_by_path(path):
            self.send({"type": "meta_message", "level": "warn",
                       "text": f"no registry row for {path}"})
            return False
        reg.save()
        self.send({"type": "meta_message", "level": "success",
                   "text": f"forgot {path}"})
        if getattr(self, "deck", None) is not None:
            try:
                self.deck.send_snapshot()
            except Exception:
                # The registry change is already committed; the deck refreshes
                # on the client's next snapshot request.
                pass
        return True

    def prune_projects(self) -> bool:
        """Drop every registry row whose tree is gone (pytest /tmp leftovers)."""
        self._run_off_reader(self._prune_projects_body, name="face-prune")
        return True

    def _prune_projects_body(self) -> None:
        from xlii.project_resolver import project_is_alive
        from xlii.registry import Registry

        reg = Registry.load()
        dead = [e for e in reg.entries if not project_is_alive(e)]
        if not dead:
            self.send({"type": "meta_message", "level": "info",
                       "text": "no ghost registry rows"})
            return
        try:
            if not self.confirm.ask(
                f"forget {len(dead)} ghost row(s)? registry only — nothing on disk"
            ):
                self.send({"type": "meta_message", "level": "info",
                           "text": "prune cancelled"})
                return
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"confirm failed: {type(e).__name__}: {e}"})
            return
        dead = reg.prune_dead()
        reg.save()
        names = ", ".join(sorted({e.name for e in dead}))
        self.send({"type": "meta_message", "level": "success",
                   "text": f"pruned {len(dead)} ghost(s): {names}"})
        if getattr(self, "deck", None) is not None:
            try:
                self.deck.send_snapshot()
            except Exception:
                # The prune already happened; the deck refreshes on the
                # client's next snapshot request.
                pass

    def drop_project(self, name: str) -> bool:
        """Remove a registered project from the face (confirm first).

        Source files stay. ``.xlii/``, the registry row, and the cloud
        Collection go. Chat islands are just another registry row.
        """
        name = (name or "").strip()
        if not name:
            self.send({"type": "meta_message", "level": "warn",
                       "text": "delete needs a project name"})
            return False
        if self._turn_mutation_busy():
            self.send({"type": "meta_message", "level": "warn",
                       "text": self._turn_mutation_busy_message("delete project")})
            return False
        self._run_off_reader(lambda: self._drop_project_body(name), name="face-drop")
        return True

    def _drop_project_body(self, name: str) -> None:
        from xlii.cmds.project.rm import _resolve_rm_target, _run_project_rm

        project = _resolve_rm_target(name)
        if project is None:
            self.send({"type": "meta_message", "level": "warn",
                       "text": f"no registered project matching {name!r}"})
            return False
        prompt = (
            f"remove {project.name}? source files stay. "
            f".xlii/, registry, and cloud collection go."
        )
        try:
            if not self.confirm.ask(prompt):
                self.send({"type": "meta_message", "level": "info",
                           "text": "delete cancelled"})
                return False
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"confirm failed: {type(e).__name__}: {e}"})
            return False

        is_current = False
        try:
            live = getattr(self.state, "project", None)
            if live is not None:
                from pathlib import Path

                is_current = (
                    Path(project.project_root).resolve()
                    == Path(live.project_root).resolve()
                )
        except OSError:
            is_current = False

        clients = None
        try:
            from xlii.client import Clients

            clients = Clients.from_config(getattr(self.state, "cfg", None))
        except Exception:
            # Face still tears down local + registry; cloud stays if no clients.
            clients = None

        class _WireConsole:
            def __init__(self, send):
                self._send = send

            def print(self, *a, **k):
                text = " ".join(str(x) for x in a)
                if text:
                    self._send({"type": "meta_message", "level": "info", "text": text})

            def status(self, *_a, **_k):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        rc = _run_project_rm(
            clients, project,
            keep_local=False, local_only=(clients is None),
            dry_run=False, assume_yes=True,
            console=_WireConsole(self.send),
        )
        if rc != 0:
            self.send({"type": "meta_message", "level": "error",
                       "text": f"delete failed (rc {rc})"})
            return False
        self.send({"type": "meta_message", "level": "success",
                   "text": f"removed {project.name}"})
        if is_current:
            return self.go_home()
        if getattr(self, "deck", None) is not None:
            try:
                self.deck.send_snapshot()
            except Exception:
                # The project is already removed; the deck refreshes on the
                # client's next snapshot request.
                pass
        return True
