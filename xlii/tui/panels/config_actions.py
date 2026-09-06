"""ConfigPanel's apply verbs (textual-only) — cfg/session mutations and their
persist/notify policy. Split out of the one-file ``panels.py`` (V1c
decomposition); behavior unchanged. Mixin assembled in
:mod:`xlii.tui.panels.config`.
"""

from __future__ import annotations

from typing import Any, Optional


class _ConfigActions:

    def _set_role(self, role: str, model: str) -> None:
        """Point the role at ``model`` and persist (cfg.save — the same sink
        as `xlii models set`; best-effort when the config object can't save,
        e.g. test fakes)."""
        cfg = self._cfg()
        field_name = self._ROLE_FIELD.get(role)
        if cfg is None or field_name is None or not model:
            return
        setattr(cfg, field_name, model)
        self._save_cfg()
        self._actions.notify(f"{role} model → {model}")

    def _cycle_role(self, role: str) -> None:
        """Advance the role to the next known model — the v1 ring, kept as
        the fallback when no modal host can push the picker (headless)."""
        ring = self._candidates()
        if not ring:
            return
        current = self._resolved(role)
        try:
            nxt = ring[(ring.index(current) + 1) % len(ring)]
        except ValueError:
            nxt = ring[0]
        self._set_role(role, nxt)

    @staticmethod
    def _parse_budget(raw: Optional[str]) -> tuple[bool, Optional[float]]:
        """``(ok, value)`` — empty/'clear' clears (ok, None); a positive USD
        amount sets; anything else is (False, None). Mirrors /budget's rules."""
        text = (raw or "").strip().lstrip("$")
        if text.lower() in ("", "clear", "none", "off"):
            return True, None
        try:
            val = float(text)
        except ValueError:
            return False, None
        return (True, val) if val > 0 else (False, None)

    def _apply_budget(self, raw: Optional[str]) -> bool:
        """Write the parsed answer to the session — the same two fields
        ``/budget`` writes (soft cap, session-only, never persisted)."""
        session = self._session()
        if session is None:
            return False
        ok, val = self._parse_budget(raw)
        if not ok:
            self._actions.notify(
                f"invalid budget: {raw!r} — use a USD amount > 0", severity="warning"
            )
            return False
        if val is None:
            session.budget_usd = None
            session.budget_env_cleared = True
            self._actions.notify("budget cleared")
        else:
            session.budget_usd = val
            session.budget_env_cleared = False
            self._actions.notify(f"budget ${val:.2f} (soft cap — warns before turns)")
        return True

    def _toggle_no_sync(self) -> bool:
        """Flip ``state.no_sync`` — the same session-only flag ``--no-sync``
        and scratch set (never persisted). Scratch locks it on (the Q3
        contract), so the toggle refuses rather than lying."""
        if getattr(self._state, "scratch", False):
            self._actions.notify("scratch mode is always no-sync", severity="warning")
            return False
        self._state.no_sync = not bool(getattr(self._state, "no_sync", False))
        self._actions.notify(
            "no-sync ON — nothing uploads this session"
            if self._state.no_sync
            else "no-sync off — end-of-turn sync resumes"
        )
        # The status strip carries the `no-sync` marker — repaint it now.
        app = getattr(self._actions, "app", None)
        refresh = getattr(app, "_refresh_status", None) if app is not None else None
        if callable(refresh):
            try:
                refresh()
            except Exception:
                # Repaint is cosmetic and intentionally non-fatal — the flag
                # already flipped and the strip re-reads it on the next render.
                pass
        return True

    def _save_cfg(self) -> None:
        """Best-effort ``cfg.save`` (test fakes may lack a working save())."""
        save = getattr(self._cfg(), "save", None)
        if callable(save):
            try:
                save()
            except Exception:
                # Persist is best-effort by design: the in-memory cfg already
                # changed (this session sees it); only the disk write failed.
                pass

    def _apply_iterations(self, raw: str) -> bool:
        """Set ``max_tool_iterations`` (1..100, /iterations' bounds) and
        persist — the panel is the config surface, so unlike the session-only
        ``/iterations`` twin this writes config.json. Applies next turn
        (run_turn reads cfg fresh)."""
        cfg = self._cfg()
        if cfg is None:
            return False
        try:
            n = int((raw or "").strip())
        except ValueError:
            n = 0
        if not 1 <= n <= 100:
            self._actions.notify(
                f"invalid iterations: {raw!r} — use 1..100", severity="warning"
            )
            return False
        cfg.max_tool_iterations = n
        self._save_cfg()
        self._actions.notify(f"max tool iterations → {n} (persisted; applies next turn)")
        return True

    def _apply_worker_iterations(self, raw: str) -> bool:
        cfg = self._cfg()
        if cfg is None:
            return False
        try:
            n = int((raw or "").strip())
        except ValueError:
            n = 0
        if not 1 <= n <= 100:
            self._actions.notify(
                f"invalid worker iterations: {raw!r} — use 1..100", severity="warning"
            )
            return False
        cfg.max_worker_iterations = n
        self._save_cfg()
        self._actions.notify(f"max worker iterations → {n} (persisted)")
        return True

    def _apply_chat_iterations(self, raw: str) -> bool:
        cfg = self._cfg()
        if cfg is None:
            return False
        try:
            n = int((raw or "").strip())
        except ValueError:
            n = 0
        if not 1 <= n <= 100:
            self._actions.notify(
                f"invalid chat iterations: {raw!r} — use 1..100", severity="warning"
            )
            return False
        cfg.max_chat_tool_iterations = n
        self._save_cfg()
        self._actions.notify(f"max chat iterations → {n} (persisted)")
        return True

    def _cycle_claim_gates(self) -> bool:
        cfg = self._cfg()
        if cfg is None:
            return False
        try:
            from xlii.turn_receipt import claim_gates_mode
        except Exception:
            return False
        cur = claim_gates_mode(cfg)
        try:
            i = self._CLAIM_GATES_RING.index(cur)
        except ValueError:
            i = 0
        nxt = self._CLAIM_GATES_RING[(i + 1) % len(self._CLAIM_GATES_RING)]
        cfg.claim_gates = nxt
        self._save_cfg()
        self._actions.notify(f"claim gates → {nxt} (persisted)")
        return True

    def _cycle_retrieval_mode(self) -> bool:
        cfg = self._cfg()
        if cfg is None:
            return False
        cur = str(getattr(cfg, "retrieval_mode", None) or "hybrid")
        try:
            i = self._RETRIEVAL_MODE_RING.index(cur)
        except ValueError:
            i = 0
        nxt = self._RETRIEVAL_MODE_RING[(i + 1) % len(self._RETRIEVAL_MODE_RING)]
        cfg.retrieval_mode = nxt
        self._save_cfg()
        self._actions.notify(f"retrieval mode → {nxt} (persisted)")
        return True

    def _toggle_keep_session(self) -> bool:
        try:
            from xlii import episode as ep
        except Exception:
            return False
        proj = getattr(self._state, "project", None)
        xli = getattr(proj, "xli_dir", None) if proj is not None else None
        if xli is None:
            self._actions.notify("keep-session needs a project", severity="warning")
            return False
        on = not ep.read_keep_session(xli)
        ep.write_keep_session(xli, on)
        self._actions.notify(
            f"keep-session {'ON' if on else 'off'} (saved for this project)"
        )
        return True

    def _apply_swarm(self, raw: str) -> bool:
        cfg = self._cfg()
        if cfg is None:
            return False
        text = (raw or "").strip().lower()
        save = text.endswith(" save") or text == "save"
        num_txt = text[:-5].strip() if text.endswith(" save") else text
        if num_txt == "save":
            self._actions.notify("usage: <n> or '<n> save' to persist", severity="warning")
            return False
        try:
            n = int(num_txt)
        except ValueError:
            self._actions.notify(f"invalid swarm size: {raw!r}", severity="warning")
            return False
        if n < 1:
            self._actions.notify("swarm size must be >= 1", severity="warning")
            return False
        cfg.max_parallel_workers = n
        if save:
            self._save_cfg()
            note = " (saved to config.json)"
        else:
            note = " (this session — append ' save' to persist)"
        self._actions.notify(f"swarm ceiling → {n}{note}")
        pool = getattr(self._state, "pool", None)
        n_keys = len(pool) if pool is not None else 1
        worker_keys = n_keys - 1 if n_keys > 1 else 1
        if n > worker_keys:
            self._actions.notify(
                f"note: only {worker_keys} worker key(s) in the pool",
                severity="warning",
            )
        return True

    def _apply_region(self, raw: str) -> bool:
        """Set ``cfg.region`` ('global'/'off'/'none' → None), persist, and
        hot-swap the pool. With XAI_REGION set the env still wins for this
        shell — say so rather than silently saving a field that won't bite."""
        cfg = self._cfg()
        if cfg is None:
            return False
        from xlii.config import normalize_api_region

        try:
            val = normalize_api_region(raw)
        except ValueError:
            self._actions.notify(
                f"invalid region: {raw!r} — single ASCII DNS label only",
                severity="warning",
            )
            return False
        cfg.region = val
        self._save_cfg()
        region, from_env = self._effective_region()
        from xlii.config import xai_api_host

        host = xai_api_host(region)
        live = self._rebuild_pool(cfg)
        note = f"api → {host} " + (
            "(live + persisted)" if live else "(persisted; lands next session)"
        )
        if from_env:
            note = (
                f"region saved, but XAI_REGION={region} overrides it in this "
                f"shell — api stays {host}"
            )
        self._actions.notify(note)
        return True

    def _rebuild_pool(self, cfg: Any) -> bool:
        """Best-effort in-place client rebuild (ClientPool.rebuild_from_config).
        False (headless fakes, missing keys) degrades to next-session honesty."""
        pool = getattr(self._state, "pool", None)
        rebuild = getattr(pool, "rebuild_from_config", None)
        if not callable(rebuild):
            return False
        try:
            rebuild(cfg)
            return True
        except Exception as exc:
            self._actions.notify(f"pool rebuild failed: {exc}", severity="warning")
            return False

    def _apply_temp(self, role: str, raw: str) -> bool:
        """Set the role's sampling temperature (0..2) and persist."""
        cfg = self._cfg()
        if cfg is None or role not in self._TEMP_ROLES:
            return False
        try:
            val = float((raw or "").strip())
        except ValueError:
            val = -1.0
        if not 0.0 <= val <= 2.0:
            self._actions.notify(
                f"invalid temperature: {raw!r} — use 0..2", severity="warning"
            )
            return False
        setattr(cfg, f"{role}_temperature", val)
        self._save_cfg()
        self._actions.notify(f"{role} temperature → {val:.2f} (applies next turn)")
        return True

    def _toggle_skills_import(self) -> bool:
        """Flip ``import_foreign_skills`` (auto-discovery of grok-build /
        Claude Code skills) and persist. Discovery runs at session start, so
        the flip lands next session."""
        cfg = self._cfg()
        if cfg is None:
            return False
        cfg.import_foreign_skills = not bool(getattr(cfg, "import_foreign_skills", True))
        self._save_cfg()
        self._actions.notify(
            "foreign-skills import ON (next session)"
            if cfg.import_foreign_skills
            else "foreign-skills import OFF (next session)"
        )
        return True

    def _cycle_panel_side(self) -> bool:
        """Flip dock side left ↔ right through ``App.set_panel_side``."""
        app = getattr(self._actions, "app", None)
        if app is None:
            try:
                app = self.app
            except Exception:
                app = None
        setter = getattr(app, "set_panel_side", None) if app is not None else None
        if not callable(setter):
            self._actions.notify("panel side is tui_panel_side in config.json")
            return False
        cur = str(
            getattr(self._cfg(), "tui_panel_side", None)
            or getattr(app, "_panel_side", None)
            or "right"
        ).lower()
        nxt = "left" if cur != "left" else "right"
        try:
            applied = setter(nxt, persist=True)
        except Exception as exc:
            self._actions.notify(f"could not flip panel side: {exc}", severity="warning")
            return False
        self._actions.notify(f"panel side → {applied} (live + persisted)")
        return True

    def _cycle_panel_width(self) -> bool:
        """Cycle face dock width percent (0 = auto). Shared config with the face."""
        from xlii.desk import PANEL_WIDTH_RING

        cfg = self._cfg()
        if cfg is None:
            return False
        cur = int(getattr(cfg, "panel_width_pct", 0) or 0)
        try:
            i = list(PANEL_WIDTH_RING).index(cur)
        except ValueError:
            i = 0
        nxt = PANEL_WIDTH_RING[(i + 1) % len(PANEL_WIDTH_RING)]
        cfg.panel_width_pct = nxt
        self._save_cfg()
        label = "auto" if nxt <= 0 else f"{nxt}%"
        self._actions.notify(f"panel width → {label} (persisted)")
        return True

    def _cycle_term_cwd(self) -> bool:
        """Cycle Tools → New terminal dest (this project / home / root / custom)."""
        from xlii.desk import TERM_CWD_CUSTOM, cycle_term_cwd, term_cwd_label

        cfg = self._cfg()
        if cfg is None:
            return False
        nxt = cycle_term_cwd(cfg)
        self._save_cfg()
        if nxt == TERM_CWD_CUSTOM and not str(
            getattr(cfg, "tui_terminal_cwd_path", "") or ""
        ).strip():
            self._edit_term_cwd_path()
            return True
        self._actions.notify(f"new terminal → {term_cwd_label(cfg)} (persisted)")
        return True

    def _edit_term_cwd_path(self) -> None:
        """Claim a folder for the custom New-terminal dest."""
        from xlii.desk import apply_terminal_cwd_path

        cfg = self._cfg()
        now = str(getattr(cfg, "tui_terminal_cwd_path", "") or "").strip() if cfg else ""

        def _apply(raw: str) -> bool:
            ok, msg = apply_terminal_cwd_path(cfg, raw)
            self._actions.notify(msg, severity="warning" if not ok else "information")
            return ok

        self._prompt(
            f"new terminal folder (now {now or 'unset'}; empty / 'clear' → this project):",
            _apply,
        )
