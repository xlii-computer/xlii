"""``/xtool`` command — list + prefill pins."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.repl_cmds.xtool import _handler


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, msg: str, **kwargs) -> None:
        self.lines.append(msg)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "xlii.xtool_catalog.shutil.which",
        lambda b: f"/bin/{b}" if b in ("ruff", "biome") else None,
    )
    state = SimpleNamespace(
        project=SimpleNamespace(project_root=tmp_path),
        pending_input="",
    )
    return {"state": state, "console": _Console()}


def test_xtool_ls_lists_groups(ctx):
    assert _handler("/xtool ls", ctx) is True
    text = "\n".join(ctx["console"].lines)
    assert "[Python]" in text
    assert "ruff-check" in text


def test_xtool_prefills_never_executes(ctx, tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("pass\n")
    assert _handler(f"/xtool ruff-check {f}", ctx) is True
    assert ctx["state"].pending_input == f"!ruff check {f}"


def test_xtool_fix_variant_in_seeded_line(ctx):
    assert _handler("/xtool ruff-check --fix", ctx) is True
    assert ctx["state"].pending_input == "!ruff check --fix <path>"


def test_xtool_refuses_missing_binary(ctx, monkeypatch):
    monkeypatch.setattr("xlii.xtool_catalog.shutil.which", lambda _b: None)
    assert _handler("/xtool ruff-check", ctx) is True
    assert ctx["state"].pending_input == ""
    assert any("missing binary" in ln for ln in ctx["console"].lines)
