"""G3 gaggle `second-opinion` (proposals/gigwork.md Phase B).

Surfaces: /gaggle, /gigwork gaggle, dispatch_subagent(gaggle=), investigate
gaggle=. Brains are faked — no live paid keys.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import xlii.agent as A
from xlii.chat_backend import GigError
from xlii.config import GlobalConfig
from xlii.jam import (
    jam_specs,
    orchestrator_may_run_gaggle,
    resolve_gaggle,
    run_gaggle,
)
from tests.helpers import make_agent, make_msg


def _resp(msg):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0),
    )


def _scripted_clients(*texts):
    replies = [_resp(make_msg(t, None)) for t in texts]
    it = iter(replies)
    completions = SimpleNamespace(create=lambda **kw: next(it))
    return SimpleNamespace(
        chat=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        label="primary",
    )


class _FakeGigBackend:
    def __init__(self, text, *, label="kimi"):
        self._text = text
        self.label = label
        self.model = "fake-gig-model"
        self.capabilities = frozenset({"chat", "tools"})

    def allows_tool(self, name):
        return name not in {"search_project", "web_search", "x_search"}

    def create(self, **kwargs):
        return _resp(make_msg(self._text, None))


def _cfg(providers=("kimi",), allow=None, jams=None):
    cfg = GlobalConfig()
    cfg.gigwork = {
        "providers": {
            p: {"kind": "openai_compat", "base_url": "https://x.test/v1",
                "api_key_env": f"{p.upper()}_TEST_KEY", "model": f"{p}-model"}
            for p in providers
        },
        "defaults": {"allow": list(allow or [])},
    }
    if jams is not None:
        cfg.gigwork["jams"] = jams
    cfg.max_worker_iterations = 2
    return cfg


def _patch_gig(monkeypatch, text="gig answer"):
    monkeypatch.setattr(
        "xlii.jam.resolve_gig_backend",
        lambda cfg, name: _FakeGigBackend(text, label=name),
    )


class _Console:
    def __init__(self):
        self.lines: list[str] = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    def text(self):
        return "\n".join(self.lines)


def _run_cmd(line, agent):
    from xlii.repl_cmds.jam import _jam_handler
    from xlii.repl_cmds.gigwork import _gigwork_handler

    console = _Console()
    ctx = {"console": console, "agent": agent, "state": None}
    if line.startswith("/gigwork"):
        handled = _gigwork_handler(line, ctx)
    else:
        handled = _jam_handler(line, ctx)
    return handled, console.text()


# --------------------------------------------------------------------------- #
#  Spec: who + how many + merge + budget cap; write: false
# --------------------------------------------------------------------------- #


def test_second_opinion_is_home_explore_plus_gig_synth():
    spec = jam_specs(_cfg())["second-opinion"]
    assert [m.backend for m in spec.members] == ["xai", "gig"]
    assert [m.kit for m in spec.members] == ["explore", "explore"]
    assert spec.merge == "synth_conflicts"
    assert spec.max_parallel == 2
    assert spec.write is False


def test_write_true_is_refused():
    cfg = _cfg(jams={"m": {"members": [{"backend": "kimi"}], "write": True}})
    with pytest.raises(GigError, match="write: true is refused"):
        jam_specs(cfg)


def test_gaggle_aliases_match_jam():
    cfg = _cfg()
    assert resolve_gaggle(cfg, "second-opinion").members[1].backend == "kimi"
    bound = resolve_gaggle(_cfg(providers=("kimi",), allow=["kimi"]), "second-opinion")
    assert orchestrator_may_run_gaggle(
        _cfg(providers=("kimi",), allow=["kimi"]), "second-opinion"
    ).members == bound.members


def test_orchestrator_gaggle_requires_allowlist():
    with pytest.raises(GigError, match="not in gigwork.defaults.allow"):
        orchestrator_may_run_gaggle(_cfg(allow=[]), "second-opinion")


# --------------------------------------------------------------------------- #
#  /gaggle + /gigwork gaggle
# --------------------------------------------------------------------------- #


def test_gaggle_command_is_jam_alias():
    from xlii.commands import iter_repl_commands
    from xlii.repl_cmds import register_all

    register_all()
    jam = next(c for c in iter_repl_commands() if c.name == "jam")
    assert "gaggle" in jam.aliases


def test_gaggle_ls_header_and_stock(tmp_path):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg()
    handled, out = _run_cmd("/gaggle ls", agent)
    assert handled
    assert "gaggles" in out
    assert "second-opinion" in out
    assert "synth_conflicts" in out


def test_gaggle_second_opinion_prints_agreements_and_conflicts(
    tmp_path, monkeypatch,
):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg()
    _patch_gig(monkeypatch, "gig view")
    agent.pool = SimpleNamespace(
        primary=lambda: None,
        acquire=lambda: _scripted_clients("home view"),
        report_success=lambda c: None,
    )
    synth_clients = _scripted_clients(
        "## Agreements\nboth see a race\n## Conflicts\nlocation\n## Verdict\ncheck"
    )
    monkeypatch.setattr(type(agent), "clients", property(lambda self: synth_clients))
    handled, out = _run_cmd(
        "/gaggle second-opinion is this racy?", agent,
    )
    assert handled
    assert "gaggle[second-opinion]" in out
    assert "jam[" not in out
    assert "## Agreements" in out and "## Conflicts" in out


def test_gigwork_gaggle_nests_to_gaggle(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg()
    _patch_gig(monkeypatch, "gig view")
    agent.pool = SimpleNamespace(
        primary=lambda: None,
        acquire=lambda: _scripted_clients("home view"),
        report_success=lambda c: None,
    )
    synth_clients = _scripted_clients(
        "## Agreements\na\n## Conflicts\nb\n## Verdict\nc"
    )
    monkeypatch.setattr(type(agent), "clients", property(lambda self: synth_clients))
    handled, out = _run_cmd(
        "/gigwork gaggle second-opinion which view is right?", agent,
    )
    assert handled
    assert "gaggle[second-opinion]" in out
    assert "## Agreements" in out


def test_gigwork_gaggle_ls(tmp_path):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg()
    handled, out = _run_cmd("/gigwork gaggle ls", agent)
    assert handled
    assert "second-opinion" in out
    assert "gaggles" in out


# --------------------------------------------------------------------------- #
#  dispatch_subagent(gaggle=)
# --------------------------------------------------------------------------- #


def _dispatch(agent, args):
    return A.Agent._run_worker(agent, args)


def test_dispatch_schema_documents_gaggle_not_jam():
    from xlii.tools import dispatch_subagent_schema

    props = dispatch_subagent_schema()["function"]["parameters"]["properties"]
    assert "gaggle" in props
    assert "jam" not in props
    assert "gig" in props
    assert "second-opinion" in props["gaggle"]["description"]


def test_dispatch_gaggle_and_gig_together_refused(tmp_path):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg(allow=["kimi"])
    text, call = _dispatch(
        agent,
        {"task": "t", "gig": "kimi", "gaggle": "second-opinion"},
    )
    assert "gig= or gaggle=" in text
    assert call.iterations == 0


def test_dispatch_gaggle_not_allowlisted_is_refused(tmp_path):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg(allow=[])
    text, call = _dispatch(
        agent, {"task": "t", "gaggle": "second-opinion"},
    )
    assert "not in gigwork.defaults.allow" in text
    assert call.iterations == 0


def test_dispatch_gaggle_second_opinion_badges_and_synths(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg(allow=["kimi"])
    _patch_gig(monkeypatch, "gig view")
    agent.pool = SimpleNamespace(
        primary=lambda: None,
        acquire=lambda: _scripted_clients("home view"),
        report_success=lambda c: None,
    )
    synth_clients = _scripted_clients(
        "## Agreements\nboth\n## Conflicts\nwhere\n## Verdict\nread both"
    )
    monkeypatch.setattr(type(agent), "clients", property(lambda self: synth_clients))
    text, call = _dispatch(
        agent, {"task": "is this racy?", "gaggle": "second-opinion"},
    )
    assert text.startswith("--- gaggle[second-opinion]")
    assert "## Agreements" in text and "## Conflicts" in text
    assert call.iterations >= 1


def test_dispatch_sneaked_jam_key_is_ignored(tmp_path):
    """Schema advertises gaggle=, not jam=. A sneaked jam= must not fan out."""
    agent = make_agent(tmp_path)
    agent.cfg.max_worker_iterations = 1
    agent.pool = SimpleNamespace(
        primary=lambda: _scripted_clients("plain worker reply"),
        acquire=lambda: _scripted_clients("plain worker reply"),
        report_success=lambda c: None,
    )
    text, call = _dispatch(agent, {"task": "hello", "jam": "second-opinion"})
    assert "gaggle[" not in text
    assert "jam[" not in text
    assert "plain worker reply" in text
    assert call.iterations >= 1


# --------------------------------------------------------------------------- #
#  investigate gaggle= (tiny G3 hook on the heavy path)
# --------------------------------------------------------------------------- #


def test_investigate_gaggle_runs_named_preset(tmp_path, monkeypatch):
    from xlii.deep_search import KIND_INVESTIGATE, SubQuery, run_deep_search

    agent = make_agent(tmp_path)
    cfg = _cfg(allow=["kimi"])
    fake = SimpleNamespace(
        spec=SimpleNamespace(name="second-opinion"),
        merged="## Agreements\nx\n## Conflicts\ny\n## Verdict\nz",
        results=[],
        synth_model="synth-model",
        ok_count=2,
    )
    seen = {}

    def _fake_run(name, question, **kw):
        seen["name"] = name
        seen["question"] = question
        return fake

    monkeypatch.setattr("xlii.jam.run_jam", _fake_run)
    res = run_deep_search(
        "q",
        clients=_scripted_clients(),
        cfg=cfg,
        project=agent.project,
        gaggle="second-opinion",
        plan_fn=lambda q, p: [SubQuery("dig", KIND_INVESTIGATE)],
        max_rounds=1,
        synthesize=False,
    )
    assert seen["name"] == "second-opinion"
    assert seen["question"] == "dig"
    assert res.findings[0].ok
    assert "## Agreements" in res.findings[0].text


def test_investigate_gaggle_refused_without_allow(tmp_path):
    from xlii.deep_search import KIND_INVESTIGATE, SubQuery, run_deep_search

    agent = make_agent(tmp_path)
    res = run_deep_search(
        "q",
        clients=_scripted_clients(),
        cfg=_cfg(allow=[]),
        project=agent.project,
        gaggle="second-opinion",
        plan_fn=lambda q, p: [SubQuery("dig", KIND_INVESTIGATE)],
        max_rounds=1,
        synthesize=False,
    )
    assert not res.findings[0].ok
    assert "not in gigwork.defaults.allow" in (res.findings[0].error or "")


def test_run_gaggle_members_never_write(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    cfg = _cfg()
    _patch_gig(monkeypatch, "gig")
    seen = {}

    real_worker = __import__("xlii.worker_agent", fromlist=["WorkerAgent"]).WorkerAgent
    orig_init = real_worker.__init__

    def _wrap(self, *a, **kw):
        seen.setdefault("writes", []).append(kw.get("worker_writes", "MISSING"))
        return orig_init(self, *a, **kw)

    monkeypatch.setattr("xlii.worker_agent.WorkerAgent.__init__", _wrap)
    clients = _scripted_clients(
        "home",
        "## Agreements\na\n## Conflicts\nb\n## Verdict\nc",
    )
    run_gaggle(
        "second-opinion", "q?",
        cfg=cfg, project=agent.project, clients=clients,
    )
    assert seen["writes"]
    assert all(w is False for w in seen["writes"])


# --------------------------------------------------------------------------- #
#  /howto gigwork gaggle
# --------------------------------------------------------------------------- #


def test_howto_gigwork_gaggle_resolves():
    from xlii.help_corpus import load_manifest

    m = load_manifest(bundled=False)
    assert m.resolve("gigwork") == "gigwork"
    assert m.resolve("gaggle") == "gigwork"
    assert m.resolve("gigwork gaggle") == "gigwork"


def test_howto_gigwork_gaggle_attaches(tmp_path, monkeypatch):
    import io

    from rich.console import Console

    from xlii.repl_cmds.howto import _howto_handler

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    class _Owner:
        def __init__(self):
            self.attached_docs: list[tuple[str, str]] = []
            self.howto_mode = False

        def attach_doc(self, name, content):
            self.attached_docs.append((name, content))

        def detach_doc(self, name):
            before = len(self.attached_docs)
            self.attached_docs = [(n, c) for n, c in self.attached_docs if n != name]
            return len(self.attached_docs) < before

    owner = _Owner()
    ctx = {
        "console": Console(file=io.StringIO(), force_terminal=False),
        "state": owner,
        "project": None,
        "command_scope": "code",
    }
    assert _howto_handler("/howto gigwork gaggle", ctx) is True
    body = dict(owner.attached_docs).get("howto", "")
    assert "/gaggle second-opinion" in body
    assert "write: false" in body.lower() or "write: false" in body
