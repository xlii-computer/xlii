"""Vector D — /model + /models collapsed into one /model.

bare /model shows (roles + temps + active slot), <id> sets, --list catalogs the
ids you can switch to; `models` is a hidden alias of /model (the old status view
folds into the bare form). Setting behavior is re-pinned in test_model_command.py.
"""

from __future__ import annotations

from xlii.commands import find_repl_command, get_repl_help
from xlii.config import GlobalConfig
from xlii.repl_cmds import register_all
from xlii.repl_cmds.code import _model_handler
from tests.helpers import FakeConsole, make_agent

register_all()


def _ctx(tmp_path, monkeypatch):
    cfg = GlobalConfig()
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr("xlii.config.GLOBAL_CONFIG_FILE", cfg_file)
    con = FakeConsole()
    agent = make_agent(tmp_path, cfg=cfg, console=con)
    return cfg, cfg_file, con, {"agent": agent, "console": con}


# --------------------------------------------------------------------------- #
#  The collapse contract
# --------------------------------------------------------------------------- #

def test_model_is_the_primary_name():
    for repl in ("code", "chat"):
        cmd = find_repl_command("/model", repl)
        assert cmd is not None and cmd.name == "model"


def test_models_is_a_hidden_alias():
    for repl in ("code", "chat"):
        cmd = find_repl_command("/models", repl)
        assert cmd is not None and cmd.name == "model"   # alias → /model
    assert "/model" in get_repl_help("code")
    assert "/models" not in get_repl_help("code")        # alias never surfaces


# --------------------------------------------------------------------------- #
#  bare shows — roles AND temps (folds in the old /models richness)
# --------------------------------------------------------------------------- #

def test_bare_model_shows_roles_and_temps(tmp_path, monkeypatch):
    cfg, cfg_file, con, ctx = _ctx(tmp_path, monkeypatch)
    assert _model_handler("/model", ctx) is True
    text = con.text
    assert "orchestrator:" in text and "worker:" in text and "chat:" in text
    assert "temp=" in text                       # temperatures, like the old /models
    assert cfg.orchestrator() in text
    assert not cfg_file.exists()                  # bare never persists


def test_models_alias_bare_shows_same_status(tmp_path, monkeypatch):
    cfg, cfg_file, con, ctx = _ctx(tmp_path, monkeypatch)
    # the alias routes the bare line into the same handler/output
    _model_handler("/models", ctx)
    assert "orchestrator:" in con.text and "temp=" in con.text
    assert not cfg_file.exists()


# --------------------------------------------------------------------------- #
#  --list catalogs the switchable ids (distinct from the status view)
# --------------------------------------------------------------------------- #

def test_list_catalogs_known_ids_and_profiles(tmp_path, monkeypatch):
    cfg, cfg_file, con, ctx = _ctx(tmp_path, monkeypatch)
    monkeypatch.setattr("xlii.bootstrap.discover_models", lambda *a, **k: [])
    assert _model_handler("/model --list", ctx) is True
    text = con.text
    assert "known models" in text
    assert cfg.orchestrator() in text             # configured ids are listed
    assert "profiles:" in text
    assert not cfg_file.exists()                  # listing never persists


def test_list_merges_live_catalog(tmp_path, monkeypatch):
    cfg, cfg_file, con, ctx = _ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "xlii.bootstrap.discover_models",
        lambda *a, **k: ["grok-4.20-reasoning", "grok-imagine-image"],
    )
    # Force a discovery attempt (empty keys would skip the HTTP path).
    monkeypatch.setattr(cfg, "key_pairs", lambda: [type("K", (), {"api_key": "x"})()])
    assert _model_handler("/model --list", ctx) is True
    text = con.text
    assert "from the live catalog" in text
    assert "grok-4.20-reasoning" in text
    assert "grok-imagine-image" in text
    assert cfg.orchestrator() in text
    assert not cfg_file.exists()


def test_set_still_works_after_collapse(tmp_path, monkeypatch):
    cfg, cfg_file, con, ctx = _ctx(tmp_path, monkeypatch)
    _model_handler("/model grok-4", ctx)
    assert cfg.orchestrator_model == "grok-4"      # <id> still sets the model


def test_list_does_not_set_a_model_named_list(tmp_path, monkeypatch):
    cfg, cfg_file, con, ctx = _ctx(tmp_path, monkeypatch)
    monkeypatch.setattr("xlii.bootstrap.discover_models", lambda *a, **k: [])
    _model_handler("/model --list", ctx)
    # `--list` is a flag, never mistaken for a model id to switch to
    assert cfg.orchestrator_model is None
