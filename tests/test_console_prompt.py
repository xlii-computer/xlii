"""The request_line seam (xlii/console_prompt.py) + the TUI PromptModal.

Regression suite for the /remote guided-add hang: a handler's blocking
input()/getpass deadlocks under Textual (stdin stays a tty but Textual owns the
keyboard), so prompts must prefer the console's ``request_input`` capability —
a modal — and only fall back to stdin where stdin actually works.

Three layers: pure seam units, the guided-add routing, and a Textual pilot
proving the worker-thread modal round-trip (bounded — a hang fails, not stalls).
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from xlii.console_prompt import can_prompt, request_line


class _Recorder:
    """A console with the TUI capability, serving canned answers."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked: list[tuple[str, bool]] = []
        self.printed: list[str] = []
        self.xlii_foreground = True

    def request_input(self, prompt, *, secret=False):
        self.asked.append((prompt, secret))
        return self.answers.pop(0)

    def print(self, *a, **k):
        self.printed.append(" ".join(str(x) for x in a))


class _Plain:
    """A console with NO capability (inline REPL shape)."""

    def __init__(self):
        self.printed = []

    def print(self, *a, **k):
        self.printed.append(" ".join(str(x) for x in a))


# --------------------------------------------------------------------------- #
#  seam units
# --------------------------------------------------------------------------- #

def test_request_line_prefers_the_console_capability(monkeypatch):
    # stdin sabotaged: if the capability isn't preferred, this test hangs/fails.
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("stdin used")))
    con = _Recorder(["hunter2"])
    assert request_line(con, "secret", secret=True) == "hunter2"
    assert con.asked == [("secret", True)]          # secret kwarg forwarded


def test_request_line_capability_cancel_and_error_mean_none():
    assert request_line(_Recorder([None]), "q") is None      # Esc in the modal

    class _Boom:
        def request_input(self, prompt, *, secret=False):
            raise RuntimeError("teardown race")

    assert request_line(_Boom(), "q") is None                 # never raises


def test_request_line_falls_back_to_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda prompt="": "answer")
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "s3cret")
    con = _Plain()
    assert request_line(con, "q") == "answer"
    assert request_line(con, "q", secret=True) == "s3cret"


def test_request_line_refuses_without_any_surface(monkeypatch):
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    con = _Plain()
    assert can_prompt(con) is False
    assert request_line(con, "q") is None
    assert can_prompt(_Recorder([])) is True        # capability alone suffices


# --------------------------------------------------------------------------- #
#  guided /remote add routes through the capability (the hang regression)
# --------------------------------------------------------------------------- #

def test_guided_add_uses_capability_never_stdin(monkeypatch):
    from xlii import remotefs as R
    from xlii.repl_cmds import remote as RC

    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("stdin used")))
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))  # TUI-ish: stdin unusable

    got = {}
    monkeypatch.setattr(R, "add_connection",
                        lambda name, **kw: got.update(name=name, **kw) or {"protocol": kw.get("protocol")})
    #                 protocol  host           port  user   key_path        secret(masked)
    con = _Recorder(["sftp", "example.net", "2222", "bob", "/id_ed25519", "hunter2"])
    RC._add(con, ["mybox"])
    assert got["name"] == "mybox" and got["protocol"] == "sftp"
    assert got["host"] == "example.net" and got["key_path"] == "/id_ed25519"
    assert got["secret"] == "hunter2"
    assert con.asked[-1][1] is True                # the secret prompt was masked


def test_guided_add_esc_aborts_cleanly(monkeypatch):
    from xlii import remotefs as R
    from xlii.repl_cmds import remote as RC

    called = []
    monkeypatch.setattr(R, "add_connection", lambda *a, **k: called.append(1))
    con = _Recorder(["sftp", None])                # Esc on the host question
    RC._add(con, ["mybox"])
    assert not called
    assert any("aborted" in p for p in con.printed)


def test_flag_add_secret_esc_aborts(monkeypatch):
    from xlii import remotefs as R
    from xlii.repl_cmds import remote as RC

    called = []
    monkeypatch.setattr(R, "add_connection", lambda *a, **k: called.append(1))
    con = _Recorder([None])                        # Esc on the secret modal
    RC._add(con, ["mybox", "--host", "h"])
    assert not called
    assert any("aborted" in p for p in con.printed)


# --------------------------------------------------------------------------- #
#  the TUI mechanics: worker-thread modal round-trip (bounded, hang = failure)
# --------------------------------------------------------------------------- #

pytest.importorskip("textual")

from xlii.tui_textual import XliiApp  # noqa: E402
from xlii.tui.app import PromptModal  # noqa: E402


def _fake_agent():
    from xlii.agent import SessionState
    return SimpleNamespace(
        console=None, rail=None, debug=None, plan_mode=False, active_mode=None,
        howto_mode=False, history=[], model_override=None, session=SessionState(),
    )


def _app(tmp_path):
    state = SimpleNamespace(
        shell_cwd=tmp_path,
        project=SimpleNamespace(project_root=tmp_path, name="proj"),
        agent=_fake_agent(),
    )
    return XliiApp(project_name="proj", agent=None,
                   run_turn=lambda q: ("", set(), None), state=state)


def _ask_in_thread(app, box, **kw):
    t = threading.Thread(
        target=lambda: box.update(answer=app._prompt_via_modal("passphrase", **kw)))
    t.start()
    return t


def test_pilot_prompt_modal_worker_roundtrip(tmp_path):
    """The exact hang shape: a worker thread asks, the UI thread serves a modal,
    the typed answer comes back. Bounded by wait_for — a deadlock FAILS."""
    async def body():
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            # The capability is wired onto the transcript console at mount.
            assert getattr(app._console, "request_input", None) is not None
            box: dict = {}
            t = _ask_in_thread(app, box, secret=True)
            for _ in range(50):                     # wait for the modal to appear
                await pilot.pause()
                if isinstance(app.screen, PromptModal):
                    break
            assert isinstance(app.screen, PromptModal)
            assert app.screen.query_one("#prompt-input").password is True  # masked
            await pilot.press("h", "i")
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause()
                if not t.is_alive():
                    break
            t.join(timeout=5)
            assert not t.is_alive() and box["answer"] == "hi"
    asyncio.run(asyncio.wait_for(body(), timeout=30))


def test_pilot_prompt_modal_escape_cancels(tmp_path):
    async def body():
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            box: dict = {}
            t = _ask_in_thread(app, box)
            for _ in range(50):
                await pilot.pause()
                if isinstance(app.screen, PromptModal):
                    break
            await pilot.press("escape")
            for _ in range(50):
                await pilot.pause()
                if not t.is_alive():
                    break
            t.join(timeout=5)
            assert not t.is_alive() and box["answer"] is None   # cancel, not ""
    asyncio.run(asyncio.wait_for(body(), timeout=30))


def test_prompt_via_modal_on_ui_thread_returns_none(tmp_path):
    """Can't block the UI thread on itself — must refuse, not deadlock."""
    async def body():
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._prompt_via_modal("q") is None
    asyncio.run(asyncio.wait_for(body(), timeout=30))