import json
import os
import stat

import pytest

from xlii.config import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_HELP_MODEL,
    DEFAULT_MODEL,
    GLOBAL_CONFIG_FILE,
    GlobalConfig,
    ProjectConfig,
)


def test_chat_model_defaults_to_general_chat_model():
    cfg = GlobalConfig()
    # persona chat defaults to a general chat-class model, not the code build-model
    assert cfg.chat() == DEFAULT_CHAT_MODEL == "grok-4.3"
    assert cfg.orchestrator() == DEFAULT_MODEL  # code surface unchanged
    assert cfg.chat() != cfg.orchestrator()


def test_help_model_defaults_to_cheap_build_model():
    cfg = GlobalConfig()
    assert cfg.help() == DEFAULT_HELP_MODEL == "grok-build-0.1"
    assert cfg.get_model_for_role("help") == "grok-build-0.1"
    # help stays cheap even when chat is a flagship-class default
    assert cfg.help() == cfg.orchestrator()
    assert cfg.help() != cfg.chat()


def test_help_model_role_override():
    cfg = GlobalConfig()
    cfg.help_model = "grok-3"
    assert cfg.help() == "grok-3"
    assert cfg.get_model_for_role("help") == "grok-3"


def test_chat_model_role_dispatch_and_override():
    cfg = GlobalConfig()
    assert cfg.get_model_for_role("chat") == "grok-4.3"
    assert cfg.get_model_for_role("orchestrator") == "grok-build-0.1"
    # an explicit config value wins over the default fallback
    cfg.chat_model = "grok-3"
    assert cfg.chat() == "grok-3"
    assert cfg.get_model_for_role("chat") == "grok-3"


def test_chat_temperature_default():
    cfg = GlobalConfig()
    assert cfg.chat_temp() == 0.7


def test_worker_models_map_overrides_default():
    cfg = GlobalConfig()
    cfg.worker_models = {"explore": "grok-4", "verify": "grok-4.20-reasoning"}
    assert cfg.get_model_for_worker_role("explore") == "grok-4"
    assert cfg.get_model_for_worker_role("verify") == "grok-4.20-reasoning"
    assert cfg.get_model_for_worker_role("general") == cfg.worker()


def test_panel_width_pct_defaults_and_round_trips():
    cfg = GlobalConfig()
    assert cfg.panel_width_pct == 0
    cfg.panel_width_pct = 50
    cfg.tui_panel_side = "left"
    cfg.save()
    loaded = GlobalConfig.load()
    assert loaded.panel_width_pct == 50
    assert loaded.tui_panel_side == "left"


def test_hub_open_defaults_this_and_round_trips():
    cfg = GlobalConfig()
    assert cfg.hub_open == "this"
    cfg.hub_open = "other"
    cfg.save()
    loaded = GlobalConfig.load()
    assert loaded.hub_open == "other"


def test_xai_docs_defaults_on_and_round_trips_off():
    cfg = GlobalConfig()
    assert cfg.xai_docs is True
    cfg.xai_docs = False
    cfg.save()
    loaded = GlobalConfig.load()
    assert loaded.xai_docs is False


def test_jobs_block_defaults_empty_and_round_trips():
    cfg = GlobalConfig()
    assert cfg.jobs == {}
    cfg.jobs = {
        "offers": ["explore"],
        "gig": "kimi",
        "node": "kimi-laptop",
        "projects": {"iXaac-lab": "/tmp/lab"},
    }
    cfg.save()
    loaded = GlobalConfig.load()
    assert loaded.jobs["offers"] == ["explore"]
    assert loaded.jobs["gig"] == "kimi"
    assert loaded.jobs["node"] == "kimi-laptop"
    assert loaded.jobs["projects"]["iXaac-lab"] == "/tmp/lab"


