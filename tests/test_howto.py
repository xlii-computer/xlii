"""Behavior of the /howto self-guide command.

/howto attaches xlii's own usage guide to the system prompt by riding the same
attachment seam as /doc. These tests use a minimal fake owner + ctx so they run
without a live session, network, or xAI account.
"""

import io

import pytest
from rich.console import Console

from xlii.commands import find_repl_command
from xlii.repl_cmds import howto, register_all

register_all()


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Standing test-isolation rule: never touch the real ~/.cache (the help
    corpus etag/body cache and corpus.json live under XDG_CACHE_HOME)."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))


class _Owner:
    """Quacks like the bits of REPLState that the handler touches."""

    def __init__(self):
        self.attached_docs: list[tuple[str, str]] = []
        self.saves = 0
        self.howto_mode = False  # the mode flag /howto toggles (REPLState property)

    def attach_doc(self, name, content):
        if not any(n == name for n, _ in self.attached_docs):
            self.attached_docs.append((name, content))
            self.saves += 1

    def detach_doc(self, name):
        before = len(self.attached_docs)
        self.attached_docs = [(n, c) for n, c in self.attached_docs if n != name]
        removed = len(self.attached_docs) < before
        if removed:
            self.saves += 1
        return removed


def _ctx(owner, project=None):
    return {
        "console": Console(file=io.StringIO(), force_terminal=False),
        "state": owner,
        "project": project,
        "command_scope": "code",
    }


def _docs(owner):
    return dict(owner.attached_docs)


def test_registration_and_howdo_is_gone():
    for repl in ("code", "chat"):
        assert find_repl_command("/howto", repl) is not None, f"/howto missing in {repl}"
    assert find_repl_command("/howto", "code").name == "howto"
    # /howdo was removed 2026-07-10 (one door: /howto) — the /shplain precedent.
    assert find_repl_command("/howdo", "code") is None
    assert find_repl_command("/howdo", "chat") is None


def test_local_content_has_framing_guide_index_and_command_index():
    body = howto._local_content("code", None)
    assert "xlii" in body
    assert "How to use xlii" in body            # the bundled guide
    assert "/howto install" in body             # topic index
    # The command listing is NAMES ONLY (howto-fast T1) — the full
    # get_repl_help block was 17 KB of prompt weight on every howto turn, and
    # per-command detail now comes from the `command_help` tool on demand.
    assert "/plan" in body and "/describe" in body
    assert "command_help" in body
    from xlii.commands import get_repl_help
    assert get_repl_help("code") not in body
    assert "SHELL" not in body                  # the help block's section headers


def test_bare_howto_attaches_guide():
    owner = _Owner()
    assert howto._howto_handler("/howto", _ctx(owner)) is True
    docs = _docs(owner)
    assert "howto" in docs
    assert "How to use xlii" in docs["howto"]


def test_rerun_refreshes_not_duplicates():
    owner = _Owner()
    howto._howto_handler("/howto", _ctx(owner))
    howto._howto_handler("/howto", _ctx(owner))
    names = [n for n, _ in owner.attached_docs]
    assert names.count("howto") == 1  # replaced, not appended twice


def test_off_detaches():
    owner = _Owner()
    howto._howto_handler("/howto", _ctx(owner))
    assert "howto" in _docs(owner)
    howto._howto_handler("/howto off", _ctx(owner))
    assert "howto" not in _docs(owner)


def test_bare_howto_enters_mode():
    owner = _Owner()
    howto._howto_handler("/howto", _ctx(owner))
    assert owner.howto_mode is True


def test_topic_attaches_shard():
    owner = _Owner()
    assert howto._howto_handler("/howto install", _ctx(owner)) is True
    content = _docs(owner)["howto"]
    assert "xlii setup" in content
    assert owner.howto_mode is True


def test_topic_alias_troubleshoot():
    # "fix" is now the /howto fix subcommand; the troubleshoot guide stays
    # reachable via its other aliases.
    owner = _Owner()
    howto._howto_handler("/howto troubleshooting", _ctx(owner))
    assert "Troubleshooting" in _docs(owner)["howto"]


def test_workflow_aliases_attach_recipe_topic():
    for alias in ("workflow", "cursor-parity", "ship", "debug-flaky"):
        owner = _Owner()
        howto._howto_handler(f"/howto {alias}", _ctx(owner))
        content = _docs(owner)["howto"]
        assert "Workflow Recipes" in content
        assert "/plan" in content and "/loop" in content


def test_latest_enters_mode(monkeypatch):
    monkeypatch.setattr(howto, "fetch_manifest", lambda: howto.load_manifest())
    monkeypatch.setattr(howto, "fetch_topic_body", lambda m, tid: f"# REMOTE {tid}\nx")
    owner = _Owner()
    howto._howto_handler("/howto latest install", _ctx(owner))
    assert owner.howto_mode is True


