"""Jams (gigwork Phase B / G3): stock presets, gig-slot binding, fan-out,
merge policies, and the /jam command. All offline — brains are faked at the
chat seam (home via fake clients, gigs via a patched resolve_gig_backend)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.chat_backend import GigError
from xlii.config import GlobalConfig
from xlii.jam import (
    MAX_MEMBERS,
    jam_specs,
    resolve_jam,
    run_jam,
)
from tests.helpers import make_agent, make_msg


def _resp(msg):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0),
    )


def _scripted_clients(*texts):
    """Home chat that replays one response per create() call (thread-safe enough
    for these tests: each worker makes exactly one call)."""
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


# --------------------------------------------------------------------------- #
#  Specs + resolution
# --------------------------------------------------------------------------- #


def test_stock_jams_are_valid_specs():
    specs = jam_specs(_cfg())
    assert {"second-opinion", "debate", "scout"} <= set(specs)
    so = specs["second-opinion"]
    assert [m.backend for m in so.members] == ["xai", "gig"]
    assert so.merge == "synth_conflicts"
    assert specs["scout"].merge == "concat_digest"


def test_gig_slot_binds_allowlist_first_then_sole_provider():
    spec = resolve_jam(_cfg(providers=("kimi", "deepseek"), allow=["deepseek"]),
                          "second-opinion")
    assert spec.members[1].backend == "deepseek"    # allowlist order wins
    spec = resolve_jam(_cfg(providers=("kimi",)), "second-opinion")
    assert spec.members[1].backend == "kimi"        # sole provider binds


def test_gig_slot_without_providers_is_the_fix():
    with pytest.raises(GigError, match="/gigwork add"):
        resolve_jam(_cfg(providers=()), "second-opinion")


def test_retired_gaggles_config_key_still_loads():
    cfg = _cfg()
    cfg.gigwork["gaggles"] = {
        "legacy-crew": {"members": [{"backend": "kimi"}], "merge": "concat_digest"},
    }
    specs = jam_specs(cfg)
    assert "legacy-crew" in specs


def test_unknown_jam_and_unknown_member_backend():
    with pytest.raises(GigError, match="unknown jam 'nope'"):
        resolve_jam(_cfg(), "nope")
    cfg = _cfg(jams={"mine": {"members": [{"backend": "notconfigured"}],
                                 "merge": "concat_digest"}})
    with pytest.raises(GigError, match="not a .*configured gig provider"):
        resolve_jam(cfg, "mine")


def test_config_jam_overrides_and_validates():
    cfg = _cfg(jams={
        "second-opinion": {"members": [{"backend": "kimi", "kit": "explore"}] * 2,
                           "merge": "concat_digest", "max_parallel": 2},
    })
    spec = resolve_jam(cfg, "second-opinion")
    assert spec.merge == "concat_digest"            # config wins over stock

    bad = _cfg(jams={"m": {"members": [{"backend": "kimi", "kit": "pilot"}]}})
    with pytest.raises(GigError, match="kit 'pilot'"):
        jam_specs(bad)
    bad = _cfg(jams={"m": {"members": [{"backend": "kimi"}], "merge": "vote"}})
    with pytest.raises(GigError, match="merge 'vote'"):
        jam_specs(bad)
    bad = _cfg(jams={"m": {"members": [{"backend": "kimi"}] * (MAX_MEMBERS + 1)}})
    with pytest.raises(GigError, match="exceeds the"):
        jam_specs(bad)


# --------------------------------------------------------------------------- #
#  Engine — fan-out + merge
# --------------------------------------------------------------------------- #


def _patch_gig(monkeypatch, text="gig answer"):
    monkeypatch.setattr(
        "xlii.jam.resolve_gig_backend",
        lambda cfg, name: _FakeGigBackend(text, label=name),
    )


def test_second_opinion_runs_both_and_synthesizes(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    cfg = _cfg()
    _patch_gig(monkeypatch, "the race is in the sweep loop")
    # Home clients answer the worker pass AND the synth call, in order.
    clients = _scripted_clients(
        "the race is in the drain",                       # home member pass
        "## Agreements\nboth see a race\n## Conflicts\nlocation differs\n## Verdict\ncheck both",
    )
    result = run_jam(
        "second-opinion", "where is the race?",
        cfg=cfg, project=agent.project, clients=clients,
    )
    assert result.ok_count == 2
    assert [r.member.label for r in result.results] == ["xai(explore)", "kimi(explore)"]
    assert "## Agreements" in result.merged and "## Conflicts" in result.merged
    assert result.synth_model                              # synth actually ran


def test_synth_prompt_carries_both_answers(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    cfg = _cfg()
    _patch_gig(monkeypatch, "GIG-ANSWER-TOKEN")
    seen = {}

    home_replies = iter([_resp(make_msg("HOME-ANSWER-TOKEN", None))])

    def create(**kw):
        if any("HOME-ANSWER-TOKEN" in str(m.get("content", ""))
               for m in kw.get("messages", [])):
            seen["synth_prompt"] = kw["messages"][0]["content"]
            return _resp(make_msg("synth", None))
        return next(home_replies)

    clients = SimpleNamespace(
        chat=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        label="primary",
    )
    run_jam("second-opinion", "q?", cfg=cfg, project=agent.project, clients=clients)
    assert "HOME-ANSWER-TOKEN" in seen["synth_prompt"]
    assert "GIG-ANSWER-TOKEN" in seen["synth_prompt"]
    assert "do not add new claims" in seen["synth_prompt"]


def test_scout_concat_digest_makes_no_synth_call(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    cfg = _cfg()
    _patch_gig(monkeypatch, "scout report")
    calls = []
    clients = SimpleNamespace(
        chat=SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: calls.append(1)))),
        label="primary",
    )
    result = run_jam("scout", "sweep the area", cfg=cfg,
                        project=agent.project, clients=clients)
    assert calls == []                                    # no home chat at all
    assert result.ok_count == 2
    assert "Answer 1 — kimi(explore)" in result.merged
    assert "Answer 2 — kimi(explore)" in result.merged
    assert result.synth_model == ""


def test_partial_failure_survives_and_is_visible(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    cfg = _cfg()

    def boom(cfg_, name):
        raise GigError(f"gig provider {name!r}: environment variable X is not set")

    monkeypatch.setattr("xlii.jam.resolve_gig_backend", boom)
    clients = _scripted_clients(
        "home still answers",
        "## Agreements\n(one member failed)\n## Conflicts\nnone\n## Verdict\nhome only",
    )
    result = run_jam("second-opinion", "q?", cfg=cfg,
                        project=agent.project, clients=clients)
    assert result.ok_count == 1
    failed = [r for r in result.results if not r.ok]
    assert len(failed) == 1 and "not set" in failed[0].error


def test_all_members_failing_raises(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    cfg = _cfg()

    def boom(cfg_, name):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr("xlii.jam.resolve_gig_backend", boom)
    with pytest.raises(GigError, match="every member failed"):
        run_jam("scout", "q?", cfg=cfg, project=agent.project,
                   clients=_scripted_clients())


def test_synth_failure_degrades_to_digest(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    cfg = _cfg()
    _patch_gig(monkeypatch, "gig answer")

    replies = iter([_resp(make_msg("home answer", None))])

    def create(**kw):
        msgs = kw.get("messages", [])
        if any("home answer" in str(m.get("content", "")) for m in msgs):
            raise RuntimeError("synth endpoint 500")
        return next(replies)

    clients = SimpleNamespace(
        chat=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        label="primary",
    )
    result = run_jam("second-opinion", "q?", cfg=cfg,
                        project=agent.project, clients=clients)
    assert "synthesis failed" in result.merged
    assert "home answer" in result.merged and "gig answer" in result.merged


def test_home_member_uses_pool_when_present(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    cfg = _cfg()
    _patch_gig(monkeypatch, "gig answer")
    pool_clients = _scripted_clients("pooled home answer")
    events = []
    pool = SimpleNamespace(
        acquire=lambda: events.append("acquire") or pool_clients,
        report_success=lambda c: events.append("ok"),
    )
    # agent-level clients handle ONLY the synth call.
    synth_clients = _scripted_clients("## Agreements\nx\n## Conflicts\ny\n## Verdict\nz")
    result = run_jam("second-opinion", "q?", cfg=cfg, project=agent.project,
                        clients=synth_clients, pool=pool)
    assert events == ["acquire", "ok"]
    assert result.ok_count == 2


# --------------------------------------------------------------------------- #
#  /jam command
# --------------------------------------------------------------------------- #


class _Console:
    def __init__(self):
        self.lines: list[str] = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    def text(self):
        return "\n".join(self.lines)


def _run_cmd(line, agent):
    from xlii.repl_cmds.jam import _jam_handler
    console = _Console()
    handled = _jam_handler(line, {"console": console, "agent": agent, "state": None})
    return handled, console.text()


def test_jam_command_registered():
    from xlii.commands import iter_repl_commands
    from xlii.repl_cmds import register_all
    register_all()
    cmds = list(iter_repl_commands())
    jam = next(c for c in cmds if c.name == "jam")
    assert "gaggle" in jam.aliases


def test_jam_ls_shows_stock_and_config(tmp_path):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg(jams={"mine": {"members": [{"backend": "kimi"}],
                                       "merge": "concat_digest"}})
    handled, out = _run_cmd("/jam ls", agent)
    assert handled
    assert "second-opinion" in out and "(stock)" in out
    assert "mine" in out and "(config)" in out


def test_jam_run_prints_members_and_merge(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg()
    _patch_gig(monkeypatch, "gig view")
    agent.pool = SimpleNamespace(
        primary=lambda: None,
        acquire=lambda: _scripted_clients("home view"),
        report_success=lambda c: None,
    )
    synth_clients = _scripted_clients("## Agreements\na\n## Conflicts\nb\n## Verdict\nc")
    monkeypatch.setattr(type(agent), "clients", property(lambda self: synth_clients))
    handled, out = _run_cmd("/jam second-opinion which view is right?", agent)
    assert handled
    assert "jam[second-opinion]" in out
    assert "xai(explore)" in out and "kimi(explore)" in out
    assert "## Agreements" in out


def test_jam_usage_and_errors(tmp_path):
    agent = make_agent(tmp_path)
    agent.cfg = _cfg(providers=())
    handled, out = _run_cmd("/jam", agent)
    assert "usage:" in out
    handled, out = _run_cmd("/jam second-opinion", agent)
    assert "pass a question" in out
    handled, out = _run_cmd("/jam second-opinion hi", agent)
    assert "/gigwork add" in out                    # no provider → the fix


def test_member_role_key_gets_rename_error():
    """The old member key 'role' errors with the fix — 'role' means persona
    loadouts elsewhere; the palette key is 'kit'."""
    cfg = _cfg(jams={"m": {"members": [{"backend": "kimi", "role": "explore"}],
                              "merge": "concat_digest"}})
    with pytest.raises(GigError, match="renamed to 'kit'"):
        jam_specs(cfg)


# --------------------------------------------------------------------------- #
#  Authoring — parse_member_token / add / rm + member @model
# --------------------------------------------------------------------------- #


def test_parse_member_token_forms():
    from xlii.jam import parse_member_token as p

    assert p("kimi") == {"backend": "kimi"}
    assert p("kimi:bash") == {"backend": "kimi", "kit": "bash"}
    assert p("kimi@k2") == {"backend": "kimi", "model": "k2"}
    assert p("ollama:general@llama3:8b") == {
        "backend": "ollama", "kit": "general", "model": "llama3:8b"}  # model keeps its colon
    with pytest.raises(GigError, match="needs a backend"):
        p(":explore")


def test_member_model_key_parses_labels_and_tokens():
    cfg = _cfg(jams={"m": {"members": [{"backend": "kimi", "model": "k9"}],
                              "merge": "concat_digest"}})
    m = jam_specs(cfg)["m"].members[0]
    assert m.model == "k9"
    assert m.label == "kimi(explore)@k9"
    assert m.token == "kimi@k9"                       # round-trips into /jam add


def test_gig_slot_binding_keeps_the_model():
    cfg = _cfg(jams={"m": {"members": [{"backend": "gig", "model": "k9"}],
                              "merge": "concat_digest"}})
    spec = resolve_jam(cfg, "m")
    assert spec.members[0].backend == "kimi" and spec.members[0].model == "k9"


def test_member_model_overrides_the_backend_model(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    cfg = _cfg(jams={"m": {"members": [{"backend": "kimi", "model": "CUSTOM-M"}],
                              "merge": "concat_digest"}})
    _patch_gig(monkeypatch, "gig answer")             # fake backend's own model differs
    result = run_jam("m", "q?", cfg=cfg, project=agent.project,
                        clients=_scripted_clients())
    assert result.ok_count == 1
    assert result.results[0].model == "CUSTOM-M"      # @model beat the backend's model


def test_add_jam_to_config_validates_and_writes():
    from xlii.jam import add_jam_to_config

    cfg = _cfg(providers=("kimi",))
    spec = add_jam_to_config(cfg, "trio", ["xai", "kimi:bash@k2", "gig"])
    assert [m.token for m in spec.members] == ["xai", "kimi:bash@k2", "gig"]
    assert spec.max_parallel == 3                      # 0/omitted = all members at once
    raw = cfg.gigwork["jams"]["trio"]
    assert raw["members"][1] == {"backend": "kimi", "kit": "bash", "model": "k2"}
    assert jam_specs(cfg)["trio"].merge == "synth_conflicts"

    with pytest.raises(GigError, match="is a /jam verb"):
        add_jam_to_config(cfg, "ls", ["xai"])
    with pytest.raises(GigError, match="/gigwork add ghost"):
        add_jam_to_config(cfg, "g2", ["ghost"])
    with pytest.raises(GigError, match="exceeds the"):
        add_jam_to_config(cfg, "big", ["xai"] * (MAX_MEMBERS + 1))


def test_remove_jam_semantics():
    from xlii.jam import add_jam_to_config, remove_jam_from_config

    cfg = _cfg(providers=("kimi",))
    add_jam_to_config(cfg, "mine", ["xai", "kimi"])
    assert remove_jam_from_config(cfg, "mine") == (True, False)
    assert remove_jam_from_config(cfg, "mine") == (False, False)

    add_jam_to_config(cfg, "scout", ["kimi", "kimi"])            # shadows stock
    assert remove_jam_from_config(cfg, "scout") == (True, True)  # stock resurfaces
    with pytest.raises(GigError, match="stock preset"):
        remove_jam_from_config(cfg, "scout")                     # bare stock: refused


def test_jam_add_rm_commands_persist(tmp_path, monkeypatch):
    import xlii.config as C

    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(C, "GLOBAL_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(C, "GLOBAL_CONFIG_FILE", tmp_path / "config.json")
    base = C.GlobalConfig()
    base.gigwork = _cfg().gigwork                     # a kimi provider, persisted
    base.save()

    agent = make_agent(tmp_path)
    agent.cfg = C.GlobalConfig.load()

    handled, out = _run_cmd(
        "/jam add trio xai kimi:bash@k2 --merge concat_digest --cap 2", agent)
    assert handled and "added" in out and "kimi(bash)@k2" in out
    assert "trio" in C.GlobalConfig.load().gigwork["jams"]
    assert "trio" in jam_specs(agent.cfg)          # live mirror

    handled, out = _run_cmd("/jam add scout kimi kimi", agent)
    assert "shadows the stock preset" in out
    handled, out = _run_cmd("/jam ls", agent)
    scout_line = next(ln for ln in out.splitlines() if ln.strip().startswith("scout"))
    assert "(config)" in scout_line                   # shadowed origin reads config

    handled, out = _run_cmd("/jam rm trio", agent)
    assert "removed" in out
    assert "trio" not in (C.GlobalConfig.load().gigwork.get("jams") or {})
    handled, out = _run_cmd("/jam rm trio", agent)
    assert "no configured jam" in out
    handled, out = _run_cmd("/jam rm scout", agent)
    assert "stock preset resurfaces" in out

    handled, out = _run_cmd("/jam add", agent)
    assert "usage" in out
    handled, out = _run_cmd("/jam add crew ghost", agent)
    assert "/gigwork add ghost" in out                # unknown backend → the fix
