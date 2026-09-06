"""ConfigPanel's modal/prompt flows (textual-only) — pickers and one-question
prompts driving the apply verbs. Split out of the one-file ``panels.py`` (V1c
decomposition); behavior unchanged. Mixin assembled in
:mod:`xlii.tui.panels.config`.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from xlii.tui.panels.models import ModelPickerModal


class _ConfigFlows:

    def _pick_profile(self) -> None:
        cfg = self._cfg()
        if cfg is None:
            return
        app = self._modal_host()
        if app is None:
            self._actions.notify("profile picker needs the full-screen TUI")
            return
        try:
            from xlii.model_profiles import effective_model_profiles, profile_matches_cfg
        except Exception:
            return
        profiles = sorted(effective_model_profiles(cfg).keys())
        current = next((n for n in profiles if profile_matches_cfg(cfg, n)), None)
        options = [(name, "") for name in profiles]

        def _picked(name: Optional[str]) -> None:
            if not name:
                return
            try:
                from xlii.model_profiles import apply_model_profile

                apply_model_profile(cfg, name, persist=True)
                agent = getattr(self._state, "agent", None)
                if agent is not None:
                    agent.model_override = None
            except KeyError:
                self._actions.notify(f"unknown profile {name!r}", severity="warning")
                return
            except Exception as exc:
                self._actions.notify(f"could not apply profile: {exc}", severity="warning")
                return
            self._actions.notify(f"profile → {name} (saved)")
            self._rebuild()

        app.push_screen(
            ModelPickerModal("profile", options, current=current), _picked
        )

    def _pick_region(self) -> None:
        """The `api region` picker: global + known regional edges + custom….
        A pick applies live — cfg.region persists AND the client pool
        rebuilds in place, because connectivity is exactly the knob you flip
        mid-outage; waiting for a relaunch would defeat it."""
        app = self._modal_host()
        if app is None:
            self._actions.notify("region picker needs the full-screen TUI")
            return
        from xlii.config import xai_api_host

        region, from_env = self._effective_region()
        options: list = [("global", f"  {xai_api_host(None)}")]
        options += [(r, f"  {xai_api_host(r)}") for r in self._KNOWN_REGIONS]
        options.append(("custom…", "  type any region"))
        current = region if (region in self._KNOWN_REGIONS) else ("global" if not region else None)

        def _picked(name: Optional[str]) -> None:
            if not name:
                return
            if name == "custom…":
                self._prompt(
                    "xAI region (e.g. us-west-2 · 'global' for the global edge):",
                    self._apply_region,
                )
                return
            if self._apply_region(name):
                self._rebuild()

        app.push_screen(
            ModelPickerModal("api region", options, current=current, title="xAI API region"),
            _picked,
        )

    def _modal_host(self) -> Any:
        """The app that can push a modal for us, or None (headless/tests)."""
        app = getattr(self._actions, "app", None)
        if app is None:
            try:
                app = self.app
            except Exception:
                app = None
        return app if callable(getattr(app, "push_screen", None)) else None

    def _prompt(self, prompt: str, apply: Callable[[str], bool]) -> None:
        """Ask one text question (PromptModal) and run ``apply`` on the
        answer; Esc/empty leaves everything untouched. Headless, degrade to
        a note (there is no modal to type into)."""
        app = self._modal_host()
        if app is None:
            self._actions.notify("editing needs the full-screen TUI")
            return
        # Lazy: panels must keep importing without the app module (no [tui]).
        from xlii.tui.app import PromptModal

        def _answered(raw: Optional[str]) -> None:
            if raw is None or not raw.strip():
                return  # esc / empty — nothing changes
            if apply(raw):
                self._rebuild()

        app.push_screen(PromptModal(prompt), _answered)

    def _edit_cmd_pref(self, field: str, *, noun: str, examples: str) -> None:
        """Set a desk command line on cfg (empty / 'clear' → auto)."""
        cfg = self._cfg()
        now = str(getattr(cfg, field, "") or "").strip() if cfg is not None else ""

        def _apply(raw: str) -> bool:
            if cfg is None:
                return False
            val = (raw or "").strip()
            if val.lower() in ("clear", "off", "none", "env", "environment", "auto"):
                val = ""
            if val:
                import shlex
                import shutil

                parts = shlex.split(val)
                if not parts or not shutil.which(parts[0]):
                    self._actions.notify(
                        f"{noun} not on PATH: {parts[0] if parts else val!r}",
                        severity="warning",
                    )
                    return False
            setattr(cfg, field, val)
            self._save_cfg()
            if val:
                self._actions.notify(f"{noun} → {val} (persisted)")
            else:
                self._actions.notify(f"{noun} cleared — using auto")
            return True

        self._prompt(
            f"{noun} command (now {now or 'auto'}) — e.g. {examples} · clear:",
            _apply,
        )

    def _edit_editor(self) -> None:
        """Set ``cfg.editor`` (empty clears to environment default)."""
        self._edit_cmd_pref(
            "editor", noun="editor", examples="nano · pluma · code -w",
        )

    def _edit_hotkey(self) -> None:
        """Change the doorway-hotkey modifier. Under the app this goes
        through ``App.set_hotkey_modifier`` — the seam built for this panel
        — which rebinds LIVE and persists; headless it degrades to a note."""
        cfg = self._cfg()
        now = str(getattr(cfg, "tui_hotkey_modifier", None) or "alt")

        def _apply(raw: str) -> bool:
            app = self._modal_host()
            setter = getattr(app, "set_hotkey_modifier", None) if app is not None else None
            if not callable(setter):
                self._actions.notify("hotkey modifier is tui_hotkey_modifier in config.json")
                return False
            try:
                applied = setter(raw)
            except Exception as exc:
                self._actions.notify(f"could not apply modifier: {exc}", severity="warning")
                return False
            self._actions.notify(f"doorway hotkeys → {applied}-<letter> (live + persisted)")
            return True

        self._prompt(
            f"doorway hotkey modifier (now {now}) — e.g. alt · ctrl+alt · ctrl+shift+alt:",
            _apply,
        )

    def _pick_role(self, role: str) -> None:
        """Open the model picker for ``role`` (v2); with no modal host the
        v1 cycle ring still advances the role."""
        app = self._modal_host()
        if app is None:
            self._cycle_role(role)
            self._rebuild()
            return
        options = [(m, self._price_hint(m)) for m in self._candidates()]

        def _picked(model: Optional[str]) -> None:
            if model:
                self._set_role(role, model)
                self._rebuild()

        app.push_screen(
            ModelPickerModal(role, options, current=self._resolved(role)), _picked
        )

    def _edit_budget(self) -> None:
        """Ask for the session cap in place (PromptModal — the one text
        question); Esc leaves it untouched. Headless, nudge to /budget."""
        app = self._modal_host()
        if app is None:
            self._actions.notify("session budget is set with /budget <usd>")
            return
        session = self._session()
        cap = getattr(session, "budget_usd", None) if session is not None else None
        now = f"${cap:.2f} cap" if cap else "no cap"
        # Lazy: panels must keep importing without the app module (no [tui]).
        from xlii.tui.app import PromptModal

        def _answered(raw: Optional[str]) -> None:
            if raw is None:
                return  # esc — nothing changes
            if self._apply_budget(raw):
                self._rebuild()

        app.push_screen(
            PromptModal(f"session budget USD (now {now}) — empty clears:"), _answered
        )
