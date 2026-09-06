"""ConfigPanel's identity flows (textual-only) — persona binding, daemon JID
+ whitelist, account privacy. The daemon.toml byte-preserving writes live
kernel-side in :mod:`xlii.daemon_toml` (V1c); these flows only drive them.
Split out of the one-file ``panels.py``; behavior unchanged. Mixin assembled
in :mod:`xlii.tui.panels.config`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from xlii import daemon_toml
from xlii.tui.panels.models import ModelPickerModal


class _ConfigIdentity:

    def _persona_names(self) -> list[str]:
        try:
            from xlii.persona import list_bindable_personas

            return [p.name for p in list_bindable_personas()]
        except Exception:
            return []

    def _bind_persona(self, name: str) -> bool:
        """Write the project's ``bound_persona`` through ``ProjectConfig.save``
        (the same field ``xlii init --id`` sets). Empty/``(unbind)`` unbinds.
        Binding is read at session start, so it lands next session."""
        project = getattr(self._state, "project", None)
        if project is None or not hasattr(project, "bound_persona"):
            self._actions.notify(
                "no project here — persona binding rides project.json", severity="warning"
            )
            return False
        project.bound_persona = None if name in ("", self._UNBIND) else name
        save = getattr(project, "save", None)
        if callable(save):
            try:
                save()
            except Exception as exc:
                self._actions.notify(f"could not save project config: {exc}", severity="warning")
                return False
        self._actions.notify(
            f"persona → {project.bound_persona} (next session)"
            if project.bound_persona
            else "persona unbound — project-local memory (next session)"
        )
        return True

    def _create_persona_from_picker(self) -> None:
        """v4: create a persona via ``create_persona`` (same seam as the
        Options menu), then bind it to the project."""

        def _apply(raw: str) -> bool:
            from xlii.persona import Persona, create_persona, is_valid_name

            name = (raw or "").strip()
            if not is_valid_name(name):
                self._actions.notify(
                    f"invalid persona name: {name!r} — letters, digits, _ . -",
                    severity="warning",
                )
                return False
            if Persona(name).exists():
                self._actions.notify(f"persona {name!r} already exists", severity="warning")
                return False
            try:
                create_persona(name)
            except (ValueError, FileExistsError, OSError) as exc:
                self._actions.notify(str(exc), severity="warning")
                return False
            self._actions.notify(f"created persona {name}")
            return self._bind_persona(name)

        self._prompt("new persona name (letters, digits, _ . -):", _apply)

    def _pick_persona(self) -> None:
        """Open the persona picker (every ``~/.config`` persona + create + unbind)."""
        project = getattr(self._state, "project", None)
        if project is None or not hasattr(project, "bound_persona"):
            self._actions.notify(
                "no project here — persona binding rides project.json", severity="warning"
            )
            return
        names = self._persona_names()
        app = self._modal_host()
        if app is None:
            self._actions.notify(
                "persona rides the project — bind with: xlii init --id <persona>"
            )
            return
        current = getattr(project, "bound_persona", None) or self._UNBIND
        options = (
            [(n, "") for n in names]
            + [(self._CREATE, "  new persona")]
            + [(self._UNBIND, "  project-local memory")]
        )

        def _picked(name: Optional[str]) -> None:
            if name == self._CREATE:
                self._create_persona_from_picker()
                return
            if name and self._bind_persona(name):
                self._rebuild()

        app.push_screen(
            ModelPickerModal("persona", options, current=current,
                             title="persona · bind to project"),
            _picked,
        )

    def _manage_whitelist(self) -> None:
        """v4: add/remove whitelist JIDs via targeted rewrite (never password)."""
        from xlii.daemon_gate import DEFAULT_CONFIG_PATH

        path = DEFAULT_CONFIG_PATH
        try:
            text = Path(path).read_text()
        except OSError:
            self._actions.notify(
                "no daemon.toml — copy daemon.toml.example there first",
                severity="warning",
            )
            return
        jids = daemon_toml.parse_allowed_jids_from_text(text) or []
        app = self._modal_host()
        if app is None:
            self._actions.notify("whitelist edits need the TUI modal host")
            return
        options = [(j, "  select to remove") for j in jids] + [
            (self._ADD_JID, "  allow a new bare JID")
        ]

        def _picked(choice: Optional[str]) -> None:
            if not choice:
                return
            if choice == self._ADD_JID:
                def _apply(raw: str) -> bool:
                    ok, msg = daemon_toml.whitelist_add_jid(path, (raw or "").strip())
                    self._actions.notify(
                        msg, severity="information" if ok else "warning"
                    )
                    return ok

                self._prompt("allow bare JID (user@domain):", _apply)
                return
            ok, msg = daemon_toml.whitelist_remove_jid(path, choice)
            self._actions.notify(msg, severity="information" if ok else "warning")
            if ok:
                self._rebuild()

        app.push_screen(
            ModelPickerModal(
                "whitelist", options,
                current=jids[0] if jids else self._ADD_JID,
                title="daemon whitelist · add / remove",
            ),
            _picked,
        )

    def _edit_jid(self) -> None:
        """Prompt-edit the daemon JID, validated by the daemon's own
        ``valid_bare_jid`` gate before anything is written."""

        def _apply(raw: str) -> bool:
            from xlii.daemon_gate import DEFAULT_CONFIG_PATH, valid_bare_jid

            jid = (raw or "").strip()
            if not valid_bare_jid(jid):
                self._actions.notify(
                    f"invalid JID: {jid!r} — bare user@domain, no /resource",
                    severity="warning",
                )
                return False
            ok, msg = daemon_toml.rewrite_daemon_jid(DEFAULT_CONFIG_PATH, jid)
            self._actions.notify(msg, severity="information" if ok else "warning")
            return ok

        self._prompt(f"daemon JID (now: {self._xmpp_label()}) — bare user@domain:", _apply)

    def _fetch_privacy_label(self) -> str:
        """Blocking account check (worker thread): tier · ZDR eligibility ·
        data-sharing. Sharing is an irreversible console-side opt-in with no
        verified write API, so this row reports rather than toggles; if the
        team object carries a sharing/training flag we surface it, else we
        say plainly that it's console-managed."""
        cfg = self._cfg()
        key = getattr(cfg, "management_api_key", None)
        if not key:
            return "no management key — export XAI_MANAGEMENT_API_KEY"
        try:
            from xlii import xai_mgmt as mgmt

            team_id = mgmt.resolve_active_team(key, getattr(cfg, "team_id", None))
            if not team_id:
                return "no team for this management key"
            team = mgmt.team_status(key, team_id)
        except Exception as exc:
            return f"account check failed: {exc}"
        zdr = "ZDR-eligible" if team.get("isSelfServeZdrEligible") else "no ZDR"
        share_key = next(
            (k for k in team if "shar" in k.lower() or "train" in k.lower()), None
        )
        sharing = (
            f"data-sharing {'ON' if team.get(share_key) else 'off'}"
            if share_key
            else "data-sharing: console-managed"
        )
        return f"{team.get('tierId', '?')} · {zdr} · {sharing}"

    def _check_privacy(self) -> None:
        """Refresh the privacy row on demand. Under the app the fetch rides
        a thread worker (the panel never blocks on the network); headless it
        runs inline so the logic stays testable."""

        def _work() -> None:
            label = self._fetch_privacy_label()

            def _done() -> None:
                self._privacy = label
                self._rebuild()

            app = self._modal_host()
            call = getattr(app, "call_from_thread", None) if app is not None else None
            if callable(call):
                try:
                    call(_done)
                    return
                except RuntimeError:
                    pass  # already on the app thread — fall through
            _done()

        runner = getattr(self, "run_worker", None)
        if self._modal_host() is not None and callable(runner):
            self._privacy = "checking…"
            self._rebuild()
            runner(_work, thread=True)
        else:
            _work()
