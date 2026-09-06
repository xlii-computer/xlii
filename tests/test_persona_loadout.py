"""PL1: persona frontmatter loadout — parsed, and never leaked into the prompt.

The load-bearing guarantee is backward compatibility: a persona with no
frontmatter must behave exactly as before, and a persona *with* frontmatter must
keep the YAML out of the system prompt the model sees.
"""

from pathlib import Path
from types import SimpleNamespace

import xlii.cmds.sessions as sessions
from xlii.config import GlobalConfig
from xlii.persona import PERSONAS_DIR, Persona


def _write(monkeypatch, tmp_path, name, text):
    monkeypatch.setattr("xlii.persona.PERSONAS_DIR", tmp_path)
    (tmp_path / f"{name}.md").write_text(text)
    return Persona(name)


def test_legacy_persona_without_frontmatter_unchanged(monkeypatch, tmp_path):
    p = _write(monkeypatch, tmp_path, "plain", "You are Bob.\nBe terse.")
    assert p.loadout() == {}
    assert p.prompt_body() == "You are Bob.\nBe terse."
    assert p.system_prompt().startswith("You are Bob.\nBe terse.")
    assert p.first_line() == "You are Bob."


def test_frontmatter_is_parsed_as_loadout(monkeypatch, tmp_path):
    p = _write(monkeypatch, tmp_path, "bob", (
        "---\n"
        "plugins: [open-meteo, hackernews]\n"
        "docs:\n"
        "  - conventions\n"
        "model: grok-4\n"
        "---\n"
        "You are Bob, the auth specialist.\n"
    ))
    lo = p.loadout()
    assert lo["plugins"] == ["open-meteo", "hackernews"]
    assert lo["docs"] == ["conventions"]
    assert lo["model"] == "grok-4"


def test_frontmatter_never_reaches_the_model(monkeypatch, tmp_path):
    p = _write(monkeypatch, tmp_path, "bob", (
        "---\nplugins: [open-meteo]\nmodel: grok-4\n---\n"
        "You are Bob, the auth specialist."
    ))
    sp = p.system_prompt()
    assert "plugins:" not in sp
    assert "grok-4" not in sp  # the model id is loadout config, not instructions
    assert not sp.startswith("---")  # frontmatter would sit at the top
    assert sp.startswith("You are Bob, the auth specialist.")
    assert p.first_line() == "You are Bob, the auth specialist."


def test_personas_dir_is_a_real_path():
    # sanity: the module constant exists and is under the config dir
    assert PERSONAS_DIR.name == "personas"


# --------------------------------------------------------------------------- #
#  PL2 — loadout reconciliation routing (collaborators stubbed)
# --------------------------------------------------------------------------- #

def _stub_state():
    docs, refs, saved = [], [], []
    state = SimpleNamespace(
        attach_doc=lambda n, c: docs.append((n, c)),
        attach_ref=lambda n, cid: refs.append((n, cid)),
        save=lambda: saved.append(True),
    )
    return state, docs, refs, saved


def test_reconciliation_routes_and_skips_unknowns(monkeypatch):
    subs: list[str] = []
    monkeypatch.setattr("xlii.plugin.add_subscription",
                        lambda xli_dir, pid: (subs.append(pid), True)[1])

    def _plugin(id):
        ns = SimpleNamespace(exists=lambda: id == "open-meteo")
        if id == "open-meteo":
            ns.manifest = lambda: SimpleNamespace(effect="read-only", trust="subscription")
        return ns

    monkeypatch.setattr("xlii.plugin.Plugin", _plugin)
    monkeypatch.setattr("xlii.doc.Doc",
                        lambda name: SimpleNamespace(
                            exists=lambda: name == "conventions", read=lambda: "DOC BODY"))
    monkeypatch.setattr("xlii.persona.Persona",
                        lambda name: SimpleNamespace(
                            collection_id=lambda: "col-123" if name == "buddy" else None))

    state, docs, refs, saved = _stub_state()
    persona = SimpleNamespace(loadout=lambda: {
        "plugins": ["open-meteo", "ghost"],          # ghost not installed
        "docs": ["conventions", "missing"],          # missing not found
        "refs": ["buddy", "uninitialized"],          # refs are RETIRED — declaring one is a no-op
    })
    project = SimpleNamespace(xli_dir=Path("/tmp/x"))

    sessions._apply_persona_loadout(state, persona, project)

    assert subs == ["open-meteo"]
    assert docs == [("conventions", "DOC BODY")]
    assert refs == []       # a loadout NEVER attaches a persona's memory (the second /ref path is gone)
    assert saved == [True]  # persisted exactly once, since plugins + docs applied


