"""``PluginsPane`` — installed plugins catalog (TUI/face panel parity).

Root ``plugins://``: ●/○ subscription list. Enter / primary action toggles
subscribe. "Open" drills into structured actions. Action leaves run via a
face prefill marker (``__plugin_call__:id:action``) handled by the face sink.

Mirrors the TUI ``PluginsPanel`` feel: catalog + subscribe first; invoke
is secondary (structured actions only).
"""

from __future__ import annotations

from typing import Any, Optional

from xlii.addressing import Address, Node
from xlii.panes import PREFILL, RETARGET_SLOT, Action, Outcome, Rendered, RenderedRow, Selection


def _xli_dir() -> Any:
    from xlii.active_session import active_session

    project = getattr(active_session(), "project", None)
    return getattr(project, "xli_dir", None) if project is not None else None


def _subscribed() -> set[str]:
    from xlii.plugin import load_subscriptions

    xli = _xli_dir()
    return set(load_subscriptions(xli)) if xli is not None else set()


def _plugin_key_caption() -> str:
    """Vault keys for subscribed plugins — lives here, not on the stream HUD."""
    from xlii.plugin import list_plugins
    from xlii.plugin_form import missing_vault_vars

    sub = _subscribed()
    need = ready = 0
    for p in list_plugins():
        if p.id not in sub:
            continue
        try:
            envs = [n for n in (p.auth_env_vars() or []) if n]
        except Exception:
            continue
        if not envs:
            continue
        need += 1
        try:
            missing = missing_vault_vars(p)
        except Exception:
            missing = envs
        if not missing:
            ready += 1
    if not need:
        return ""
    return f"keys · {ready}/{need} set"


