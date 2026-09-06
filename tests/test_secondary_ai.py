"""secondary_ai.query_with_profile — the /consult · /sh · loop-judge backend.

Regression guard for the GlobalConfig shadowing bug: a local
``from xlii.config import GlobalConfig`` inside the ``xai`` branch made the name
FUNCTION-LOCAL for the whole body, so the anthropic/openai branches (which never
run that import) hit ``UnboundLocalError`` at the pricing line. Both provider
paths must resolve ``GlobalConfig`` to the module global. No network: urlopen is
stubbed.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import xlii.secondary_ai as SA


class _FakeResp:
    def __init__(self, body: str):
        self._b = body.encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_global_config(**extra):
    """A stand-in GlobalConfig whose .load() returns a config-ish namespace.

    Patching the *module attribute* is itself the assertion: if the function
    ever re-shadows GlobalConfig with a local import, this patched global is the
    one it must (and, when fixed, does) read on the pricing path."""
    return SimpleNamespace(load=lambda: SimpleNamespace(pricing={}, **extra))


def test_anthropic_path_does_not_unboundlocal_globalconfig(monkeypatch):
    monkeypatch.setattr(SA, "GlobalConfig", _fake_global_config())
    monkeypatch.setenv("ANTHROPIC_TEST_KEY", "sk-test")
    body = json.dumps({
        "content": [{"type": "text", "text": "ls -la"}],
        "usage": {"input_tokens": 3, "output_tokens": 5},
    })
    monkeypatch.setattr(SA.urllib.request, "urlopen",
                        lambda req, timeout=0: _FakeResp(body))

    resp = SA.query_with_profile(
        None, "list files",
        profile={"provider": "anthropic", "model": "claude-x",
                 "api_key_env": "ANTHROPIC_TEST_KEY"},
        system="be terse",
    )
    assert resp.text == "ls -la"
    assert resp.provider == "anthropic"


def test_xai_path_pins_region_base_url_via_module_global(monkeypatch):
    """The region pin (cfg.api_base_url) still resolves through the module global
    — no local re-import — and the consult call hits that same ingress."""
    monkeypatch.setattr(
        SA, "GlobalConfig",
        _fake_global_config(api_base_url=lambda: "https://us-east.api.x.ai/v1"),
    )
    monkeypatch.setenv("XAI_TEST_KEY", "xai-test")
    captured: dict = {}

    def _fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        return _FakeResp(json.dumps(
            {"choices": [{"message": {"content": "hi"}}], "usage": {}}))

    monkeypatch.setattr(SA.urllib.request, "urlopen", _fake_urlopen)

    resp = SA.query_with_profile(
        None, "hi",
        profile={"provider": "xai", "model": "grok-x",
                 "api_key_env": "XAI_TEST_KEY"},
    )
    assert resp.provider == "xai" and resp.text == "hi"
    assert captured["url"] == "https://us-east.api.x.ai/v1/chat/completions"
