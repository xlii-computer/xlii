"""generate_image agent tool + the paid-action gate (media-artifacts M2)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import xlii.tools as tools_mod
from xlii.tool_context import ToolContext
from xlii.tool_handlers import confirm_paid_action, t_generate_image


def _ctx(tmp_path, *, yolo=False, console="c"):
    return ToolContext(
        project=SimpleNamespace(project_root=Path(tmp_path)),
        clients=SimpleNamespace(chat=SimpleNamespace(api_key="k-test")),
        cfg=SimpleNamespace(),
        pool=None,
        yolo=yolo,
        console=console,
    )


def _fake_image():
    from xlii.media_client import GeneratedImage
    return GeneratedImage(data=b"\x89PNG-fake", mime_type="image/png", model="img-m")


# --- schema flags: paid + side-effecting stays out of every fan-out surface ---


def test_flags_no_parallel_no_plan_no_worker():
    from xlii.tool_schemas import (
        PARALLEL_SAFE,
        PLAN_MODE_TOOLS,
        WORKER_REGISTRY,
        tool_schemas,
    )

    names = {s["function"]["name"] for s in tool_schemas()}
    assert "generate_image" in names
    assert "generate_image" not in PARALLEL_SAFE
    assert "generate_image" not in PLAN_MODE_TOOLS
    assert "generate_image" not in WORKER_REGISTRY


# --- the paid-action gate ------------------------------------------------------


def test_gate_skipped_under_yolo(tmp_path):
    assert confirm_paid_action(_ctx(tmp_path, yolo=True), "x", "$0.10") is None


def test_gate_refuses_headless(tmp_path):
    res = confirm_paid_action(_ctx(tmp_path, console=None), "make art", "$0.10")
    assert res is not None and res.is_error
    assert "headless" in res.content


def test_gate_prompts_and_denies_on_n(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_mod, "_confirm", lambda prompt: "n")
    res = confirm_paid_action(_ctx(tmp_path), "make art", "$0.10")
    assert res is not None and "denied by user" in res.content


def test_gate_prompts_and_approves_on_y(tmp_path, monkeypatch):
    seen = {}

    def _yes(prompt):
        seen["prompt"] = prompt
        return "y"

    monkeypatch.setattr(tools_mod, "_confirm", _yes)
    assert confirm_paid_action(_ctx(tmp_path), "make art", "$0.10") is None
    assert "make art" in seen["prompt"]          # the ask travels WITH the prompt
    assert "$0.10" in seen["prompt"]


# --- the tool --------------------------------------------------------------------


def test_generates_saves_artifact_returns_path_not_blob(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.media_client.generate_image",
                        lambda prompt, **kw: [_fake_image()])
    res = t_generate_image(_ctx(tmp_path, yolo=True), {"prompt": "a fox"})
    assert not res.is_error
    assert ".xlii/artifacts/" in res.content
    assert "PNG-fake" not in res.content          # metadata, never the payload
    rel = next(line.strip() for line in res.content.splitlines()
               if ".xlii/artifacts/" in line)
    assert (tmp_path / rel).read_bytes() == b"\x89PNG-fake"


def test_denied_gate_never_calls_the_api(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr("xlii.media_client.generate_image",
                        lambda prompt, **kw: called.append(prompt) or [_fake_image()])
    monkeypatch.setattr(tools_mod, "_confirm", lambda prompt: "n")
    res = t_generate_image(_ctx(tmp_path), {"prompt": "a fox"})
    assert res.is_error and called == []          # money gate before the wire


def test_empty_prompt_refused(tmp_path):
    res = t_generate_image(_ctx(tmp_path, yolo=True), {"prompt": "  "})
    assert res.is_error


def test_api_failure_is_a_clean_tool_error(tmp_path, monkeypatch):
    def _boom(prompt, **kw):
        raise RuntimeError("api down")

    monkeypatch.setattr("xlii.media_client.generate_image", _boom)
    res = t_generate_image(_ctx(tmp_path, yolo=True), {"prompt": "a fox"})
    assert res.is_error and "api down" in res.content
