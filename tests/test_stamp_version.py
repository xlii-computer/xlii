"""Version stamping — MAJOR.YYDDD.TESTS, with the zodiac derived at display.

Covers the date serial, the format, the xlii-side season translation (the sign
is computed from the date, never stored), and the single-source seam; the
suite-running mint path is exercised for its REFUSAL behavior only (a stubbed
runner) — never by running the real suite inside itself.
"""

from __future__ import annotations

import datetime
import importlib.util
from pathlib import Path

import xlii

ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("stamp_version", ROOT / "scripts" / "stamp_version.py")
SV = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SV)


def test_date_serial_is_yyddd():
    assert SV.date_serial(datetime.date(2026, 7, 20)) == 26201   # day 201 of 2026
    assert SV.date_serial(datetime.date(2027, 1, 5)) == 27005    # zero-padded day


def test_date_serial_sorts_across_year_boundaries():
    assert SV.date_serial(datetime.date(2027, 1, 5)) > SV.date_serial(datetime.date(2026, 12, 31))


def test_build_version_shape_and_pep440():
    import re

    v = SV.build_version(1, datetime.date(2026, 7, 20), 4241)
    assert v == "1.26201.4241"
    assert re.fullmatch(r"\d+\.\d+\.\d+", v)                     # pure release segments


def test_version_sign_derives_the_season_from_the_date():
    assert xlii.version_sign("1.26201.4241") == "cancer"         # July 20
    assert xlii.version_sign("1.26204.5000") == "leo"            # July 23 — the cusp
    assert xlii.version_sign("1.27005.9999") == "capricorn"      # Jan 5 (wrapped sign)
    assert xlii.version_sign("1.27019.1000") == "capricorn"      # Jan 19, last day
    assert xlii.version_sign("1.27020.1000") == "aquarius"       # Jan 20
    assert xlii.version_sign("1.26356.1000") == "capricorn"      # Dec 22 2026


def test_version_animal_follows_lunar_new_year():
    assert xlii.version_animal("1.26201.4241") == "horse"       # 20 Jul 2026
    assert xlii.version_animal("1.27005.9999") == "horse"       # 5 Jan 2027 — still horse
    assert xlii.version_animal("1.26040.1") == "snake"          # 9 Feb 2026 — before CNY
    assert xlii.version_animal("1.26048.1") == "horse"          # 17 Feb 2026 — CNY
    assert xlii.version_season_line("1.26201.4241") == "cancer · year of the horse"


def test_version_sign_handles_leap_years():
    # 2028 is a leap year: day 60 = Feb 29 → pisces; in 2026 day 60 = Mar 1 → pisces too,
    # but day 366 exists only in the leap year.
    assert xlii.version_sign("1.28060.1") == "pisces"
    assert xlii.version_sign("1.28366.1") == "capricorn"         # Dec 31 2028
    assert xlii.version_sign("1.26366.1") is None                # 2026 has no day 366


def test_version_sign_rejects_non_yyddd_forms():
    assert xlii.version_sign("0.5.0") is None
    assert xlii.version_sign("1.260730.4241") is None            # the six-digit experiment
    assert xlii.version_sign("1.2630.4238+cancer") is None       # the tag experiment
    assert xlii.version_sign("1.26000.5") is None                # day 0


def test_a_full_year_of_stamps_covers_all_twelve_signs():
    d = datetime.date(2026, 1, 1)
    seen = set()
    while d.year == 2026:
        seen.add(xlii.version_sign(SV.build_version(1, d, 1)))
        d += datetime.timedelta(days=1)
    assert None not in seen
    assert len(seen) == 12


def test_current_major_reads_any_prior_form():
    assert SV.current_major("1.26201.4241") == 1
    assert SV.current_major("1.2630.4238+cancer") == 1
    assert SV.current_major("0.5.0") == 0


def test_parse_passed_reads_the_summary():
    assert SV.parse_passed("4241 passed, 5 skipped in 199.15s") == 4241
    assert SV.parse_passed("1 failed, 4207 passed in 60s") == 4207
    assert SV.parse_passed("no tests ran in 0.01s") == 0


def test_current_version_reads_the_real_source():
    v = SV.current_version((ROOT / "xlii" / "__init__.py").read_text())
    assert SV.current_major(v) >= 0


def test_red_suite_refuses_to_stamp(monkeypatch, capsys, tmp_path):
    """The mint authority: suite exit != 0 (or zero passed) refuses the stamp
    and writes nothing."""
    fake_init = tmp_path / "__init__.py"
    fake_init.write_text('__version__ = "1.26201.4241"\n')
    monkeypatch.setattr(SV, "INIT", fake_init)
    monkeypatch.setattr(SV, "run_suite", lambda: (1, 4207))   # red
    monkeypatch.setattr("sys.argv", ["stamp_version.py"])
    assert SV.main() == 1
    assert fake_init.read_text() == '__version__ = "1.26201.4241"\n'  # untouched
    assert "REFUSED" in capsys.readouterr().err


def test_green_suite_stamps_the_single_source(monkeypatch, tmp_path):
    fake_init = tmp_path / "__init__.py"
    fake_init.write_text('__version__ = "0.5.0"\n')
    monkeypatch.setattr(SV, "INIT", fake_init)
    monkeypatch.setattr(SV, "run_suite", lambda: (0, 4241))
    monkeypatch.setattr("sys.argv", ["stamp_version.py", "--set", "1"])
    assert SV.main() == 0
    stamped = SV.current_version(fake_init.read_text())
    assert stamped.startswith("1.")
    assert stamped.endswith(".4241")
    assert xlii.version_sign(stamped) is not None              # today decodes to a season


def test_major_bump_defaults_to_restamp(monkeypatch, tmp_path):
    fake_init = tmp_path / "__init__.py"
    fake_init.write_text('__version__ = "1.26201.4200"\n')
    monkeypatch.setattr(SV, "INIT", fake_init)
    monkeypatch.setattr(SV, "run_suite", lambda: (0, 4300))
    monkeypatch.setattr("sys.argv", ["stamp_version.py"])          # bare = restamp
    assert SV.main() == 0
    assert SV.current_major(SV.current_version(fake_init.read_text())) == 1
    monkeypatch.setattr("sys.argv", ["stamp_version.py", "--major"])
    assert SV.main() == 0
    assert SV.current_major(SV.current_version(fake_init.read_text())) == 2


def test_pyproject_reads_version_from_the_package():
    """pyproject must declare the dynamic seam, not a second hardcoded version."""
    text = (ROOT / "pyproject.toml").read_text()
    assert 'dynamic = ["version"]' in text
    assert 'version = {attr = "xlii.__version__"}' in text
    assert not any(line.strip().startswith('version = "0.')
                   for line in text.splitlines())
