"""ConfigPanel's row/label readers (textual-only) — display shaping over cfg,
session, project, and daemon.toml. Split out of the one-file ``panels.py``
(V1c decomposition); behavior unchanged. Mixin assembled in
:mod:`xlii.tui.panels.config`.
"""

from __future__ import annotations

from typing import Any, Optional


class _ConfigRows:

    def _cfg(self) -> Any:
        return getattr(self._state, "cfg", None)

    def _resolved(self, role: str) -> str:
        cfg = self._cfg()
        try:
            return str(cfg.get_model_for_role(role))
        except Exception:
            return "?"

    def _price_hint(self, model: str) -> str:
        pricing = getattr(self._cfg(), "pricing", None) or {}
        p = pricing.get(model)
        try:
            return f"  ${p['input']}/{p['output']} per M" if p else ""
        except (KeyError, TypeError):
            return ""

    def _candidates(self) -> list[str]:
        """The cycle set: every priced model + whatever the roles resolve to
        now — stable order so repeated clicks walk a predictable ring."""
        cfg = self._cfg()
        pricing = getattr(cfg, "pricing", None) or {}
        names = set(pricing)
        for role in self._ROLES:
            m = self._resolved(role)
            if m and m != "?":
                names.add(m)
        return sorted(names)

    def _session(self) -> Any:
        return getattr(getattr(self._state, "agent", None), "session", None)

    def _effective_region(self) -> "tuple[Optional[str], bool]":
        """(active region or None, is_env_override). The precedence lives in
        GlobalConfig.api_region — call it when the cfg has it (the panel is
        a client of existing seams), and mirror it only for fake cfgs."""
        import os

        env = (os.environ.get("XAI_REGION") or "").strip()
        api_region = getattr(self._cfg(), "api_region", None)
        if callable(api_region):
            try:
                region = api_region()
                return region, bool(env and region == env)
            except Exception:
                pass  # fall through to the field read
        if env:
            return env, True
        saved = (str(getattr(self._cfg(), "region", None) or "")).strip()
        return (saved or None), False

    def _region_label(self) -> str:
        region, from_env = self._effective_region()
        from xlii.config import xai_api_host

        host = xai_api_host(region)
        if from_env:
            return f"{host} · env XAI_REGION (overrides config)"
        return f"{host}{self._saved_badge()}"

    def _session_badge(self) -> str:
        return " · session"

    def _saved_badge(self) -> str:
        return " · saved"

    def _swarm_badge(self) -> str:
        """``max_parallel_workers`` is dual-mode: ``_apply_swarm`` sets it for
        the session, or persists to config.json with 'save'. So — unlike a
        static badge, which would lie about persistence — reflect the actual
        scope: ' · saved' once the live value matches config.json, ' · session'
        while an unsaved override is live."""
        cfg = self._cfg()
        live = getattr(cfg, "max_parallel_workers", None)
        try:
            from xlii.config import GlobalConfig

            disk = getattr(GlobalConfig.load(), "max_parallel_workers", None)
        except Exception:
            # Can't read disk (test fakes, no config) — it's a config setting,
            # so default to the config-backed badge rather than imply session.
            return self._saved_badge()
        return self._saved_badge() if live == disk else self._session_badge()

    def _profile_label(self) -> str:
        cfg = self._cfg()
        if cfg is None:
            return "custom"
        try:
            from xlii.model_profiles import effective_model_profiles, profile_matches_cfg

            for name in sorted(effective_model_profiles(cfg)):
                if profile_matches_cfg(cfg, name):
                    return name
        except Exception:
            # Profile lookup is best-effort; fall back to "custom" on any failure.
            return "custom"
        return "custom"

    def _keep_session_label(self) -> str:
        try:
            from xlii import episode as ep

            proj = getattr(self._state, "project", None)
            xli = getattr(proj, "xli_dir", None) if proj is not None else None
            if xli is None:
                return "no project"
            return "ON" if ep.read_keep_session(xli) else "off"
        except Exception:
            return "?"

    def _claim_gates_label(self) -> str:
        try:
            from xlii.turn_receipt import claim_gates_mode

            return claim_gates_mode(self._cfg())
        except Exception:
            return "?"

    def _swarm_label(self) -> str:
        cfg = self._cfg()
        n = getattr(cfg, "max_parallel_workers", "?")
        pool = getattr(self._state, "pool", None)
        n_keys = len(pool) if pool is not None else 1
        worker_keys = n_keys - 1 if n_keys > 1 else 1
        return f"{n} · {worker_keys} worker key(s)"

    def _resolved_temp(self, role: str) -> str:
        """The role's sampling temperature as display text ('?' when the
        config object can't answer, e.g. minimal test fakes)."""
        cfg = self._cfg()
        fn = getattr(cfg, f"{role}_temp", None)
        if callable(fn):
            try:
                return f"{float(fn()):.2f}"
            except Exception:
                # A non-numeric or failing temp accessor falls through to the default below.
                pass
        raw = getattr(cfg, f"{role}_temperature", None)
        try:
            return f"{float(raw):.2f}" if raw is not None else "?"
        except (TypeError, ValueError):
            return "?"

    def _persona_label(self) -> str:
        """Who talk addresses: a project bind, else the named mobile journal."""
        bound = (getattr(getattr(self._state, "project", None), "bound_persona", None) or "").strip()
        try:
            from xlii.persona import canonicalize_persona_id, factory_persona_id

            cfg = self._cfg()
            mojo = factory_persona_id(cfg)
            if bound and canonicalize_persona_id(bound, cfg=cfg) != mojo:
                return f"{bound} (project-bound)"
            return f"{mojo} (mojo)"
        except Exception:
            return bound or "mojo"

    def _xmpp_label(self) -> str:
        """The daemon's JID + whitelist size from daemon.toml, or a 'not
        configured' note. Display-only — no secret is read (the password
        lives in an env var), and edits stay in the TOML."""
        try:
            import tomllib

            from xlii.daemon_gate import DEFAULT_CONFIG_PATH

            with open(DEFAULT_CONFIG_PATH, "rb") as f:
                data = tomllib.load(f)
            jid = str(data.get("daemon", {}).get("jid", "") or "")
            if not jid:
                return "not configured"
            allowed = data.get("whitelist", {}).get("allowed_jids", []) or []
            return f"{jid} · {len(allowed)} allowed"
        except Exception:
            return "not configured"

    def _whitelist_label(self) -> str:
        """Whitelist size for the identity row (v4) — never the password."""
        try:
            import tomllib

            from xlii.daemon_gate import DEFAULT_CONFIG_PATH

            with open(DEFAULT_CONFIG_PATH, "rb") as f:
                data = tomllib.load(f)
            allowed = list(data.get("whitelist", {}).get("allowed_jids", []) or [])
            if not allowed:
                return "empty — select to manage"
            return f"{len(allowed)} JID(s) — select to manage"
        except Exception:
            return "not configured — select to manage"

    def _rows(self) -> list[tuple[str, str]]:
        """(id, label) rows; a ``hdr:`` id is a disabled section header.
        Role rows pick; budget/temps/iterations/hotkey edit in place;
        no-sync/skills-import toggle; persona/xmpp are display-only."""
        cfg = self._cfg()
        rows: list[tuple[str, str]] = [("hdr:models", "── models ──")]
        rows.append((
            "profile",
            f"{'profile…':<13} {self._profile_label()}{self._saved_badge()} — select to apply",
        ))
        for role in self._ROLES:
            model = self._resolved(role)
            rows.append((
                f"role:{role}",
                f"{role:<13} {model}{self._price_hint(model)}{self._saved_badge()}",
            ))
        for role in self._TEMP_ROLES:
            rows.append((
                f"temp:{role}",
                f"{'temp·' + role:<13} {self._resolved_temp(role)}{self._saved_badge()}",
            ))
        rows.append(("hdr:session", "── session ──"))
        session = self._session()
        cap = getattr(session, "budget_usd", None) if session is not None else None
        spent = float(getattr(session, "session_cost", 0.0) or 0.0) if session is not None else 0.0
        cap_txt = f"${cap:.2f} cap" if cap else "no cap"
        spent_txt = f" · ${spent:.2f} spent" if spent > 0 else ""
        rows.append((
            "budget",
            f"{'budget':<13} {cap_txt}{spent_txt}{self._session_badge()} — select to edit",
        ))
        on = bool(getattr(self._state, "no_sync", False))
        locked = " · scratch-locked" if getattr(self._state, "scratch", False) else ""
        rows.append((
            "nosync",
            f"{'no-sync':<13} {'ON' if on else 'off'}{locked}{self._session_badge()}",
        ))
        rows.append((
            "keepsession",
            f"{'keep-session':<13} {self._keep_session_label()}{self._saved_badge()} — select to flip",
        ))
        rows.append((
            "claimgates",
            f"{'claim gates':<13} {self._claim_gates_label()}{self._saved_badge()} — select to cycle",
        ))
        mode = str(getattr(cfg, "retrieval_mode", None) or "hybrid")
        rows.append((
            "retrieval",
            f"{'retrieval':<13} {mode}{self._saved_badge()} — select to cycle",
        ))
        rows.append((
            "swarm",
            f"{'swarm':<13} {self._swarm_label()}{self._swarm_badge()} — select to edit",
        ))
        iters = getattr(cfg, "max_tool_iterations", None)
        rows.append((
            "iterations",
            f"{'tool iter':<13} {iters if iters else '?'}{self._saved_badge()} — select to edit",
        ))
        worker_iters = getattr(cfg, "max_worker_iterations", None)
        rows.append((
            "workeriter",
            f"{'worker iter':<13} {worker_iters if worker_iters else '?'}{self._saved_badge()} — select to edit",
        ))
        chat_iters = getattr(cfg, "max_chat_tool_iterations", None)
        rows.append((
            "chatiter",
            f"{'chat iter':<13} {chat_iters if chat_iters else '?'}{self._saved_badge()} — select to edit",
        ))
        rows.append(("hdr:app", "── app ──"))
        panel_side = str(
            getattr(cfg, "tui_panel_side", None)
            or getattr(getattr(self._actions, "app", None), "_panel_side", None)
            or "right"
        )
        rows.append(("panelside", f"{'panel side':<13} {panel_side}{self._saved_badge()} — select to flip"))
        width_pct = int(getattr(cfg, "panel_width_pct", 0) or 0)
        width_s = "auto" if width_pct <= 0 else f"{width_pct}%"
        rows.append(("panewidth", f"{'panel width':<13} {width_s}{self._saved_badge()} — select to cycle"))
        editor = str(getattr(cfg, "editor", "") or "").strip()
        if editor:
            editor_label = f"{editor} · config"
        else:
            try:
                from xlii.editor import editor_source, resolve_editor

                src = editor_source(cfg)
                resolved = resolve_editor(cfg) or "(none)"
                editor_label = f"{resolved} · {src or 'env'}"
            except Exception:
                editor_label = "(environment)"
        rows.append(("editor", f"{'editor':<13} {editor_label}{self._saved_badge()} — select to edit"))
        image_ed = str(getattr(cfg, "image_editor", "") or "").strip() or "auto"
        rows.append(("imageed", f"{'image ed':<13} {image_ed}{self._saved_badge()} — select to edit"))
        browser = str(getattr(cfg, "browser", "") or "").strip() or "auto"
        rows.append(("browser", f"{'os browser':<13} {browser}{self._saved_badge()} — human opens, not research"))
        term = str(getattr(cfg, "tui_terminal", "") or "").strip() or "auto"
        rows.append(("terminal", f"{'terminal':<13} {term}{self._saved_badge()} — select to edit"))
        from xlii.desk import TERM_CWD_CUSTOM, normalize_term_cwd, term_cwd_label

        dest = term_cwd_label(cfg)
        rows.append(("termcwd", f"{'new term':<13} {dest}{self._saved_badge()} — select to cycle"))
        if normalize_term_cwd(getattr(cfg, "tui_terminal_cwd", "")) == TERM_CWD_CUSTOM:
            rows.append((
                "termcwdpath",
                f"{'term path':<13} {dest}{self._saved_badge()} — select to set",
            ))
        canvas = str(getattr(cfg, "tui_canvas", None) or "dark")
        rows.append(("canvas", f"{'canvas':<13} {canvas}{self._saved_badge()} — transcript paper"))
        rows.append(("theme", f"{'theme…':<13} open the theme picker (trim)"))
        hotkey = str(getattr(cfg, "tui_hotkey_modifier", None) or "alt")
        rows.append(("hotkey", f"{'hotkey':<13} {hotkey}{self._saved_badge()} — doorway modifier"))
        skills_on = bool(getattr(cfg, "import_foreign_skills", True))
        rows.append(("skills", f"{'skills-import':<13} {'on' if skills_on else 'OFF'}{self._saved_badge()}"))
        rows.append(("hdr:identity", "── identity ──"))
        rows.append(("persona", f"{'persona':<13} {self._persona_label()}"))
        rows.append(("xmpp", f"{'xmpp':<13} {self._xmpp_label()}"))
        rows.append(("whitelist", f"{'whitelist':<13} {self._whitelist_label()}"))
        rows.append((
            "region",
            f"{'api region':<13} {self._region_label()} — select to pick",
        ))
        privacy = self._privacy or "select to check (xAI account)"
        rows.append(("privacy", f"{'privacy':<13} {privacy}"))
        return rows

    def _options(self) -> list:
        """The rows as OptionList options — a ``hdr:`` row is a disabled
        section header (arrow-keys skip it, clicks bounce off)."""
        from textual.widgets.option_list import Option

        return [
            Option(label, id=rid, disabled=rid.startswith("hdr:"))
            for rid, label in self._rows()
        ]
