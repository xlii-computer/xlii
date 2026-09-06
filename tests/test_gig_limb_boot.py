"""A gig-only limb can still open Face — Kimi is a larynx, not a missing brain."""

from __future__ import annotations

import json
from types import SimpleNamespace

from xlii.client import MissingCredentials
from xlii.session_boot import build_code_session
from tests.helpers import FakeConsole


def test_build_code_session_uses_gig_when_no_xai(tmp_path, monkeypatch):
    root = tmp_path / "desk"
    xli = root / ".xlii"
    xli.mkdir(parents=True)
    (xli / "project.json").write_text(json.dumps({
        "name": "desk",
        "collection_id": "",
        "created_at": "t",
        "local_only": True,
        "kind": "code",
        "conversation_id": "c",
        "root": str(root.resolve()),
    }))

    monkeypatch.setattr(
        "xlii.session_boot.ClientPool.from_config",
        lambda cfg, **kw: (_ for _ in ()).throw(MissingCredentials("no API keys configured")),
    )
    monkeypatch.setattr("xlii.farm.job_gig", lambda cfg: "kimi")
    backend = SimpleNamespace(
        label="kimi", model="kimi-latest",
        allows_tool=lambda n: True, create=lambda **k: None,
    )
    monkeypatch.setattr("xlii.chat_backend.resolve_gig_backend", lambda cfg, name: backend)
    monkeypatch.setattr("xlii.session_boot.nested_session_guard", lambda *a, **k: True)
    monkeypatch.setattr("xlii.session_boot.mark_session_active", lambda *a, **k: None)
    captured = {}

    class _Agent:
        def __init__(self, **kw):
            captured.update(kw)
            self.rail = None
            self.chat_backend = kw.get("chat_backend")
            self.discovery_mode = False
            self.ops_mode = False

    monkeypatch.setattr("xlii.session_boot.Agent", _Agent)
    monkeypatch.setattr(
        "xlii.session_boot.code_profile",
        lambda project, seed_limit=0: SimpleNamespace(
            memory=SimpleNamespace(seed_into=lambda agent: 0, count=lambda: 0),
            loadout=SimpleNamespace(apply=lambda *a, **k: None),
        ),
    )
    monkeypatch.setattr("xlii.conversation.ensure_conversation", lambda state: None)
    monkeypatch.setattr("xlii.workbench.resolve_active", lambda d: None)
    monkeypatch.setattr("xlii.journal.build_project_journal", lambda s: None)
    monkeypatch.setattr("xlii.journal.dispatch_catchup", lambda s: None)
    monkeypatch.setattr("xlii.session_boot.apply_episode_continuity", lambda *a, **k: None)
    monkeypatch.setattr("xlii.loop.LoopController.load", lambda *a, **k: None)
    monkeypatch.setattr("xlii.session_boot.apply_startup_task", lambda *a, **k: None)
    monkeypatch.setattr("xlii.session_boot._apply_default_role", lambda *a, **k: None)
    def _make_repl_state(**kw):
        return SimpleNamespace(
            **kw, command_scope="", workbench=None, journal=None, loop=None,
            no_sync=False, shell_cwd=root, profile=None,
        )

    monkeypatch.setattr("xlii.session_boot.REPLState", _make_repl_state)
    monkeypatch.setattr("xlii.session_boot.CodeSession", SimpleNamespace)

    cfg = SimpleNamespace(
        jobs={"gig": "kimi"},
        effective_judges=lambda: {},
        management_api_key=None,
    )
    outcome = build_code_session(
        root, cfg=cfg, console=FakeConsole(), launch=True, force=True,
    )
    assert outcome.status == "ok", outcome.reason
    assert captured.get("chat_backend") is backend