def test_off_exits_mode():
    owner = _Owner()
    howto._howto_handler("/howto", _ctx(owner))
    assert owner.howto_mode is True
    howto._howto_handler("/howto off", _ctx(owner))
    assert owner.howto_mode is False


def test_howto_mode_flips_routing_to_conversational():
    from types import SimpleNamespace

    from xlii.repl import _is_shell_primary
    st = SimpleNamespace(persona=None, plan_mode=False, howto_mode=False)
    assert _is_shell_primary(st) is True
    st.howto_mode = True
    assert _is_shell_primary(st) is False


def test_off_when_nothing_attached_is_noop():
    owner = _Owner()
    assert howto._howto_handler("/howto off", _ctx(owner)) is True
    assert owner.attached_docs == []


def test_latest_attaches_fetched_topic(monkeypatch):
    monkeypatch.setattr(howto, "fetch_manifest", lambda: howto.load_manifest())
    monkeypatch.setattr(
        howto, "fetch_topic_body",
        lambda m, tid: "# REMOTE README\nfresh install content",
    )
    owner = _Owner()
    assert howto._howto_handler("/howto latest install", _ctx(owner)) is True
    content = _docs(owner)["howto"]
    assert "fresh install content" in content
    assert "latest from GitHub" in content


def test_latest_fetch_failure_does_not_attach(monkeypatch):
    def boom(*a, **k):
        raise OSError("no network")

    # Stub BOTH network entry points of the bare-latest path: the tarball sync
    # (which would otherwise hit the real network — and, online, write into the
    # cache) and the legacy fetch_manifest fallback it degrades to.
    monkeypatch.setattr(howto, "sync_corpus_cache", boom)
    monkeypatch.setattr(howto, "fetch_manifest", boom)
    owner = _Owner()
    assert howto._howto_handler("/howto latest", _ctx(owner)) is True
    assert "howto" not in _docs(owner)


def test_unknown_option_is_rejected_without_attaching():
    owner = _Owner()
    assert howto._howto_handler("/howto bogus", _ctx(owner)) is True
    assert owner.attached_docs == []


def test_model_role_selection():
    from types import SimpleNamespace

    from xlii.agent import Agent
    from tests.helpers import make_cfg

    def role(conversational, howto_mode):
        stand_in = SimpleNamespace(
            cfg=make_cfg(),
            model_override=None,
            session=SimpleNamespace(conversational=conversational, howto_mode=howto_mode),
        )
        return Agent._model_role(stand_in)

    assert role(False, False) == "orchestrator"
    assert role(True, False) == "chat"
    assert role(False, True) == "help"
    # howto wins over conversational when both are set
    assert role(True, True) == "help"


def test_per_project_override(tmp_path):
    override_dir = tmp_path / "prompts"
    override_dir.mkdir(parents=True)
    (override_dir / "howto.md").write_text("CUSTOM PROJECT GUIDE")
    body = howto._local_content("code", tmp_path)
    assert "CUSTOM PROJECT GUIDE" in body
    assert "How to use xlii" not in body


# --- interrogative reconstruction (the /howto fix miss path) -----------------


def _ctx_cap(owner, project=None):
    """Like _ctx but returns the StringIO so a test can read console output."""
    sio = io.StringIO()
    ctx = {
        "console": Console(file=sio, force_terminal=False),
        "state": owner,
        "project": project,
        "command_scope": "code",
    }
    return ctx, sio


def test_reconstruct_question_variants():
    assert howto._reconstruct_question("reset my keys") == "how do I reset my keys"
    assert howto._reconstruct_question("to undo a turn") == "how to undo a turn"
    assert howto._reconstruct_question("i sync a project") == "how do I sync a project"
    # already a question — left untouched
    assert howto._reconstruct_question("how do I sync") == "how do I sync"
    assert howto._reconstruct_question("what is a persona") == "what is a persona"


def test_howto_fix_runs_doctor_then_searches(monkeypatch):
    monkeypatch.setattr(howto, "_run_doctor", lambda: None)
    owner = _Owner()
    ctx, sio = _ctx_cap(owner)
    assert howto._howto_handler("/howto fix sync", ctx) is True
    out = sio.getvalue()
    assert "xlii doctor" in out
    assert "related help" in out
    assert "troubleshoot" in out  # 'sync' is a troubleshoot tag


def test_howto_fix_no_symptom_runs_doctor_only(monkeypatch):
    calls = []
    monkeypatch.setattr(howto, "_run_doctor", lambda: calls.append(1))
    owner = _Owner()
    ctx, sio = _ctx_cap(owner)
    assert howto._howto_handler("/howto fix", ctx) is True
    assert calls == [1]
    assert "name a symptom" in sio.getvalue()
    assert owner.attached_docs == []  # diagnostic only, nothing attached


