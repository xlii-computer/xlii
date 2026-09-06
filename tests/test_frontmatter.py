"""parse_frontmatter — the YAML-ish subset xlii relies on for personas, plugins,
and skills. Block-scalar support (`>` folded / `|` literal) was added so a
`description: >` no longer parses to the literal marker `">"`.
"""

from xlii.frontmatter import parse_frontmatter


def test_folded_block_scalar_joins_with_spaces():
    meta, body = parse_frontmatter(
        "---\n"
        "name: x\n"
        "description: >\n"
        "  line one\n"
        "  line two\n"
        "---\n"
        "body here\n"
    )
    assert meta["name"] == "x"
    assert meta["description"] == "line one line two"
    assert body == "body here"


def test_literal_block_scalar_preserves_newlines():
    meta, _ = parse_frontmatter("---\nsteps: |\n  a\n  b\n---\nx")
    assert meta["steps"] == "a\nb"


def test_block_scalar_followed_by_more_keys():
    meta, _ = parse_frontmatter(
        "---\n"
        "description: >\n"
        "  folded text\n"
        "model: grok-4\n"
        "---\n"
        "body"
    )
    assert meta["description"] == "folded text"
    assert meta["model"] == "grok-4"


def test_chomping_indicator_accepted():
    meta, _ = parse_frontmatter("---\ndescription: >-\n  hello\n---\nb")
    assert meta["description"] == "hello"


def test_value_starting_with_gt_but_not_a_block_scalar():
    # A real value that merely begins with '>' must not be treated as a block.
    meta, _ = parse_frontmatter("---\nk: >ready\n---\nb")
    assert meta["k"] == ">ready"


def test_no_frontmatter_passthrough():
    meta, body = parse_frontmatter("just text\nno fm")
    assert meta == {}
    assert body == "just text\nno fm"


def test_inline_and_multiline_lists_still_work():
    meta, _ = parse_frontmatter(
        "---\n"
        "a: [x, y]\n"
        "b:\n"
        "  - one\n"
        "  - two\n"
        "---\n"
        "body"
    )
    assert meta["a"] == ["x", "y"]
    assert meta["b"] == ["one", "two"]
