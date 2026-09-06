"""The coverage ratchet: 'no fake commands' → 'no undocumented commands'.

Every top-level subcommand and every registered slash command must be named in
some hand-written doc or help page. These tests pin the pure diff (teeth +
allowlist behaviour) and assert the live tree is fully covered.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_docs  # noqa: E402
from check_docs import (  # noqa: E402
    _coverage_errors,
    _referenced_commands,
    _strip_generated,
    check_command_coverage,
)
from xlii.repl_cmds import register_all  # noqa: E402

register_all()


def test_flags_undocumented_subcommand_and_slash():
    errs = _coverage_errors(
        ref_subs=set(), ref_slash=set(),
        all_subs={"ghostsub"}, all_slash={"ghostslash"},
    )
    joined = " ".join(errs)
    assert "xlii ghostsub" in joined
    assert "/ghostslash" in joined


def test_documented_command_passes():
    assert _coverage_errors(
        ref_subs={"foo"}, ref_slash={"bar"},
        all_subs={"foo"}, all_slash={"bar"},
    ) == []


def test_allowlist_grandfathers_a_gap(monkeypatch):
    monkeypatch.setattr(check_docs, "COVERAGE_ALLOWLIST_SUBS", {"legacy"})
    # `legacy` is a real subcommand, undocumented, but allowlisted → no error.
    assert _coverage_errors(set(), set(), {"legacy"}, set()) == []


def test_stale_allowlist_entry_is_reported(monkeypatch):
    monkeypatch.setattr(check_docs, "COVERAGE_ALLOWLIST_SUBS", {"nowdocumented"})
    # It became documented — the allowlist must shrink, so this is flagged.
    errs = _coverage_errors({"nowdocumented"}, set(), {"nowdocumented"}, set())
    assert any("allowlisted but now documented" in e for e in errs)


def test_allowlist_entry_for_gone_command_is_reported(monkeypatch):
    monkeypatch.setattr(check_docs, "COVERAGE_ALLOWLIST_SLASH", {"deleted"})
    errs = _coverage_errors(set(), set(), set(), set())  # 'deleted' no longer exists
    assert any("no longer a slash" in e for e in errs)


def test_generated_regions_do_not_count_as_coverage():
    # A command mentioned ONLY inside a generated region is not "documented".
    text = (
        "<!-- BEGIN GENERATED: x -->\n"
        "| `xlii ghostonly` | — | ... |\n"
        "<!-- END GENERATED: x -->\n"
    )
    subs, _ = _referenced_commands(_strip_generated(text))
    assert "ghostonly" not in subs
    # …but the same reference outside the region does count.
    subs2, _ = _referenced_commands("Run `xlii ghostonly` to do the thing.")
    assert "ghostonly" in subs2


def test_live_tree_is_fully_covered():
    """The whole point: on this branch every command is documented."""
    assert check_command_coverage() == []