def test_howto_fix_unknown_symptom_queues_question(monkeypatch):
    monkeypatch.setattr(howto, "_run_doctor", lambda: None)
    owner = _Owner()
    assert howto._howto_handler("/howto fix zxqwv nonsense", _ctx(owner)) is True
    assert getattr(owner, "pending_input", "").startswith("how do I fix zxqwv")


def test_howto_fix_unelevated_stays_readonly(monkeypatch):
    from xlii.cmds.diag import DoctorFinding, DoctorReport

    report = DoctorReport(
        findings=[
            DoctorFinding("warn", ".xlii/ not in .gitignore", "echo '.xlii/' >> .gitignore"),
        ],
        warn_count=1,
    )
    monkeypatch.setattr(howto, "_run_doctor", lambda: report)
    owner = _Owner()
    ctx, sio = _ctx_cap(owner)
    assert howto._howto_handler("/howto fix gitignore", ctx) is True
    out = sio.getvalue()
    assert "runnable fixes" in out
    assert "echo '.xlii/' >> .gitignore" in out
    assert "read-only" in out
    assert "/admin unlock" in out


def test_howto_fix_unelevated_never_applies(monkeypatch):
    """Unelevated /howto fix stays read-only: apply_doctor_fix is NOT called, and
    the printed output keeps BOTH the human prose and the exact runnable command
    (the two-channel DoctorFinding fix — prose + fix_cmd)."""
    from xlii.cmds.diag import DoctorFinding, DoctorReport

    report = DoctorReport(
        findings=[
            DoctorFinding(
                "warn",
                ".xlii/ not in .gitignore",
                "add it so private state stays uncommitted: echo '.xlii/' >> .gitignore",
                "echo '.xlii/' >> .gitignore",
            ),
        ],
        warn_count=1,
    )
    monkeypatch.setattr(howto, "_run_doctor", lambda: report)
    calls = []
    monkeypatch.setattr(
        "xlii.doctor.apply_doctor_fix",
        lambda *a, **k: calls.append((a, k)),
    )
    owner = _Owner()  # NOT elevated
    ctx, sio = _ctx_cap(owner)
    assert howto._howto_handler("/howto fix gitignore", ctx) is True
    out = sio.getvalue()
    assert calls == []                       # gate held — never applied
    assert "read-only" in out
    assert "/admin unlock" in out
    assert "echo '.xlii/' >> .gitignore" in out   # exact command shown
    assert "private state stays uncommitted" in out  # human prose retained


def test_howto_fix_elevated_confirm_applies(monkeypatch, tmp_path):
    from xlii.cmds.diag import DoctorFinding, DoctorReport

    gi = tmp_path / ".gitignore"
    gi.write_text("node_modules/\n")
    report = DoctorReport(
        findings=[
            DoctorFinding("warn", ".xlii/ not in .gitignore", "echo '.xlii/' >> .gitignore"),
        ],
        warn_count=1,
    )
    monkeypatch.setattr(howto, "_run_doctor", lambda: report)
    monkeypatch.setattr(howto, "_confirm_fix", lambda _p: True)
    applied = []
    monkeypatch.setattr(
        "xlii.doctor.apply_doctor_fix",
        lambda fix, cwd=None: applied.append((fix, cwd)) or f"ok:{fix}",
    )
    owner = _Owner()
    owner.elevated = True
    project = type("P", (), {"project_root": tmp_path})()
    ctx, sio = _ctx_cap(owner, project=project)
    assert howto._howto_handler("/howto fix", ctx) is True
    out = sio.getvalue()
    assert "would run:" in out
    assert applied == [("echo '.xlii/' >> .gitignore", tmp_path)]
    assert "ok:echo" in out or "✓" in out


def test_howto_fix_elevated_decline_skips(monkeypatch):
    from xlii.cmds.diag import DoctorFinding, DoctorReport

    report = DoctorReport(
        findings=[DoctorFinding("bad", "perms", "chmod 600 /tmp/x")],
        problems=1,
    )
    monkeypatch.setattr(howto, "_run_doctor", lambda: report)
    monkeypatch.setattr(howto, "_confirm_fix", lambda _p: False)
    calls = []
    monkeypatch.setattr(
        "xlii.doctor.apply_doctor_fix",
        lambda *a, **k: calls.append(1),
    )
    owner = _Owner()
    owner.elevated = True
    ctx, sio = _ctx_cap(owner)
    assert howto._howto_handler("/howto fix", ctx) is True
    assert calls == []
    assert "skipped" in sio.getvalue()
