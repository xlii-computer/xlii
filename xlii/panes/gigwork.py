"""``GigworkPane`` — providers + jams over ``gigwork://`` (the Panel "Gigwork" doorway).

Two sections, one selectable list: **Providers** (configured gig endpoints — model, key
state, agent-allow and cache marks) and **Jams** (stock + configured crews — members,
merge, cap, origin). Every mutating action **seeds a ``/gigwork`` / ``/jam`` command
into the command line** (review-before-run, the ``tasks://`` pattern) — the pane itself
executes nothing and spends nothing. The jam "Edit as command" action seeds the crew's
full ``/jam add`` round-trip (``backend[:kit][@model]`` tokens), which is the on-the-fly
composer: recall, tweak a member or a model, Enter.

Pure projection of ``(address, selection)``: config is read through the ambient session
(:func:`~xlii.addressing.builtins.gigwork.ambient_gig_cfg` — the live agent's cfg, which
every /gigwork · /jam write mirrors), so the post-run refresh reflects a verb at once.
Bad gigwork config renders as one caption row carrying the error instead of a crash.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node
from xlii.panes import (
    PREFILL,
    RETARGET_SLOT,
    Action,
    Outcome,
    Rendered,
    RenderedRow,
    Selection,
)


class GigworkPane:
    """The providers + jams view over ``gigwork://``; actions seed commands only."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="gigwork")
        self._rows: "list[dict]" = []  # selectable rows, display order
        self._sel: int = 0
        self._title: str = "gigwork://"
        self._error: str = ""
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        self._address = address if isinstance(address, Address) else Address.parse(address)
        self._load()
        self._sel = 0
        target = select or self._address.target.strip()
        if target:
            for i, r in enumerate(self._rows):
                r_target = r["address"].split("://", 1)[1]
                if target in (r["address"], r_target, r["name"]) or select == r["address"]:
                    self._sel = i
                    break

    def _load(self) -> None:
        from xlii.addressing.builtins.gigwork import ambient_gig_cfg
        from xlii.chat_backend import GigError, gig_allowlist, gig_providers
        from xlii.jam import _user_jams, jam_specs

        self._rows, self._error = [], ""
        cfg = ambient_gig_cfg()
        try:
            providers = gig_providers(cfg)
            specs = jam_specs(cfg)
        except GigError as e:
            self._error = str(e)
            self._title = "gigwork://"
            return
        allow = set(gig_allowlist(cfg))
        configured = set(_user_jams(cfg))

        for name in sorted(providers):
            p = providers[name]
            if p.key_optional:
                key = "local" if p.is_local else "no key needed"
                ready = True
            elif p.key_set:
                key, ready = "key set", True
            else:
                key, ready = f"${p.api_key_env} unset", False
            marks = (" · agent" if name in allow else "") + \
                    (" · cache" if p.cache_effective else "")
            self._rows.append({
                "kind": "provider", "name": name,
                "address": f"gigwork://provider/{name}",
                "allowed": name in allow,
                "text": f"{name:<12} {p.model} · {key}{marks}",
                "tone": "" if ready else "modified",
            })
        for name in sorted(specs):
            s = specs[name]
            origin = "config" if name in configured else "stock"
            members = " + ".join(m.label for m in s.members)
            self._rows.append({
                "kind": "jam", "name": name,
                "address": f"gigwork://jam/{name}",
                "origin": origin,
                "tokens": " ".join(m.token for m in s.members),
                "merge": s.merge, "cap": s.max_parallel,
                "text": f"{name:<16} {members} · {s.merge} · ≤{s.max_parallel} · {origin}",
                "tone": "",
            })
        n_p = sum(1 for r in self._rows if r["kind"] == "provider")
        n_g = len(self._rows) - n_p
        self._title = f"gigwork://  ·  {n_p} providers · {n_g} jams"

    def render(self) -> Rendered:
        if self._error:
            return Rendered(title=self._title, empty=False, rows=(
                RenderedRow(text=f"config error: {self._error}", address="", kind="caption"),
            ))
        rows: "list[RenderedRow]" = []
        fi = 0
        # self._rows is already in display order (providers, then jams), so the
        # running selectable index IS the row's index in it.
        for section, kind in (("Providers", "provider"), ("Jams", "jam")):
            items = [r for r in self._rows if r["kind"] == kind]
            count = f" ({len(items)})" if items else " (none)"
            rows.append(RenderedRow(text=f"─ {section}{count} ", address="", kind="caption"))
            for r in items:
                rows.append(RenderedRow(
                    text=r["text"], address=r["address"], kind="leaf",
                    selected=(fi == self._sel), tone=r["tone"]))
                fi += 1
        return Rendered(title=self._title, rows=tuple(rows), empty=False)

    def selection(self) -> Selection:
        if not self._rows:
            return Selection(node=None)
        r = self._rows[self._sel]
        return Selection(node=Node(
            address=r["address"], name=r["name"], kind="leaf",
            extra={"type": "gig-provider" if r["kind"] == "provider" else "jam"}))

    def actions(self) -> "list[Action]":
        """Everything mutating is a ``PREFILL`` (review-before-run). Providers: hire ·
        allow/deny toggle · new · remove · view. Jams: ask · edit-as-command (the
        on-the-fly composer) · new · remove (config only — stock shadows, not deletes) ·
        view. First action is the Enter default; each name's first letter is its hotkey."""
        if self._error:
            return []
        if not self._rows:
            return [Action("new", "New provider…", Outcome(PREFILL, "gigwork://", text="/gigwork add "))]
        r = self._rows[self._sel]
        name, addr = r["name"], r["address"]
        if r["kind"] == "provider":
            acts = [Action("hire", "Hire…", Outcome(PREFILL, addr, text=f"/gigwork {name} "))]
            if r["allowed"]:
                acts.append(Action("deny", "Deny (agent)", Outcome(PREFILL, addr, text=f"/gigwork deny {name}")))
            else:
                acts.append(Action("allow", "Allow (agent)", Outcome(PREFILL, addr, text=f"/gigwork allow {name}")))
            acts.append(Action("new", "New provider…", Outcome(PREFILL, "gigwork://", text="/gigwork add ")))
            acts.append(Action("remove", "Remove", Outcome(PREFILL, addr, text=f"/gigwork rm {name}")))
            acts.append(Action("view", "View", Outcome(RETARGET_SLOT, addr)))
            return acts
        acts = [
            Action("ask", "Ask…", Outcome(PREFILL, addr, text=f"/jam {name} ")),
            Action("edit", "Edit as command…", Outcome(
                PREFILL, addr,
                text=f"/jam add {name} {r['tokens']} --merge {r['merge']} --cap {r['cap']}")),
            Action("new", "New jam…", Outcome(PREFILL, "gigwork://", text="/jam add ")),
        ]
        if r["origin"] == "config":
            acts.append(Action("remove", "Remove", Outcome(PREFILL, addr, text=f"/jam rm {name}")))
        acts.append(Action("view", "View", Outcome(RETARGET_SLOT, addr)))
        return acts

    def handle(self, key: str) -> bool:
        if not self._rows:
            return False
        if key == "down":
            self._sel = min(self._sel + 1, len(self._rows) - 1)
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            return True
        if key == "home":
            self._sel = 0
            return True
        if key == "end":
            self._sel = len(self._rows) - 1
            return True
        return False  # enter/back fall through to the surface (actions / close)

    def select_index(self, i: int) -> bool:
        """Select the row at index ``i`` (a mouse click's target). Returns True if in range."""
        if 0 <= i < len(self._rows):
            self._sel = i
            return True
        return False