def test_desk_prefs_and_fallback_persona_round_trip():
    cfg = GlobalConfig()
    assert cfg.editor == ""
    assert cfg.image_editor == ""
    assert cfg.browser == ""
    assert cfg.tui_terminal == ""
    assert cfg.tui_terminal_cwd == "project"
    assert cfg.tui_terminal_cwd_path == ""
    assert cfg.fallback_persona == ""
    assert cfg.face_skin == ""
    assert cfg.face_fkeys is True
    assert cfg.face_bold is False
    assert cfg.chat_tier == "auto"
    cfg.editor = "nano"
    cfg.image_editor = "gimp"
    cfg.browser = "firefox"
    cfg.tui_terminal = "kitty"
    cfg.tui_terminal_cwd = "custom"
    cfg.tui_terminal_cwd_path = "/tmp"
    cfg.fallback_persona = "primebot"
    cfg.face_skin = "mojo"
    cfg.face_fkeys = False
    cfg.face_bold = True
    cfg.chat_tier = "expert"
    cfg.max_tool_iterations = 40
    cfg.save()
    loaded = GlobalConfig.load()
    assert loaded.editor == "nano"
    assert loaded.image_editor == "gimp"
    assert loaded.browser == "firefox"
    assert loaded.tui_terminal == "kitty"
    assert loaded.tui_terminal_cwd == "custom"
    assert loaded.tui_terminal_cwd_path == "/tmp"
    assert loaded.fallback_persona == "primebot"
    assert loaded.face_skin == "mojo"
    assert loaded.face_fkeys is False
    assert loaded.face_bold is True
    assert loaded.chat_tier == "expert"
    assert loaded.max_tool_iterations == 40


def test_fabric_pull_interval_defaults_and_round_trips():
    cfg = GlobalConfig()
    assert cfg.fabric_pull_interval_s == 900
    cfg.fabric_pull_interval_s = 0
    cfg.save()
    data = json.loads(GLOBAL_CONFIG_FILE.read_text())
    assert data["fabric_pull_interval_s"] == 0
    loaded = GlobalConfig.load()
    assert loaded.fabric_pull_interval_s == 0


def test_chat_temperature_round_trips_through_save():
    cfg = GlobalConfig()
    cfg.chat_temperature = 0.9
    cfg.save()
    data = json.loads(GLOBAL_CONFIG_FILE.read_text())
    assert data["chat_temperature"] == 0.9


def test_worker_models_round_trips_through_save():
    cfg = GlobalConfig()
    cfg.worker_models = {"explore": "grok-4"}
    cfg.save()
    data = json.loads(GLOBAL_CONFIG_FILE.read_text())
    assert data["worker_models"] == {"explore": "grok-4"}


def test_chat_model_round_trips_through_save():
    cfg = GlobalConfig()
    cfg.chat_model = "grok-3"
    cfg.save()
    data = json.loads(GLOBAL_CONFIG_FILE.read_text())
    assert data["chat_model"] == "grok-3"


def test_management_key_never_persisted():
    cfg = GlobalConfig()
    cfg.management_api_key = "SUPER-SECRET"
    cfg.save()
    data = json.loads(GLOBAL_CONFIG_FILE.read_text())
    assert "management_api_key" not in data


def test_config_file_perms_0600():
    GlobalConfig().save()
    mode = stat.S_IMODE(os.stat(GLOBAL_CONFIG_FILE).st_mode)
    assert mode == 0o600


def _write_project(project_dir, **overrides):
    d = project_dir / ".xlii"
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "name": "t",
        "collection_id": "c1",
        "created_at": "2026-01-01",
        "conversation_id": "abc",
        "local_only": True,
        **overrides,
    }
    (d / "project.json").write_text(json.dumps(data))


def test_bound_persona_round_trips(tmp_path):
    _write_project(tmp_path, root=str(tmp_path.resolve()), bound_persona="bob")
    cfg = ProjectConfig.load(tmp_path)
    assert cfg.bound_persona == "bob"
    cfg.save()
    data = json.loads((tmp_path / ".xlii" / "project.json").read_text())
    assert data["bound_persona"] == "bob"


