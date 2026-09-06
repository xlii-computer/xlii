"""Pins the built-in slash-command registration contract.

Registration used to be an import side effect of xlii.cli; it is now an
explicit `register_all()`. This test locks in (a) that every built-in command
is registered, (b) that per-REPL scoping holds (code-only vs chat-only, and the
two distinct /status commands), and (c) that register_all() is idempotent.

Dependency-free enough to run directly:
    ./venv/bin/python -m pytest tests/test_command_registration.py
"""

from xlii.commands import find_repl_command, iter_repl_commands
from xlii.repl_cmds import register_all
from xlii.repl_cmds.chat import _chat_status_handler
from xlii.repl_cmds.code import _code_status_handler

register_all()


# (token, repls it must resolve in)  — drives the bulk of the assertions
EXPECTED = [
    # cross-REPL session / mode / knowledge / meta commands
    ("reset", {"code", "chat"}),
    ("yolo", {"code", "chat"}),
    ("safe", {"code", "chat"}),
    ("cost", {"code", "chat"}),
    ("sync", {"code", "chat"}),
    ("iterations", {"code", "chat"}),
    ("plan", {"code", "chat"}),
    ("execute", {"code", "chat"}),
    ("cancel", {"code", "chat"}),
    ("attach", {"code", "chat"}),   # the Fold: /attach doc|ref|bookmark umbrella
    ("detach", {"code", "chat"}),   # the Fold: unifies /undoc + /unref
    ("ref", {"code", "chat"}),      # now a hidden alias of /recall
    ("doc", {"code", "chat"}),      # now a hidden alias of /attach
    ("lib", {"code", "chat"}),
    ("get", {"code", "chat"}),
    ("help", {"code", "chat"}),
    ("describe", {"code", "chat"}),
    ("howto", {"code", "chat"}),     # self-guide loader
    ("image", {"code", "chat"}),     # multimodal mode + inline preview
    ("imagine", {"code", "chat"}),   # xAI Imagine artifact generation
    ("loadout", {"code", "chat"}),  # RP5: a loadout is cross-mode (was chat-only)
    ("mark", {"code", "chat"}),     # RP6: marks work in any profile (were chat-only)
    ("marks", {"code", "chat"}),
    ("recall", {"code", "chat"}),
    # code-only
    ("rail", {"code"}),
    ("models", {"code", "chat"}),
    ("model", {"code", "chat"}),
    ("temp", {"code"}),
    ("project", {"code"}),
    # chat-only
    ("personas", {"chat"}),
    ("persona", {"chat"}),
    ("edit", {"code", "chat"}),
    ("forget", {"chat"}),
    # commands.py's own built-ins — folded into register_all() in R4, so they
    # must be present after register_all() and absent before it.
    ("commands", {"code", "chat"}),
    ("tools", {"code", "chat"}),
    ("workspace", {"code", "chat"}),
    ("attachments", {"code", "chat"}),
    ("context", {"code", "chat"}),
    ("interactive", {"code", "chat"}),  # full-screen program list (mc/vim/htop + !!)
    ("sweep", {"code", "chat"}),  # throne housekeep
    ("home", {"code", "chat"}),  # /panel home alias
]

ALL_REPLS = {"code", "chat"}


def test_every_builtin_resolves_in_its_scope():
    for token, scopes in EXPECTED:
        for repl in scopes:
            assert find_repl_command(f"/{token}", repl) is not None, \
                f"/{token} should resolve in {repl}"
        for repl in ALL_REPLS - scopes:
            assert find_repl_command(f"/{token}", repl) is None, \
                f"/{token} should NOT resolve in {repl}"


def test_status_is_scoped_to_distinct_handlers():
    code_status = find_repl_command("/status", "code")
    chat_status = find_repl_command("/status", "chat")
    assert code_status is not None and chat_status is not None
    assert code_status.handler is _code_status_handler
    assert chat_status.handler is _chat_status_handler


