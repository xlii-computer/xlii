"""The ``/config`` panel — session knobs as clickable rows (textual-only).

Split out of the one-file ``panels.py`` (V1c decomposition): the class is
assembled here from four single-concern mixins (row readers · apply verbs ·
modal flows · identity flows), the class attributes, the Textual compose, and
the option dispatch. Behavior unchanged — methods moved verbatim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from textual.widgets import Static

from xlii.tui.panels.base import _PanelBase
from xlii.tui.panels.config_actions import _ConfigActions
from xlii.tui.panels.config_flows import _ConfigFlows
from xlii.tui.panels.config_identity import _ConfigIdentity
from xlii.tui.panels.config_rows import _ConfigRows

if TYPE_CHECKING:
    from xlii.tui.panels.host import PanelActions


class ConfigPanel(_ConfigRows, _ConfigActions, _ConfigFlows, _ConfigIdentity, _PanelBase):
    """Session knobs as clickable rows — the ``/config`` panel (tui-config-panel).

    One row per model role showing the RESOLVED model (+ price hint when the
    pricing table knows it); select opens the ``ModelPickerModal`` (v2) and
    persists the choice through the same config layer as ``xlii models set``
    — this panel is a client of existing seams, not a second config system.
    With no modal host (headless) the v1 cycle ring still works. The budget
    row edits the session soft cap in place (same fields ``/budget`` writes,
    session-only — never persisted) and shows spend so far; the no-sync row
    toggles ``state.no_sync`` (scratch locks it on); the theme row hops to
    the Themes panel.

    v2.1 grows sections (disabled ``hdr:`` rows): per-role temperatures and
    max tool iterations edit via prompt and persist (config, not session);
    the doorway-hotkey modifier goes through ``App.set_hotkey_modifier`` —
    the seam that was built waiting for this panel — so it rebinds live;
    skills-import toggles ``import_foreign_skills``.

    v5 (Track A) adds session/saved badges on rows, ``claim_gates``,
    sticky ``keep-session``, swarm ceiling, worker iterations, and a model
    profile picker — all clients of existing seams.

    v3 makes identity live: the persona row opens a picker over
    ``list_personas()`` and binds through ``ProjectConfig.save`` (the same
    field ``xlii init --id`` writes; lands next session); the xmpp row
    prompt-edits the daemon JID — validated by ``valid_bare_jid``, written
    as a targeted line rewrite that leaves the rest of daemon.toml (and the
    env-var password) untouched; the privacy row fetches account facts on
    demand (tier · ZDR eligibility · data-sharing) via ``xai_mgmt`` on a
    thread worker. Data-sharing stays a *report*, not a toggle — the opt-in
    is irreversible and console-side, so a panel click must never flip it.

    v6 adds the ``api region`` row (identity section — its kin are the
    xmpp/whitelist/privacy connectivity rows): a picker over the regional
    xAI edges (``cfg.region``) that persists AND hot-swaps the client pool
    in place, so a mid-outage flip lands without a relaunch. An
    ``XAI_REGION`` env override is surfaced, never silently fought."""

    DEFAULT_CSS = """
    ConfigPanel .panel-header {
        height: 1;
        width: 1fr;
    }
    ConfigPanel .panel-title {
        height: 1;
        width: auto;
        text-style: bold;
        color: $accent;
        padding: 0 1;
    }
    ConfigPanel .panel-spacer {
        width: 1fr;
    }
    ConfigPanel .panel-close {
        height: 1;
        min-width: 3;
        width: auto;
        border: none;
        padding: 0 1;
        margin: 0;
        background: $panel-darken-2;
        color: $text;
    }
    ConfigPanel .panel-close:hover {
        background: $accent;
    }
    ConfigPanel > #panel-config-list {
        height: 1fr;
        width: 1fr;
        border: none;
        padding: 0 1;
    }
    """

    _ROLES = ("orchestrator", "worker", "chat", "help")
    _ROLE_FIELD = {
        "orchestrator": "orchestrator_model",
        "worker": "worker_model",
        "chat": "chat_model",
        "help": "help_model",
    }

    _TEMP_ROLES = ("orchestrator", "worker", "chat")
    _CLAIM_GATES_RING = ("warn", "strict", "off")
    _RETRIEVAL_MODE_RING = ("hybrid", "semantic", "keyword")
    # Known regional edges (custom… covers whatever xAI adds later).
    _KNOWN_REGIONS = ("us-west-2", "us-east-1", "eu-west-1")

    _UNBIND = "(unbind)"
    _CREATE = "(create…)"
    _ADD_JID = "(add…)"

    def __init__(self, state: Any, actions: "PanelActions") -> None:
        super().__init__()
        self._state = state
        self._actions = actions
        # privacy row cache — filled by an on-demand account check (v3),
        # never fetched at render time (the panel must open instantly).
        self._privacy: Optional[str] = None

    def compose(self):  # type: ignore[override]
        from rich.text import Text
        from textual.containers import Horizontal
        from textual.widgets import Button, OptionList

        with Horizontal(classes="panel-header"):
            yield Static(Text("config · models & knobs"), classes="panel-title")
            yield Static("", classes="panel-spacer")
            close = Button("✕", classes="panel-close")
            close.can_focus = False
            yield close
        ol = OptionList(id="panel-config-list")
        ol.add_options(self._options())
        yield ol

    def on_button_pressed(self, event) -> None:
        event.stop()
        app = getattr(self, "app", None)
        if app is not None and hasattr(app, "hide_panel"):
            try:
                app.hide_panel()
            except Exception as exc:
                print(f"[xlii.tui.config] hide_panel failed: {exc}")

    def on_option_list_option_selected(self, event) -> None:
        event.stop()
        rid = getattr(getattr(event, "option", None), "id", None) or ""
        if rid.startswith("role:"):
            self._pick_role(rid.split(":", 1)[1])
            return
        if rid == "profile":
            self._pick_profile()
            return
        if rid.startswith("temp:"):
            role = rid.split(":", 1)[1]
            self._prompt(
                f"{role} temperature (now {self._resolved_temp(role)}, 0..2):",
                lambda raw, r=role: self._apply_temp(r, raw),
            )
            return
        if rid == "budget":
            self._edit_budget()
            return
        if rid == "nosync":
            if self._toggle_no_sync():
                self._rebuild()
            return
        if rid == "keepsession":
            if self._toggle_keep_session():
                self._rebuild()
            return
        if rid == "claimgates":
            if self._cycle_claim_gates():
                self._rebuild()
            return
        if rid == "retrieval":
            if self._cycle_retrieval_mode():
                self._rebuild()
            return
        if rid == "swarm":
            now = self._swarm_label()
            self._prompt(
                f"swarm ceiling (now {now}; 1..N, append ' save' to persist):",
                self._apply_swarm,
            )
            return
        if rid == "iterations":
            now = getattr(self._cfg(), "max_tool_iterations", "?")
            self._prompt(
                f"max tool iterations per turn (now {now}, 1..100):",
                self._apply_iterations,
            )
            return
        if rid == "workeriter":
            now = getattr(self._cfg(), "max_worker_iterations", "?")
            self._prompt(
                f"max worker iterations (now {now}, 1..100):",
                self._apply_worker_iterations,
            )
            return
        if rid == "chatiter":
            now = getattr(self._cfg(), "max_chat_tool_iterations", "?")
            self._prompt(
                f"max chat iterations (now {now}, 1..100):",
                self._apply_chat_iterations,
            )
            return
        if rid == "editor":
            self._edit_editor()
            return
        if rid == "imageed":
            self._edit_cmd_pref(
                "image_editor", noun="image editor",
                examples="gimp · krita · pinta",
            )
            return
        if rid == "browser":
            self._edit_cmd_pref(
                "browser", noun="browser",
                examples="firefox · chromium",
            )
            return
        if rid == "terminal":
            self._edit_cmd_pref(
                "tui_terminal", noun="terminal",
                examples="kitty · gnome-terminal · wezterm start --cwd {cwd}",
            )
            return
        if rid == "termcwd":
            if self._cycle_term_cwd():
                self._rebuild()
            return
        if rid == "termcwdpath":
            self._edit_term_cwd_path()
            return
        if rid == "panelside":
            if self._cycle_panel_side():
                self._rebuild()
            return
        if rid == "panewidth":
            if self._cycle_panel_width():
                self._rebuild()
            return
        if rid == "hotkey":
            self._edit_hotkey()
            return
        if rid == "skills":
            if self._toggle_skills_import():
                self._rebuild()
            return
        if rid == "persona":
            self._pick_persona()
            return
        if rid == "xmpp":
            self._edit_jid()
            return
        if rid == "whitelist":
            self._manage_whitelist()
            return
        if rid == "privacy":
            self._check_privacy()
            return
        if rid == "region":
            self._pick_region()
            return
        if rid == "canvas":
            app = getattr(self._actions, "app", None)
            if app is not None and hasattr(app, "_apply_canvas"):
                cur = str(getattr(self._cfg(), "tui_canvas", "dark") or "dark")
                nxt = "light" if cur == "dark" else "dark"
                app._apply_canvas(nxt, persist=True)
                self._rebuild()
            return
        if rid == "theme":
            app = getattr(self._actions, "app", None)
            if app is not None and hasattr(app, "_show_panel_view"):
                side = "right"
                pref = getattr(app, "_preferred_panel_side", None)
                if callable(pref):
                    try:
                        side = pref()
                    except Exception:
                        # Preferred side lookup is best-effort; default to "right".
                        side = "right"
                try:
                    app._show_panel_view(side, "themes")
                except Exception as exc:
                    self._actions.notify(f"could not open themes panel: {exc}")

    def _rebuild(self) -> None:
        from textual.widgets import OptionList

        try:
            ol = self.query_one("#panel-config-list", OptionList)
        except Exception:
            return
        keep = ol.highlighted
        ol.clear_options()
        ol.add_options(self._options())
        if keep is not None:
            try:
                ol.highlighted = keep
            except Exception:
                # Highlight may not survive a full option-list rebuild.
                pass
