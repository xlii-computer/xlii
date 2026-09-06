"""Host OS / distro environment profile for platform-correct shell guidance.

Detected once per process (in-memory memo) and injected as a ``[SYSTEM]`` addendum
in the code system prompt (terminal-native-toolkit Phase 2). Use ``/os --refresh``
to re-probe within a session.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Wall-clock budget for the full probe pass (each subprocess is also capped).
_DETECT_BUDGET_S = 8.0

# Cross-distro package renames the model often gets wrong on Debian derivatives.
_DEBIAN_LIKE_PKG_HINTS: dict[str, str] = {
    "fd": "fd-find",
    "bat": "batcat",
    "python": "python3",
    "pip": "python3-pip",
    "rg": "ripgrep",
}

_PKG_PROBE_ORDER = (
    ("apt", "apt"),
    ("pacman", "pacman"),
    ("dnf", "dnf"),
    ("yum", "yum"),
    ("zypper", "zypper"),
    ("brew", "brew"),
    ("apk", "apk"),
    ("nix-env", "nix"),
)

_INIT_PROBE_ORDER = (
    ("systemctl", "systemd"),
    ("launchctl", "launchd"),
    ("rc-service", "openrc"),
)

_session_cache: Optional["SystemProfile"] = None


@dataclass
class SystemProfile:
    os_name: str = ""
    os_id: str = ""
    os_like: list[str] = field(default_factory=list)
    os_version: str = ""
    kernel: str = ""
    package_manager: str = ""
    init_system: str = ""
    coreutils: str = ""  # GNU | BSD | busybox | unknown
    shell: str = ""
    terminal: str = ""
    container: str = ""  # docker | wsl | …
    selinux: str = ""
    remote: str = ""  # ssh when SSH_CONNECTION is set
    detected_at: str = ""

    def debian_like(self) -> bool:
        like = {x.lower() for x in self.os_like}
        oid = self.os_id.lower()
        return oid in {"debian", "ubuntu", "parrot", "kali", "linuxmint", "pop"} or "debian" in like


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_os_release() -> dict[str, str]:
    path = Path("/etc/os-release")
    if not path.is_file():
        return {}
    data: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        data[key] = val
    return data


def _probe_package_manager() -> str:
    for binary, label in _PKG_PROBE_ORDER:
        if shutil.which(binary):
            return label
    return ""


def _probe_init_system() -> str:
    for binary, label in _INIT_PROBE_ORDER:
        if shutil.which(binary):
            return label
    return ""


def _probe_coreutils() -> str:
    try:
        proc = subprocess.run(
            ["ls", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    first = (proc.stdout or proc.stderr or "").splitlines()[:1]
    if not first:
        return "unknown"
    line = first[0]
    lower = line.lower()
    if "gnu" in lower:
        return "GNU"
    if "busybox" in lower:
        return "busybox"
    if "bsd" in lower or platform.system() == "Darwin":
        return "BSD"
    return "unknown"


def _probe_container() -> str:
    if os.environ.get("WSL_DISTRO_NAME"):
        return f"wsl:{os.environ.get('WSL_DISTRO_NAME', '').strip()}"
    cgroup = Path("/proc/1/cgroup")
    if cgroup.is_file():
        body = cgroup.read_text(encoding="utf-8", errors="replace")
        if "docker" in body:
            return "docker"
        if "kubepods" in body:
            return "kubernetes"
    return ""


def _probe_selinux() -> str:
    if not shutil.which("getenforce"):
        return ""
    try:
        proc = subprocess.run(
            ["getenforce"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "").strip()


def _probe_remote() -> str:
    conn = os.environ.get("SSH_CONNECTION", "").strip()
    if not conn:
        return ""
    parts = conn.split()
    if len(parts) >= 3:
        return f"ssh:{parts[0]}→{parts[2]}"
    return "ssh"


def _macos_version() -> tuple[str, str, str]:
    try:
        proc = subprocess.run(
            ["sw_vers"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "macOS", "darwin", ""
    product = version = ""
    for line in (proc.stdout or "").splitlines():
        if line.startswith("ProductName:"):
            product = line.split(":", 1)[1].strip()
        elif line.startswith("ProductVersion:"):
            version = line.split(":", 1)[1].strip()
    return product or "macOS", "darwin", version


def detect() -> SystemProfile:
    """Run cheap host probes and return a SystemProfile."""
    started = time.monotonic()
    os_release = _read_os_release()
    pretty = os_release.get("PRETTY_NAME") or os_release.get("NAME") or ""
    os_id = os_release.get("ID") or ""
    os_like_raw = os_release.get("ID_LIKE") or ""
    os_like = [x for x in re.split(r"[\s,]+", os_like_raw) if x]
    os_version = os_release.get("VERSION_ID") or ""

    if platform.system() == "Darwin":
        pretty, os_id, os_version = _macos_version()
        if "darwin" not in os_like:
            os_like = ["darwin"]

    kernel = platform.uname().machine
    sysname = platform.system()
    release = platform.release()
    kernel_line = f"{sysname} {release} {kernel}".strip()

    def _over_budget() -> bool:
        return (time.monotonic() - started) > _DETECT_BUDGET_S

    pkg = "" if _over_budget() else _probe_package_manager()
    init = "" if _over_budget() else _probe_init_system()
    core = "unknown" if _over_budget() else _probe_coreutils()
    container = "" if _over_budget() else _probe_container()
    selinux = "" if _over_budget() else _probe_selinux()
    remote = "" if _over_budget() else _probe_remote()

    return SystemProfile(
        os_name=pretty or sysname,
        os_id=os_id or sysname.lower(),
        os_like=os_like,
        os_version=os_version,
        kernel=kernel_line,
        package_manager=pkg,
        init_system=init,
        coreutils=core,
        shell=Path(os.environ.get("SHELL", "")).name or "sh",
        terminal=os.environ.get("TERM", "").strip(),
        container=container,
        selinux=selinux,
        remote=remote,
        detected_at=_now_iso(),
    )


def _pkg_hint_line(profile: SystemProfile) -> str:
    if not profile.debian_like():
        return ""
    hints = ", ".join(f"{k}→{v}" for k, v in sorted(_DEBIAN_LIKE_PKG_HINTS.items()))
    return f" Debian-like pkg renames: {hints}."


def summary_line(profile: SystemProfile) -> str:
    """One-line ``[SYSTEM]`` blurb for the code system prompt."""
    like = ""
    if profile.os_like:
        like = f" ({profile.os_like[0]}-like)"
    bits = [
        f"{profile.os_name}{like}",
        f"pkg={profile.package_manager or '?'}",
        f"init={profile.init_system or '?'}",
        f"coreutils={profile.coreutils or '?'}",
        f"shell={profile.shell or '?'}",
    ]
    if profile.container:
        bits.append(f"env={profile.container}")
    if profile.selinux and profile.selinux.lower() not in ("disabled", "permissive"):
        bits.append(f"selinux={profile.selinux}")
    if profile.remote:
        bits.append(profile.remote)
    line = " · ".join(bits)
    line += (
        ". Generate commands for THIS platform; never assume macOS/BSD flags"
        " or generic package names."
    )
    try:
        from xlii.desk import downloads_dir

        line += f" Drop zone={downloads_dir()} (user downloads; not CWD)."
    except Exception:
        # The drop-zone sentence is omitted; the rest of the summary still stands.
        pass
    line += _pkg_hint_line(profile)
    return line


def format_profile(profile: SystemProfile) -> str:
    """Multi-line display for ``/os``."""
    lines = [
        f"  OS:           {profile.os_name} ({profile.os_id} {profile.os_version})".rstrip(),
        f"  Kernel:       {profile.kernel}",
        f"  Package mgr:  {profile.package_manager or '(none detected)'}",
        f"  Init:         {profile.init_system or '(none detected)'}",
        f"  Coreutils:    {profile.coreutils or 'unknown'}",
        f"  Shell:        {profile.shell or '?'}",
        f"  Terminal:     {profile.terminal or '?'}",
    ]
    if profile.container:
        lines.append(f"  Container:    {profile.container}")
    if profile.selinux:
        lines.append(f"  SELinux:      {profile.selinux}")
    if profile.remote:
        lines.append(f"  Remote:       {profile.remote}")
    if profile.detected_at:
        lines.append(f"  Detected:     {profile.detected_at}")
    hint = _pkg_hint_line(profile).strip()
    if hint:
        lines.append(f"  Hints:        {hint}")
    return "\n".join(lines)


def clear_session_profile() -> None:
    """Drop the in-memory memo (tests and explicit refresh)."""
    global _session_cache
    _session_cache = None


def load_system_profile(*, refresh: bool = False) -> Optional[SystemProfile]:
    """Return the session memo, detecting on first use. Returns None on failure."""
    global _session_cache
    if refresh:
        _session_cache = None
    if _session_cache is not None:
        return _session_cache
    try:
        _session_cache = detect()
    except Exception:
        return None
    return _session_cache


def refresh_system_profile() -> Optional[SystemProfile]:
    return load_system_profile(refresh=True)


def resolve_package_name(profile: SystemProfile, name: str) -> str:
    """Map a common binary name to the distro package when known."""
    if profile.debian_like():
        return _DEBIAN_LIKE_PKG_HINTS.get(name, name)
    return name
