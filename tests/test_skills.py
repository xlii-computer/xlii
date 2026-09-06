"""Skills: loader, /skill attach/detach, index in preamble + /status (A1)."""

from types import SimpleNamespace

from xlii.commands import dispatch_repl_command, find_repl_command
from xlii.repl_cmds import register_all
from xlii.skills import load_skills, skill_index_line
from tests.helpers import FakeConsole

register_all()


def _write_skill(base_dir, name, desc, body):
    d = base_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{body}\n")
    return d


def _write_project_skill(project_root, name, desc, body):
    return _write_skill(project_root / ".xlii" / "skills", name, desc, body)


class _Owner:
    """Quacks like the bits of REPLState /skill touches."""

    def __init__(self, root):
        self.project = SimpleNamespace(project_root=root)
        self.attached_docs: list[tuple[str, str]] = []
        self.agent = SimpleNamespace(
            model_override=None,
            session=SimpleNamespace(model_pin_stack=[]),
        )

    def attach_doc(self, name, content):
        if not any(n == name for n, _ in self.attached_docs):
            self.attached_docs.append((name, content))

    def detach_doc(self, name):
        before = len(self.attached_docs)
        self.attached_docs = [(n, c) for n, c in self.attached_docs if n != name]
        return len(self.attached_docs) < before


def _ctx(root):
    return {"console": FakeConsole(), "state": _Owner(root), "command_scope": "code"}


# --------------------------------------------------------------------------- #
#  loader
# --------------------------------------------------------------------------- #

def test_load_parses_frontmatter_and_body(tmp_path):
    _write_project_skill(tmp_path, "pr", "Open a pull request", "1. branch\n2. push")
    skills = load_skills(tmp_path)
    assert "pr" in skills
    s = skills["pr"]
    assert s.description == "Open a pull request"
    assert "branch" in s.body
    assert s.scope == "project"


def test_project_overrides_global(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path / "cfg"))
    from xlii.skills import global_skills_dir

    _write_skill(global_skills_dir(), "pr", "GLOBAL", "global body")
    _write_project_skill(tmp_path, "pr", "PROJECT", "project body")

    skills = load_skills(tmp_path)
    assert skills["pr"].description == "PROJECT"
    assert skills["pr"].scope == "project"


def test_skill_index_line_format(tmp_path):
    _write_project_skill(tmp_path, "pr", "Open a PR", "b")
    # (bundled stock skills also load now — assert this skill's line is present)
    assert "- pr: Open a PR" in skill_index_line(load_skills(tmp_path))


# --------------------------------------------------------------------------- #
#  /skill command
# --------------------------------------------------------------------------- #

def test_skill_registered_in_both_repls():
    assert find_repl_command("/skill", "code") is not None
    assert find_repl_command("/skill", "chat") is not None


def test_skill_list_shows_available(tmp_path):
    _write_project_skill(tmp_path, "pr", "Open a PR", "body")
    ctx = _ctx(tmp_path)
    assert dispatch_repl_command("/skill", ctx) is True
    assert "pr" in ctx["console"].text


def test_skill_attach_then_detach(tmp_path):
    _write_project_skill(tmp_path, "pr", "Open a PR", "1. do x\n2. do y")
    ctx = _ctx(tmp_path)

    assert dispatch_repl_command("/skill pr", ctx) is True
    docs = dict(ctx["state"].attached_docs)
    assert "skill:pr" in docs
    assert "do x" in docs["skill:pr"] and "Skill: pr" in docs["skill:pr"]

    assert dispatch_repl_command("/skill off pr", ctx) is True
    assert not any(n == "skill:pr" for n, _ in ctx["state"].attached_docs)


def test_skill_model_frontmatter_pins_on_attach(tmp_path):
    d = tmp_path / ".xlii" / "skills" / "review"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: review\ndescription: deep review\nmodel: grok-4\n---\nsteps\n"
    )
    skills = load_skills(tmp_path)
    assert skills["review"].model == "grok-4"

    ctx = _ctx(tmp_path)
    assert dispatch_repl_command("/skill review", ctx) is True
    assert ctx["state"].agent.model_override == "grok-4"
    assert "model pinned" in ctx["console"].text

    assert dispatch_repl_command("/skill off review", ctx) is True
    assert ctx["state"].agent.model_override is None


def test_skill_detach_keeps_loadout_model_pin(tmp_path):
    # A role loadout pinned the model directly (no pin-stack push). Detaching a
    # skill that happens to share that model must NOT clear it — detach only
    # unwinds a pin THIS skill pushed. (Bugbot PR#75: "Skill detach clears
    # loadout model")
    d = tmp_path / ".xlii" / "skills" / "review"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: review\ndescription: deep review\nmodel: grok-4\n---\nsteps\n"
    )
    ctx = _ctx(tmp_path)
    state = ctx["state"]
    state.agent.model_override = "grok-4"       # loadout pin, equal to skill model
    state.attach_doc("skill:review", "steps")   # attached without a stack push
    assert state.agent.session.model_pin_stack == []

    assert dispatch_repl_command("/skill off review", ctx) is True
    assert not any(n == "skill:review" for n, _ in state.attached_docs)
    assert state.agent.model_override == "grok-4"   # loadout pin preserved


