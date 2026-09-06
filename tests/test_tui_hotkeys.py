"""Configurable doorway-hotkey modifier + the accelerator-letter underline (commander mode)."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from xlii.tui_textual import (  # noqa: E402
    _DOORWAY_LETTERS,
    _doorway_key_set,
    _folder_tab,
    _menu_accel_key_map,
    _parse_modifier,
)


# --- modifier parsing --------------------------------------------------------


def test_parse_modifier_normalizes_and_defaults():
    assert _parse_modifier("alt") == ["alt"]
    assert _parse_modifier("ctrl-alt") == ["ctrl", "alt"]          # '-' accepted like '+'
    assert _parse_modifier("CTRL+SHIFT+ALT") == ["ctrl", "shift", "alt"]
    assert _parse_modifier("control+option") == ["ctrl", "alt"]    # aliases normalized
    assert _parse_modifier("") == ["alt"]                          # empty → default
    assert _parse_modifier("bogus") == ["alt"]                     # junk → default
    assert _parse_modifier("alt+alt") == ["alt"]                   # de-duped


def test_doorway_key_set_is_order_independent():
    keys = _doorway_key_set("ctrl+alt")
    # every doorway letter, and BOTH modifier orderings, so a match survives Textual's canonical order
    for letter in _DOORWAY_LETTERS:
        assert f"ctrl+alt+{letter}" in keys
        assert f"alt+ctrl+{letter}" in keys
    # the letter is always last so callers recover it with split('+')[-1]
    assert all(k.split("+")[-1] in _DOORWAY_LETTERS for k in keys)


def test_doorway_key_set_alt_default():
    keys = _doorway_key_set("alt")
    assert keys == {f"alt+{c}" for c in _DOORWAY_LETTERS}


# --- the accelerator underline on the doorway/files chips --------------------


def _has_underline(text) -> bool:
    return any("underline" in str(s.style) for s in text.spans)


def test_focused_chip_gets_underline():
    tabs = [("skills 1", "door", "skills"), ("files", "files", None)]
    out, spans = _folder_tab(60, tabs, "#00cc66", focused=0)
    assert _has_underline(out)
    assert len(spans) == 2


def test_unfocused_chips_have_no_underline():
    out, _ = _folder_tab(60, [("role:ada", "role", "ada")], "#00cc66", focused=None)
    assert not _has_underline(out)


# --- the app wires the configured modifier -----------------------------------


def _app_with_modifier(modifier):
    from types import SimpleNamespace

    from xlii.tui_textual import XliiApp

    cfg = SimpleNamespace(tui_hotkey_modifier=modifier) if modifier is not None else SimpleNamespace()
    st = SimpleNamespace(shell_cwd=None, project=SimpleNamespace(project_root=None, name="p"),
                         agent=SimpleNamespace(session=None), cfg=cfg)
    return XliiApp(project_name="p", agent=st.agent, run_turn=lambda q: ("", set(), None), state=st)


def test_app_builds_doorway_keys_from_config():
    app = _app_with_modifier("ctrl+alt")
    assert "ctrl+alt+s" in app._doorway_keys and "alt+ctrl+s" in app._doorway_keys
    # unset → default alt
    assert "alt+d" in _app_with_modifier(None)._doorway_keys


def test_set_hotkey_modifier_rebuilds_the_key_set():
    app = _app_with_modifier("alt")
    applied = app.set_hotkey_modifier("shift+ctrl+alt", persist=False)
    assert applied == "shift+ctrl+alt"                 # preserves the input token order
    assert "ctrl+shift+alt+m" in app._doorway_keys     # some permutation is present
    assert "alt+s" not in app._doorway_keys            # the old alt-only keys are gone


# --- the menu-bar accelerators (the underlined mnemonics), wired to the keyboard ----


def test_menu_accel_key_map_covers_every_title():
    m = _menu_accel_key_map("alt")
    assert m == {"alt+x": "Xlii", "alt+p": "Project", "alt+t": "Tools",
                 "alt+c": "Commands", "alt+o": "Options",
                 "alt+n": "Panel Workbench", "alt+h": "Help"}


def test_menu_accel_key_map_is_order_independent():
    m = _menu_accel_key_map("ctrl+alt")
    assert m.get("ctrl+alt+p") == "Project" and m.get("alt+ctrl+p") == "Project"


def test_menus_win_the_letters_they_share_with_doorways():
    # 'p' (Project) and 't' (Tools) are menu accelerators, so they are NOT doorway letters —
    # the plan/tasks panes stay reachable via their menu entries, not a colliding Alt key.
    assert "p" not in _DOORWAY_LETTERS and "t" not in _DOORWAY_LETTERS
    assert "alt+p" not in _doorway_key_set("alt")


def test_app_builds_menu_accel_keys_and_rebuilds_with_modifier():
    app = _app_with_modifier("alt")
    assert app._menu_accel_keys.get("alt+p") == "Project"
    app.set_hotkey_modifier("ctrl+alt", persist=False)
    assert app._menu_accel_keys.get("ctrl+alt+p") == "Project"
    assert "alt+p" not in app._menu_accel_keys          # old single-mod key retired


def test_commander_hotkey_menu_wins_then_doorway_then_falls_through():
    opened, doors = [], []
    app = _app_with_modifier("alt")
    app._open_menu = lambda title, x=0: opened.append(title)
    app.action_doorway = lambda letter: doors.append(letter)

    assert app._commander_hotkey("alt+p") is True       # menu accelerator wins the shared letter
    assert opened == ["Project"] and doors == []
    assert app._commander_hotkey("alt+f") is True       # a pure doorway still fires
    assert doors == ["f"]
    assert app._commander_hotkey("alt+z") is False       # not a hotkey → not handled


def test_commander_hotkeys_fire_through_the_real_key_path(tmp_path):
    """End-to-end: Alt+P opens the Project menu and Alt+F toggles the files doorway through
    Textual's actual on_key dispatch (the underlined mnemonics, finally wired to the keyboard).
    Regression for 'none of the alt hotkeys ever work'."""
    import asyncio
    from types import SimpleNamespace

    from xlii.tui_textual import XliiApp

    def _fake_agent():
        from xlii.agent import SessionState

        return SimpleNamespace(console=None, rail=None, debug=None, plan_mode=False,
                               active_mode=None, howto_mode=False, history=[],
                               model_override=None, session=SessionState())

    async def body():
        st = SimpleNamespace(shell_cwd=tmp_path,
                             project=SimpleNamespace(project_root=tmp_path, name="p"),
                             agent=_fake_agent(),
                             cfg=SimpleNamespace(tui_hotkey_modifier="alt"))
        app = XliiApp(project_name="p", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        opened, doors = [], []
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._open_menu = lambda title, x=0: opened.append(title)   # don't push a real modal
            app.action_doorway = lambda letter: doors.append(letter)
            await pilot.press("alt+p")    # menu accelerator → Project
            await pilot.pause()
            await pilot.press("alt+f")    # content doorway → files
            await pilot.pause()
        assert opened == ["Project"]
        assert doors == ["f"]

    asyncio.run(body())
