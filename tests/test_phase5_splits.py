"""Structure tests for grades Phase 5 god-module splits."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_tui_app_shell_under_size_budget():
    app = ROOT / "xlii" / "tui" / "app.py"
    n = sum(1 for _ in app.open())
    assert n <= 900, f"app.py still too large: {n} lines"


def test_tui_mixins_exist_and_export():
    from xlii.tui.app import XliiApp

    mro = {c.__name__ for c in XliiApp.__mro__}
    assert "AppMenuMixin" in mro
    assert "AppPanelMixin" in mro
    assert "AppInputMixin" in mro
    assert "AppTurnMixin" in mro
    # critical methods still on the app class (via MRO)
    assert hasattr(XliiApp, "_agent_turn")
    assert hasattr(XliiApp, "action_doorway")
    assert hasattr(XliiApp, "show_panel")
    assert hasattr(XliiApp, "_open_palette")


def test_hotkey_menu_notification_failure_does_not_crash(capsys):
    from types import SimpleNamespace

    from xlii.tui.app import XliiApp  # noqa: F401 - initializes mixin module globals
    from xlii.tui.app_menu_mixin import AppMenuMixin

    class FakeMenu(AppMenuMixin):
        def __init__(self):
            self.applied = None
            self._state = SimpleNamespace(cfg=SimpleNamespace(tui_hotkey_modifier="alt"))

        def set_hotkey_modifier(self, raw: str, *, persist: bool = True) -> str:
            self.applied = raw
            return raw

        def notify(self, *args, **kwargs):
            raise RuntimeError("toast unavailable")

    fake = FakeMenu()
    fake._run_menu_action("opt:hotkey:ctrl+alt")

    assert fake.applied == "ctrl+alt"
    assert "toast unavailable" in capsys.readouterr().err


def _fake_menu(surface="code", scratch=False):
    from types import SimpleNamespace

    from xlii.tui.app import XliiApp  # noqa: F401 — initializes mixin module globals
    from xlii.tui.app_menu_mixin import AppMenuMixin

    class FakeMenu(AppMenuMixin):
        def __init__(self):
            self._state = SimpleNamespace(
                profile=SimpleNamespace(mode=surface), scratch=scratch,
                shell_cwd="/tmp/proj", cfg=SimpleNamespace(tui_hotkey_modifier="alt"))
            self.submitted: list[str] = []
            self.claimed: list[tuple] = []

        def _submit_prompt(self, s):
            self.submitted.append(s)

        def _claim_line(self, prompt, on_submit, *, initial="", on_cancel=None):
            self.claimed.append((prompt, initial))
            return True

    return FakeMenu()


def test_mode_rows_enabled_and_route_to_switch_commands():
    """The Xlii-menu mode rows (once (V3 gate)-disabled) are ON and each runs its
    in-session switch command through the shared dispatch."""
    fake = _fake_menu(surface="code")
    rows = {i: (label, enabled) for i, label, enabled in fake._menu_items("Xlii")}
    assert rows["xlii:mode:chat"][1] is True
    assert rows["xlii:mode:code"][1] is True
    assert "✓" in rows["xlii:mode:code"][0]           # current surface is marked
    assert "✓" not in rows["xlii:mode:chat"][0]
    for iid, cmd in (("xlii:mode:chat", "/chat"), ("xlii:mode:code", "/code"),
                     ("xlii:mode:scratch", "/scratch")):
        fake.submitted.clear()
        fake._run_menu_action(iid)
        assert fake.submitted == [cmd]


def test_scratch_row_offered_only_on_the_code_surface():
    def scratch_enabled(surface):
        return {i: e for i, _l, e in _fake_menu(surface=surface)._menu_items("Xlii")}["xlii:mode:scratch"]
    assert scratch_enabled("code") is True      # scratch is a code-surface overlay
    assert scratch_enabled("chat") is False     # …not offered from chat


def test_create_project_row_is_on_and_claims_the_input():
    fake = _fake_menu()
    rows = {i: e for i, _l, e in fake._menu_items("Project")}
    assert rows["proj:create"] is True
    fake._run_menu_action("proj:create")
    assert fake.claimed and "adopt folder" in fake.claimed[0][0]
    assert fake.claimed[0][1] == "/tmp/proj"     # seeded with the live cwd


def test_builtin_providers_facade_and_package():
    from xlii.addressing._builtin_providers import register_builtins
    from xlii.addressing.builtins.register import register_builtins as rb2
    from xlii.addressing import providers

    assert register_builtins is rb2 or callable(register_builtins)
    assert callable(providers)
    # package must not shadow providers() — schemes registered
    schemes = set(providers())
    for s in ("file", "project", "conv", "wiki", "git", "map"):
        assert s in schemes


def test_no_provider_module_over_900_lines():
    base = ROOT / "xlii" / "addressing" / "builtins"
    for p in base.glob("*.py"):
        n = sum(1 for _ in p.open())
        assert n <= 250, f"{p.name} is {n} lines (split further if needed)"
    facade = ROOT / "xlii" / "addressing" / "_builtin_providers.py"
    facade_lines = sum(1 for _ in facade.open())
    assert facade_lines <= 40
