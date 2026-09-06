"""Self-health tests for grades plan Phases 1–3 (+ related polish).

These lock the product claims we shipped on branch groked:

* Phase 1 — progressive /help, primary status axes, attach teaching
* Phase 2 — LEGACY contract, vault env alias, doctor legacy line
* Phase 3 — golden first-hour spine, setup → doctor, first-session topic

Run::

    pytest tests/test_grades_phases.py tests/test_help_tiers.py \\
           tests/test_vault_legacy_env.py tests/test_golden_path_doc.py \\
           tests/test_paste_collapse.py tests/test_tui_status.py -q
"""

from __future__ import annotations

import io
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from xlii.repl_cmds import register_all

register_all()

ROOT = Path(__file__).resolve().parents[1]


def _console():
    buf = io.StringIO()
    con = Console(file=buf, force_terminal=False, no_color=True, width=200)
    return con, buf


# --------------------------------------------------------------------------- #
# Phase 1 — progressive /help (handler + registration)
# --------------------------------------------------------------------------- #


def test_help_default_is_daily_tier():
    from xlii.repl_cmds.meta import _help_handler

    con, buf = _console()
    assert _help_handler("/help", {"console": con, "persona": None}) is True
    out = buf.getvalue()
    assert "· daily" in out
    assert "/plan" in out or "plan" in out.lower()
    # power verbs must not appear as command rows (footer may say "plan/rail/loop")
    cmd_lines = [
        ln.strip()
        for ln in out.splitlines()
        if ln.strip().startswith("/") and " " in ln.strip()[:20]
    ]
    assert not any(ln.startswith("/rail") for ln in cmd_lines)
    assert not any(ln.startswith("/loop") for ln in cmd_lines)
    assert not any(ln.startswith("/admin") for ln in cmd_lines)
    # teaching footer
    assert "/attach" in out
    assert "/help compose" in out
    assert "/help power" in out
    assert "/help all" in out


def test_help_all_includes_power_verbs():
    from xlii.repl_cmds.meta import _help_handler

    con, buf = _console()
    _help_handler("/help all", {"console": con, "persona": None})
    out = buf.getvalue()
    assert "· all" in out
    assert "rail" in out.lower()
    assert "loop" in out.lower()


def test_help_compose_and_power_tiers():
    from xlii.repl_cmds.meta import _help_handler

    con, buf = _console()
    _help_handler("/help compose", {"console": con, "persona": None})
    out = buf.getvalue()
    assert "· compose" in out
    assert "plugin" in out.lower()
    # compose must not list /rail as a command row
    body = out.split("More:")[0]
    assert "/rail" not in body or "plan/rail/loop" in body  # allow footer phrase only
    assert not any(line.strip().startswith("/rail") for line in body.splitlines())

    con2, buf2 = _console()
    _help_handler("/help power", {"console": con2, "persona": None})
    out2 = buf2.getvalue()
    assert "· power" in out2
    assert "rail" in out2.lower()


def test_help_unknown_tier_prints_usage():
    from xlii.repl_cmds.meta import _help_handler

    con, buf = _console()
    assert _help_handler("/help banana", {"console": con, "persona": None}) is True
    out = buf.getvalue()
    assert "usage" in out.lower()
    assert "/help compose" in out


def test_help_command_registered_with_tier_usage():
    from xlii.commands import find_repl_command

    cmd = find_repl_command("/help", "code")
    assert cmd is not None
    assert "compose" in (cmd.usage or "")
    assert "daily" in (cmd.description or "").lower() or "compose" in (cmd.description or "")


def test_render_repl_help_daily_matches_get_repl_help_membership():
    """Rich and plain builders share _help_sections — daily must not list rail."""
    from xlii.commands_help import get_repl_help, render_repl_help

    plain = get_repl_help("code", tier="daily")
    assert "/rail" not in plain
    # render must not raise; smoke that it returns a Rich renderable
    r = render_repl_help("code", tier="daily")
    con, buf = _console()
    con.print(r)
    rendered = buf.getvalue()
    assert "SHELL" in rendered or "shell" in rendered.lower() or "<command>" in rendered


# --------------------------------------------------------------------------- #
# Phase 1 — status axes on /status handlers
# --------------------------------------------------------------------------- #