def test_skill_detach_restores_prior_model_pin(tmp_path):
    d = tmp_path / ".xlii" / "skills" / "review"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: review\ndescription: deep review\nmodel: grok-4\n---\nsteps\n"
    )
    ctx = _ctx(tmp_path)
    ctx["state"].agent.model_override = "grok-build-0.1"
    assert dispatch_repl_command("/skill review", ctx) is True
    assert ctx["state"].agent.model_override == "grok-4"

    assert dispatch_repl_command("/skill off review", ctx) is True
    assert ctx["state"].agent.model_override == "grok-build-0.1"


def test_skill_reattach_does_not_duplicate_pin_stack(tmp_path):
    d = tmp_path / ".xlii" / "skills" / "review"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: review\ndescription: deep review\nmodel: grok-4\n---\nsteps\n"
    )
    ctx = _ctx(tmp_path)
    ctx["state"].agent.model_override = "grok-build-0.1"
    assert dispatch_repl_command("/skill review", ctx) is True
    assert len(ctx["state"].agent.session.model_pin_stack) == 1

    assert dispatch_repl_command("/skill review", ctx) is True
    assert len(ctx["state"].agent.session.model_pin_stack) == 1
    assert ctx["state"].agent.model_override == "grok-4"


def test_skill_detach_cleans_stack_when_model_changed(tmp_path):
    d = tmp_path / ".xlii" / "skills" / "review"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: review\ndescription: deep review\nmodel: grok-4\n---\nsteps\n"
    )
    ctx = _ctx(tmp_path)
    ctx["state"].agent.model_override = "grok-build-0.1"
    assert dispatch_repl_command("/skill review", ctx) is True
    ctx["state"].agent.model_override = "grok-4.20-reasoning"

    assert dispatch_repl_command("/skill off review", ctx) is True
    assert ctx["state"].agent.session.model_pin_stack == []
    assert ctx["state"].agent.model_override == "grok-4.20-reasoning"


def test_skill_attach_unknown_name(tmp_path):
    ctx = _ctx(tmp_path)
    assert dispatch_repl_command("/skill nope", ctx) is True
    assert "no skill named" in ctx["console"].text


def test_multiword_skill_name_attaches_and_detaches(tmp_path):
    _write_project_skill(tmp_path, "Voice Rename", "rename voices", "steps")
    ctx = _ctx(tmp_path)

    assert dispatch_repl_command("/skill Voice Rename", ctx) is True
    assert any(n == "skill:Voice Rename" for n, _ in ctx["state"].attached_docs)

    assert dispatch_repl_command("/skill off Voice Rename", ctx) is True
    assert not any(n == "skill:Voice Rename" for n, _ in ctx["state"].attached_docs)


def test_listy_frontmatter_name_falls_back_to_dir(tmp_path):
    d = tmp_path / ".xlii" / "skills" / "mydir"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: [a, b]\ndescription: x\n---\nbody\n")
    skills = load_skills(tmp_path)
    assert "mydir" in skills          # fell back to the dir name
    assert "['a', 'b']" not in skills  # not the garbled stringified list


# --------------------------------------------------------------------------- #
#  preamble index
# --------------------------------------------------------------------------- #

def test_system_prompt_lists_skills(tmp_path):
    _write_project_skill(tmp_path, "pr", "Open a PR", "body")
    from xlii.agent import build_code_system_prompt

    project = SimpleNamespace(
        project_root=tmp_path, xli_dir=tmp_path / ".xlii", local_only=False
    )
    prompt = build_code_system_prompt(project)
    assert "[SKILLS]" in prompt
    assert "pr: Open a PR" in prompt


# --------------------------------------------------------------------------- #
#  imported (foreign) skills — grok / Claude auto-discovery
# --------------------------------------------------------------------------- #

def _foreign(monkeypatch, *pairs):
    """Pin foreign discovery to controlled (dir, scope) pairs (hermetic — does
    not touch the real ~/.grok or ~/.claude)."""
    monkeypatch.setattr("xlii.skills.foreign_skill_dirs",
                        lambda project_root=None: list(pairs))


def test_foreign_skills_discovered_with_origin_scope(tmp_path, monkeypatch):
    grok = tmp_path / "grok_home" / "skills"
    _write_skill(grok, "best-of-n", "parallel impls", "steps")
    _foreign(monkeypatch, (grok, "grok"))
    skills = load_skills(import_foreign=True)
    assert "best-of-n" in skills
    assert skills["best-of-n"].scope == "grok"


def test_foreign_import_can_be_disabled(tmp_path, monkeypatch):
    grok = tmp_path / "grok_home" / "skills"
    _write_skill(grok, "best-of-n", "x", "y")
    _foreign(monkeypatch, (grok, "grok"))
    assert "best-of-n" not in load_skills(import_foreign=False)


