"""Judges bind to gigwork (consult unification): a judge profile's ``gig:
<name>`` resolves endpoint/key/model from gigwork.providers at call time —
/consult and loop judges inherit the binding through the one
query_with_profile seam; /consult --set-to writes it. Offline: urlopen faked."""

from __future__ import annotations

import json

import pytest

import xlii.config as C
from xlii.secondary_ai import query_verdict, query_with_profile
from tests.helpers import make_agent


def _sandbox(monkeypatch, tmp_path, *, providers=None, judges=None, consult=None):
    """Persisted GlobalConfig in a tmp dir — _profile_from_gig re-loads it."""
    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(C, "GLOBAL_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(C, "GLOBAL_CONFIG_FILE", tmp_path / "config.json")
    cfg = C.GlobalConfig()
    cfg.gigwork = {
        "providers": providers if providers is not None else {
            "kimi": {"kind": "openai_compat", "base_url": "https://api.kimi.test/v1",
                     "api_key_env": "KIMI_JUDGE_KEY", "model": "k3"},
        },
        "defaults": {"allow": []},
    }
    if judges is not None:
        cfg.judges = judges
    if consult is not None:
        cfg.consult = consult
    cfg.save()
    return cfg


def _fake_urlopen(monkeypatch, reply="the verdict"):
    seen: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": reply}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            }).encode()

    def fake(req, timeout=0):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["payload"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake)
    return seen


# --- the engine seam -----------------------------------------------------------

def test_gig_bound_profile_routes_to_the_provider(tmp_path, monkeypatch):
    _sandbox(monkeypatch, tmp_path)
    monkeypatch.setenv("KIMI_JUDGE_KEY", "sk-judge")
    seen = _fake_urlopen(monkeypatch)

    resp = query_with_profile(None, "is this safe?",
                              profile={"kind": "cross_vendor", "gig": "kimi"})
    assert seen["url"] == "https://api.kimi.test/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-judge"
    assert seen["payload"]["model"] == "k3"          # the provider's model, by reference
    assert resp.text == "the verdict"
    assert resp.provider == "gigwork[kimi]"          # honest label for records
    assert resp.model == "k3"
    assert (resp.prompt_tokens, resp.completion_tokens) == (7, 3)


def test_profile_model_overrides_the_provider_model(tmp_path, monkeypatch):
    _sandbox(monkeypatch, tmp_path)
    monkeypatch.setenv("KIMI_JUDGE_KEY", "sk-judge")
    seen = _fake_urlopen(monkeypatch)
    query_with_profile(None, "q", profile={"gig": "kimi", "model": "k3-turbo"})
    assert seen["payload"]["model"] == "k3-turbo"


def test_unknown_gig_binding_is_the_fix(tmp_path, monkeypatch):
    _sandbox(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="/gigwork add ghost"):
        query_with_profile(None, "q", profile={"gig": "ghost"})


def test_key_optional_provider_judges_without_env(tmp_path, monkeypatch):
    _sandbox(monkeypatch, tmp_path, providers={
        "ollama": {"kind": "openai_compat", "base_url": "http://127.0.0.1:11434/v1",
                   "api_key_env": "OLLAMA_API_KEY", "model": "llama3.2",
                   "key_optional": True},
    })
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    seen = _fake_urlopen(monkeypatch)
    resp = query_with_profile(None, "q", profile={"gig": "ollama"})
    assert seen["auth"] == "Bearer unused"           # local endpoints ignore it
    assert resp.provider == "gigwork[ollama]"


def test_missing_env_still_refuses_for_keyed_providers(tmp_path, monkeypatch):
    _sandbox(monkeypatch, tmp_path)
    monkeypatch.delenv("KIMI_JUDGE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="KIMI_JUDGE_KEY not set"):
        query_with_profile(None, "q", profile={"gig": "kimi"})


def test_loop_judges_inherit_the_binding(tmp_path, monkeypatch):
    """query_verdict is the loop's door — a gig-bound profile flows through."""
    _sandbox(monkeypatch, tmp_path)
    monkeypatch.setenv("KIMI_JUDGE_KEY", "sk-judge")
    seen = _fake_urlopen(monkeypatch, reply="VERDICT: PASS")
    resp = query_verdict("diff brief", profile={"gig": "kimi", "mode": "verify"})
    assert resp.text == "VERDICT: PASS"
    assert seen["payload"]["messages"][0]["role"] == "system"   # verifier prompt rides
    assert resp.provider == "gigwork[kimi]"


def test_default_judge_binding_resolves_from_config(tmp_path, monkeypatch):
    """No explicit profile: the persisted judges.consult binding is found."""
    _sandbox(monkeypatch, tmp_path,
             judges={"consult": {"kind": "cross_vendor", "gig": "kimi"}},
             consult={"default_judge": "consult"})
    monkeypatch.setenv("KIMI_JUDGE_KEY", "sk-judge")
    seen = _fake_urlopen(monkeypatch)
    resp = query_with_profile(None, "q")
    assert seen["url"].startswith("https://api.kimi.test/v1/")
    assert resp.provider == "gigwork[kimi]"


# --- /consult --set-to -----------------------------------------------------------

class _Console:
    def __init__(self):
        self.lines: list[str] = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    def text(self):
        return "\n".join(self.lines)


def _run_consult(line, agent):
    from xlii.repl_cmds.consult import _consult_handler

    console = _Console()
    handled = _consult_handler(line, {"console": console, "agent": agent,
                                      "state": None, "project": agent.project})
    return handled, console.text()


def test_set_to_writes_the_binding_and_mirrors(tmp_path, monkeypatch):
    _sandbox(monkeypatch, tmp_path)
    monkeypatch.setenv("KIMI_JUDGE_KEY", "sk-judge")
    agent = make_agent(tmp_path)
    agent.cfg = C.GlobalConfig.load()

    handled, out = _run_consult("/consult --set-to kimi", agent)
    assert handled and "consult →" in out and "gigwork[kimi]" in out
    reloaded = C.GlobalConfig.load()
    assert reloaded.judges["consult"] == {"kind": "cross_vendor", "mode": "verify",
                                          "gig": "kimi"}
    assert reloaded.consult["default_judge"] == "consult"
    assert agent.cfg.judges["consult"]["gig"] == "kimi"       # live mirror
    # The binding IS the consult profile now.
    assert reloaded.consult_profile()["gig"] == "kimi"


def test_set_to_refuses_unknown_and_empty(tmp_path, monkeypatch):
    _sandbox(monkeypatch, tmp_path)
    agent = make_agent(tmp_path)
    agent.cfg = C.GlobalConfig.load()

    handled, out = _run_consult("/consult --set-to ghost", agent)
    assert "unknown gig provider 'ghost'" in out and "/gigwork add ghost" in out
    assert "consult" not in (C.GlobalConfig.load().judges or {})

    handled, out = _run_consult("/consult --set-to", agent)
    assert "pass a gigwork provider name" in out


# --- the status line: one definition ---------------------------------------------

def test_status_line_folds_consult_into_the_gig_row(tmp_path, monkeypatch):
    _sandbox(monkeypatch, tmp_path,
             judges={"consult": {"kind": "cross_vendor", "gig": "kimi"}},
             consult={"default_judge": "consult"})
    monkeypatch.setenv("KIMI_JUDGE_KEY", "sk-judge")
    from xlii.repl_cmds.consult import consult_status_line

    line = consult_status_line()
    # No standalone /consult: area — the judge is marked on its own gig row.
    assert "/consult:" not in line
    kimi_row = next(ln for ln in line.split("\n") if "gig/kimi:" in ln)
    assert "k3" in kimi_row and "consult judge" in kimi_row  # model + judge marker, one row
    assert "KIMI_JUDGE_KEY" not in line                      # env name never leaks (key is set)


def test_status_line_flags_a_dangling_binding(tmp_path, monkeypatch):
    _sandbox(monkeypatch, tmp_path, providers={},
             judges={"consult": {"kind": "cross_vendor", "gig": "gone"}},
             consult={"default_judge": "consult"})
    from xlii.repl_cmds.consult import consult_status_line

    line = consult_status_line()
    assert "bound to gigwork[gone]" in line and "/gigwork add gone" in line
