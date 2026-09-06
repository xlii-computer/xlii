"""`xlii artifact edit` — reference-image edits (media M5, faked wire)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import xlii.cmds.artifact as art
import xlii.tools as tools_mod
from xlii.media_client import GeneratedImage, edit_image


@pytest.fixture(autouse=True)
def _no_real_key(monkeypatch):
    monkeypatch.setattr(art, "_api_key_from_config", lambda: "k-test")


def _ref(tmp_path, name="ref.png"):
    p = tmp_path / name
    p.write_bytes(b"\x89PNG-ref")
    return p


def _args(tmp_path, prompt="make it neon", ref=None, yolo=True, model=None):
    return SimpleNamespace(prompt=prompt, ref=ref, path=str(tmp_path),
                           yolo=yolo, model=model)


def _fake_out():
    return [GeneratedImage(data=b"EDITED", mime_type="image/png", model="m")]


# --- the client edit_image ------------------------------------------------------


def test_edit_image_validates_before_the_paid_call(tmp_path, monkeypatch):
    called = []
    # never reach the wire: missing ref must raise first
    monkeypatch.setattr("xlii.media_client.OpenAI",
                        lambda **kw: called.append(1) or SimpleNamespace())
    with pytest.raises(FileNotFoundError):
        edit_image("x", [tmp_path / "nope.png"], api_key="k")
    with pytest.raises(ValueError, match="at least one"):
        edit_image("x", [], api_key="k")
    with pytest.raises(ValueError, match="too many"):
        edit_image("x", [_ref(tmp_path, f"r{i}.png") for i in range(4)], api_key="k")
    assert called == []


def test_edit_image_decodes_response(tmp_path, monkeypatch):
    import base64

    captured = {}

    def fake_post(path, body, *, api_key):
        captured["path"] = path
        captured["body"] = body
        captured["api_key"] = api_key
        return {"data": [{"b64_json": base64.b64encode(b"NEW").decode()}]}

    monkeypatch.setattr("xlii.media_client._post_json", fake_post)
    out = edit_image("neon", [_ref(tmp_path)], api_key="k")
    assert out[0].data == b"NEW"
    assert captured["path"] == "/images/edits"
    body = captured["body"]
    assert body["prompt"] == "neon"
    assert body["image"]["type"] == "image_url"
    assert body["image"]["url"].startswith("data:image/png;base64,")


def test_post_json_raises_api_message(monkeypatch):
    from xlii import media_client as mc

    class _Resp:
        status_code = 400
        text = ""

        def json(self):
            return {"error": {"message": "image too large"}}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(mc.httpx, "Client", lambda **k: _Client())
    with pytest.raises(RuntimeError, match="image too large"):
        mc._post_json("/images/edits", {"prompt": "x"}, api_key="k")


# --- the CLI command ------------------------------------------------------------


def test_edit_writes_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.media_client.edit_image",
                        lambda prompt, refs, **kw: _fake_out())
    ref = _ref(tmp_path)
    rc = art.cmd_artifact_edit(_args(tmp_path, ref=[str(ref)]))
    assert rc == 0
    arts = list((tmp_path / ".xlii" / "artifacts").glob("img-*.png"))
    assert len(arts) == 1 and arts[0].read_bytes() == b"EDITED"


def test_edit_needs_a_reference(tmp_path):
    assert art.cmd_artifact_edit(_args(tmp_path, ref=None)) == 1


def test_edit_rejects_too_many_refs(tmp_path):
    refs = [str(_ref(tmp_path, f"r{i}.png")) for i in range(4)]
    assert art.cmd_artifact_edit(_args(tmp_path, ref=refs)) == 1


def test_edit_missing_ref_file_reported(tmp_path):
    assert art.cmd_artifact_edit(
        _args(tmp_path, ref=[str(tmp_path / "ghost.png")])) == 1


def test_edit_paid_gate_denies_without_yolo(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr("xlii.media_client.edit_image",
                        lambda prompt, refs, **kw: called.append(1) or _fake_out())
    # The kernel spend gate refuses outright on non-tty; fake a console stdin
    # so the deny path itself is exercised.
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(tools_mod, "_confirm", lambda prompt: "n")
    rc = art.cmd_artifact_edit(_args(tmp_path, ref=[str(_ref(tmp_path))], yolo=False))
    assert rc == 0 and called == []            # denied gate never calls the wire


def test_edit_paid_gate_approves_with_y(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.media_client.edit_image",
                        lambda prompt, refs, **kw: _fake_out())
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(tools_mod, "_confirm", lambda prompt: "y")
    rc = art.cmd_artifact_edit(_args(tmp_path, ref=[str(_ref(tmp_path))], yolo=False))
    assert rc == 0


def test_edit_paid_gate_refuses_on_non_tty(tmp_path, monkeypatch):
    """Deliberate safety unification: the shared kernel gate refuses a paid
    edit on non-tty stdin instead of prompting a headless body (the old inline
    gate prompted). The refusal never reaches the wire."""
    from tests.helpers import FakeConsole

    called = []
    fake = FakeConsole()
    monkeypatch.setattr(art, "console", fake)
    monkeypatch.setattr("xlii.media_client.edit_image",
                        lambda prompt, refs, **kw: called.append(1) or _fake_out())
    # Default confirm is input() — no hook. Non-tty must refuse, not prompt.
    rc = art.cmd_artifact_edit(_args(tmp_path, ref=[str(_ref(tmp_path))], yolo=False))
    assert rc == 0 and called == []
    assert "refused" in fake.text


def test_confirm_spend_uses_installed_hook_without_tty(monkeypatch):
    """Face / TUI install a confirm hook; stdin is not a tty. Must ask, not refuse."""
    from xlii.imagine_run import confirm_spend

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(tools_mod, "_confirm", lambda prompt: "y")
    assert confirm_spend(SimpleNamespace(print=lambda *a, **k: None),
                         "~$0.04", assume_yes=False) is True
    monkeypatch.setattr(tools_mod, "_confirm", lambda prompt: "n")
    assert confirm_spend(SimpleNamespace(print=lambda *a, **k: None),
                         "~$0.04", assume_yes=False) is False


def test_cli_parser_accepts_edit_form():
    from xlii.cli import build_parser

    args = build_parser().parse_args(
        ["artifact", "edit", "make it neon", "--ref", "a.png", "--ref", "b.png"])
    assert args.prompt == "make it neon"
    assert args.ref == ["a.png", "b.png"]
