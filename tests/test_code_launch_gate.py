"""The `xlii code` launch gate: the crude 'launch in this folder?' decision that
replaced the old hard-refuse on a non-project, plus the ephemeral preview project.

All disk-only / monkeypatched — no network, no real ~/.config, no terminal.
Run with `python -m pytest`.
"""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from xlii.cmds.sessions import code as S
from xlii.config import ProjectConfig
from xlii.sync import make_preview_project


def _args(**kw):
    base = dict(preview=False, init=False, launch=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _proj():
    return SimpleNamespace(name="repo", local_only=False, project_root=Path("/tmp"))


# --------------------------------------------------------------------------- #
#  _resolve_launch_choice — flags win, non-interactive never prompts
# --------------------------------------------------------------------------- #

def test_flags_override_the_gate():
    root = Path("/tmp/x")
    assert S._resolve_launch_choice(_args(preview=True), None, root, interactive=True) == "preview"
    assert S._resolve_launch_choice(_args(init=True), None, root, interactive=True) == "init"
    # --launch on a non-project has nothing to open → refuse (scriptable, like before)
    assert S._resolve_launch_choice(_args(launch=True), None, root, interactive=True) == "refuse"
    assert S._resolve_launch_choice(_args(launch=True), _proj(), root, interactive=True) == "launch"


def test_non_interactive_never_prompts():
    root = Path("/tmp/x")
    # No flags, no TTY: existing project launches; a non-project refuses (rc 1
    # path) — preserving the pre-gate behaviour for scripts/pipes/the daemon.
    assert S._resolve_launch_choice(_args(), _proj(), root, interactive=False) == "launch"
    assert S._resolve_launch_choice(_args(), None, root, interactive=False) == "refuse"


def test_gate_honors_explicit_flags_on_existing_project():
    """A1 regression: a satisfied EntryGate must NOT swallow --preview/--init on
    an existing project. --preview → 'preview' (skips the Collection sync the
    user opted out of); --init → 'launch' (the 'already a project' path)."""
    from xlii.session_boot import gate_code_entry

    root = Path("/tmp/x")
    proj = _proj()  # existing project ⇒ EntryGate(requires_project) is satisfied
    assert gate_code_entry(root, project=proj).choice == "launch"
    g = gate_code_entry(root, preview=True, project=proj)
    assert g.choice == "preview"
    assert g.project is proj  # the real project is kept, not an ephemeral preview
    assert gate_code_entry(root, init=True, project=proj).choice == "launch"


def test_interactive_no_flags_consults_the_prompt(monkeypatch):
    seen = {}

    def _fake_prompt(project, root):
        seen["called"] = (project, root)
        return "preview"

    monkeypatch.setattr(S, "_prompt_launch_gate", _fake_prompt)
    root = Path("/tmp/x")
    assert S._resolve_launch_choice(_args(), None, root, interactive=True) == "preview"
    assert seen["called"] == (None, root)


# --------------------------------------------------------------------------- #
#  ephemeral preview project — opens in a folder without writing .xlii there
# --------------------------------------------------------------------------- #

def test_ephemeral_preview_leaves_target_clean(tmp_path):
    proj = make_preview_project(tmp_path)
    assert isinstance(proj, ProjectConfig)
    assert proj.local_only and proj.collection_id == ""
    assert proj.project_root == tmp_path.resolve()
    # All state redirected to a temp dir, NOT the target folder.
    assert proj.state_dir_override is not None
    assert proj.xli_dir == proj.state_dir_override
    assert proj.xli_dir != tmp_path / ".xlii"

    # A write that the REPL would do (history) lands in the temp dir, never the target.
    (proj.xli_dir / "repl_history").write_text("x")
    assert not (tmp_path / ".xlii").exists()

    import shutil
    shutil.rmtree(proj.state_dir_override, ignore_errors=True)


def test_state_dir_override_not_persisted(tmp_path):
    """save()/load() must ignore the runtime-only override — a preview's temp
    redirect must never leak into a real project.json."""
    proj = make_preview_project(tmp_path)
    # Point at a normal project dir and save there.
    proj.state_dir_override = None  # so save() writes to tmp_path/.xlii
    proj.save()
    reloaded = ProjectConfig.load(tmp_path)
    assert reloaded is not None
    assert reloaded.state_dir_override is None
    assert "state_dir_override" not in json.loads(
        (tmp_path / ".xlii" / "project.json").read_text())