def test_code_status_leads_with_primary_axes(tmp_path):
    from xlii.repl_cmds.code import _code_status_handler

    xli = tmp_path / ".xlii"
    xli.mkdir()
    project = SimpleNamespace(
        name="hour-proj",
        project_root=tmp_path,
        local_only=True,
        conversation_id="abcd1234efgh",
        collection_id=None,
        xli_dir=xli,
    )
    agent = SimpleNamespace(
        plan_mode=False,
        discovery_mode=False,
        ops_mode=False,
        rail=None,
        debug=None,
        active_mode=None,
        session=SimpleNamespace(
            plan_mode=False,
            yolo=False,
            freeball=False,
            conversational=False,
        ),
    )
    state = SimpleNamespace(
        project=project,
        agent=agent,
        plan_mode=False,
        discovery_mode=False,
        ops_mode=False,
        yolo=True,
        freeball=False,
        persona=None,
        scratch=False,
        profile=SimpleNamespace(mode="code"),
        pool=[],
        attached_docs=[],
        attached_refs=[],
        attached_files=[],
        format_status=lambda include_attachments=True: "",
    )
    con, buf = _console()
    assert _code_status_handler("/status", {"state": state, "project": project, "agent": agent, "console": con}) is True
    out = buf.getvalue()
    assert "mode:" in out
    assert "trust:" in out
    assert "surface:" in out
    assert "yolo" in out
    # project sync label (not overloaded as "mode")
    assert "sync:" in out
    assert "local-only" in out


def test_chat_status_leads_with_primary_axes(tmp_path):
    from xlii.repl_cmds.chat import _chat_status_handler

    persona = SimpleNamespace(
        name="ixaac",
        prompt_path=tmp_path / "p.md",
        project_root=tmp_path,
        turns_dir=tmp_path / "turns",
    )
    (tmp_path / "turns").mkdir()
    state = SimpleNamespace(
        persona=persona,
        project=None,
        yolo=False,
        freeball=False,
        scratch=False,
        profile=SimpleNamespace(mode="chat"),
        agent=SimpleNamespace(active_mode=None),
        format_status=lambda include_attachments=True: "",
    )
    con, buf = _console()
    assert _chat_status_handler("/status", {"state": state, "persona": persona, "console": con}) is True
    out = buf.getvalue()
    assert "mode:" in out
    assert "trust: safe" in out
    assert "surface: chat" in out
    assert "ixaac" in out


def test_primary_axes_freeball_and_rail():
    from xlii.mode_controller import PlanController
    from xlii.rail import RailController
    from xlii.tui import status

    st = SimpleNamespace(
        agent=SimpleNamespace(active_mode=PlanController(), rail=None),
        yolo=True,
        freeball=True,
        persona=None,
        scratch=False,
        profile=None,
    )
    assert status.exclusive_mode(st) == "plan"
    assert status.trust_axis(st) == "freeball"
    assert status.surface_axis(st) == "code"

    rail = RailController()
    st2 = SimpleNamespace(
        agent=SimpleNamespace(active_mode=rail, rail=rail),
        yolo=False,
        freeball=False,
        persona=None,
        scratch=False,
        profile=None,
    )
    assert status.exclusive_mode(st2) == "rail"
    # one exclusive mode only
    assert status.primary_axes(st2)[0] == "rail"


# --------------------------------------------------------------------------- #
# Phase 2 — LEGACY contract + doctor self-health
# --------------------------------------------------------------------------- #


def test_legacy_md_contract_exists():
    text = (ROOT / "docs" / "LEGACY.md").read_text(encoding="utf-8")
    for needle in (
        "KEYRING_SERVICE",
        "XLI_VAULT_KEY",
        "XLII_VAULT_KEY",
        "xli/",
        ".xlii/",
        "~/.config/xlii",
        "xlii doctor",
    ):
        assert needle in text, f"LEGACY.md missing {needle!r}"
    # must not teach the wrong config dir as canonical
    assert "~/.config/xli/" not in text or "no** supported `~/.config/xli" in text or "There is **no** supported" in text


def test_overview_and_reference_link_legacy():
    assert "LEGACY.md" in (ROOT / "docs" / "OVERVIEW.md").read_text(encoding="utf-8")
    ref = (ROOT / "docs" / "REFERENCE.md").read_text(encoding="utf-8")
    assert "LEGACY.md" in ref
    assert "XLI_VAULT_KEY" in ref


