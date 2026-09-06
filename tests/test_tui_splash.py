"""Unit tests for the Textual startup splash (no textual dependency)."""

from rich.console import Console

from xlii.tui.splash import (
    _NFO_FILENAME,
    _NFO_MAX_LINES,
    _TOP_PAD_LINES,
    _XLII,
    default_splash_text,
    ensure_setup_splash,
    read_nfo,
    resolve_splash_nfo,
    splash_renderable,
)


def _render(**kwargs) -> str:
    out = Console(width=80, record=True)
    out.print(splash_renderable(**kwargs))
    return out.export_text()


def test_splash_renders_xlii_wordmark():
    text = _render(project="demo")
    assert "demo" in text
    assert "/help" in text
    assert "/howto" in text
    assert "the answer, at the command line" in text  # 42 tagline, not an acronym
    assert "╚███╔╝" in text  # the XLII slant-X
    assert " ·" in text       # the Roman-numeral 42 wink in the wordmark


def test_splash_logo_is_xlii():
    logo = "\n".join(_XLII)
    assert "╚███╔╝" in logo            # X crossing
    assert logo.rstrip().endswith("·")  # the 42 wink
    assert "IXAAC" not in logo


def test_splash_has_top_padding():
    text = _render()
    logo_at = text.index(_XLII[0].strip())
    assert text[:logo_at].count("\n") >= _TOP_PAD_LINES


# --- .nfo override (the "make it your own" drop-in) ---------------------------------------


def test_nfo_replaces_the_whole_splash_verbatim():
    text = _render(project="demo", nfo_text="MY ART\nline2")
    assert "MY ART" in text
    assert "line2" in text
    # Verbatim override owns the canvas — no wordmark, no code tagline, no hint chrome.
    assert "╚███╔╝" not in text
    assert "the answer, at the command line" not in text
    assert "/help" not in text


def test_none_nfo_keeps_the_wordmark():
    # Regression guard: absence of an override is exactly today's splash.
    text = _render(project="demo", nfo_text=None)
    assert "╚███╔╝" in text
    assert "the answer, at the command line" in text


def test_resolve_precedence_project_beats_root_beats_global(tmp_path, monkeypatch):
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    monkeypatch.setenv("XLII_CONFIG_DIR", str(global_dir))
    (global_dir / "splash.nfo").write_text("global")

    project_root = tmp_path / "proj"
    xli_dir = project_root / ".xlii"
    xli_dir.mkdir(parents=True)
    (project_root / "xlii.nfo").write_text("root")
    (xli_dir / "splash.nfo").write_text("project")

    # All three present → per-project wins.
    got = resolve_splash_nfo(project_root=project_root, xli_dir=xli_dir)
    assert got == xli_dir / "splash.nfo"

    # Drop the project one → repo-root wins.
    (xli_dir / "splash.nfo").unlink()
    got = resolve_splash_nfo(project_root=project_root, xli_dir=xli_dir)
    assert got == project_root / "xlii.nfo"

    # Drop the root one → global fallback.
    (project_root / "xlii.nfo").unlink()
    got = resolve_splash_nfo(project_root=project_root, xli_dir=xli_dir)
    assert got == global_dir / "splash.nfo"


def test_resolve_returns_none_when_nothing_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path / "empty"))
    assert resolve_splash_nfo(project_root=tmp_path, xli_dir=tmp_path / ".xlii") is None


def test_read_nfo_missing_and_oversized(tmp_path):
    assert read_nfo(tmp_path / "nope.nfo") is None

    good = tmp_path / "good.nfo"
    good.write_text("art\n")
    assert read_nfo(good) == "art"  # trailing newline stripped

    huge = tmp_path / "huge.nfo"
    huge.write_text("x\n" * (_NFO_MAX_LINES + 5))
    assert read_nfo(huge) is None


# --- shipped default + setup-folder seeding ----------------------------------------------


def test_bundled_default_ships_and_renders():
    # The shipped standard splash (xlii/tui/splash.nfo) is readable via importlib.resources and
    # carries the wordmark markers the default UI relies on.
    txt = default_splash_text()
    assert txt is not None
    assert "╚███╔╝" in txt
    assert "the answer, at the command line" in txt
    assert "/help" in txt and "/howto" in txt


def test_ensure_setup_splash_seeds_then_leaves_custom_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path))  # an existing dir
    target = tmp_path / _NFO_FILENAME
    assert not target.exists()

    ensure_setup_splash()  # first run seeds the shipped default
    assert target.exists()
    assert target.read_text().rstrip("\n") == default_splash_text()

    target.write_text("MY OWN ART\n")  # user hacks it
    ensure_setup_splash()  # idempotent — never clobbers a customized file
    assert target.read_text() == "MY OWN ART\n"


def test_ensure_setup_splash_noop_without_config_dir(tmp_path, monkeypatch):
    missing = tmp_path / "nope"  # deliberately not created
    monkeypatch.setenv("XLII_CONFIG_DIR", str(missing))
    ensure_setup_splash()
    assert not missing.exists()  # we never create the dir just to seed
