"""The Pane-2 add wizard (xlii/tui/remote_wizard.py) + its /remote routing.

The wizard inverts the guided flow's control: no blocked worker, the panel owns
the form and calls add_connection once on Save. Units cover the pure halves
(field tables, opts collection, the secret-omitting audit echo, the panel-first
routing); pilots cover the widget (fill→Save kwargs, Esc cancel, protocol
switch re-rendering, masked secret).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from xlii.tui.remote_wizard import build_flag_echo, collect_opts, wizard_fields


# --------------------------------------------------------------------------- #
#  pure units
# --------------------------------------------------------------------------- #

def test_wizard_fields_follow_the_shared_tables():
    smb = [f for f, _l, _r in wizard_fields("smb")]
    assert smb == ["host", "port", "user", "share", "domain"]
    # the webdav host-optional rule the walk applies is mirrored exactly
    dav = {f: req for f, _l, req in wizard_fields("webdav")}
    assert dav["host"] is False and "base_url" in dav


def test_collect_opts_mirrors_the_walk():
    opts, err = collect_opts("smb", {"host": "nas", "share": "docs", "port": "1445"})
    assert err is None and opts == {"protocol": "smb", "host": "nas",
                                    "share": "docs", "port": "1445"}
    _opts, err = collect_opts("smb", {"host": "nas"})          # share required
    assert err and "share" in err
    opts, err = collect_opts("ftps", {"host": "h", "insecure": "y"})
    assert err is None and opts["insecure"] is True             # y/N parsing
    _opts, err = collect_opts("webdav", {})                     # host OR base_url
    assert err and "base_url" in err
    opts, err = collect_opts("webdav", {"base_url": "https://c/dav"})
    assert err is None and "host" not in opts


def test_flag_echo_never_carries_the_secret():
    opts = {"protocol": "sftp", "host": "h", "port": "2222",
            "key_path": "/id", "secret": "hunter2"}
    echo = build_flag_echo("box", opts)
    assert "hunter2" not in echo
    assert echo == "/remote add box --protocol sftp --host h --port 2222 --key-path /id"
    assert build_flag_echo("s", {"protocol": "ftp", "host": "h", "insecure": True}) \
        == "/remote add s --host h --insecure"


# --------------------------------------------------------------------------- #
#  /remote add routing: panel-first, walk as fallback
# --------------------------------------------------------------------------- #

class _Con:
    def __init__(self):
        self.printed = []

    def print(self, *a, **k):
        self.printed.append(" ".join(str(x) for x in a))


def test_add_prefers_the_wizard_panel(monkeypatch):
    from xlii.repl_cmds import remote as RC
    from xlii.tui import panels

    shown = {}
    monkeypatch.setattr(panels, "current_panel_host", lambda: object())
    monkeypatch.setattr(panels, "show_panel",
                        lambda side, view, *, state: shown.update(side=side, view=view) or True)
    monkeypatch.setattr(RC, "_guided_add",
                        lambda *a: (_ for _ in ()).throw(AssertionError("walk used despite panel")))
    state = SimpleNamespace()
    RC._add(_Con(), ["box"], state=state)
    assert shown == {"side": "right", "view": "remote-add"}
    assert state._remote_wizard_prefill == "box"        # one-shot prefill stashed


def test_add_falls_back_to_the_walk_without_a_host(monkeypatch):
    from xlii.repl_cmds import remote as RC
    from xlii.tui import panels

    monkeypatch.setattr(panels, "current_panel_host", lambda: None)
    walked = []
    monkeypatch.setattr(RC, "_guided_add", lambda console, name: walked.append(name))
    RC._add(_Con(), ["box"], state=SimpleNamespace())
    assert walked == ["box"]                            # inline REPL path intact


# --------------------------------------------------------------------------- #
#  pilots — the widget itself
# --------------------------------------------------------------------------- #

pytest.importorskip("textual")

from textual.app import App  # noqa: E402
from textual.widgets import Input, Select  # noqa: E402

from xlii.tui.remote_wizard import RemoteAddWizard  # noqa: E402


class _Host(App):
    def __init__(self, state, prefill=""):
        super().__init__()
        self._state = state
        self._prefill = prefill

    def compose(self):
        yield RemoteAddWizard(self._state, prefill_name=self._prefill)


def _state():
    return SimpleNamespace(console=_Con())


def test_pilot_fill_and_save_calls_add_connection(tmp_path, monkeypatch):
    from xlii import remotefs as R

    got = {}
    monkeypatch.setattr(R, "add_connection",
                        lambda name, **kw: got.update(name=name, **kw) or {"protocol": kw.get("protocol")})
    closed = []
    from xlii.tui import panels
    monkeypatch.setattr(panels, "hide_panel", lambda: closed.append(1) or True)

    async def body():
        st = _state()
        app = _Host(st, prefill="box")
        async with app.run_test() as pilot:
            await pilot.pause()
            wiz = app.query_one(RemoteAddWizard)
            wiz.query_one("#wiz-protocol", Select).value = "smb"
            await pilot.pause()                          # re-render smb fields
            wiz.query_one("#wiz-f-host", Input).value = "nas.local"
            wiz.query_one("#wiz-f-share", Input).value = "docs"
            secret = wiz.query_one("#wiz-secret", Input)
            assert secret.password is True               # masked by construction
            secret.value = "hunter2"
            wiz.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="wiz-save")))
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert got["name"] == "box" and got["protocol"] == "smb"
        assert got["host"] == "nas.local" and got["share"] == "docs"
        assert got["secret"] == "hunter2"
        assert closed == [1]                             # panel closed on success
        joined = "\n".join(st.console.printed)
        assert "--share docs" in joined                  # the audit echo…
        assert "hunter2" not in joined                   # …never the secret
    asyncio.run(asyncio.wait_for(body(), timeout=30))


def test_pilot_protocol_switch_rerenders_fields(tmp_path):
    async def body():
        app = _Host(_state())
        async with app.run_test() as pilot:
            await pilot.pause()
            wiz = app.query_one(RemoteAddWizard)
            assert not wiz.query("#wiz-f-share")         # ftp: no smb fields
            wiz.query_one("#wiz-protocol", Select).value = "smb"
            await pilot.pause()
            assert wiz.query_one("#wiz-f-share", Input)  # smb fields appeared
            wiz.query_one("#wiz-protocol", Select).value = "webdav"
            await pilot.pause()
            assert wiz.query_one("#wiz-f-base_url", Input)
            assert not wiz.query("#wiz-f-share")
    asyncio.run(asyncio.wait_for(body(), timeout=30))


def test_pilot_escape_cancels_without_saving(tmp_path, monkeypatch):
    from xlii import remotefs as R

    called = []
    monkeypatch.setattr(R, "add_connection", lambda *a, **k: called.append(1))
    closed = []
    from xlii.tui import panels
    monkeypatch.setattr(panels, "hide_panel", lambda: closed.append(1) or True)

    async def body():
        app = _Host(_state(), prefill="box")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one(RemoteAddWizard).query_one("#wiz-secret", Input).focus()
            await pilot.press("escape")
            await pilot.pause()
        assert closed == [1] and not called
    asyncio.run(asyncio.wait_for(body(), timeout=30))


def test_pilot_missing_required_shows_error_not_save(tmp_path, monkeypatch):
    from xlii import remotefs as R

    called = []
    monkeypatch.setattr(R, "add_connection", lambda *a, **k: called.append(1))

    async def body():
        app = _Host(_state(), prefill="box")
        async with app.run_test() as pilot:
            await pilot.pause()
            wiz = app.query_one(RemoteAddWizard)         # host left empty (required for ftp)
            wiz.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="wiz-save")))
            await pilot.pause()
            from textual.widgets import Static
            err = str(wiz.query_one("#wiz-error", Static).render())
            assert "required" in err and not called
    asyncio.run(asyncio.wait_for(body(), timeout=30))