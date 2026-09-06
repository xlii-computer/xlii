"""Tests for /imagine and /image slash commands."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace


import xlii.media_client as mc

from xlii.imagine_run import (
    ImagineRequest,
    _preview_enabled,
    parse_imagine_tokens,
)
from xlii.repl_cmds.image import _image_handler
from xlii.repl_cmds.imagine import _imagine_handler
from tests.helpers import FakeConsole, make_agent

_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _ctx(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr("xlii.config.GLOBAL_CONFIG_FILE", cfg_file)
    con = FakeConsole()
    xli = tmp_path / ".xlii"
    xli.mkdir()
    proj = SimpleNamespace(project_root=tmp_path, xli_dir=xli, name="proj")
    agent = make_agent(tmp_path, console=con)
    agent.image_preview_backend = "auto"
    agent.session.yolo = True
    pool = SimpleNamespace(primary=lambda: SimpleNamespace(chat=SimpleNamespace(api_key="test-key")))
    ctx = {
        "console": con,
        "agent": agent,
        "state": agent,
        "project": proj,
        "cfg": agent.cfg,
        "pool": pool,
    }
    return ctx, agent


def _repl_ctx(tmp_path, monkeypatch):
    """Like `_ctx`, but `state` is a real REPLState (has the locker API)."""
    ctx, agent = _ctx(tmp_path, monkeypatch)
    from xlii.repl import REPLState

    state = REPLState(
        console=ctx["console"],
        agent=agent,
        project=ctx["project"],
        cfg=agent.cfg,
        pool=ctx["pool"],
    )
    ctx["state"] = state
    return ctx, agent, state


def _make_fake_store(record):
    """A fake `imagine_and_store` that writes a real artifact + session and
    records the prompt it was generated from."""
    from xlii.artifacts import ArtifactMeta, ImagineSession, write_artifact, write_session

    def fake_store(root, prompt, **kw):
        record.append(prompt)
        rel = write_artifact(root, _TINY_PNG, ext="png")
        meta = ArtifactMeta(
            rel_path=rel,
            abs_path=root / rel,
            mime_type="image/png",
            bytes=len(_TINY_PNG),
            model="grok-imagine-image",
            prompt=prompt,
        )
        sess = ImagineSession(prompt=prompt, paths=[rel])
        write_session(root, sess)
        return [meta], mc.estimate_image_cost("grok-imagine-image", 1), sess

    return fake_store


def _no_real_preview(monkeypatch):
    monkeypatch.setattr(
        "xlii.terminal_image.display_image",
        lambda *a, **k: SimpleNamespace(tier="path", message=""),
    )


def test_parse_imagine_tokens():
    req = parse_imagine_tokens(["--redo", "--inline"])
    assert req.redo is True
    assert req.force_preview is True
    req2 = parse_imagine_tokens(["--editprompt", "bigger button", "--save", "assets/x.png"])
    assert req2.edit_prompt == "bigger button"
    assert req2.save_path == "assets/x.png"
    req3 = parse_imagine_tokens(["--save", "assets/hero.png", "--from", "img-abc.png"])
    assert req3.save_path == "assets/hero.png"
    assert req3.from_name == "img-abc.png"


def test_imagine_save_from_named_artifact(tmp_path):
    from xlii.artifacts import write_artifact
    from xlii.imagine_run import run_imagine

    rel = write_artifact(tmp_path, _TINY_PNG, ext="png")
    name = Path(rel).name
    req = parse_imagine_tokens(["--save", "assets/keep.png", "--from", name])
    console = FakeConsole()
    assert run_imagine(tmp_path, req, api_key="x", console=console) is True
    dest = tmp_path / "assets" / "keep.png"
    assert dest.is_file()
    assert dest.read_bytes() == _TINY_PNG


def test_global_preview_env_enables_imagine_preview(monkeypatch):
    monkeypatch.setenv("XLII_IMAGE_PREVIEW", "auto")
    assert _preview_enabled(ImagineRequest()) is True


def test_imagine_generates_with_mock(tmp_path, monkeypatch):
    ctx, agent = _ctx(tmp_path, monkeypatch)

    def fake_store(root, prompt, **kw):
        from xlii.artifacts import write_artifact, ImagineSession, write_session

        rel = write_artifact(root, _TINY_PNG, ext="png")
        from xlii.artifacts import ArtifactMeta

        meta = ArtifactMeta(
            rel_path=rel,
            abs_path=root / rel,
            mime_type="image/png",
            bytes=len(_TINY_PNG),
            model="grok-imagine-image",
            prompt=prompt,
        )
        sess = ImagineSession(prompt=prompt, paths=[rel])
        write_session(root, sess)
        return [meta], mc.estimate_image_cost("grok-imagine-image", 1), sess

    monkeypatch.setattr("xlii.imagine_run.imagine_and_store", fake_store)
    monkeypatch.setattr("xlii.terminal_image.display_image", lambda *a, **k: SimpleNamespace(tier="path", message=""))

    assert _imagine_handler('/imagine "dark login"', ctx) is True
    assert "saved" in ctx["console"].text


def test_preview_enabled_ambient_default(monkeypatch):
    # tui-media-delivery P1: ambient preview — the UNSET default is on (auto);
    # a made image is never invisible. Off-like env still opts out.
    monkeypatch.delenv("XLII_IMAGE_PREVIEW", raising=False)
    req = ImagineRequest()
    assert _preview_enabled(req) is True

    monkeypatch.setenv("XLII_IMAGE_PREVIEW", "auto")
    assert _preview_enabled(req) is True

    monkeypatch.setenv("XLII_IMAGE_PREVIEW", "off")
    assert _preview_enabled(req) is False


def test_imagine_redo_uses_session(tmp_path, monkeypatch):
    ctx, _agent = _ctx(tmp_path, monkeypatch)
    from xlii.artifacts import ImagineSession, write_session, write_artifact

    rel = write_artifact(tmp_path, _TINY_PNG, ext="png")
    write_session(tmp_path, ImagineSession(prompt="first prompt", paths=[rel]))

    calls = []

    def fake_store(root, prompt, **kw):
        calls.append(prompt)
        from xlii.artifacts import ArtifactMeta

        rel2 = write_artifact(root, _TINY_PNG, ext="png")
        meta = ArtifactMeta(
            rel_path=rel2,
            abs_path=root / rel2,
            mime_type="image/png",
            bytes=1,
            model="grok-imagine-image",
            prompt=prompt,
        )
        return [meta], mc.estimate_image_cost("grok-imagine-image", 1), ImagineSession(prompt=prompt, paths=[rel2])

    monkeypatch.setattr("xlii.imagine_run.imagine_and_store", fake_store)
    monkeypatch.setattr("xlii.terminal_image.display_image", lambda *a, **k: SimpleNamespace(tier="path", message=""))

    _imagine_handler("/imagine --redo", ctx)
    assert calls == ["first prompt"]


def test_image_latest_rejects_traversal_path(tmp_path, monkeypatch):
    import os

    ctx, _agent = _ctx(tmp_path, monkeypatch)
    secret = tmp_path.parent / "secret.png"
    secret.write_bytes(_TINY_PNG)
    bad_rel = os.path.relpath(secret, tmp_path)

    from xlii.artifacts import ImagineSession, write_session

    write_session(tmp_path, ImagineSession(prompt="tampered", paths=[bad_rel]))

    preview_calls = []
    monkeypatch.setattr(
        "xlii.repl_cmds.image.maybe_preview",
        lambda path, **kw: preview_calls.append(path),
    )

    assert _image_handler("/image latest", ctx) is True
    assert "invalid artifact" in ctx["console"].text
    assert preview_calls == []


def test_generate_image_bounds_network_timeout(monkeypatch):
    # The OpenAI SDK default (600s + 2 retries) turns a stalled endpoint into a
    # ~30min silent hang; the client must be built with a bounded timeout/retries.
    captured = {}

    class _FakeImages:
        def generate(self, **kw):
            b64 = base64.b64encode(_TINY_PNG).decode()
            return SimpleNamespace(
                data=[SimpleNamespace(b64_json=b64, mime_type="image/png", revised_prompt="")]
            )

    class _FakeClient:
        def __init__(self, **kw):
            captured.update(kw)
            self.images = _FakeImages()

    monkeypatch.setattr(mc, "OpenAI", _FakeClient)
    monkeypatch.delenv("XLII_IMAGE_TIMEOUT", raising=False)

    out = mc.generate_image("a cat", api_key="k")
    assert out and out[0].mime_type == "image/png"
    assert captured["timeout"] == mc._DEFAULT_IMAGE_TIMEOUT_S
    assert captured["timeout"] < 600
    assert captured["max_retries"] == mc._DEFAULT_IMAGE_RETRIES


def test_generate_image_routes_xai_params_via_extra_body(monkeypatch):
    # aspect_ratio / resolution are xAI extensions, not OpenAI SDK kwargs — the
    # real images.generate() raises "unexpected keyword argument" if they're
    # passed directly, so they must ride in extra_body. This fake's signature
    # mirrors the SDK (explicit params + extra_body) to catch a regression.
    captured = {}

    class _FakeImages:
        def generate(self, *, model, prompt, n, response_format, extra_body=None, **kw):
            captured["extra_body"] = extra_body
            captured["stray"] = kw
            b64 = base64.b64encode(_TINY_PNG).decode()
            return SimpleNamespace(
                data=[SimpleNamespace(b64_json=b64, mime_type="image/png", revised_prompt="")]
            )

    class _FakeClient:
        def __init__(self, **kw):
            self.images = _FakeImages()

    monkeypatch.setattr(mc, "OpenAI", _FakeClient)
    out = mc.generate_image("a big brown dog", api_key="k", aspect_ratio="16:9", resolution="1k")

    assert out and out[0].mime_type == "image/png"
    assert captured["extra_body"] == {"aspect_ratio": "16:9", "resolution": "1k"}
    assert captured["stray"] == {}  # nothing leaked as an unexpected top-level kwarg


def test_image_timeout_env_override(monkeypatch):
    monkeypatch.setenv("XLII_IMAGE_TIMEOUT", "45")
    assert mc._image_timeout() == 45.0
    monkeypatch.setenv("XLII_IMAGE_TIMEOUT", "bogus")
    assert mc._image_timeout() == mc._DEFAULT_IMAGE_TIMEOUT_S
    monkeypatch.setenv("XLII_IMAGE_TIMEOUT", "-5")
    assert mc._image_timeout() == mc._DEFAULT_IMAGE_TIMEOUT_S


def test_parse_imagine_ref_and_from_locker():
    req = parse_imagine_tokens([
        "--editprompt", "make it blue",
        "--ref", "shot.png",
        "--ref", "locker:banner",
        "--from-locker",
        "--yolo",
    ])
    assert req.edit_prompt == "make it blue"
    assert req.refs == ["shot.png", "locker:banner"]
    assert req.from_locker is True
    assert req.assume_yes is True


def test_resolve_editprompt_uses_last_artifact(tmp_path):
    from xlii.artifacts import ImagineSession, write_artifact, write_session
    from xlii.imagine_run import ImagineRequest, resolve_imagine_refs

    rel = write_artifact(tmp_path, _TINY_PNG, ext="png")
    write_session(tmp_path, ImagineSession(prompt="a cat", paths=[rel]))
    refs = resolve_imagine_refs(
        tmp_path,
        ImagineRequest(edit_prompt="make it blue"),
        prior_session=ImagineSession(prompt="a cat", paths=[rel]),
    )
    assert len(refs) == 1
    assert refs[0].exists() and refs[0].read_bytes() == _TINY_PNG


def test_resolve_ref_bare_artifact_name(tmp_path):
    from xlii.artifacts import write_artifact
    from xlii.imagine_run import resolve_one_ref

    rel = write_artifact(tmp_path, _TINY_PNG, ext="png")
    name = rel.rsplit("/", 1)[-1]
    hit = resolve_one_ref(tmp_path, name)
    assert hit.exists() and hit.name == name


def test_resolve_editprompt_uses_canvas_work_before_last_artifact(tmp_path):
    from xlii.artifacts import ImagineSession, write_artifact, write_session
    from xlii.imagine_run import ImagineRequest, resolve_imagine_refs

    older = write_artifact(tmp_path, _TINY_PNG, ext="png")
    write_session(tmp_path, ImagineSession(prompt="a cat", paths=[older]))
    pin = tmp_path / "on-canvas.png"
    pin.write_bytes(_TINY_PNG + b"CANVAS")
    state = SimpleNamespace(attached_files=[{
        "name": "on-canvas.png", "path": str(pin), "kind": "image",
        "enabled": True, "once": False, "role": "canvas",
    }])
    refs = resolve_imagine_refs(
        tmp_path,
        ImagineRequest(edit_prompt="add a hat"),
        state=state,
        prior_session=ImagineSession(prompt="a cat", paths=[older]),
    )
    assert refs == [pin]


def test_resolve_editprompt_uses_focus_pin_before_last_artifact(tmp_path):
    from xlii.artifacts import ImagineSession, write_artifact, write_session
    from xlii.imagine_run import ImagineRequest, resolve_imagine_refs

    older = write_artifact(tmp_path, _TINY_PNG, ext="png")
    write_session(tmp_path, ImagineSession(prompt="a cat", paths=[older]))
    pin = tmp_path / "focused.jpg"
    pin.write_bytes(_TINY_PNG + b"PIN")
    state = SimpleNamespace(attached_files=[{
        "name": "focused.jpg", "path": str(pin), "kind": "image",
        "enabled": True, "once": True,
    }])
    refs = resolve_imagine_refs(
        tmp_path,
        ImagineRequest(edit_prompt="add a lion"),
        state=state,
        prior_session=ImagineSession(prompt="a cat", paths=[older]),
    )
    assert refs == [pin]


def test_resolve_ref_from_enabled_locker(tmp_path):
    from xlii.imagine_run import ImagineRequest, resolve_imagine_refs

    img = tmp_path / "staged.png"
    img.write_bytes(_TINY_PNG)
    state = SimpleNamespace(attached_files=[
        {"name": "staged.png", "path": str(img), "kind": "image", "enabled": True},
        {"name": "off.png", "path": str(tmp_path / "off.png"), "kind": "image", "enabled": False},
    ])
    (tmp_path / "off.png").write_bytes(_TINY_PNG)
    refs = resolve_imagine_refs(
        tmp_path,
        ImagineRequest(prompt="warmer", refs=["staged.png"]),
        state=state,
    )
    assert refs == [img]
    refs2 = resolve_imagine_refs(
        tmp_path,
        ImagineRequest(prompt="warmer", from_locker=True),
        state=state,
    )
    assert refs2 == [img]  # disabled entry skipped


def test_imagine_editprompt_routes_through_edit_and_store(tmp_path, monkeypatch):
    """Exit gate: --editprompt auto-refs the last artifact and edits (faked wire)."""
    from xlii.artifacts import ArtifactMeta, ImagineSession, write_artifact, write_session

    seen: dict = {}

    def fake_edit(root, prompt, reference_paths, **kw):
        seen["prompt"] = prompt
        seen["refs"] = [str(p) for p in reference_paths]
        rel = write_artifact(root, _TINY_PNG, ext="png")
        meta = ArtifactMeta(
            rel_path=rel, abs_path=root / rel, mime_type="image/png",
            bytes=len(_TINY_PNG), model="m", prompt=prompt,
        )
        sess = ImagineSession(prompt=prompt, paths=[rel])
        write_session(root, sess)
        return [meta], "~$0.05 (approx)", sess

    monkeypatch.setattr("xlii.imagine_run.edit_and_store", fake_edit)
    monkeypatch.setattr(
        "xlii.imagine_run.imagine_and_store",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("generate must not run")),
    )
    _no_real_preview(monkeypatch)

    ctx, _agent, state = _repl_ctx(tmp_path, monkeypatch)
    prior = write_artifact(tmp_path, _TINY_PNG, ext="png")
    write_session(tmp_path, ImagineSession(prompt="a cat", paths=[prior]))
    state.attach_file(str(tmp_path / prior))
    assert _imagine_handler('/imagine --editprompt "make it blue" --yolo', ctx) is True
    assert seen["prompt"] == "make it blue"
    assert any(prior in r or r.endswith(Path(prior).name) for r in seen["refs"])
    assert "(approx)" in ctx["console"].text


def test_imagine_from_locker_edit(tmp_path, monkeypatch):
    """Exit gate: an edit using a staged locker image produces a new artifact."""
    from xlii.artifacts import ArtifactMeta, ImagineSession, write_artifact, write_session

    img = tmp_path / "locker-shot.png"
    img.write_bytes(_TINY_PNG)
    seen: dict = {}

    def fake_edit(root, prompt, reference_paths, **kw):
        seen["refs"] = list(reference_paths)
        rel = write_artifact(root, _TINY_PNG, ext="png")
        meta = ArtifactMeta(
            rel_path=rel, abs_path=root / rel, mime_type="image/png",
            bytes=len(_TINY_PNG), model="m", prompt=prompt,
        )
        sess = ImagineSession(prompt=prompt, paths=[rel])
        write_session(root, sess)
        return [meta], "~$0.05 (approx)", sess

    monkeypatch.setattr("xlii.imagine_run.edit_and_store", fake_edit)
    _no_real_preview(monkeypatch)
    ctx, _agent, state = _repl_ctx(tmp_path, monkeypatch)
    state.attach_file(str(img))
    assert _imagine_handler('/imagine "warmer tones" --from-locker --yolo', ctx) is True
    assert seen["refs"] and seen["refs"][0] == img
    # New artifact session written.
    from xlii.artifacts import read_session
    sess = read_session(tmp_path)
    assert sess and sess.prompt == "warmer tones" and sess.paths


# --------------------------------------------------------------------------- #
#  /image — verbs on an existing image (the P1 collapse: no mode, no shumup)
# --------------------------------------------------------------------------- #

def test_image_bare_prints_usage_not_a_mode(tmp_path, monkeypatch):
    ctx, agent = _ctx(tmp_path, monkeypatch)
    assert _image_handler("/image", ctx) is True
    text = ctx["console"].text
    assert "usage" in text and "edit" in text
    # The mode is dead: nothing set image_mode anywhere.
    assert not hasattr(agent.session, "image_mode") or not agent.session.image_mode


def test_image_menu_usage_hides_latest():
    from xlii.commands import find_repl_command

    cmd = find_repl_command("/image", "code")
    assert cmd is not None
    usage = (cmd.usage or "").strip()
    desc = (cmd.description or "").lower()
    assert not usage.startswith("/image latest")
    assert "canvas" in desc
    assert "latest" not in usage


def test_image_edit_requires_a_prompt(tmp_path, monkeypatch):
    ctx, _agent = _ctx(tmp_path, monkeypatch)
    assert _image_handler("/image edit", ctx) is True
    assert "usage" in ctx["console"].text


def test_image_edit_fronts_the_editprompt_machinery(tmp_path, monkeypatch):
    ctx, _agent = _ctx(tmp_path, monkeypatch)
    seen = {}

    def fake_run(root, req, **kw):
        seen["req"] = req
        seen["kw"] = kw
        return True

    monkeypatch.setattr("xlii.imagine_run.run_imagine", fake_run)
    assert _image_handler('/image edit "make the sky stormy" --ref pic.png', ctx) is True
    req = seen["req"]
    assert req.edit_prompt == "make the sky stormy"
    assert req.refs == ["pic.png"] and req.from_locker is False
    # Honest help: a guided re-render, not a pixel-precise edit.
    assert "re-render" in ctx["console"].text


def test_image_edit_from_locker(tmp_path, monkeypatch):
    ctx, _agent = _ctx(tmp_path, monkeypatch)
    seen = {}
    monkeypatch.setattr("xlii.imagine_run.run_imagine",
                        lambda root, req, **kw: seen.update(req=req) or True)
    assert _image_handler('/image edit "warmer" --from locker', ctx) is True
    assert seen["req"].from_locker is True


def test_image_edit_accepts_from_locker_flag(tmp_path, monkeypatch):
    ctx, _agent = _ctx(tmp_path, monkeypatch)
    seen = {}
    monkeypatch.setattr("xlii.imagine_run.run_imagine",
                        lambda root, req, **kw: seen.update(req=req) or True)
    assert _image_handler('/image edit "add a lion" --from-locker', ctx) is True
    assert seen["req"].from_locker is True


def test_image_off_is_no_longer_a_verb(tmp_path, monkeypatch):
    # `/image off` was the mode exit; the mode is gone — it now just prints usage.
    ctx, _agent = _ctx(tmp_path, monkeypatch)
    assert _image_handler("/image off", ctx) is True
    assert "usage" in ctx["console"].text


def test_preview_mode_defaults_to_auto(monkeypatch):
    monkeypatch.delenv("XLII_IMAGE_PREVIEW", raising=False)
    from xlii.terminal_image import preview_mode
    assert preview_mode() == "auto"
