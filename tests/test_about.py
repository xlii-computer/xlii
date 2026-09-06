"""About credits + Help menu howto rows."""

from datetime import date

from xlii.about import (
    TAGLINES,
    about_lines,
    about_payload,
    howto_menu_rows,
    tagline,
)


def test_tagline_rotates_by_day():
    a = tagline(when=date(2026, 1, 1))
    b = tagline(when=date(2026, 1, 2))
    assert a in TAGLINES and b in TAGLINES
    assert a != b
    assert tagline(when=date(2026, 1, 1)) == a


def test_about_lines_credit_hello():
    lines = about_lines(when=date(2026, 8, 16))
    assert lines[0].startswith("xlii")
    assert lines[1] == "Say hello — hello@xlii.computer"
    assert "Nick" not in "\n".join(lines)
    assert lines[-1] in TAGLINES
    body = "\n".join(lines[2:-1])
    assert " · alpha" in body or " · public" in body
    assert "release date " in body
    assert "tests green" in body


def test_about_payload_shape():
    p = about_payload()
    assert p["product"] == "xlii"
    assert p["credit"] == "Say hello — hello@xlii.computer"
    assert p["line"] in p["taglines"]
    assert len(p["taglines"]) == len(TAGLINES)
    assert " · " in p["version"]
    assert p["version"].endswith("alpha") or p["version"].endswith("public")
    facts = "\n".join(p["facts"])
    assert "release date " in facts
    assert "tests green" in facts
    assert "modules" in facts
    assert "year of the" in p["signs"]
    assert all(not row.startswith("0.") for row in p["facts"])


def test_howto_menu_rows_skip_index():
    rows = howto_menu_rows()
    ids = [r["id"] for r in rows]
    assert "index" not in ids
    assert "install" in ids
    assert "first-session" in ids
    assert all(r.get("title") for r in rows)


def test_howto_menu_rows_are_short_and_grouped():
    from xlii.help_corpus import HELP_MENU_GROUPS, HELP_MENU_MAX

    rows = howto_menu_rows()
    assert rows
    assert all(r.get("menu") and len(r["menu"]) <= HELP_MENU_MAX for r in rows)
    assert all(" — " not in r["menu"] for r in rows)
    groups = [r["group"] for r in rows if r.get("group")]
    assert set(groups) <= set(HELP_MENU_GROUPS)
    # start before work before more
    rank = {n: i for i, n in enumerate(HELP_MENU_GROUPS)}
    idxs = [rank[g] for g in groups]
    assert idxs == sorted(idxs)
