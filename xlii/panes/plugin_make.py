"""``PluginMakePane`` — face-native plugin scaffold. No CLAIM_INPUT, no $EDITOR.

Cycle effect / trust / auth / output, then seed ``/plugin new`` for review.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node
from xlii.panes import PREFILL, RETARGET_SLOT, Action, Outcome, Rendered, RenderedRow, Selection
from xlii.plugin_scaffold import AUTH_RING, EFFECT_RING, OUTPUT_RING, TRUST_RING

_CAPTION = "caption"


def _next(ring: tuple, cur: str) -> str:
    try:
        i = list(ring).index(cur)
    except ValueError:
        return ring[0]
    return ring[(i + 1) % len(ring)]


class PluginMakePane:
    """Knob pane: pick plugin badges, seed ``/plugin new``."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="pluginmake")
        self._id: str = "my-api"
        self._effect: str = "read-only"
        self._trust: str = "subscription"
        self._auth: str = "none"
        self._output: str = "interpret"
        self._subscribe: bool = True
        self._rows: list[tuple[str, str, str, str]] = []
        self._sel: int = 0
        self._edit_id: str = ""
        self._form_cache: Optional[tuple] = None
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        del select
        self._address = address if isinstance(address, Address) else Address.parse(str(address))
        edit = (
            (self._address.key or "").strip()
            or str(getattr(self._address, "target", "") or "").strip()
        )
        if edit != getattr(self, "_edit_id", ""):
            self._form_cache = None
            self._form_html_sent = None
        self._edit_id = edit
        if edit:
            self._id = edit
        self._reload()

    def _cached_form(self):
        edit = getattr(self, "_edit_id", "") or ""
        mtime = 0
        try:
            from xlii.plugin import Plugin

            p = Plugin(id=edit) if edit else None
            if p is not None and p.path.exists():
                mtime = p.path.stat().st_mtime_ns
        except Exception:
            mtime = 0
        key = (edit, mtime)
        cached = getattr(self, "_form_cache", None)
        if cached and cached[0] == key:
            return cached[1]
        form = None
        try:
            from xlii.plugin_make_form import form_spec

            form = form_spec(edit)
        except Exception:
            form = None
        self._form_cache = (key, form)
        return form

    def scaffold_command(self) -> str:
        cmd = (
            f"/plugin new {self._id}"
            f" --effect {self._effect}"
            f" --trust {self._trust}"
            f" --auth {self._auth}"
            f" --output {self._output}"
        )
        if self._subscribe:
            cmd += " --subscribe"
        return cmd

    def _reload(self) -> None:
        rows: list[tuple[str, str, str, str]] = []
        rows.append(("hdr:make", "── plugin maker ──", _CAPTION, ""))
        rows.append((
            "id",
            f"id · {self._id}  (edit in the seeded line)",
            "leaf",
            f"prefill:{self.scaffold_command()}",
        ))
        rows.append(("effect", f"effect · {self._effect}", "leaf", "cycle:effect"))
        rows.append(("trust", f"trust · {self._trust}", "leaf", "cycle:trust"))
        rows.append(("auth", f"auth · {self._auth}", "leaf", "cycle:auth"))
        rows.append(("output", f"output · {self._output}", "leaf", "cycle:output"))
        rows.append((
            "sub",
            f"subscribe here · {'yes' if self._subscribe else 'no'}",
            "leaf",
            "cycle:sub",
        ))
        rows.append(("hdr:go", "── write ──", _CAPTION, ""))
        rows.append((
            "scaffold",
            "scaffold · seed /plugin new (writes on send, no editor)",
            "leaf",
            f"prefill:{self.scaffold_command()}",
        ))
        rows.append(("back", "◂ plugins", "leaf", "back"))
        self._rows = rows
        if self._sel >= len(self._rows):
            self._sel = 0
        while self._rows and self._rows[self._sel][2] == _CAPTION:
            self._sel = (self._sel + 1) % len(self._rows)
            if self._sel == 0:
                break

    def render(self) -> Rendered:
        self._reload()
        out = []
        for i, (rid, label, kind, verb) in enumerate(self._rows):
            out.append(RenderedRow(
                text=label,
                address=f"pluginmake://{rid}",
                kind=kind,
                selected=(i == self._sel and kind != _CAPTION),
                tone="knob" if verb.startswith("cycle:") else "",
            ))
        edit = getattr(self, "_edit_id", "") or ""
        form = self._cached_form()
        if form:
            rev = form.get("rev")
            slim = {
                "plugin": form.get("plugin"),
                "action": form.get("action"),
                "name": form.get("name"),
                "id": form.get("id"),
                "title": form.get("title"),
                "rev": rev,
                "html": form.get("html") or "",
            }
            if getattr(self, "_form_html_sent", None) == rev:
                slim["html"] = ""
            else:
                self._form_html_sent = rev
            form = slim
        return Rendered(
            title="pluginmake:// — the file" if not edit else f"pluginmake://{edit}",
            rows=tuple(out),
            empty=not out,
            form=form,
        )

    def selection(self) -> Selection:
        if not self._rows:
            return Selection(node=None)
        rid, label, kind, verb = self._rows[self._sel]
        return Selection(node=Node(
            address=f"pluginmake://{rid}",
            name=label,
            kind=kind,
            extra={"verb": verb},
        ))

    def actions(self) -> list[Action]:
        if not self._rows:
            return []
        rid, _label, kind, verb = self._rows[self._sel]
        if kind == _CAPTION or not verb:
            return []
        if verb.startswith("cycle:"):
            return [Action("apply", "Cycle", Outcome(PREFILL, "pluginmake://", text=""))]
        if verb == "back":
            return [Action("back", "◂ Plugins", Outcome(RETARGET_SLOT, address="plugins://"))]
        if verb.startswith("prefill:"):
            return [Action(
                "seed",
                "Seed into input",
                Outcome(PREFILL, f"pluginmake://{rid}", text=verb.split(":", 1)[1]),
            )]
        return []

    def apply_selection(self) -> bool:
        if not self._rows:
            return False
        verb = self._rows[self._sel][3]
        if verb.startswith("cycle:"):
            self._cycle(verb.split(":", 1)[1])
            return True
        return False

    def handle(self, key: str) -> bool:
        if not self._rows:
            return False
        if key == "enter":
            return self.apply_selection()
        if key == "down":
            self._sel = min(self._sel + 1, len(self._rows) - 1)
            while self._sel < len(self._rows) - 1 and self._rows[self._sel][2] == _CAPTION:
                self._sel += 1
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            while self._sel > 0 and self._rows[self._sel][2] == _CAPTION:
                self._sel -= 1
            return True
        return False

    def select_index(self, index: int) -> bool:
        leaves = [i for i, r in enumerate(self._rows) if r[2] != _CAPTION]
        if 0 <= index < len(leaves):
            self._sel = leaves[index]
            return True
        return False

    def _cycle(self, name: str) -> None:
        if name == "effect":
            self._effect = _next(EFFECT_RING, self._effect)
        elif name == "trust":
            self._trust = _next(TRUST_RING, self._trust)
        elif name == "auth":
            self._auth = _next(AUTH_RING, self._auth)
        elif name == "output":
            self._output = _next(OUTPUT_RING, self._output)
        elif name == "sub":
            self._subscribe = not self._subscribe