class PluginsPane:
    """Headless plugins catalog — pure projection of plugins:// + selection."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="plugins")
        self._rows: list[tuple[str, str, str, bool]] = []  # id, label, hint, accent
        self._sel: int = 0
        self._mode: str = "list"  # list | actions
        self._plugin_id: str = ""
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        from xlii.plugin import Plugin, list_plugins

        self._address = address if isinstance(address, Address) else Address.parse(address)
        target = self._address.target.strip()
        parts = [p for p in target.split("/") if p]
        sub = _subscribed()

        if len(parts) >= 1 and parts[0]:
            # Drill: plugins://id  or  plugins://id/action
            self._mode = "actions"
            self._plugin_id = parts[0]
            p = Plugin(id=self._plugin_id)
            self._rows = []
            if p.exists():
                try:
                    from xlii.plugin_manifest import parse_manifest
                    m = parse_manifest(p.read_raw())
                except Exception:
                    m = None
                if m and m.actions:
                    for a in m.actions:
                        req = [
                            spec.name for spec in a.params.values()
                            if spec.const is None and spec.required
                            and spec.default is None
                        ]
                        hint = (a.description or "")[:60]
                        if req:
                            need = "needs " + ", ".join(req)
                            hint = f"{need} — {hint}" if hint else need
                        self._rows.append((
                            a.id,
                            a.id,
                            hint,
                            False,
                        ))
                else:
                    self._rows.append(("", "(no actions — not a plugin)", "", False))
            else:
                self._rows.append(("", f"(missing {self._plugin_id})", "", False))
        else:
            self._mode = "list"
            self._plugin_id = ""
            plugs = sorted(list_plugins(), key=lambda x: (0 if x.id in sub else 1, x.id.lower()))
            self._rows = []
            cap = _plugin_key_caption()
            if cap:
                self._rows.append(("", cap, "", False))
            for p in plugs:
                effect, trust = p.effect_trust()
                mark = "● " if p.id in sub else "○ "
                self._rows.append((
                    p.id,
                    f"{mark}{p.id}",
                    f"{effect} · {trust}" + (f" — {p.description()}" if p.description() else ""),
                    p.id in sub,
                ))

        self._sel = 0
        if select:
            sel = select if "://" not in select else Address.parse(select).target.strip()
            sel = sel.split("/")[-1] if sel else ""
            for i, (rid, *_rest) in enumerate(self._rows):
                if rid and (rid == sel or select.endswith(rid)):
                    self._sel = i
                    break
        if self._mode == "list" and self._sel == 0 and self._rows and not self._rows[0][0]:
            if any(r[0] for r in self._rows[1:]):
                self._sel = 1

    def _leaves(self) -> list[int]:
        """Selectable row indexes — captions (empty id) are not in this list.

        Face/TUI ``select_index`` is a leaf ordinal (captions already skipped
        at the click). ``_sel`` is still a raw ``_rows`` index.
        """
        return [i for i, (rid, *_rest) in enumerate(self._rows) if rid]

    def render(self) -> Rendered:
        title = (
            f"plugins://{self._plugin_id}" if self._mode == "actions" else "plugins://"
        )
        rows = [
            RenderedRow(
                text=label + (f"  {hint}" if hint else ""),
                address=(
                    f"plugins://{self._plugin_id}/{rid}" if self._mode == "actions" and rid
                    else (f"plugins://{rid}" if rid else "plugins://")
                ),
                kind="leaf" if rid else "caption",
                selected=(i == self._sel and bool(rid)),
                accent=accent,
            )
            for i, (rid, label, hint, accent) in enumerate(self._rows)
        ]
        return Rendered(title=title, rows=tuple(rows), empty=not self._rows)

    def selection(self) -> Selection:
        if not self._rows:
            return Selection(node=None)
        rid, label, hint, _ = self._rows[self._sel]
        if self._mode == "actions":
            addr = f"plugins://{self._plugin_id}/{rid}" if rid else f"plugins://{self._plugin_id}"
        else:
            addr = f"plugins://{rid}" if rid else "plugins://"
        return Selection(node=Node(
            address=addr, name=rid or label, kind="leaf",
            extra={"type": "plugin" if self._mode == "list" else "plugin_action"},
        ))

    def actions(self) -> list[Action]:
        if not self._rows:
            if self._mode == "list":
                return [Action(
                    "new", "New plugin…",
                    Outcome(RETARGET_SLOT, address="pluginmake://"),
                )]
            return []
        rid, _label, _hint, accent = self._rows[self._sel]
        if self._mode == "list":
            make = Action(
                "new", "New plugin…",
                Outcome(RETARGET_SLOT, address="pluginmake://"),
            )
            if not rid:
                return [make]
            return [
                Action(
                    "toggle",
                    "Unsubscribe" if accent else "Subscribe",
                    Outcome(PREFILL, text=f"__plugin_toggle__:{rid}"),
                ),
                Action(
                    "edit", "Edit in maker",
                    Outcome(RETARGET_SLOT, address=f"pluginmake://{rid}"),
                ),
                Action(
                    "view", "View source",
                    Outcome(RETARGET_SLOT, address=f"plugins://{rid}/source"),
                ),
                Action(
                    "open", "Actions…",
                    Outcome(RETARGET_SLOT, address=f"plugins://{rid}"),
                ),
                make,
            ]
        # actions mode
        out: list[Action] = []
        if rid:
            from xlii.plugin import Plugin
            from xlii.plugin_form import action_needs_form, auth_form_target

            p = Plugin(id=self._plugin_id)
            m = p.manifest() if p.exists() else None
            act = m.get_action(rid) if m is not None else None
            setup = auth_form_target(p) if p.exists() else None
            if setup is not None:
                sid, said = setup
                out.append(Action(
                    "run", "Run",
                    Outcome(RETARGET_SLOT, address=f"pluginform://{sid}/{said}"),
                ))
            elif act is not None and action_needs_form(act, {}):
                out.append(Action(
                    "run", "Run",
                    Outcome(RETARGET_SLOT, address=f"pluginform://{self._plugin_id}/{rid}"),
                ))
            else:
                out.append(Action(
                    "run", "Run",
                    Outcome(PREFILL, text=f"__plugin_call__:{self._plugin_id}:{rid}"),
                ))
        if self._plugin_id:
            out.append(Action(
                "view", "View source",
                Outcome(RETARGET_SLOT, address=f"plugins://{self._plugin_id}/source"),
            ))
        if self._plugin_id and self._plugin_id not in _subscribed():
            out.append(Action(
                "subscribe", "Subscribe first",
                Outcome(PREFILL, text=f"__plugin_toggle__:{self._plugin_id}"),
            ))
        out.append(Action("back", "◂ Plugins", Outcome(RETARGET_SLOT, address="plugins://")))
        return out

    def handle(self, key: str) -> bool:
        leaves = self._leaves()
        if not leaves:
            return False
        try:
            pos = leaves.index(self._sel)
        except ValueError:
            pos = 0
        if key == "down":
            self._sel = leaves[min(pos + 1, len(leaves) - 1)]
            return True
        if key == "up":
            self._sel = leaves[max(pos - 1, 0)]
            return True
        if key == "home":
            self._sel = leaves[0]
            return True
        if key == "end":
            self._sel = leaves[-1]
            return True
        return False

    def select_index(self, i: int) -> bool:
        """``i`` is a leaf ordinal (Face/TUI skip captions before calling)."""
        leaves = self._leaves()
        if not (0 <= i < len(leaves)):
            return False
        self._sel = leaves[i]
        return True