def test_project_kind_defaults_to_code(tmp_path):
    from xlii.config import PROJECT_KIND_CODE, project_kind

    _write_project(tmp_path, root=str(tmp_path.resolve()))
    cfg = ProjectConfig.load(tmp_path)
    assert project_kind(cfg) == PROJECT_KIND_CODE


def test_project_kind_collection_round_trips(tmp_path):
    from xlii.config import PROJECT_KIND_COLLECTION, project_kind

    _write_project(tmp_path, root=str(tmp_path.resolve()), kind="collection")
    cfg = ProjectConfig.load(tmp_path)
    assert project_kind(cfg) == PROJECT_KIND_COLLECTION
    cfg.save()
    data = json.loads((tmp_path / ".xlii" / "project.json").read_text())
    assert data["kind"] == "collection"


def test_named_scratch_unstamped_is_collection():
    from xlii.config import PROJECT_KIND_COLLECTION, project_kind
    from types import SimpleNamespace

    p = SimpleNamespace(kind=None, name="scratch/italy")
    assert project_kind(p) == PROJECT_KIND_COLLECTION


def test_scratch_home_is_not_a_collection():
    from xlii.config import PROJECT_KIND_CODE, project_kind
    from types import SimpleNamespace

    p = SimpleNamespace(kind=None, name="scratch/home")
    assert project_kind(p) == PROJECT_KIND_CODE


def test_unbound_persona_defaults_to_none(tmp_path):
    _write_project(tmp_path, root=str(tmp_path.resolve()))   # no bound_persona key
    assert ProjectConfig.load(tmp_path).bound_persona is None


def test_default_role_round_trips(tmp_path):
    _write_project(tmp_path, root=str(tmp_path.resolve()), default_role="app-operator")
    cfg = ProjectConfig.load(tmp_path)
    assert cfg.default_role == "app-operator"
    cfg.save()
    data = json.loads((tmp_path / ".xlii" / "project.json").read_text())
    assert data["default_role"] == "app-operator"


def test_default_role_defaults_to_none(tmp_path):
    _write_project(tmp_path, root=str(tmp_path.resolve()))   # no default_role key
    assert ProjectConfig.load(tmp_path).default_role is None


def test_files_root_and_repo_round_trip(tmp_path):
    _write_project(
        tmp_path,
        root=str(tmp_path.resolve()),
        files_root="sftp://appbox/srv/apps/foo",
        repo="git@host:lab/foo.git",
    )
    cfg = ProjectConfig.load(tmp_path)
    assert cfg.files_root == "sftp://appbox/srv/apps/foo"
    assert cfg.repo == "git@host:lab/foo.git"
    cfg.save()
    data = json.loads((tmp_path / ".xlii" / "project.json").read_text())
    assert data["files_root"] == "sftp://appbox/srv/apps/foo"
    assert data["repo"] == "git@host:lab/foo.git"


def test_files_root_absent_is_none(tmp_path):
    _write_project(tmp_path, root=str(tmp_path.resolve()))
    cfg = ProjectConfig.load(tmp_path)
    assert cfg.files_root is None
    assert cfg.repo is None


def test_project_root_guard_refuses_moved_checkout(tmp_path, capsys):
    _write_project(tmp_path, root="/somewhere/else/entirely")
    assert ProjectConfig.load(tmp_path) is None
    assert "Refusing" in capsys.readouterr().err


def test_project_root_guard_backfills_missing_root(tmp_path):
    _write_project(tmp_path)  # legacy: no root recorded
    cfg = ProjectConfig.load(tmp_path)
    assert cfg is not None
    data = json.loads((tmp_path / ".xlii" / "project.json").read_text())
    assert data["root"] == str(tmp_path.resolve())
    # And a second load with the matching root succeeds
    assert ProjectConfig.load(tmp_path) is not None


def _write_legacy_project(project_dir, **overrides):
    d = project_dir / ".xli"
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "name": "legacy",
        "collection_id": "c-legacy",
        "created_at": "2026-01-01",
        "conversation_id": "legacy-id",
        "root": str(project_dir.resolve()),
        **overrides,
    }
    (d / "project.json").write_text(json.dumps(data))


