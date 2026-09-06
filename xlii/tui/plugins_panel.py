"""``PluginsPanel`` — the click-to-subscribe plugin catalog (the-fold Vector B).

The parallel of the skills/themes panels for the plugin surface: the installed
catalog listed with subscribed plugins marked (``●``), each row carrying its
**effect/trust badges**, click a row to subscribe/unsubscribe into the project's
``.xlii/plugins.txt``. It replaces the old Command-dropdown "plugins" entry (which
pre-prompted ``/get`` — the invocation verb, not the subscription act).

Two safety properties are load-bearing:

* **The elevation gate is respected.** Subscribing a *high-risk* plugin
  (local-system / destructive / always-confirm) requires an elevated session
  (``/admin unlock``). A frictionless click surface must never become the easiest
  way around the gate ``role.gate_loadout_plugins`` enforces for auto-attach —
  so the panel refuses the high-risk subscribe and points at ``/admin`` instead.
* **Untrusted text never becomes markup.** A plugin's description is rendered
  through ``Text`` (never interpolated into console markup).

Registration is a published-function call (``register_plugins_panel`` →
``panels.register_panel_view``) from *this* module, never a shared edit to
``tui/panels.py``. The widget is textual-guarded exactly like ``tui/panels`` and
``tui/dock_surface``: the registry stays import-light without the ``[tui]`` extra
(``plugins_view`` degrades to ``None``), so ``/plugin panel`` can register at
session start without pulling in textual.
"""

from __future__ import annotations

from typing import Any

# Effect → the accent the badge paints in. Mirrors knowledge._EFFECT_COLORS so the
# panel and the /plugin list read the same.
_EFFECT_COLORS = {
    "read-only": "green",
    "external-write": "yellow",
    "local-system": "magenta",
    "destructive": "red",
}


def register_plugins_panel(name: str = "plugins") -> None:
    """Register the plugin catalog under the panel-view registry (additive,
    last-wins). Called from the ``/plugin`` command's ``register()``."""
    from xlii.tui import panels

    panels.register_panel_view(name, plugins_view)


def _xli_dir(state: Any):
    project = getattr(state, "project", None)
    return getattr(project, "xli_dir", None) if project is not None else None


def _subscribed(state: Any) -> "set[str]":
    from xlii.plugin import load_subscriptions

    xli = _xli_dir(state)
    return set(load_subscriptions(xli)) if xli is not None else set()


# --------------------------------------------------------------------------- #
#  Gate + toggle live in xlii.plugin (kernel) so face/serve may call them
#  without importing the TUI package. Re-export for panel + existing tests.
# --------------------------------------------------------------------------- #

from xlii.plugin import can_subscribe, toggle_subscription  # noqa: F401


# --------------------------------------------------------------------------- #
#  The widget (textual-only, guarded exactly like tui/panels.py)
# --------------------------------------------------------------------------- #

try:
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Button, OptionList, Static
    from textual.widgets.option_list import Option

    _TEXTUAL = True
except Exception:  # pragma: no cover - exercised only without [tui]
    _TEXTUAL = False


