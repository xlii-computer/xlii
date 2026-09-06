import json

from xlii.config import GlobalConfig, ProjectConfig
from xlii.storage import LocalIndex, local_search_text


def _project(tmp_path, **over):
    d = tmp_path / ".xlii"
    d.mkdir(exist_ok=True)
    (d / "project.json").write_text(json.dumps({
        "name": "t", "root": str(tmp_path.resolve()), "collection_id": "",
        "created_at": "2026-01-01", "conversation_id": "x", "local_only": True, **over,
    }))
    return ProjectConfig.load(tmp_path)


def test_local_only_persona_turns_searchable_after_index_rebuild(tmp_path):
    """A local-only persona keeps its turns as files under the project root, so a
    sync (index rebuild) makes older turns searchable offline via the local floor.
    This is the guarantee behind chat startup rebuilding the index for local
    personas — long-term memory works with no Collection."""
    from xlii.sync import sync_project

    turns = tmp_path / "turns"
    turns.mkdir()
    (turns / "turn-1.md").write_text(
        "user: how do we mint keys?\nassistant: through the UNIQUEKEYMINT flow\n"
    )
    p = _project(tmp_path)                     # local_only project
    sync_project(None, p, GlobalConfig())      # local_only → rebuild index, no network
    text = local_search_text(p, "UNIQUEKEYMINT", limit=5)
    assert text and "turn-1.md" in text


def test_rebuild_and_search(tmp_path):
    (tmp_path / "auth.py").write_text("def verify_password(hash):\n    return bcrypt.checkpw(hash)\n")
    (tmp_path / "readme.md").write_text("This project parses invoices.\n")
    p = _project(tmp_path)
    n = LocalIndex(p).rebuild(GlobalConfig())
    assert n == 2

    hits = LocalIndex(p).search("password bcrypt", limit=5)
    assert hits and hits[0][0] == "auth.py"

    text = local_search_text(p, "invoices")
    assert "readme.md" in text and "LOCAL index" in text


def test_search_sanitizes_fts_operators(tmp_path):
    (tmp_path / "a.py").write_text("near the start\n")
    p = _project(tmp_path)
    LocalIndex(p).rebuild(GlobalConfig())
    # NEAR/quotes/* are FTS5 syntax — must not raise
    assert LocalIndex(p).search('NEAR("start") OR a*') != []


def test_no_index_returns_none(tmp_path):
    p = _project(tmp_path)
    assert local_search_text(p, "anything") is None


def test_rebuild_respects_ignores(tmp_path):
    (tmp_path / ".env").write_text("SECRET=hunter2")
    (tmp_path / "ok.py").write_text("fine")
    p = _project(tmp_path)
    LocalIndex(p).rebuild(GlobalConfig())
    assert LocalIndex(p).search("hunter2") == []
    assert LocalIndex(p).search("fine") != []
