"""Chrome, command/plugin/workbench/pane catalogs.

Mixin extracted from :mod:`xlii.serve_face.server`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from xlii.serve_face.wire import _UNAVAILABLE_SLASH
from xlii.turn_events import CommandCatalog, CommandEntry, ModeState
from xlii.ws_protocol import serialize_event


class FaceCatalogsMixin:
    """Chrome, command/plugin/workbench/pane catalogs."""

    # ------------------------------------------------------------ chrome

    def _chrome_binds(self) -> list:
        try:
            from xlii.binds import chrome_rows

            project = getattr(self.state, "project", None)
            xli = getattr(project, "xli_dir", None) if project is not None else None
            return chrome_rows(xli)
        except Exception:
            return []

    def _fabric_node_names(self) -> list[str]:
        try:
            cfg = getattr(self.state, "cfg", None)
            nodes = getattr(cfg, "fabric_nodes", None) or {}
            return sorted(str(k) for k in nodes if str(k).strip())
        except Exception:
            return []

    def chrome_state(self) -> dict[str, Any]:
        """The F1 HUD rail: project · workbench · persona · model · posture ·
        provider readiness. Best-effort per field — the rail degrades to
        blanks, never to an error frame."""
        from pathlib import Path

        from xlii.turn_events import ChromeState

        self._prefer_home_pack()
        state = self.state
        project = getattr(state, "project", None)
        wb = getattr(state, "workbench", None)
        persona = model = ""
        try:
            from xlii.persona import talk_persona_id

            persona = talk_persona_id(state=state, project=project,
                                      cfg=getattr(state, "cfg", None))
        except Exception:
            persona = ""  # HUD degrades to blank — persona resolution is optional
        try:
            from xlii.agent import resolve_orchestrator_model_for_session

            model, _ = resolve_orchestrator_model_for_session(
                cfg=state.cfg, agent=state.agent, state=state
            )
        except Exception:
            model = getattr(getattr(state, "cfg", None), "orchestrator_model", "") or ""
        ready = total = 0
        try:
            import os

            from xlii.providers import load_manifests

            manifests = load_manifests(getattr(project, "xli_dir", None))
            total = len(manifests)
            ready = sum(
                1 for m in manifests.values()
                if m.auth.kind and os.environ.get(m.auth.key_env, "").strip()
            )
        except Exception:
            ready = total = 0  # provider manifests are optional — degrade to zeros
        quick: list = []
        try:
            from xlii.workbench import quick_launch_buttons

            quick = list(quick_launch_buttons(wb))
        except Exception:
            quick = []
        surface = "code"
        try:
            from xlii.status import surface_axis

            surface = surface_axis(state) or "code"
        except Exception:
            if getattr(state, "scratch", False):
                surface = "scratch"
            elif self.posture == "chat":
                surface = "chat"
        # three-faces: Home/scratch is not a "project" identity — omit name so
        # the face does not show a project pill until they switch into a real tree.
        proj_name = getattr(project, "name", "") or ""
        # Home roam has no folder identity. A named collection (even
        # scratch/<pile>) keeps its name — it's a real directory.
        # Persona islands (chat/<name>) are the persona chip, not a second label.
        if surface == "scratch" or proj_name == "scratch/home":
            proj_name = ""
        else:
            try:
                from xlii.persona import persona_id_from_project_name as _pid_from_proj

                if _pid_from_proj(proj_name):
                    proj_name = ""
            except Exception:
                # Persona mapping is optional here; on any failure keep the
                # current project label unchanged.
                pass
        # Home always offers switch first (even if pack predates the home type).
        if surface == "scratch":
            ids = {str(b.get("id") or "") for b in quick}
            switch_ids = ("switch", "join")  # join = legacy id
            has_switch = bool(ids & set(switch_ids))
            if not has_switch and "projects" not in ids:
                quick = [{"id": "switch", "label": "switch",
                          "action": "pane:projects"}] + quick
            elif not has_switch and "projects" in ids:
                # Prefer the product label "switch" at the front.
                rest = [b for b in quick if b.get("id") != "projects"]
                quick = [{"id": "switch", "label": "switch",
                          "action": "pane:projects"}] + rest
            elif "join" in ids and "switch" not in ids:
                # Normalize legacy join id → product label without reordering.
                quick = [
                    ({"id": "switch", "label": "switch", "action": "pane:projects"}
                     if b.get("id") == "join" else b)
                    for b in quick
                ]
        chat_tier = "off"
        try:
            sess = getattr(getattr(state, "agent", None), "session", None)
            raw_t = getattr(sess, "chat_tier", None) if sess is not None else None
            chat_tier = str(raw_t) if raw_t else "off"
        except Exception:
            chat_tier = "off"
        trust = "safe"
        try:
            from xlii.status import trust_axis

            trust = trust_axis(state) or "safe"
        except Exception:
            if getattr(state, "freeball", False):
                trust = "freeball"
            elif getattr(state, "yolo", False):
                trust = "yolo"
        meter = session = ""
        try:
            from xlii.session_meter import context_meter_text, session_usage_text

            meter = context_meter_text(state, model=model or "")
            session = session_usage_text(state)
        except Exception:
            meter = session = ""
        pane_side = "right"
        pane_width_pct = 0
        try:
            cfg = getattr(state, "cfg", None)
            raw_side = str(getattr(cfg, "tui_panel_side", "") or "right").lower()
            pane_side = "left" if raw_side == "left" else "right"
            pane_width_pct = int(getattr(cfg, "panel_width_pct", 0) or 0)
        except Exception:
            pane_side, pane_width_pct = "right", 0
        cwd_label = ""
        try:
            from xlii.desk_files import files_address

            pointed = files_address(project)
            if pointed:
                cwd_label = pointed
        except Exception:
            cwd_label = ""
        if not cwd_label:
            try:
                from xlii.repl import format_shell_cwd

                cwd_label = format_shell_cwd(state) or ""
            except Exception:
                cwd_label = ""
        jobs_active = jobs_unseen = 0
        jobs_pills: list = []
        try:
            from xlii.jobs import _peek_registry, chrome_pills

            reg = _peek_registry(state)
            if reg is not None:
                jobs_active = len(reg.active_jobs())
                jobs_unseen = len(reg.unseen_done())
            jobs_pills = chrome_pills(state)
        except Exception:
            jobs_active = jobs_unseen = 0
            jobs_pills = []
        slot_a, slot_b, slot_focus = "stream", "", "a"
        slot_catalog: list = []
        try:
            slot_a, slot_b, slot_focus = self.deck.slot_tuple()
            slot_catalog = self.deck.slot_catalog()
        except Exception:
            slot_a, slot_b, slot_focus = "stream", "", "a"
            slot_catalog = []
        journal = ""
        try:
            from xlii.status import journal as journal_badge

            if str(journal_badge(state) or "").endswith("●"):
                journal = "on"
        except Exception:
            journal = ""
        browser = browser_url = ""
        try:
            from xlii.agent_browser import session_peek

            peek = session_peek()
            if peek.ok:
                browser = "hidden" if peek.headless else "window"
                browser_url = (peek.url or peek.title or "").strip()
        except Exception:
            browser = browser_url = ""
        recent: list = []
        try:
            from xlii.recent_desks import list_recent

            recent = list_recent()
            here = ""
            if surface != "scratch":
                root = getattr(getattr(state, "project", None), "project_root", None)
                if root is not None:
                    here = str(Path(root).resolve())
            if here:
                for row in recent:
                    if str(row.get("path") or "") == here:
                        row["current"] = True
        except Exception:
            recent = []
        term_cwd = "this project"
        try:
            from xlii.desk import term_cwd_label

            term_cwd = term_cwd_label(getattr(state, "cfg", None))
        except Exception:
            term_cwd = "this project"
        face_skin = ""
        face_fkeys = True
        face_bold = False
        try:
            cfg = getattr(state, "cfg", None)
            raw_skin = str(getattr(cfg, "face_skin", "") or "").strip().lower()
            from xlii.skin_packs import is_known_skin

            if is_known_skin(raw_skin):
                face_skin = raw_skin
            if cfg is not None:
                face_fkeys = bool(getattr(cfg, "face_fkeys", True))
                face_bold = bool(getattr(cfg, "face_bold", False))
        except Exception:
            face_skin = ""
            face_fkeys = True
            face_bold = False
        howto: list = []
        about_line = about_credit = ""
        about_taglines: list = []
        about_version = about_signs = ""
        about_facts: list = []
        try:
            from xlii.about import about_payload, howto_menu_rows

            howto = howto_menu_rows()
            about = about_payload()
            about_line = str(about.get("line") or "")
            about_credit = str(about.get("credit") or "")
            about_taglines = list(about.get("taglines") or [])
            about_version = str(about.get("version") or "")
            raw_facts = about.get("facts") or []
            about_facts = [str(x) for x in raw_facts if x] if isinstance(raw_facts, list) else []
            about_signs = str(about.get("signs") or "")
        except Exception:
            howto = []
            about_line = about_credit = ""
            about_taglines = []
            about_version = about_signs = ""
            about_facts = []
        glass = mouth = mouth_via = ""
        try:
            from xlii.glass import live_chrome

            glass, mouth, mouth_via = live_chrome()
        except Exception:
            glass, mouth, mouth_via = "", "desk", ""
        return serialize_event(ChromeState(
            project=proj_name,
            workbench=getattr(wb, "name", "") or "",
            persona=persona, model=model or "", posture=self.posture,
            surface=surface,
            providers_ready=ready, providers_total=total,
            quick_launch=quick,
            chat_tier=chat_tier,
            trust=trust,
            meter=meter,
            session=session,
            pane_side=pane_side,
            pane_width_pct=pane_width_pct,
            cwd=cwd_label,
            jobs_active=jobs_active,
            jobs_unseen=jobs_unseen,
            jobs=jobs_pills,
            slot_a=slot_a,
            slot_b=slot_b,
            slot_focus=slot_focus,
            slot_catalog=slot_catalog,
            journal=journal,
            browser=browser,
            browser_url=browser_url,
            recent=recent,
            binds=self._chrome_binds(),
            term_cwd=term_cwd,
            face_skin=face_skin,
            face_fkeys=face_fkeys,
            face_bold=face_bold,
            howto=howto,
            about_line=about_line,
            about_credit=about_credit,
            about_taglines=about_taglines,
            about_version=about_version,
            about_facts=about_facts,
            about_signs=about_signs,
            fabric_nodes=self._fabric_node_names(),
            glass=glass,
            mouth=mouth,
            mouth_via=mouth_via,
            view_posture=getattr(self, "view_posture", "desk") or "desk",
        ))

    def mode_state(self) -> dict[str, Any]:
        state = self.state
        from xlii.status import overlay_word
        overlay = overlay_word(state)
        if self.posture == "chat":
            # Surface word is "chat" — posture of the input. Persona name
            # (ixaac/mojo/…) already lives on the HUD persona chip; repeating
            # "mojo" under the bar confused the desk with identity.
            phone = getattr(self, "view_posture", "desk") == "phone" or getattr(
                self, "_client_glass", False)
            return serialize_event(ModeState(
                mode="chat", color="magenta",
                exit_hint="" if phone else "[$] for code",
                placeholder="chat", hint="", ask_primary=True, posture="chat",
                overlay=overlay))
        # Best-effort chrome: a hint/status hiccup must never kill the worker
        # (the face just shows a bare mode word until the next refresh).
        try:
            from xlii.hints import resolve_hint
            from xlii.repl import _is_shell_primary
            from xlii.status import exit_hint, frame_mode, placeholder_key
            word, color = frame_mode(state)
            return serialize_event(ModeState(
                mode=word, color=color, exit_hint=exit_hint(state),
                placeholder=placeholder_key(state),
                hint=resolve_hint(state, shell_primary=_is_shell_primary(state)),
                ask_primary=bool(getattr(state, "ask_primary", False)),
                posture="code", overlay=overlay))
        except Exception:
            return serialize_event(ModeState(
                mode="code", color="", exit_hint="", placeholder="code",
                hint="", ask_primary=bool(getattr(state, "ask_primary", False)),
                posture="code", overlay=overlay))

    def command_catalog(self) -> dict[str, Any]:
        """Slash names for the face live-narrowing popup.

        Scoped to the live posture: [M] = chat-safe verbs, [$] = code.
        Surface-bound commands the face cannot run are filtered out."""
        from xlii.commands import _REPL_COMMANDS
        from xlii.repl_cmds import register_all

        register_all()  # idempotent; tests inject boot without session_boot
        scope = "chat" if self.posture == "chat" else "code"
        policy = None
        if scope == "chat":
            try:
                from xlii.mode_contract import get_mode

                policy = get_mode("chat").capabilities.slash_commands
            except Exception:
                policy = None
        seen: set[str] = set()
        entries: list[CommandEntry] = []
        for cmd in _REPL_COMMANDS:
            if scope not in (cmd.repls or ()):
                continue
            name = cmd.name
            if not name or name in seen:
                continue
            if f"/{name}" in _UNAVAILABLE_SLASH:
                continue
            if policy is not None and not policy.permits(name):
                continue
            seen.add(name)
            entries.append(CommandEntry(
                name=name, description=(cmd.description or "").strip()))
            for alias in cmd.aliases or ():
                a = str(alias).strip()
                if a and a not in seen:
                    seen.add(a)
                    entries.append(CommandEntry(
                        name=a, description=(cmd.description or "").strip()))
        entries.sort(key=lambda e: e.name)
        return serialize_event(CommandCatalog(commands=entries))

    def workbench_catalog(self) -> dict[str, Any]:
        """Named quick-launch packs for the face Workbench menu."""
        from xlii.workbench import list_workbenches, load_active_type, quick_launch_buttons

        project = getattr(self.state, "project", None)
        xli = getattr(project, "xli_dir", None) if project is not None else None
        active = ""
        try:
            active = load_active_type(xli) if xli is not None else (
                getattr(getattr(self.state, "workbench", None), "name", "") or ""
            )
        except Exception:
            active = getattr(getattr(self.state, "workbench", None), "name", "") or ""
        rows = []
        try:
            for t in list_workbenches(xli):
                ql = " · ".join(b["label"] for b in quick_launch_buttons(t)[:5]) or "—"
                rows.append({
                    "name": t.name,
                    "active": t.name == active,
                    "ambient": (t.ambient or "")[:80],
                    "quick": ql,
                })
        except Exception:  # noqa: BLE001
            rows = []
        return {"type": "workbench_catalog", "active": active, "workbenches": rows}

    def pane_catalog(self) -> dict[str, Any]:
        """Panels + slot-dropdown rows for the active workbench pack."""
        try:
            panes = self.deck.pane_catalog()
        except Exception:  # noqa: BLE001
            panes = []
        return {"type": "pane_catalog", "panes": panes}

    def set_workbench(self, name: str) -> bool:
        """Switch the pack — slot views + leftover quick-launch (not a mode)."""
        name = (name or "").strip().lower()
        if not name:
            self.send({"type": "meta_message", "level": "warn",
                       "text": "workbench needs a name"})
            return False
        try:
            from xlii.workbench import get_workbench, save_active_type

            project = getattr(self.state, "project", None)
            xli = getattr(project, "xli_dir", None) if project is not None else None
            wb = get_workbench(name, xli)
            if wb is None:
                self.send({"type": "meta_message", "level": "warn",
                           "text": f"unknown workbench {name!r}"})
                return False
            if xli is not None:
                save_active_type(Path(xli), wb.name)
            self.state.workbench = wb
            self._home_pack_settled = True  # do not re-force home pack
            self._wb_posture_applied = None  # force chrome re-apply
            # Deck remounts the new pack; drop sticky / off-pack slot views.
            if self._deck is not None:
                self._deck.sync_pack(force=True)
            self.send(self.chrome_state())
            self.deck.send_snapshot()
            self.send(self.pane_catalog())
            self.send(self.workbench_catalog())
            self.send({"type": "meta_message", "level": "info",
                       "text": f"workbench → {wb.name} (views + pack, not a mode)"})
            return True
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"workbench: {type(e).__name__}: {e}"})
            return False

    def console_catalog(self) -> dict[str, Any]:
        """Commands menu OS shortcuts (flavor-aware Packages)."""
        from xlii.console_catalog import CATEGORY_ORDER, console_categories, package_manager_label

        cats = console_categories()
        rows = [
            {"name": name, "commands": list(cats.get(name, ()))}
            for name in CATEGORY_ORDER
            if name in cats
        ]
        return {
            "type": "console_catalog",
            "package_manager": package_manager_label() or "",
            "categories": rows,
        }