if _TEXTUAL:

    def _row_label(p, subscribed: bool) -> Any:
        """A safe Rich ``Text`` row: marker · id · effect/trust badges · description.
        The description is UNTRUSTED plugin frontmatter, so it rides as plain
        ``Text`` and is never parsed as markup."""
        from rich.text import Text

        effect, trust = p.effect_trust()
        high = p.is_high_risk()
        t = Text()
        t.append("● " if subscribed else "○ ", style="green" if subscribed else "dim")
        t.append(p.id, style="bold" if subscribed else "")
        t.append("  ")
        if high:
            t.append("⚠ ", style="red")
        t.append(effect, style=_EFFECT_COLORS.get(effect, "white"))
        t.append(f" · {trust}", style="dim")
        desc = p.description() or ""
        if desc:
            t.append(f"  {desc}", style="dim")
        return t

    class PluginsPanel(Vertical):
        """The installed plugin catalog as a clickable list. Selecting a row (click
        or Enter) toggles that plugin's subscription for the project; the ``●``
        marker follows. High-risk subscribes are gated on an elevated session."""

        DEFAULT_CSS = """
        PluginsPanel {
            width: 1fr;
            height: 1fr;
        }
        PluginsPanel .panel-header {
            height: 1;
            width: 1fr;
        }
        PluginsPanel .panel-title {
            height: 1;
            width: auto;
            text-style: bold;
            color: $accent;
            padding: 0 1;
        }
        PluginsPanel .panel-spacer {
            width: 1fr;
        }
        PluginsPanel .panel-close {
            height: 1;
            min-width: 3;
            width: auto;
            border: none;
            padding: 0 1;
            margin: 0;
            background: $panel-darken-2;
            color: $text;
        }
        PluginsPanel .panel-close:hover {
            background: $accent;
        }
        PluginsPanel > #panel-plugins-list {
            height: 1fr;
            width: 1fr;
            border: none;
            padding: 0 1;
        }
        PluginsPanel > .panel-hint {
            height: auto;
            width: 1fr;
            padding: 0 1;
            color: $text-muted;
        }
        """

        def __init__(self, state: Any, actions: Any) -> None:
            super().__init__()
            self._state = state
            self._actions = actions

        def _plugins(self) -> list:
            from xlii.plugin import list_plugins

            return list_plugins()

        def compose(self):  # type: ignore[override]
            from rich.text import Text

            plugins = self._plugins()
            subs = _subscribed(self._state)
            with Horizontal(classes="panel-header"):
                yield Static(Text(f"plugins · {len(subs)}/{len(plugins)} subscribed"), classes="panel-title")
                yield Static("", classes="panel-spacer")
                close = Button("✕", classes="panel-close")
                close.can_focus = False   # the ✕ never steals focus from the list (mc/ranger idiom)
                yield close
            ol = OptionList(id="panel-plugins-list")
            if not plugins:
                ol.add_option(Option(Text("no plugins installed — xlii plugin --new <id>", style="dim italic"),
                                     id="__none__", disabled=True))
            else:
                for p in plugins:
                    ol.add_option(Option(_row_label(p, p.id in subs), id=p.id))
            yield ol
            yield Static(Text("click a plugin to subscribe/unsubscribe · ⚠ high-risk needs /admin unlock",
                              style="dim"), classes="panel-hint")

        def on_button_pressed(self, event) -> None:
            # ✕ — undock the whole panel, mirroring the other docked panels.
            event.stop()
            app = getattr(self, "app", None)
            if app is not None and hasattr(app, "hide_panel"):
                try:
                    app.hide_panel()
                except Exception:
                    # The panel is already closing, or has no host to close it.
                    pass

        def on_option_list_option_selected(self, event) -> None:
            # select = toggle subscription (click or Enter).
            event.stop()
            pid = getattr(getattr(event, "option", None), "id", None)
            if pid and pid != "__none__":
                self._toggle(pid)
                self._rebuild()

        def _toggle(self, pid: str) -> None:
            """Subscribe/unsubscribe ``pid`` via the pure :func:`toggle_subscription`
            (which owns the high-risk elevation gate), then notify the outcome."""
            outcome, message = toggle_subscription(self._state, pid)
            severity = "warning" if outcome in ("gated", "no-project") else "information"
            self._notify(message, severity=severity)

        def _notify(self, message: str, *, severity: str = "information") -> None:
            notify = getattr(self._actions, "notify", None)
            if callable(notify):
                try:
                    notify(message, severity=severity)
                    return
                except Exception:
                    # PanelActions notify is unavailable -- fall through to the app toast below.
                    pass
            app = getattr(self, "app", None)
            if app is not None and hasattr(app, "notify"):
                try:
                    app.notify(message, severity=severity, timeout=3)
                except Exception:
                    # Both notify paths failed; the subscription change still applied, just silently.
                    pass

        def _rebuild(self) -> None:
            """Repaint the ``●`` markers after a toggle, preserving the cursor row."""
            try:
                ol = self.query_one("#panel-plugins-list", OptionList)
            except Exception:
                return
            plugins = self._plugins()
            subs = _subscribed(self._state)
            keep = ol.highlighted
            ol.clear_options()
            for p in plugins:
                ol.add_option(Option(_row_label(p, p.id in subs), id=p.id))
            if keep is not None:
                try:
                    ol.highlighted = keep
                except Exception:
                    # The remembered row no longer exists -- leave the default highlight.
                    pass
            # Repaint the header count too.
            try:
                from rich.text import Text
                self.query_one(".panel-title", Static).update(
                    Text(f"plugins · {len(subs)}/{len(plugins)} subscribed")
                )
            except Exception:
                # The header count is cosmetic; the rows above already rebuilt.
                pass

    def plugins_view(state: Any, *, actions: Any = None) -> Any:
        return PluginsPanel(state, actions)

else:  # pragma: no cover - no [tui]: the view is unavailable, the registry isn't

    def plugins_view(state: Any, *, actions: Any = None) -> Any:  # type: ignore[misc]
        return None
