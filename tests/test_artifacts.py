"""Tests for xlii/artifacts.py and imagine session."""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from xlii.artifacts import (
    ImagineSession,
    display_store_path,
    imagine_and_store,
    locate_made_file,
    read_session,
    resolve_artifact_path,
    save_artifact_to,
    write_artifact,
    write_session,
)
from xlii.media_client import generate_image_from_response_json


_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_write_artifact_returns_relative_path(tmp_path):
    rel = write_artifact(tmp_path, _TINY_PNG, ext="png")
    assert rel.startswith(".xlii/artifacts/img-")
    assert (tmp_path / rel).is_file()
    assert (tmp_path / rel).read_bytes() == _TINY_PNG


def test_session_round_trip(tmp_path):
    sess = ImagineSession(prompt="a cat", paths=[".xlii/artifacts/img-test.png"])
    write_session(tmp_path, sess)
    loaded = read_session(tmp_path)
    assert loaded is not None
    assert loaded.prompt == "a cat"
    assert loaded.paths == [".xlii/artifacts/img-test.png"]


def test_imagine_and_store_with_fake_api(tmp_path, monkeypatch):
    payload = {
        "data": [
            {
                "b64_json": base64.b64encode(_TINY_PNG).decode(),
                "mime_type": "image/png",
            }
        ]
    }

    def fake_generate(*_a, **_k):
        return generate_image_from_response_json(payload, model="grok-imagine-image")

    monkeypatch.setattr("xlii.artifacts.generate_image", fake_generate)

    metas, cost, session = imagine_and_store(
        tmp_path, "login mockup", api_key="fake-key"
    )
    assert len(metas) == 1
    assert metas[0].rel_path.startswith(".xlii/artifacts/")
    assert "(approx)" in cost
    assert session.prompt == "login mockup"
    assert read_session(tmp_path).paths == [metas[0].rel_path]


def test_imagine_and_store_batch_prune_protects_all_new_files(tmp_path, monkeypatch):
    payload = {
        "data": [
            {"b64_json": base64.b64encode(_TINY_PNG).decode(), "mime_type": "image/png"},
            {"b64_json": base64.b64encode(_TINY_PNG).decode(), "mime_type": "image/png"},
        ]
    }

    def fake_generate(*_a, **_k):
        return generate_image_from_response_json(payload, model="grok-imagine-image")

    monkeypatch.setattr("xlii.artifacts._KEEP", 1)
    monkeypatch.setattr("xlii.artifacts.generate_image", fake_generate)

    metas, _cost, session = imagine_and_store(tmp_path, "batch", api_key="fake-key", n=2)
    assert len(metas) == 2
    assert all((tmp_path / p).is_file() for p in session.paths)


def test_locate_made_file_prefers_artifacts_store(tmp_path):
    rel = write_artifact(tmp_path, _TINY_PNG, ext="png")
    name = Path(rel).name
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    copy = outbox / name
    copy.write_bytes(_TINY_PNG)
    path, addr = locate_made_file(copy, tmp_path)
    assert path == (tmp_path / rel).resolve()
    assert addr == f"artifacts://{name}"
    assert display_store_path(path, tmp_path) == rel


def test_save_artifact_to_project_tree(tmp_path):
    rel = write_artifact(tmp_path, _TINY_PNG, ext="png")
    dest = save_artifact_to(tmp_path, rel, "assets/out.png")
    assert dest == (tmp_path / "assets" / "out.png").resolve()
    assert dest.read_bytes() == _TINY_PNG


def test_save_artifact_rejects_source_outside_artifact_store(tmp_path):
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(_TINY_PNG)

    with pytest.raises(ValueError, match="escapes store"):
        save_artifact_to(tmp_path, "../outside.png", "assets/out.png")


def test_resolve_artifact_rejects_traversal(tmp_path):
    secret = tmp_path.parent / "secret.png"
    secret.write_bytes(_TINY_PNG)
    bad_rel = Path(os.path.relpath(secret, tmp_path)).as_posix()

    with pytest.raises(ValueError, match="escapes store"):
        resolve_artifact_path(tmp_path, bad_rel)


def test_save_artifact_rejects_destination_outside_project(tmp_path):
    rel = write_artifact(tmp_path, _TINY_PNG, ext="png")

    with pytest.raises(ValueError, match="escapes project"):
        save_artifact_to(tmp_path, rel, "../out.png")
