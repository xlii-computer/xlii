"""The check_docs guardrail validates command references inside docs/help/**."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_docs import (  # noqa: E402
    _command_ref_errors,
    _real_subcommands,
    check_help_menu,
)
from xlii.repl_cmds import register_all  # noqa: E402

register_all()


def test_validator_flags_bogus_but_not_real():
    subs = _real_subcommands()
    text = (
        "Use `/loop` to walk away, `/describe rail` for detail, and `xlii doctor` "
        "to diagnose. But `/notarealcmd` and `xlii blarg` do not exist."
    )
    errs = " ".join(_command_ref_errors(text, "docs/help/topics/x.md", subs))
    assert "/notarealcmd" in errs
    assert "xlii blarg" in errs
    # real commands must not be flagged
    assert "`/loop`" not in errs
    assert "`/describe`" not in errs
    assert "xlii doctor" not in errs


def test_help_menu_ratchet_is_clean():
    assert check_help_menu() == []


def test_validator_ignores_non_command_inline_code():
    subs = _real_subcommands()
    text = "Config lives at `~/.config/xlii`; the model is `grok-4`; see `main`."
    assert _command_ref_errors(text, "x.md", subs) == []