def test_load_returns_none_when_incomplete_xlii_blocks_migration(tmp_path):
    """Migration is skipped when ``.xlii/`` already exists; no legacy fallback."""
    _write_legacy_project(tmp_path)
    (tmp_path / ".xlii").mkdir()

    assert ProjectConfig.load(tmp_path) is None


def test_load_returns_none_when_xlii_project_json_corrupt(tmp_path):
    _write_legacy_project(tmp_path)
    (tmp_path / ".xlii").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".xlii" / "project.json").write_text("not json")

    assert ProjectConfig.load(tmp_path) is None


def test_auto_migrate_moves_legacy_only_xli(tmp_path):
    _write_legacy_project(tmp_path)
    assert not (tmp_path / ".xlii").exists()

    cfg = ProjectConfig.load(tmp_path)
    assert cfg is not None
    assert cfg.xli_dir == tmp_path / ".xlii"
    assert not (tmp_path / ".xli").exists()
    assert (tmp_path / ".xlii" / "project.json").exists()


# --- xAI region (regional API edges) ---------------------------------------- #

def test_xai_api_host_shapes():
    from xlii.config import xai_api_host

    assert xai_api_host(None) == "api.x.ai"
    assert xai_api_host("") == "api.x.ai"
    assert xai_api_host("  ") == "api.x.ai"
    assert xai_api_host("us-west-2") == "us-west-2.api.x.ai"
    # tolerate a stray trailing dot / padding — DNS judges real validity
    assert xai_api_host(" eu-west-1. ") == "eu-west-1.api.x.ai"


def test_invalid_region_values_fall_back_to_global_edge(monkeypatch):
    from xlii.client import Clients

    cfg = GlobalConfig(keys=["xai-test"], region="²²")
    monkeypatch.delenv("XAI_REGION", raising=False)

    with pytest.warns(RuntimeWarning, match="ignoring invalid config region"):
        assert cfg.api_region() is None
        assert cfg.api_host() == "api.x.ai"
        clients = Clients.from_config(cfg, require_management=False)
    assert str(clients.chat.base_url).startswith("https://api.x.ai/v1")

    monkeypatch.setenv("XAI_REGION", "bad.region")
    with pytest.warns(RuntimeWarning, match="ignoring invalid XAI_REGION"):
        assert cfg.api_region() is None
        assert cfg.api_base_url() == "https://api.x.ai/v1"


def test_api_region_env_overrides_config_field(monkeypatch):
    cfg = GlobalConfig()
    monkeypatch.delenv("XAI_REGION", raising=False)
    assert cfg.api_region() is None
    assert cfg.api_host() == "api.x.ai"
    assert cfg.api_base_url() == "https://api.x.ai/v1"

    cfg.region = "us-east-1"
    assert cfg.api_region() == "us-east-1"
    assert cfg.api_base_url() == "https://us-east-1.api.x.ai/v1"

    # env wins per-shell, without touching the persisted field
    monkeypatch.setenv("XAI_REGION", "us-west-2")
    assert cfg.api_region() == "us-west-2"
    assert cfg.api_host() == "us-west-2.api.x.ai"
    assert cfg.region == "us-east-1"


def test_clients_ride_the_regional_edge(monkeypatch):
    from xlii.client import Clients
    from xlii.config import KeyPair

    monkeypatch.delenv("XAI_REGION", raising=False)
    kp = KeyPair(api_key="xai-test", management_api_key="mgmt-test", label="t")

    # no region → the SDK defaults (byte-identical to before the feature)
    c = Clients.from_keypair(kp)
    assert str(c.chat.base_url).startswith("https://api.x.ai/v1")

    c = Clients.from_keypair(kp, region="us-west-2")
    assert str(c.chat.base_url).startswith("https://us-west-2.api.x.ai/v1")
