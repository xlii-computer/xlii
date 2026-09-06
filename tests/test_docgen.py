"""Pins the generated doc reference to the live code.

The substantive assertion is `check() == []` — if anyone adds/changes a
subcommand or slash command without running `python -m xlii.docgen`, the
README's generated regions go stale and this fails. That's the doc-truth ratchet
(ROADMAP 6.1; wired into CI by the documentation reset).
"""

from xlii import docgen


def test_generated_regions_are_fresh():
    stale = docgen.check()
    assert stale == [], (
        f"stale generated doc regions in {stale}; "
        "run `python -m xlii.docgen` and commit the result."
    )


def test_write_is_idempotent():
    # check() already confirms freshness; a write on top must change nothing.
    rewritten = docgen.write()
    assert rewritten == []


def test_subcommands_table_covers_every_top_level_command():
    from xlii.cli import build_parser

    table = docgen.render_subcommands()
    sub = next(a for a in build_parser()._actions
               if a.__class__.__name__ == "_SubParsersAction")
    # Aliases map extra names onto the SAME parser object (e.g. `ftp` → `remote`);
    # docgen documents each parser once under its first/canonical name, so
    # coverage is asserted per unique parser, not per choices key.
    seen: set[int] = set()
    for name, p in sub.choices.items():
        if id(p) in seen:
            continue
        seen.add(id(p))
        assert f"`xlii {name}" in table, f"{name} missing from generated subcommand table"


def test_every_documented_region_marker_exists():
    for region_id, target, _ in docgen.REGIONS:
        text = (docgen.REPO_ROOT / target).read_text()
        assert f"<!-- BEGIN GENERATED: {region_id} -->" in text
        assert f"<!-- END GENERATED: {region_id} -->" in text


def test_cli_reference_is_flag_level():
    """The deep reference renders every leaf command AND each flag's help string
    (the compact GUIDE table only carries bare flag names)."""
    from xlii.cli import build_parser

    ref = docgen.render_cli_reference()
    # every leaf command appears
    for prog, _help, _leaf in docgen._iter_leaf_commands(build_parser(), "xlii"):
        assert f"`{prog}" in ref, f"{prog} missing from cli-reference"
    # flags render with their metavar + argparse help, not just the bare name
    assert "`--dry-run`" in ref
    assert "Show what would be deleted, take no action" in ref  # gc --dry-run help
    assert "`--older-than <days>`" in ref                        # value flag → metavar
    assert "`--commit {never,each,final}`" in ref                # choices flag


def test_cli_reference_renders_command_description_when_present():
    """A command that carries a parser `description=` gets that paragraph in the
    deep reference (not just its short help), so depth added on the parser
    documents itself on regen. `gc` has one; a flagless command without a
    description still falls back to its short help."""
    ref = docgen.render_cli_reference()
    # gc's description prose (distinct from its short help one-liner)
    assert "stop paying for orphaned storage" in ref
    # export's description example survives into the reference
    assert "Round-trips with `xlii import`" in ref


def test_cli_reference_region_targets_reference_doc():
    ids = {rid: target for rid, target, _ in docgen.REGIONS}
    assert ids.get("cli-reference") == "docs/REFERENCE.md"


def test_docs_name_only_real_commands():
    """Run the CI doc-truth checker in-process (same script CI runs)."""
    import subprocess
    import sys

    script = docgen.REPO_ROOT / "scripts" / "check_docs.py"
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
