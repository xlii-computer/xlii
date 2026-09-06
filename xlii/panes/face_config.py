"""``FaceConfigPane`` — session knobs + a read-only xAI account snapshot.

Face draws cycle knobs as native ``<select>`` menus (no click-to-cycle flash).
TUI still cycles on Enter. Account rows are fetch-on-demand (the panel must
open instantly). Money/key mutation stays off this pane — ``/account`` is
still read-only. Mid-use mojo rename is NOT a cycle — it orphans ``chat/<id>``.
"""

from __future__ import annotations

from typing import Any, Optional

from xlii.addressing import Address, Node
from xlii.panes import PREFILL, Action, Outcome, Rendered, RenderedRow, Selection

_CAPTION = "caption"
_TIER_RING = ("off", "auto", "fast", "expert", "heavy")
_RETRIEVAL_RING = ("hybrid", "semantic", "keyword")
_FABRIC_RING = (0, 300, 900, 1800, 3600)


def _state() -> Any:
    from xlii.active_session import active_session

    return active_session()


def _next(ring: tuple, cur) -> Any:
    try:
        i = list(ring).index(cur)
    except ValueError:
        return ring[0]
    return ring[(i + 1) % len(ring)]


class FaceConfigPane:
    """Headless session-knobs list for the face side dock."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="faceconfig")
        # (id, label, kind, verb)  kind=caption|leaf  verb=cycle:x|load|prefill:…
        self._rows: list[tuple[str, str, str, str]] = []
        self._sel: int = 0
        self._account: Optional[dict] = None
        self._wire_after: Optional[dict] = None
        if address is not None:
            self.mount(address)

    def pop_wire(self) -> Optional[dict]:
        ev = self._wire_after
        self._wire_after = None
        return ev

    def _ask_term_cwd_path(self) -> None:
        cfg = self._cfg()
        initial = str(getattr(cfg, "tui_terminal_cwd_path", "") or "") if cfg else ""
        self._wire_after = {
            "type": "claim_line",
            "prompt": "new terminal folder:",
            "initial": initial,
            "submit": "set_terminal_cwd_path",
        }

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        del select
        self._address = Address(scheme="faceconfig")
        self._reload()

    def _cfg(self):
        st = _state()
        if st is None:
            return None
        # Same object chrome_state reads — not agent.cfg if those ever diverge.
        return getattr(st, "cfg", None) or getattr(getattr(st, "agent", None), "cfg", None)

    def _session(self):
        st = _state()
        agent = getattr(st, "agent", None) if st else None
        return getattr(agent, "session", None) if agent else None

    def _reload(self) -> None:
        st = _state()
        agent = getattr(st, "agent", None) if st else None
        sess = getattr(agent, "session", None) if agent else None
        cfg = self._cfg()
        project = getattr(st, "project", None) if st else None

        tier = getattr(sess, "chat_tier", None) or "off"
        yolo = bool(getattr(sess, "yolo", False) or getattr(st, "yolo", False))
        retrieval = str(getattr(cfg, "retrieval_mode", None) or "hybrid")
        fabric = int(getattr(cfg, "fabric_pull_interval_s", 900) or 0)
        budget_on = bool(getattr(sess, "budget_note", None))
        persona = getattr(project, "bound_persona", None) or ""
        name = getattr(project, "name", "") or ""
        orch = chat = worker = "—"
        if cfg is not None:
            try:
                orch = cfg.orchestrator() or "—"
                chat = cfg.chat() or "—"
                worker = cfg.worker() or "—"
            except Exception:
                # The em-dash placeholders set above stand in for models that can't be read.
                pass
        profile = "custom"
        try:
            from xlii.model_profiles import effective_model_profiles, profile_matches_cfg

            if cfg is not None:
                for n in sorted(effective_model_profiles(cfg)):
                    if profile_matches_cfg(cfg, n):
                        profile = n
                        break
        except Exception:
            # profile stays "custom", which is the honest label when matching can't run.
            pass

        rows: list[tuple[str, str, str, str]] = []
        rows.append(("hdr:acct", "── account ──", _CAPTION, ""))
        snap = self._account
        if snap is None:
            rows.append(("acct", "xAI account · select to load", "leaf", "load"))
        elif snap.get("error"):
            rows.append(("acct", f"xAI account · {snap['error']}", "leaf", "load"))
        else:
            extra = f" · prepaid {snap['prepaid']}" if snap.get("prepaid") else ""
            rows.append((
                "acct",
                f"team · {snap.get('team', '—')} · tier {snap.get('tier', '—')}"
                f" · models {snap.get('models', '—')}",
                "leaf", "load",
            ))
            rows.append(("spend", f"credits · {snap.get('spend', '—')}{extra}",
                         "leaf", "prefill:/account"))
            rows.append(("akeys", f"keys · {snap.get('keys', '—')}",
                         "leaf", "prefill:/account keys"))

        rows.append(("hdr:models", "── models ──", _CAPTION, ""))
        rows.append(("orch", f"orchestrator · {orch}", "leaf", "prefill:/model --list"))
        rows.append(("chatm", f"chat · {chat}", "leaf", "prefill:/model --chat "))
        rows.append(("workerm", f"worker · {worker}", "leaf", "prefill:/model --worker "))
        rows.append(("profile", f"profile · {profile}", "leaf", "prefill:/model --profile "))

        rows.append(("hdr:session", "── session ──", _CAPTION, ""))
        rows.append(("tier", f"chat tier · {tier}", "leaf", "cycle:tier"))
        rows.append(("yolo", f"bash gate · {'yolo' if yolo else 'safe'}",
                     "leaf", "cycle:yolo"))
        rows.append(("retrieval", f"retrieval · {retrieval}", "leaf", "cycle:retrieval"))
        fabric_s = "off" if fabric <= 0 else f"{fabric // 60}m"
        rows.append(("fabric", f"fabric pull · {fabric_s}", "leaf", "cycle:fabric"))
        rows.append(("budget", f"budget-awareness · {'on' if budget_on else 'off'}",
                     "leaf", "cycle:budget"))
        tool_iters = getattr(cfg, "max_tool_iterations", 20) if cfg is not None else 20
        chat_iters = getattr(cfg, "max_chat_tool_iterations", 8) if cfg is not None else 8
        worker_iters = getattr(cfg, "max_worker_iterations", 10) if cfg is not None else 10
        rows.append(("iters", f"tool iterations · {tool_iters}", "leaf", "cycle:iters"))
        rows.append(("chatiters", f"chat iterations · {chat_iters}",
                     "leaf", "cycle:chatiters"))
        rows.append(("workiters", f"worker iterations · {worker_iters}",
                     "leaf", "cycle:workiters"))

        from xlii.desk import pref_label

        editor = pref_label(str(getattr(cfg, "editor", "") or "") if cfg else "")
        image_ed = pref_label(str(getattr(cfg, "image_editor", "") or "") if cfg else "")
        browser = pref_label(str(getattr(cfg, "browser", "") or "") if cfg else "")
        term_pref = pref_label(str(getattr(cfg, "tui_terminal", "") or "") if cfg else "")
        skills_on = bool(getattr(cfg, "import_foreign_skills", True)) if cfg is not None else True
        rows.append(("hdr:desk", "── desk ──", _CAPTION, ""))
        rows.append(("editor", f"editor · {editor}", "leaf", "cycle:editor"))
        rows.append(("imged", f"image editor · {image_ed}", "leaf", "cycle:imged"))
        rows.append(("browser", f"os browser · {browser}", "leaf", "cycle:browser"))
        rows.append(("termpref", f"terminal · {term_pref}", "leaf", "cycle:termpref"))
        from xlii.desk import TERM_CWD_CUSTOM, term_cwd_label

        dest = term_cwd_label(cfg)
        rows.append(("termcwd", f"new terminal · {dest}", "leaf", "cycle:termcwd"))
        mode = ""
        if cfg is not None:
            from xlii.desk import normalize_term_cwd

            mode = normalize_term_cwd(getattr(cfg, "tui_terminal_cwd", ""))
        if mode == TERM_CWD_CUSTOM:
            raw_path = str(getattr(cfg, "tui_terminal_cwd_path", "") or "").strip()
            path_s = dest if raw_path else "click to set"
            rows.append(("termcwdpath", f"new terminal path · {path_s}",
                         "leaf", "termcwdpath"))
        rows.append(("skills", f"import foreign skills · {'on' if skills_on else 'off'}",
                     "leaf", "cycle:skills"))
        side = str(getattr(cfg, "tui_panel_side", "") or "right").lower()
        if side not in ("left", "right"):
            side = "right"
        width_pct = int(getattr(cfg, "panel_width_pct", 0) or 0) if cfg is not None else 0
        width_s = "auto" if width_pct <= 0 else f"{width_pct}%"
        rows.append(("side", f"panel side · {side}", "leaf", "cycle:side"))
        rows.append(("width", f"panel width · {width_s}", "leaf", "cycle:width"))
        from xlii.panes.home import hub_open_mode

        hub = hub_open_mode(st)
        where = "this panel" if hub == "this" else "other panel"
        rows.append(("hubopen", f"home hub opens · {where}", "leaf", "cycle:hubopen"))

        rows.append(("hdr:identity", "── identity ──", _CAPTION, ""))
        try:
            from xlii.persona import factory_persona_id

            mojo = factory_persona_id(cfg)
        except Exception:
            mojo = "mojo"
        rows.append(("mojo",
                     f"mojo · {mojo}  (mobile journal — /name to call it something else)",
                     "leaf", "prefill:/name "))
        # A leftover folder bind is NOT a persona. Code folders talk to mojo.
        if persona and persona != mojo:
            rows.append((
                "bind",
                f"folder bind · {persona} · click to drop (talk here is mojo)",
                "leaf", "cycle:unbind",
            ))
        rows.append(("project", f"project · {name or '—'}", "leaf", "prefill:/project "))
        from xlii.desk import resolve_terminal_cwd, compact_home_path

        here = compact_home_path(resolve_terminal_cwd(st, cfg)) or dest
        rows.append(("term", f"open terminal · {here}", "leaf", "term"))

        self._rows = rows
        if self._sel >= len(self._rows):
            self._sel = 0
        # Don't land on a caption
        while self._rows and self._rows[self._sel][2] == _CAPTION:
            self._sel = (self._sel + 1) % len(self._rows)
            if self._sel == 0:
                break

    def render(self) -> Rendered:
        self._reload()
        out = []
        for i, (rid, label, kind, verb) in enumerate(self._rows):
            choices, value = self._choices_for(verb)
            out.append(RenderedRow(
                text=label,
                address=f"faceconfig://{rid}",
                kind=kind,
                selected=(i == self._sel and kind != _CAPTION),
                tone="knob" if verb.startswith("cycle:") or verb == "termcwdpath" else "",
                choices=choices,
                value=value,
            ))
        return Rendered(
            title="config:// — account · models · knobs",
            rows=tuple(out),
            empty=not out,
        )

    def selection(self) -> Selection:
        if not self._rows:
            return Selection(node=None)
        rid, label, kind, verb = self._rows[self._sel]
        return Selection(node=Node(
            address=f"faceconfig://{rid}",
            name=label,
            kind=kind,
            extra={"verb": verb},
        ))

    def actions(self) -> "list[Action]":
        if not self._rows:
            return []
        _rid, _label, kind, verb = self._rows[self._sel]
        if kind == _CAPTION or not verb:
            return []
        if verb.startswith("cycle:"):
            return [Action("apply", "Cycle", Outcome(PREFILL, "faceconfig://", text=""))]
        if verb == "load":
            return [Action("apply", "Load account", Outcome(PREFILL, "faceconfig://", text=""))]
        if verb == "term":
            return [Action("apply", "Open terminal", Outcome(PREFILL, "faceconfig://", text=""))]
        if verb == "termcwdpath":
            return [Action("apply", "Set folder", Outcome(PREFILL, "faceconfig://", text=""))]
        if verb.startswith("prefill:"):
            seed = verb.split(":", 1)[1]
            return [Action(
                "seed",
                "Seed into input",
                Outcome(PREFILL, f"faceconfig://{_rid}", text=seed),
            )]
        return []

    def apply_value(self, address: str, value: str) -> bool:
        """Face ``<select>``: set this knob to *value* (no cycle)."""
        rid = str(address or "").rstrip("/").rsplit("/", 1)[-1]
        for row in self._rows:
            if row[0] != rid:
                continue
            verb = row[3]
            if verb.startswith("cycle:"):
                self._set(verb.split(":", 1)[1], value)
                return True
            return False
        return False

    def apply_selection(self) -> bool:
        """Cycle / load / terminal — used by FaceDeck instead of a fake prefill."""
        if not self._rows:
            return False
        verb = self._rows[self._sel][3]
        if verb.startswith("cycle:"):
            self._cycle(verb.split(":", 1)[1])
            return True
        if verb == "load":
            self._load_account()
            return True
        if verb == "term":
            from xlii.desk import resolve_terminal_cwd
            from xlii.interactive import launch_in_external_terminal

            st = _state()
            cfg = self._cfg()
            cwd = resolve_terminal_cwd(st, cfg)
            pref = str(getattr(cfg, "tui_terminal", "") or "") if cfg else ""
            launch_in_external_terminal(cwd, run="", preferred=pref)
            return True
        if verb == "termcwdpath":
            self._ask_term_cwd_path()
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
        # index is among non-caption rows (FaceDeck maps that way)
        leaves = [i for i, r in enumerate(self._rows) if r[2] != _CAPTION]
        if 0 <= index < len(leaves):
            self._sel = leaves[index]
            return True
        return False

    def _load_account(self) -> None:
        try:
            from xlii.cmds.account import account_snapshot

            self._account = account_snapshot()
        except Exception as e:
            self._account = {"error": f"{type(e).__name__}"}

    def _save_cfg(self) -> None:
        save = getattr(self._cfg(), "save", None)
        if callable(save):
            try:
                save()
            except Exception:
                # The change already applied to the live cfg; only persisting it is lost.
                pass

    def _choices_for(self, verb: str) -> tuple[tuple, str]:
        """``((value, label), …), current`` for Face ``<select>``. Empty = click."""
        if not verb.startswith("cycle:"):
            return (), ""
        name = verb.split(":", 1)[1]
        if name == "unbind":
            return (), ""
        sess = self._session()
        cfg = self._cfg()
        st = _state()

        def pairs(ring, cur, labels=None) -> tuple[tuple, str]:
            cur_s = "" if cur is None else str(cur)
            opts = []
            seen = set()
            for x in ring:
                v = str(x)
                if v in seen:
                    continue
                seen.add(v)
                lab = labels.get(x, v) if labels else v
                opts.append((v, lab))
            if cur_s and cur_s not in seen:
                opts.insert(0, (cur_s, cur_s))
            return tuple(opts), cur_s

        if name == "tier":
            cur = getattr(sess, "chat_tier", None) or "off"
            return pairs(_TIER_RING, cur)
        if name == "yolo":
            on = bool(getattr(sess, "yolo", False) or getattr(st, "yolo", False))
            return (("safe", "safe"), ("yolo", "yolo")), ("yolo" if on else "safe")
        if name == "retrieval":
            cur = str(getattr(cfg, "retrieval_mode", None) or "hybrid") if cfg else "hybrid"
            return pairs(_RETRIEVAL_RING, cur)
        if name == "fabric":
            cur = int(getattr(cfg, "fabric_pull_interval_s", 900) or 0) if cfg else 900
            labels = {0: "off", 300: "5m", 900: "15m", 1800: "30m", 3600: "60m"}
            return pairs(_FABRIC_RING, cur, labels)
        if name == "budget":
            on = bool(getattr(sess, "budget_note", None))
            return (("off", "off"), ("on", "on")), ("on" if on else "off")
        if name == "iters":
            from xlii.desk import TOOL_ITER_RING

            cur = getattr(cfg, "max_tool_iterations", 20) if cfg is not None else 20
            return pairs(TOOL_ITER_RING, cur)
        if name == "chatiters":
            from xlii.desk import CHAT_ITER_RING

            cur = getattr(cfg, "max_chat_tool_iterations", 8) if cfg is not None else 8
            return pairs(CHAT_ITER_RING, cur)
        if name == "workiters":
            from xlii.desk import WORKER_ITER_RING

            cur = getattr(cfg, "max_worker_iterations", 10) if cfg is not None else 10
            return pairs(WORKER_ITER_RING, cur)
        if name in ("editor", "imged", "browser", "termpref"):
            from xlii.desk import (
                BROWSER_CANDIDATES,
                EDITOR_CANDIDATES,
                IMAGE_EDITOR_CANDIDATES,
                TERMINAL_CANDIDATES,
                installed,
            )

            field = {
                "editor": ("editor", EDITOR_CANDIDATES),
                "imged": ("image_editor", IMAGE_EDITOR_CANDIDATES),
                "browser": ("browser", BROWSER_CANDIDATES),
                "termpref": ("tui_terminal", TERMINAL_CANDIDATES),
            }[name]
            cur = str(getattr(cfg, field[0], "") or "") if cfg else ""
            ring = [""] + installed(field[1])
            labels = {"": "auto"}
            return pairs(ring, cur, labels)
        if name == "termcwd":
            from xlii.desk import TERM_CWD_LABELS, TERM_CWD_RING, normalize_term_cwd

            cur = normalize_term_cwd(getattr(cfg, "tui_terminal_cwd", "") if cfg else "")
            return pairs(TERM_CWD_RING, cur, TERM_CWD_LABELS)
        if name == "skills":
            on = bool(getattr(cfg, "import_foreign_skills", True)) if cfg is not None else True
            return (("on", "on"), ("off", "off")), ("on" if on else "off")
        if name == "side":
            cur = str(getattr(cfg, "tui_panel_side", "") or "right").lower() if cfg else "right"
            if cur not in ("left", "right"):
                cur = "right"
            return (("right", "right"), ("left", "left")), cur
        if name == "width":
            from xlii.desk import PANEL_WIDTH_RING

            cur = int(getattr(cfg, "panel_width_pct", 0) or 0) if cfg is not None else 0
            labels = {0: "auto", 25: "25%", 33: "33%", 50: "50%", 60: "60%", 70: "70%"}
            return pairs(PANEL_WIDTH_RING, cur, labels)
        if name == "hubopen":
            from xlii.panes.home import HUB_OPEN_OTHER, HUB_OPEN_THIS, hub_open_mode

            cur = hub_open_mode(st)
            labels = {HUB_OPEN_THIS: "this panel", HUB_OPEN_OTHER: "other panel"}
            return pairs((HUB_OPEN_THIS, HUB_OPEN_OTHER), cur, labels)
        return (), ""

    def _set(self, name: str, value: str) -> None:
        """Set a cycle knob to an explicit value (Face dropdown)."""
        st = _state()
        sess = self._session()
        cfg = self._cfg()
        raw = str(value or "")
        if name == "tier" and sess is not None:
            nxt = raw.strip().lower() or "off"
            if nxt not in _TIER_RING:
                return
            sess.chat_tier = None if nxt == "off" else nxt
            from xlii.repl_cmds.code import _persist_chat_tier

            _persist_chat_tier(cfg, sess.chat_tier)
        elif name == "yolo" and st is not None:
            on = raw.strip().lower() in ("yolo", "on", "1", "true")
            if sess is not None and hasattr(sess, "yolo"):
                sess.yolo = on
            try:
                st.yolo = on
            except Exception:
                # The session flag above already carries the toggle; this state mirror is a convenience.
                pass
        elif name == "retrieval" and cfg is not None:
            if raw in _RETRIEVAL_RING:
                cfg.retrieval_mode = raw
                self._save_cfg()
        elif name == "fabric" and cfg is not None:
            try:
                n = int(raw)
            except ValueError:
                return
            if n in _FABRIC_RING:
                cfg.fabric_pull_interval_s = n
                self._save_cfg()
        elif name == "budget":
            if sess is None:
                return
            want = raw.strip().lower() in ("on", "1", "true")
            have = bool(getattr(sess, "budget_note", None))
            if want == have:
                return
            self._cycle("budget")
        elif name in ("iters", "chatiters", "workiters") and cfg is not None:
            from xlii.desk import CHAT_ITER_RING, TOOL_ITER_RING, WORKER_ITER_RING

            field, ring = {
                "iters": ("max_tool_iterations", TOOL_ITER_RING),
                "chatiters": ("max_chat_tool_iterations", CHAT_ITER_RING),
                "workiters": ("max_worker_iterations", WORKER_ITER_RING),
            }[name]
            try:
                n = int(raw)
            except ValueError:
                return
            if n in ring:
                setattr(cfg, field, n)
                self._save_cfg()
        elif name in ("editor", "imged", "browser", "termpref") and cfg is not None:
            field = {
                "editor": "editor",
                "imged": "image_editor",
                "browser": "browser",
                "termpref": "tui_terminal",
            }[name]
            setattr(cfg, field, "" if raw in ("", "auto") else raw)
            self._save_cfg()
        elif name == "termcwd" and cfg is not None:
            from xlii.desk import TERM_CWD_CUSTOM, TERM_CWD_RING

            if raw not in TERM_CWD_RING:
                return
            cfg.tui_terminal_cwd = raw
            self._save_cfg()
            if raw == TERM_CWD_CUSTOM and not str(
                getattr(cfg, "tui_terminal_cwd_path", "") or ""
            ).strip():
                self._ask_term_cwd_path()
        elif name == "skills" and cfg is not None:
            cfg.import_foreign_skills = raw.strip().lower() not in ("off", "0", "false")
            self._save_cfg()
        elif name == "side" and cfg is not None:
            if raw in ("left", "right"):
                cfg.tui_panel_side = raw
                self._save_cfg()
        elif name == "width" and cfg is not None:
            from xlii.desk import PANEL_WIDTH_RING

            try:
                n = int(raw)
            except ValueError:
                return
            if n in PANEL_WIDTH_RING:
                cfg.panel_width_pct = n
                self._save_cfg()
        elif name == "hubopen":
            from xlii.panes.home import HUB_OPEN_OTHER, HUB_OPEN_THIS, hub_open_mode

            if raw not in (HUB_OPEN_THIS, HUB_OPEN_OTHER):
                return
            if hub_open_mode(st) != raw:
                self._cycle("hubopen")
        elif name == "unbind":
            self._cycle("unbind")

    def _cycle_int(self, field: str, ring: tuple) -> None:
        cfg = self._cfg()
        if cfg is None:
            return
        cur = int(getattr(cfg, field, ring[0]) or ring[0])
        setattr(cfg, field, _next(ring, cur))
        self._save_cfg()

    def _cycle_desk(self, field: str, candidates: tuple) -> None:
        from xlii.desk import cycle_pref, installed

        cfg = self._cfg()
        if cfg is None:
            return
        cur = str(getattr(cfg, field, "") or "")
        setattr(cfg, field, cycle_pref(cur, installed(candidates)))
        self._save_cfg()

    def _cycle(self, name: str) -> None:
        st = _state()
        sess = self._session()
        cfg = self._cfg()
        if name == "tier" and sess is not None:
            cur = getattr(sess, "chat_tier", None) or "off"
            nxt = _next(_TIER_RING, cur)
            sess.chat_tier = None if nxt == "off" else nxt
            from xlii.repl_cmds.code import _persist_chat_tier

            _persist_chat_tier(cfg, sess.chat_tier)
        elif name == "yolo" and st is not None:
            on = not bool(getattr(sess, "yolo", False) or getattr(st, "yolo", False))
            if sess is not None and hasattr(sess, "yolo"):
                sess.yolo = on
            try:
                st.yolo = on
            except Exception:
                # As in the toggle path: the session flag above already carries it.
                pass
        elif name == "retrieval" and cfg is not None:
            cur = str(getattr(cfg, "retrieval_mode", None) or "hybrid")
            cfg.retrieval_mode = _next(_RETRIEVAL_RING, cur)
            self._save_cfg()
        elif name == "fabric" and cfg is not None:
            cur = int(getattr(cfg, "fabric_pull_interval_s", 900) or 0)
            cfg.fabric_pull_interval_s = _next(_FABRIC_RING, cur)
            self._save_cfg()
        elif name == "iters":
            from xlii.desk import TOOL_ITER_RING

            self._cycle_int("max_tool_iterations", TOOL_ITER_RING)
        elif name == "chatiters":
            from xlii.desk import CHAT_ITER_RING

            self._cycle_int("max_chat_tool_iterations", CHAT_ITER_RING)
        elif name == "workiters":
            from xlii.desk import WORKER_ITER_RING

            self._cycle_int("max_worker_iterations", WORKER_ITER_RING)
        elif name == "editor":
            from xlii.desk import EDITOR_CANDIDATES

            self._cycle_desk("editor", EDITOR_CANDIDATES)
        elif name == "imged":
            from xlii.desk import IMAGE_EDITOR_CANDIDATES

            self._cycle_desk("image_editor", IMAGE_EDITOR_CANDIDATES)
        elif name == "browser":
            from xlii.desk import BROWSER_CANDIDATES

            self._cycle_desk("browser", BROWSER_CANDIDATES)
        elif name == "termpref":
            from xlii.desk import TERMINAL_CANDIDATES

            self._cycle_desk("tui_terminal", TERMINAL_CANDIDATES)
        elif name == "termcwd" and cfg is not None:
            from xlii.desk import TERM_CWD_CUSTOM, cycle_term_cwd

            nxt = cycle_term_cwd(cfg)
            self._save_cfg()
            if nxt == TERM_CWD_CUSTOM and not str(
                getattr(cfg, "tui_terminal_cwd_path", "") or ""
            ).strip():
                self._ask_term_cwd_path()
        elif name == "skills" and cfg is not None:
            cfg.import_foreign_skills = not bool(getattr(cfg, "import_foreign_skills", True))
            self._save_cfg()
        elif name == "side" and cfg is not None:
            cur = str(getattr(cfg, "tui_panel_side", "") or "right").lower()
            cfg.tui_panel_side = "left" if cur != "left" else "right"
            self._save_cfg()
        elif name == "width":
            from xlii.desk import PANEL_WIDTH_RING

            self._cycle_int("panel_width_pct", PANEL_WIDTH_RING)
        elif name == "hubopen":
            from xlii.panes.home import cycle_hub_open

            cycle_hub_open(st)
        elif name == "unbind":
            proj = getattr(st, "project", None) if st is not None else None
            if proj is not None and getattr(proj, "bound_persona", None):
                proj.bound_persona = None
                save = getattr(proj, "save", None)
                if callable(save):
                    try:
                        save()
                    except Exception:
                        # The project change already applied in memory; only persisting it is lost.
                        pass
        elif name == "budget":
            if sess is None:
                return
            if getattr(sess, "budget_note", None):
                sess.budget_note = None
                return
            try:
                from xlii.cmds.account import budget_note
                from xlii.config import GlobalConfig
                from xlii import xai_mgmt as mgmt

                g = GlobalConfig.load()
                if not g.management_api_key:
                    return
                tid = mgmt.resolve_active_team(g.management_api_key, getattr(g, "team_id", None))
                if tid:
                    sess.budget_note = budget_note(g.management_api_key, tid)
            except Exception:
                # The budget note is decorative: a missing management key, an
                # unreachable API, or a team-lookup miss leaves it unset.
                pass
