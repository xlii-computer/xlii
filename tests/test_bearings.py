"""Mojo-keeper Phase C — bearings block: body · surface · desk · reach · delta."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from xlii.bearings import (
    LAST_TURN_FILENAME,
    bearings_block,
    compute,
    read_last_turn_sidecar,
    render,
    stamp_last_turn,
    write_last_turn_sidecar,
)
from xlii.fabric import last_sync_stamp, stamp_pull


def _cfg(*, node="", throne=False, **extra):
    return SimpleNamespace(
        jobs={"node": node} if node else {},
        node_name=node,
        management_api_key="k" if throne else "",
        **extra,
    )


def _state(*, project=None, hire=None, scratch=False, no_sync=False, cfg=None):
    return SimpleNamespace(
        project=project,
        hire=hire,
        scratch=scratch,
        no_sync=no_sync,
        journal_mute=False,
        cfg=cfg or _cfg(),
        agent=None,
        journal=None,
    )


def _folder_desk(name="iXaac-lab", root="/home/b/iXaac-lab"):
    return SimpleNamespace(name=name, project_root=root, xli_dir=None)


def _home_desk():
    return SimpleNamespace(name="scratch/home", project_root=None, xli_dir=None)


def _persona(tmp_path: Path):
    turns = tmp_path / "turns"
    turns.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(turns_dir=turns, project_root=tmp_path, name="mojo")


def test_same_body_surface_desk_twice_has_no_changed(tmp_path):
    persona = _persona(tmp_path)
    cfg = _cfg(node="throne", throne=True)
    desk = _folder_desk()
    state = _state(project=desk, hire="write", cfg=cfg)
    write_last_turn_sidecar(
        persona.turns_dir, body="throne", surface="desk face", desk="iXaac-lab",
    )
    text = render(compute(
        state, surface="desk face", cfg=cfg, persona_project=persona,
    ))
    assert "[bearings]" in text and "[/bearings]" in text
    assert "CHANGED" not in text
    assert "body: throne (this is the throne)" in text
    assert "surface: desk face" in text
    assert "desk: iXaac-lab" in text


def test_sidecar_delta_emits_changed_body_surface_desk(tmp_path):
    persona = _persona(tmp_path)
    write_last_turn_sidecar(
        persona.turns_dir, body="throne", surface="desk face", desk="iXaac-lab",
    )
    cfg = _cfg(node="vm-1", throne=False)
    state = _state(project=_home_desk(), hire="read", cfg=cfg)
    text = render(compute(
        state, surface="phone glass", cfg=cfg, persona_project=persona,
    ))
    assert "CHANGED since last turn: body, surface, desk" in text
    assert "body: vm-1 (limb; throne = throne)" in text
    assert "surface: phone glass" in text
    assert "desk: none (Home / scratch)" in text
    assert "last turn:" in text
    assert "body throne" in text
    assert "surface desk face" in text
    assert "desk iXaac-lab" in text
    assert "confirm: desk modal" in text


def test_no_sidecar_last_turn_none_recorded_no_changed(tmp_path):
    persona = _persona(tmp_path)
    cfg = _cfg(node="throne", throne=True)
    state = _state(project=_folder_desk(), hire="write", cfg=cfg)
    text = render(compute(
        state, surface="repl", cfg=cfg, persona_project=persona,
    ))
    assert "last turn: none recorded" in text
    assert "CHANGED" not in text


def test_fabric_never_synced_says_last_pull_never(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.fabric.last_sync_stamp", lambda path=None: None)
    persona = _persona(tmp_path)
    cfg = _cfg(node="vm-1", throne=False)
    state = _state(project=_home_desk(), hire="read", cfg=cfg)
    text = render(compute(
        state, surface="repl", cfg=cfg, persona_project=persona,
    ))
    assert "last pull never" in text
    assert "reach:" in text


def test_home_desk_hire_read_only_folder_desk_write(tmp_path):
    persona = _persona(tmp_path)
    cfg = _cfg(throne=True)
    home = render(compute(
        _state(project=_home_desk(), hire="read", cfg=cfg),
        surface="repl", cfg=cfg, persona_project=persona,
    ))
    assert "hire: read only (no desk)" in home
    folder = render(compute(
        _state(project=_folder_desk(), hire="write", cfg=cfg),
        surface="repl", cfg=cfg, persona_project=persona,
    ))
    assert "hire: read+write on this desk" in folder


def test_source_raise_that_line_reads_unknown(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr("xlii.fabric.last_sync_stamp", boom)
    persona = _persona(tmp_path)
    cfg = _cfg(node="vm-1", throne=False)
    text = render(compute(
        _state(project=_folder_desk(), hire="write", cfg=cfg),
        surface="repl", cfg=cfg, persona_project=persona,
    ))
    assert "reach: unknown" in text
    assert "[bearings]" in text and "[/bearings]" in text
    assert "desk: iXaac-lab" in text  # other lines still render


def test_stamp_last_turn_writes_sidecar_fields(tmp_path):
    persona = _persona(tmp_path)
    cfg = _cfg(node="throne", throne=True)
    stamp_last_turn(
        persona.turns_dir, cfg=cfg, surface="desk face", desk=_folder_desk(),
    )
    data = read_last_turn_sidecar(persona.turns_dir)
    assert data is not None
    assert data["body"] == "throne"
    assert data["surface"] == "desk face"
    assert data["desk"] == "iXaac-lab"
    assert "at" in data
    assert (Path(persona.turns_dir) / LAST_TURN_FILENAME).is_file()


def test_last_sync_stamp_none_then_epoch(tmp_path):
    stamp = tmp_path / "fabric-last-pull.json"
    assert last_sync_stamp(stamp) is None
    stamp_pull(now=1000.0, path=stamp)
    assert last_sync_stamp(stamp) == 1000.0


def test_bearings_block_never_raises_on_empty_state():
    text = bearings_block(SimpleNamespace(), surface="repl", cfg=None, persona_project=None)
    assert text.startswith("[bearings]")
    assert "[/bearings]" in text


def test_build_mojo_ambient_prepends_bearings_before_desk_banner():
    from xlii.repl_cmds.mojo import build_mojo_ambient

    state = SimpleNamespace(
        project=_folder_desk(), journal=None, workbench=None,
        cfg=_cfg(throne=True), agent=None, scratch=False, no_sync=False,
        hire="write",
    )
    out = build_mojo_ambient(state, "hello", surface="desk face")
    assert "[bearings]" in out
    assert "current desk: iXaac-lab" in out
    assert out.find("[bearings]") < out.find("current desk")
