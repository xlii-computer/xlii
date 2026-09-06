"""search_project isolation — the §5 structural guards (menu-families, d96c9d35).

Personas are sealed islands: search_project reaches the project's OWN Collection
only. These pin the ban at every layer — the ToolContext field is GONE (any
future re-thread is a TypeError fleet-wide), the search handler builds its
collection set from the project alone, and legacy persisted collection refs are
purged at the session-restore boundary.
"""

from __future__ import annotations

import dataclasses
import json

from xlii.tool_context import ToolContext


def test_toolcontext_has_no_extra_collection_ids_field():
    # THE fleet-wide guard: every dispatch path (agent, workers, swarm, gaggle,
    # gigwork, judge, MCP bridge) constructs ToolContext — with the field gone,
    # re-adding the leak anywhere is a loud TypeError, not a silent rearm.
    assert "extra_collection_ids" not in {f.name for f in dataclasses.fields(ToolContext)}


def test_search_project_reads_the_project_collection_only(tmp_path, monkeypatch):
    """Even with contaminated attached_refs upstream, the backend sees exactly
    [project.collection_id] — there is no path from refs into the search set."""
    from types import SimpleNamespace

    import xlii.tool_handlers as th

    captured = {}

    class _FakeBackend:
        degraded_from = None

        def __init__(self, clients, collection_ids):
            captured["cids"] = list(collection_ids)

        def search(self, query, limit, retrieval_mode="hybrid"):
            return []

    monkeypatch.setattr("xlii.storage_backend.CollectionsBackend", _FakeBackend)
    monkeypatch.setattr("xlii.storage_backend.compose_with_local_floor",
                        lambda backend, project: backend)
    ctx = SimpleNamespace(
        project=SimpleNamespace(collection_id="col-own", xli_dir=tmp_path,
                                local_only=False, project_root=tmp_path),
        clients=SimpleNamespace(),
        cfg=SimpleNamespace(retrieval_mode="hybrid"),
        spill_seq=0,
        # contamination upstream — must be unreachable by construction
        session=SimpleNamespace(attached_refs=[("alice", "col-evil")]),
    )
    th.t_search_project(ctx, {"query": "anything"})
    assert captured["cids"] == ["col-own"]
    assert "col-evil" not in captured["cids"]


def test_session_restore_purges_legacy_collection_refs(tmp_path):
    """A session.json persisted BEFORE the ban (carrying a persona-Collection
    ref) loads clean: the collection entry is dropped, bookmarks survive."""
    from tests.helpers import FakeConsole, make_agent
    from xlii.repl import REPLState

    root = tmp_path / "repo"
    xli = root / ".xlii"
    xli.mkdir(parents=True)
    (xli / "session.json").write_text(json.dumps({
        "attached_refs": [["alice", "col-legacy"], ["spark", ""]],
        "attached_docs": [],
    }))
    agent = make_agent(root)
    state = REPLState(console=FakeConsole(), agent=agent, project=agent.project,
                      cfg=agent.cfg, pool=agent.pool)
    state.load()
    assert state.attached_refs == [("spark", "")]
    blob = repr(state.attached_refs)
    assert "col-legacy" not in blob
