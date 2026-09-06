"""xlii (42) — personal AI substrate. Grok + xAI Collections, user-curated personas / docs / plugins, multi-key swarm, plan mode, hallucination guards. Complements Grok Build; keeps its own Emacs-like soul."""

__version__ = "0.26249.5831"

# Version format MAJOR.<YY><DDD>.<TESTS> — year, day-of-year, green-test count
# (scripts/stamp_version.py mints; a red suite refuses to stamp).
# MAJOR 0 = alpha (not public). MAJOR 1 = public / community-viable — stamp
# with ``--set 1`` only when that gate is real. The version stores NO zodiac —
# tropical sign + Chinese animal are derived from the date at display time:
# "xlii 0.26201.4241 cancer · year of the horse".
_SIGN_STARTS = (
    (1, 20, "aquarius"), (2, 19, "pisces"), (3, 21, "aries"),
    (4, 20, "taurus"), (5, 21, "gemini"), (6, 21, "cancer"),
    (7, 23, "leo"), (8, 23, "virgo"), (9, 23, "libra"),
    (10, 23, "scorpio"), (11, 22, "sagittarius"), (12, 22, "capricorn"),
)
_ANIMALS = (
    "rat", "ox", "tiger", "rabbit", "dragon", "snake",
    "horse", "goat", "monkey", "rooster", "dog", "pig",
)
# Gregorian (month, day) of Chinese New Year — animal year starts here.
_CNY = {
    2016: (2, 8), 2017: (1, 28), 2018: (2, 16), 2019: (2, 5),
    2020: (1, 25), 2021: (2, 12), 2022: (2, 1), 2023: (1, 22),
    2024: (2, 10), 2025: (1, 29), 2026: (2, 17), 2027: (2, 6),
    2028: (1, 26), 2029: (2, 13), 2030: (2, 3), 2031: (1, 23),
    2032: (2, 11), 2033: (1, 31), 2034: (2, 19), 2035: (2, 8),
    2036: (1, 28),
}


def version_stamp(version: "str | None" = None):
    """``(major, date, tests)`` for a YYDDD stamp, else ``None``."""
    import datetime as _dt
    import re

    v = version if version is not None else __version__
    m = re.match(r"^(\d+)\.(\d{2})(\d{3})\.(\d+)$", v)
    if not m:
        return None
    major, yy, ddd, tests = (
        int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)),
    )
    try:
        day = _dt.date(2000 + yy, 1, 1) + _dt.timedelta(days=ddd - 1)
    except OverflowError:
        return None
    if ddd < 1 or day.year != 2000 + yy:
        return None
    return (major, day, tests)


def version_sign(version: "str | None" = None) -> "str | None":
    """The tropical sign a stamp's date falls in — ``"cancer"`` for
    ``0.26201.4241`` (day 201 of 2026 = July 20) — or ``None`` for any
    non-YYDDD form. Display-only: the zodiac lives in this translation,
    never in the version string."""
    stamp = version_stamp(version)
    if stamp is None:
        return None
    _major, date, _tests = stamp
    current = _SIGN_STARTS[-1][2]          # Jan 1–19 = capricorn (wrapped)
    for month, day, name in _SIGN_STARTS:
        if (date.month, date.day) >= (month, day):
            current = name
    return current


def version_animal(version: "str | None" = None) -> "str | None":
    """Chinese zodiac animal for the stamp date (lunar new year), or ``None``."""
    stamp = version_stamp(version)
    if stamp is None:
        return None
    _major, day, _tests = stamp
    year = day.year
    cny = _CNY.get(year)
    if cny and (day.month, day.day) < cny:
        year -= 1
    return _ANIMALS[(year - 4) % 12]


def version_season_line(version: "str | None" = None) -> str:
    """Display line: ``cancer · year of the horse``. Empty when unparseable."""
    sign = version_sign(version)
    animal = version_animal(version)
    if sign and animal:
        return f"{sign} · year of the {animal}"
    if sign:
        return sign
    if animal:
        return f"year of the {animal}"
    return ""


from xlii.context import DeepContext, load_deep_context, save_deep_context, list_deep_contexts  # noqa: E402,F401
