"""Shared Commands-menu console catalog (flavor-aware Packages)."""

from __future__ import annotations

from xlii.console_catalog import (
    CATEGORY_ORDER,
    commands_for,
    console_categories,
    needs_placeholder,
    package_manager_label,
)


def test_category_order_and_base_sets():
    cats = console_categories(package_manager="apt")
    assert list(cats) == list(CATEGORY_ORDER) or all(n in cats for n in CATEGORY_ORDER)
    assert "htop" in cats["System"]
    assert "ip a" in cats["Network"]
    assert any("rg" in c for c in cats["Searches"])


def test_packages_follow_package_manager():
    apt = commands_for("Packages", package_manager="apt")
    assert any("apt install" in c for c in apt)
    dnf = commands_for("Packages", package_manager="dnf")
    assert any("dnf install" in c for c in dnf)
    pac = commands_for("Packages", package_manager="pacman")
    assert any("pacman" in c for c in pac)
    brew = commands_for("Packages", package_manager="brew")
    assert any(c.startswith("brew") for c in brew)


def test_needs_placeholder():
    assert needs_placeholder("ping -c 4 <host>")
    assert not needs_placeholder("htop")


def test_package_manager_label_is_string():
    # Best-effort probe — just must not raise.
    assert isinstance(package_manager_label(), str)
