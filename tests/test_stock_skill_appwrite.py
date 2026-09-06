"""Stock Appwrite skill — ships, loads, routes through the plugin."""

from __future__ import annotations

from pathlib import Path

import xlii
from xlii.skills import load_skills, stock_skills_dir


def test_stock_skill_file_exists():
    md = Path(xlii.__file__).parent / "stock_skills" / "appwrite" / "SKILL.md"
    assert md.is_file()
    text = md.read_text(encoding="utf-8")
    assert "name: appwrite" in text
    assert "/plugin call" in text
    assert "cloud.appwrite.io" in text  # named as what NOT to hardcode
    assert "APPWRITE_JWT" in text
    assert "multipart" in text.lower()
    assert "Vault.unlock" in text
    assert "no `create_file`" in text
    # Secrets stay names.
    assert "sk-" not in text.lower()
    assert "key_demo" not in text


def test_stock_skill_loads_as_stock():
    assert (stock_skills_dir() / "appwrite" / "SKILL.md").is_file()
    skills = load_skills(import_foreign=False)
    assert "appwrite" in skills
    s = skills["appwrite"]
    assert s.scope == "stock"
    assert s.name == "appwrite"
    assert "plugin" in s.description.lower()
    assert "self-hosted" in s.description.lower() or "self hosted" in s.description.lower()
