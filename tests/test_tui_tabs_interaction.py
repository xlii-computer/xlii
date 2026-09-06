"""Vector A1 — Context Tabs & Kinds: the interactive surface (Textual pilot).

The tab row is now interactive: a click maps its x to the tab it hit and opens
that tab's surface; keyboard focus (ctrl+b) + ←/→ cycle a highlighted tab and
Enter opens it. The surface itself is Vector A2's seam (`tui/preview.py`); A1
only *opens* it, degrading to a toast while A2 hasn't landed (parallel build).

Driven through Textual's run_test() pilot (no real TTY); skipped when textual
isn't installed (the optional [tui] extra).
"""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from xlii.tui_textual import XliiApp, _ChipRow, _PromptInput  # noqa: E402


def _fake_agent():
    from xlii.agent import SessionState
    return SimpleNamespace(
        console=None, rail=None, debug=None, plan_mode=False, active_mode=None,
        howto_mode=False, history=[], model_override=None, session=SessionState(),
    )


def _state(tmp_path):
    return SimpleNamespace(
        shell_cwd=tmp_path,
        project=SimpleNamespace(project_root=tmp_path, name="proj"),
        agent=_fake_agent(),
    )


def _app(tmp_path, **state_kw):
    st = _state(tmp_path)
    for k, v in state_kw.items():
        setattr(st, k, v)
    return XliiApp(project_name="proj", agent=st.agent,
                   run_turn=lambda q: ("", set(), None), state=st), st


def _run(coro_fn):
    asyncio.run(coro_fn())


def _span_for(cap, kind):
    return next(s for s in cap._spans if s[2] == kind)


# --------------------------------------------------------------------------- #
#  Click → activate the tab whose chip span covers the click x
# --------------------------------------------------------------------------- #

def test_click_on_tab_activates_that_kind(tmp_path):
    async def body():
        app, st = _app(tmp_path, attached_docs=[("conventions", "x")])  # a doc → the docs doorway rides
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause()
            app._refresh_status()
            await pilot.pause()
            cap = app.query_one("#input-chips", _ChipRow)

            calls = []
            app._activate_tab = lambda kind, payload: calls.append((kind, payload))

            # Click the dead-center of a content-type doorway chip; its (kind, scheme) must fire.
            # The cap is a single notched border row now, so the chips live on row 0.
            start, end, kind, _payload = _span_for(cap, "door")
            await pilot.click(cap, offset=((start + end) // 2, 0))
            await pilot.pause()
            assert calls and calls[0][0] == "door"
            assert calls[0][1] in ("skills", "docs", "mark", "locker")  # a scheme payload
    _run(body)


def test_click_outside_any_tab_is_a_noop(tmp_path):
    async def body():
        app, st = _app(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            app._refresh_status()
            await pilot.pause()
            cap = app.query_one("#input-chips", _ChipRow)
            calls = []
            app._activate_tab = lambda kind, payload: calls.append((kind, payload))
            # The chips group at the RIGHT now; the left ─ fill (before the first
            # notch) is past no chip, so a click there hits nothing.
            await pilot.click(cap, offset=(5, 0))
            await pilot.pause()
            assert calls == []
    _run(body)


# --------------------------------------------------------------------------- #
#  Keyboard: ctrl+b focuses the cap, ←/→ cycle, Enter opens, Esc returns
# --------------------------------------------------------------------------- #

def test_ctrl_b_focuses_cap_and_cycle_then_enter_activates(tmp_path):
    async def body():
        app, st = _app(tmp_path, active_role="ada",
                       attached_docs=[("conventions", "x")])
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause()
            app._refresh_status()
            await pilot.pause()
            calls = []
            app._activate_tab = lambda kind, payload: calls.append((kind, payload))

            assert app.query_one("#input", _PromptInput).has_focus
            await pilot.press("ctrl+b")
            await pilot.pause()
            cap = app.query_one("#input-chips", _ChipRow)
            assert cap.has_focus
            assert cap._focused_idx == 0            # role tab leads

            await pilot.press("right")              # → the docs doorway
            await pilot.pause()
            assert cap._focused_idx == 1
            await pilot.press("enter")
            await pilot.pause()
            assert calls and calls[0][0] == "door"

            await pilot.press("escape")             # hand focus back to the input
            await pilot.pause()
            assert app.query_one("#input", _PromptInput).has_focus
    _run(body)


def test_cycle_wraps_around(tmp_path):
    async def body():
        # a doc + a /ref bookmark → tabs: docs, bookmarks
        app, st = _app(tmp_path, attached_docs=[("conventions", "d"), ("point:thesis", "p")])
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause()
            app._refresh_status()
            await pilot.pause()
            cap = app.query_one("#input-chips", _ChipRow)
            cap.focus()
            await pilot.pause()
            last = len(cap._tabs) - 1
            await pilot.press("left")               # wrap backward 0 → last
            await pilot.pause()
            assert cap._focused_idx == last
            await pilot.press("right")              # wrap forward last → 0
            await pilot.pause()
            assert cap._focused_idx == 0
    _run(body)


def test_focus_highlights_the_focused_tab(tmp_path):
    async def body():
        app, st = _app(tmp_path)
        st.attached_docs = [("a.md", "x")]
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            app._refresh_status()
            await pilot.pause()
            cap = app.query_one("#input-chips", _ChipRow)

            def underline_chars():
                # cap.render() is Textual Content; sum the columns carrying an underline. Doorway
                # chips underline just their accelerator letter (always); focusing a tab underlines
                # its WHOLE label on top, so focus shows up as an INCREASE in underline coverage.
                return sum(sp.end - sp.start for sp in cap.render().spans if "underline" in str(sp.style))

            before = underline_chars()
            cap.focus()
            await pilot.pause()
            assert underline_chars() > before   # focusing highlights (fully underlines) the focused tab
    _run(body)


# --------------------------------------------------------------------------- #
#  Surface seam (A2): present → push; absent → graceful toast, never a raise
# --------------------------------------------------------------------------- #

def test_activate_is_graceful_when_surface_unavailable(tmp_path, monkeypatch):
    # A1's fallback contract: when A2's surface can't be opened (module absent in a
    # parallel build, or the factory yields nothing), _activate_tab degrades to a
    # toast rather than raising. Force the unavailable path directly — robust
    # regardless of whether A2's module is imported elsewhere in a full-suite run.
    async def body():
        app, st = _app(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            monkeypatch.setattr(app, "_open_preview_surface", lambda kind, payload: False)
            app._activate_tab("doc", [("a.md", "x")])  # toast, must not raise
            await pilot.pause()
    _run(body)


def test_activate_pushes_a2_surface_when_present(tmp_path, monkeypatch):
    async def body():
        import xlii.tui
        from textual.screen import ModalScreen

        class _FakeSurface(ModalScreen):
            def __init__(self, kind, payload):
                super().__init__()
                self.kind, self.payload = kind, payload

        captured = {}

        def surface(kind, payload):
            captured["args"] = (kind, payload)
            return _FakeSurface(kind, payload)

        fake = types.ModuleType("xlii.tui.preview")
        fake.surface = surface
        monkeypatch.setitem(sys.modules, "xlii.tui.preview", fake)
        monkeypatch.setattr(xlii.tui, "preview", fake, raising=False)

        app, st = _app(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            ok = app._open_preview_surface("doc", [("a.md", "x")])
            assert ok is True
            assert captured["args"] == ("doc", [("a.md", "x")])
            await pilot.pause()
            assert isinstance(app.screen, _FakeSurface)
    _run(body)