def test_doctor_prints_legacy_aliases_ok(tmp_path, monkeypatch):
    from xlii.cmds import diag

    con, buf = _console()
    monkeypatch.setattr(diag, "console", con)
    monkeypatch.setattr(diag, "GLOBAL_CONFIG_FILE", tmp_path / "nope.json")
    monkeypatch.setattr(diag.ProjectConfig, "load", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr("xlii.vault._resolve_key", lambda: (None, None))

    rc = diag.cmd_doctor(Namespace(migrate_legacy=False, dry_run=False, online=False))
    assert rc in (0, 1)  # missing config is a problem but we still print legacy line
    out = buf.getvalue()
    assert "legacy aliases OK" in out
    assert "XLII_VAULT_KEY" in out
    assert "XLI_VAULT_KEY" in out
    assert "xli" in out  # keyring service name


def test_init_guard_teaches_xliiignore_not_legacy_name():
    src = (ROOT / "xlii" / "cmds" / "project" / "_guards.py").read_text(encoding="utf-8")
    assert ".xliiignore" in src
    # user-facing safer-options blurb should not recommend creating .xliignore as the normal file
    assert "add a [cyan].xliiignore[/cyan]" in src or 'add a [cyan].xliiignore' in src


def test_vault_user_errors_mention_xlii_not_xli_paths():
    src = (ROOT / "xlii" / "vault.py").read_text(encoding="utf-8")
    assert "~/.config/xlii" in src or "GLOBAL_CONFIG_DIR" in src
    assert "xlii auth set" in src
    assert "xli auth set" not in src
    assert "ENV_VAR_LEGACY" in src


def test_bootstrap_log_tag_is_xlii():
    src = (ROOT / "xlii" / "bootstrap.py").read_text(encoding="utf-8")
    assert "[xlii] model discovery" in src
    assert "[xli] model discovery" not in src


# --------------------------------------------------------------------------- #
# Phase 2/3 — models list resolves vault-backed keys
# --------------------------------------------------------------------------- #


def test_resolved_chat_keys_uses_key_pairs(monkeypatch):
    from xlii.cmds.provision import models as models_mod

    class KP:
        def __init__(self, k):
            self.api_key = k

    class Cfg:
        def key_pairs(self):
            return [KP("sk-a"), KP(""), KP("sk-b")]

    assert models_mod._resolved_chat_keys(Cfg()) == ["sk-a", "sk-b"]

    class Boom:
        def key_pairs(self):
            raise RuntimeError("vault locked")

    con, buf = _console()
    monkeypatch.setattr(models_mod, "console", con)
    assert models_mod._resolved_chat_keys(Boom()) == []
    assert "could not unlock" in buf.getvalue().lower() or "vault" in buf.getvalue().lower()


# --------------------------------------------------------------------------- #
# Phase 3 — golden path + setup + howto alignment
# --------------------------------------------------------------------------- #


def test_golden_path_checklist_and_no_xmpp_required():
    text = (ROOT / "docs" / "GOLDEN-PATH.md").read_text(encoding="utf-8")
    assert "xlii doctor" in text
    assert "xlii export" in text
    assert "open-meteo" in text
    assert "/plan" in text
    assert "not required" in text.lower() or "Not required" in text
    assert "XMPP" in text  # mentioned as optional/not required only
    # local-only first-hour path
    assert "--local" in text


def test_setup_finish_points_at_doctor_and_golden_path(monkeypatch):
    from xlii.cmds.provision import setup as setup_mod
    from xlii.config import GlobalConfig

    con, buf = _console()
    cfg = GlobalConfig()
    monkeypatch.setattr(cfg, "key_pairs", lambda: [])
    # avoid companion install side effects if heavy
    monkeypatch.setattr(
        setup_mod,
        "_install_companion_persona",
        lambda console_, **_: "ixaac",
    )
    monkeypatch.setattr(
        "xlii.interactive.installed_defaults",
        lambda: [],
    )
    rc = setup_mod._setup_finish(cfg, con)
    assert rc == 0
    out = buf.getvalue()
    assert "doctor" in out.lower()
    assert "GOLDEN-PATH" in out or "golden" in out.lower()
    assert "ixaac" in out or "chat" in out.lower()


def test_first_session_topic_matches_golden_path():
    text = (ROOT / "docs" / "help" / "topics" / "first-session.md").read_text(encoding="utf-8")
    assert "GOLDEN-PATH" in text
    assert "/attach" in text
    assert "xlii doctor" in text
    assert "/help" in text


def test_howto_prompt_teaches_attach_and_golden_path():
    text = (ROOT / "xlii" / "prompts" / "howto.md").read_text(encoding="utf-8")
    assert "/attach" in text
    assert "GOLDEN-PATH" in text or "golden" in text.lower()
    assert "/help" in text


def test_help_footer_does_not_lead_with_legacy_ref_doc_only():
    """Regression: footer used to say '/ref and /doc' as the primary story."""
    from xlii.repl_cmds.meta import _help_handler

    con, buf = _console()
    _help_handler("/help", {"console": con, "persona": None})
    out = buf.getvalue()
    assert "/attach" in out
    # legacy names may appear as aliases, but attach must be present
    assert "durable" in out.lower()