def test_reconciliation_attaches_declared_skills(monkeypatch):
    # Roles R0: a loadout `skills:` list resolves via load_skills and attaches each
    # through the skill: doc channel, exactly like `/skill <name>`. Unknown skills
    # warn and are skipped.
    from xlii.skills import SKILL_ATTACH_PREFIX

    fake = {"grounded-analysis": SimpleNamespace(name="grounded-analysis")}
    monkeypatch.setattr("xlii.skills.load_skills", lambda root=None: fake)
    monkeypatch.setattr("xlii.skills.render_skill", lambda sk: f"BODY:{sk.name}")

    state, docs, refs, saved = _stub_state()
    persona = SimpleNamespace(loadout=lambda: {
        "skills": ["grounded-analysis", "ghost"],  # ghost not found → skipped
    })
    project = SimpleNamespace(project_root=Path("/tmp/x"), xli_dir=Path("/tmp/x"))

    sessions._apply_persona_loadout(state, persona, project)

    assert docs == [(SKILL_ATTACH_PREFIX + "grounded-analysis", "BODY:grounded-analysis")]
    assert refs == []
    assert saved == [True]  # one skill applied → persisted exactly once


def test_reconciliation_noop_for_empty_loadout():
    state, docs, refs, saved = _stub_state()
    persona = SimpleNamespace(loadout=lambda: {})  # legacy persona, no frontmatter
    project = SimpleNamespace(xli_dir=Path("/tmp/x"))

    sessions._apply_persona_loadout(state, persona, project)

    assert docs == [] and refs == [] and saved == []  # nothing touched, no save


# --------------------------------------------------------------------------- #
#  PL4 — per-persona model / temperature pinning
# --------------------------------------------------------------------------- #

def test_session_and_agent_model_override_delegation():
    from xlii.agent import Agent, SessionState

    s = SessionState()
    assert s.model_override is None and s.temperature_override is None

    a = object.__new__(Agent)
    a.session = s
    assert a.model_override is None and a.temperature_override is None
    a.model_override = "grok-4"
    a.temperature_override = 0.5
    assert s.model_override == "grok-4"
    assert s.temperature_override == 0.5


def _stub_state_with_agent(pricing=("grok-4",)):
    saved: list = []
    agent = SimpleNamespace(model_override=None, temperature_override=None)
    cfg = SimpleNamespace(orchestrator=lambda: "grok-4", worker=lambda: "grok-4-fast",
                          pricing={m: {} for m in pricing})
    state = SimpleNamespace(
        attach_doc=lambda n, c: None,
        attach_ref=lambda n, cid: None,
        save=lambda: saved.append(True),
        cfg=cfg,
        agent=agent,
    )
    return state, agent, saved


def test_reconciliation_applies_model_and_temperature():
    state, agent, saved = _stub_state_with_agent()
    persona = SimpleNamespace(loadout=lambda: {"model": "grok-4", "temperature": "0.3"})
    sessions._apply_persona_loadout(state, persona, SimpleNamespace(xli_dir=Path("/tmp/x")))
    assert agent.model_override == "grok-4"
    assert agent.temperature_override == 0.3
    assert saved == [True]


def test_reconciliation_applies_profile_to_cfg(tmp_path):
    cfg = GlobalConfig()
    saved: list = []
    agent = SimpleNamespace(model_override=None, temperature_override=None)
    state = SimpleNamespace(
        attach_doc=lambda n, c: None,
        attach_ref=lambda n, cid: None,
        save=lambda: saved.append(True),
        cfg=cfg,
        agent=agent,
    )
    persona = SimpleNamespace(loadout=lambda: {"profile": "vision"})
    sessions._apply_persona_loadout(
        state, persona, SimpleNamespace(xli_dir=tmp_path / ".xlii", project_root=tmp_path)
    )
    assert cfg.orchestrator_model == "grok-4.3"
    assert cfg.chat_model == "grok-4.3"
    assert agent.model_override is None
    assert saved == [True]


def test_unknown_model_is_applied_with_warning_not_ignored():
    # An unlisted model is the user's declared intent — apply it (warn), don't
    # silently run on the wrong model.
    state, agent, _ = _stub_state_with_agent(pricing=("grok-4",))
    persona = SimpleNamespace(loadout=lambda: {"model": "some-new-model"})
    sessions._apply_persona_loadout(state, persona, SimpleNamespace(xli_dir=Path("/tmp/x")))
    assert agent.model_override == "some-new-model"


def test_out_of_range_temperature_is_skipped():
    state, agent, _ = _stub_state_with_agent()
    persona = SimpleNamespace(loadout=lambda: {"temperature": "9"})  # > 2.0
    sessions._apply_persona_loadout(state, persona, SimpleNamespace(xli_dir=Path("/tmp/x")))
    assert agent.temperature_override is None  # rejected, not applied