def test_xlii_skill_overrides_imported(tmp_path, monkeypatch):
    grok = tmp_path / "grok_home" / "skills"
    _write_skill(grok, "pr", "FROM GROK", "g")
    _write_project_skill(tmp_path, "pr", "FROM XLII", "x")
    _foreign(monkeypatch, (grok, "grok"))
    skills = load_skills(tmp_path, import_foreign=True)
    assert skills["pr"].description == "FROM XLII"
    assert skills["pr"].scope == "project"


def test_index_tags_imported_provenance(tmp_path, monkeypatch):
    grok = tmp_path / "grok_home" / "skills"
    _write_skill(grok, "best-of-n", "parallel", "s")
    _write_project_skill(tmp_path, "mine", "my skill", "b")
    _foreign(monkeypatch, (grok, "grok"))
    line = skill_index_line(load_skills(tmp_path, import_foreign=True))
    assert "- best-of-n [grok]: parallel" in line
    assert "- mine: my skill" in line  # xlii's own: untagged


def test_disable_model_invocation_hidden_from_model(tmp_path, monkeypatch):
    grok = tmp_path / "grok_home" / "skills"
    d = grok / "code-review"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: code-review\ndescription: strict review\n"
        "disable-model-invocation: true\n---\nbody\n")
    _foreign(monkeypatch, (grok, "grok"))
    skills = load_skills(import_foreign=True)
    assert "code-review" in skills                      # still attachable via /skill
    assert skills["code-review"].model_invocable is False
    assert "code-review" not in skill_index_line(skills, model_facing=True)
    assert "code-review" in skill_index_line(skills, model_facing=False)


def test_claude_plugin_skills_kept_off_model_preamble(tmp_path, monkeypatch):
    plug = tmp_path / "plugin" / "skills"
    _write_skill(plug, "discord-access", "discord plugin skill", "b")
    _foreign(monkeypatch, (plug, "claude-plugin"))
    skills = load_skills(import_foreign=True)
    assert "discord-access" in skills                                       # usable on demand
    assert "discord-access" not in skill_index_line(skills, model_facing=True)
    assert "discord-access [claude-plugin]" in skill_index_line(skills, model_facing=False)


def test_config_flag_controls_default_import(tmp_path, monkeypatch):
    grok = tmp_path / "grok_home" / "skills"
    _write_skill(grok, "best-of-n", "x", "y")
    _foreign(monkeypatch, (grok, "grok"))
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("XLII_CONFIG_DIR", str(cfg))
    assert "best-of-n" in load_skills()                  # no config.json -> default True
    (cfg / "config.json").write_text('{"import_foreign_skills": false}')
    assert "best-of-n" not in load_skills()              # flag respected


# --------------------------------------------------------------------------- #
#  brief quickview vs extended view
# --------------------------------------------------------------------------- #

def test_authored_short_description_used_for_quickview(tmp_path, monkeypatch):
    grok = tmp_path / "g" / "skills"
    d = grok / "best-of-n"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: best-of-n\n"
        "description: >\n"
        "  Implement a task N ways in parallel and pick the best. Use when asked to\n"
        '  "best of n" or "/bon".\n'
        "metadata:\n"
        '  short-description: "Parallel implementation tournament"\n'
        "---\nbody steps\n")
    _foreign(monkeypatch, (grok, "grok"))
    sk = load_skills(import_foreign=True)["best-of-n"]
    assert sk.short_description == "Parallel implementation tournament"
    assert "Implement a task N ways" in sk.description           # full preserved
    line = skill_index_line(load_skills(import_foreign=True))
    assert "best-of-n [grok]: Parallel implementation tournament" in line
    assert "Implement a task N ways" not in line                 # full NOT in quickview


def test_brief_derived_from_first_sentence_when_no_short(tmp_path, monkeypatch):
    grok = tmp_path / "g" / "skills"
    long = ("Run an extremely strict maintainability review for quality and giant "
            "files. Use for a deep code quality audit of the whole tree.")
    _write_skill(grok, "reviewer", long, "body")
    _foreign(monkeypatch, (grok, "grok"))
    sk = load_skills(import_foreign=True)["reviewer"]
    assert sk.short_description == (
        "Run an extremely strict maintainability review for quality and giant files.")
    assert sk.description == long                                 # full retained


def test_skill_show_prints_full_without_attaching(tmp_path):
    _write_project_skill(
        tmp_path, "pr", "Open a pull request the careful way", "1. branch\n2. open PR")
    ctx = _ctx(tmp_path)
    assert dispatch_repl_command("/skill show pr", ctx) is True
    txt = ctx["console"].text
    assert "Open a pull request the careful way" in txt          # full description
    assert "branch" in txt and "open PR" in txt                  # body shown
    assert not ctx["state"].attached_docs                        # NOT attached


def test_skill_list_shows_brief_not_full(tmp_path):
    body = "step one\nstep two"
    _write_project_skill(
        tmp_path, "pr",
        "Open a PR. Then wait for review and merge once the checks are green and "
        "the branch is fully up to date with the main line of development.", body)
    ctx = _ctx(tmp_path)
    assert dispatch_repl_command("/skill", ctx) is True
    txt = ctx["console"].text
    assert "Open a PR." in txt                                    # brief first sentence
    assert "wait for review and merge" not in txt                # extended tail hidden