def test_aliases_resolve():
    assert find_repl_command("/unref", "code") is not None       # /recall alias (the Fold)
    assert find_repl_command("/undoc", "code") is not None       # /detach alias (the Fold)
    assert find_repl_command("/iter", "code") is not None        # iterations alias
    assert find_repl_command("/yolo!", "chat") is not None       # yolo alias
    assert find_repl_command("/projects", "code").name == "project"
    assert find_repl_command("/shum", "code").name == "sh"
    assert find_repl_command("/explain", "code").name == "sh"


def test_fold_consolidation_alias_map():
    """The Fold (Vector A): absorbed spellings resolve to the consolidated verbs,
    and the consolidated verbs are the visible primaries."""
    # /doc + /undoc ride the /attach + /detach umbrellas as hidden aliases.
    assert find_repl_command("/doc", "code").name == "attach"
    assert find_repl_command("/undoc", "code").name == "detach"
    # /ref + /unref are hidden aliases of the honestly-named /recall.
    assert find_repl_command("/ref", "code").name == "recall"
    assert find_repl_command("/unref", "code").name == "recall"
    # /personas folds into /persona (chat-only).
    assert find_repl_command("/personas", "chat").name == "persona"
    assert find_repl_command("/personas", "code") is None
    # The absorbed spellings are aliases, never their own primaries — so /help
    # (which walks primary names) shows only the consolidated verbs.
    primaries = {c.name for c in iter_repl_commands()}
    assert {"attach", "detach", "recall", "persona"} <= primaries
    assert not ({"undoc", "unref", "personas"} & primaries)


def test_register_all_is_idempotent():
    # A second call must not raise (the registry hard-errors on duplicate names).
    register_all()
    register_all()
    assert find_repl_command("/help", "code") is not None


def test_conversational_flag_round_trips():
    assert find_repl_command("/cursor", "code").conversational is True
    assert find_repl_command("/delegate", "code").conversational is True
    assert find_repl_command("/claude", "code").conversational is True
    assert find_repl_command("/grok-build", "code").conversational is True
    assert find_repl_command("/help", "code").conversational is False


def test_registration_is_not_an_import_side_effect():
    """Importing the command modules must register nothing on its own — only an
    explicit register_all() should populate the registry (the R4 contract)."""
    import subprocess
    import sys
    out = subprocess.run(
        [sys.executable, "-c",
         ("import xlii.cli, xlii.commands, xlii.repl_cmds;"
          "from xlii.commands import _REPL_COMMANDS;"
          "print(len(_REPL_COMMANDS))")],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "0", f"import registered {out.stdout.strip()} commands"


if __name__ == "__main__":
    test_every_builtin_resolves_in_its_scope()
    test_status_is_scoped_to_distinct_handlers()
    test_aliases_resolve()
    test_register_all_is_idempotent()
    print("ok")


def test_dispatch_crash_printer_survives_markup_shaped_errors():
    """Regression: a handler exception whose message contains markup-shaped text
    (a literal "[/path]") crashed the crash printer itself and killed the REPL.
    The printer must escape the exception text and keep the session alive."""
    import io

    from rich.console import Console

    from xlii.commands import (
        REPLCommand,
        dispatch_repl_command,
        register_repl_command,
        unregister_repl_command,
    )

    def _boom(line, ctx):
        raise RuntimeError("closing tag '[/path]' lookalike")

    register_repl_command(REPLCommand(name="crashy-markup-test", handler=_boom,
                                      description="test-only", category="general"))
    try:
        con = Console(file=io.StringIO(), force_terminal=False, width=200)
        assert dispatch_repl_command("/crashy-markup-test", {"console": con}) is True
        out = con.file.getvalue()
        assert "crashed" in out and "[/path]" in out
    finally:
        # The registry is process-global — a leaked test command makes the docgen
        # freshness tests (same pytest process) see a "stale" command reference.
        unregister_repl_command("crashy-markup-test")
