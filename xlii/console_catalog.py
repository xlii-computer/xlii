"""OS console shortcut catalog — Commands menu (TUI + face).

Shared kernel table of *nix recipes grouped by category. Packages lines are
filled from :func:`xlii.system_profile.load_system_profile` so apt/dnf/pacman/
brew/apk hosts get matching install/update/search templates.

Deliberately NOT file ops (explorer territory) and NOT git (git panel).
"""

from __future__ import annotations

from typing import Optional

# Categories shown in Commands → <name>… (order matters for menus).
CATEGORY_ORDER: tuple[str, ...] = ("System", "Network", "Packages", "Searches")

_SYSTEM: tuple[str, ...] = (
    "htop",
    "df -h",
    "free -h",
    "du -sh * | sort -hr",
    "fastfetch",
)

_NETWORK: tuple[str, ...] = (
    "ip a",
    "ss -tuln",
    "ping -c 4 <host>",
    "curl -I <url>",
)

_SEARCHES: tuple[str, ...] = (
    'rg "<pattern>"',
    'fd "<name>"',
    'grep -rn "<term>" .',
)


def _packages_for_mgr(mgr: str) -> tuple[str, ...]:
    m = (mgr or "").strip().lower()
    if m in ("apt",):
        return (
            "sudo apt update && sudo apt upgrade -y",
            "sudo apt install <pkg>",
            "sudo apt search <term>",
        )
    if m in ("dnf",):
        return (
            "sudo dnf upgrade -y",
            "sudo dnf install <pkg>",
            "sudo dnf search <term>",
        )
    if m in ("yum",):
        return (
            "sudo yum update -y",
            "sudo yum install <pkg>",
            "sudo yum search <term>",
        )
    if m in ("pacman",):
        return (
            "sudo pacman -Syu",
            "sudo pacman -S <pkg>",
            "pacman -Ss <term>",
        )
    if m in ("zypper",):
        return (
            "sudo zypper refresh && sudo zypper update -y",
            "sudo zypper install <pkg>",
            "zypper search <term>",
        )
    if m in ("brew",):
        return (
            "brew update && brew upgrade",
            "brew install <pkg>",
            "brew search <term>",
        )
    if m in ("apk",):
        return (
            "sudo apk update && sudo apk upgrade",
            "sudo apk add <pkg>",
            "apk search <term>",
        )
    if m in ("nix",):
        return (
            "nix-channel --update",
            "nix-env -iA nixpkgs.<pkg>",
            "nix search nixpkgs <term>",
        )
    # Unknown — soft apt-shaped defaults (common on servers) + honest note via empty first?
    return (
        "sudo apt update && sudo apt upgrade -y",
        "sudo apt install <pkg>",
        "sudo apt search <term>",
    )


def package_manager_label() -> str:
    """Detected package manager id, or empty string."""
    try:
        from xlii.system_profile import load_system_profile

        prof = load_system_profile()
        return (getattr(prof, "package_manager", None) or "") if prof else ""
    except Exception:
        return ""


def console_categories(*, package_manager: Optional[str] = None) -> dict[str, tuple[str, ...]]:
    """Category name → ordered shell command templates.

    When *package_manager* is None, probe via system_profile (best-effort).
    """
    mgr = package_manager if package_manager is not None else package_manager_label()
    return {
        "System": _SYSTEM,
        "Network": _NETWORK,
        "Packages": _packages_for_mgr(mgr),
        "Searches": _SEARCHES,
    }


def category_names() -> tuple[str, ...]:
    return CATEGORY_ORDER


def commands_for(category: str, *, package_manager: Optional[str] = None) -> tuple[str, ...]:
    return console_categories(package_manager=package_manager).get(category, ())


def needs_placeholder(cmd: str) -> bool:
    """True when the recipe still needs a ``<placeholder>`` filled in."""
    return "<" in (cmd or "")
