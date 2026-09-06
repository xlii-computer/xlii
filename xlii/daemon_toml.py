"""daemon.toml byte-preserving edits — the whitelist / JID writers.

Kernel home since godzilla-mothra V1c: these moved verbatim out of
``xlii.tui.panels``' ConfigPanel (they are config-file mutation policy, not
drawing): a headless body managing the daemon whitelist needs exactly this
without a terminal. Every edit is a targeted single-line rewrite (or section
append) that preserves the rest of daemon.toml byte-for-byte and NEVER touches
the password (it lives in an env var). Validation rides ``xlii.daemon_gate``'s
own ``valid_bare_jid`` gate; writes go through ``xlii.atomicio`` at 0o600.

Leaf-ish: stdlib + lazy xlii imports (atomicio / daemon_gate) — no rich, no
textual, no xlii.tui.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


def rewrite_daemon_jid(path: Any, jid: str) -> tuple[bool, str]:
    """Rewrite (or insert) the ``[daemon] jid`` line in daemon.toml,
    preserving the rest of the file byte-for-byte. Never touches the
    password (that lives in an env var). ``(ok, message)``."""
    import re

    try:
        text = Path(path).read_text()
    except OSError:
        return False, "no daemon.toml — copy daemon.toml.example there first"
    new_text, n = re.subn(
        r'(?m)^(\s*jid\s*=\s*)"[^"]*"', lambda m: f'{m.group(1)}"{jid}"', text, count=1
    )
    if n == 0:
        new_text, n = re.subn(
            r"(?m)^\[daemon\][ \t]*$", f'[daemon]\njid = "{jid}"', text, count=1
        )
    if n == 0:
        return False, "daemon.toml has no [daemon] section — edit it by hand"
    from xlii.atomicio import write_text_atomic

    write_text_atomic(Path(path), new_text, mode=0o600)
    return True, f"daemon JID → {jid} (restart the daemon to apply)"

def format_allowed_jids_line(jids: list[str]) -> str:
    inner = ", ".join(f'"{j}"' for j in jids)
    return f"allowed_jids = [{inner}]"

def parse_allowed_jids_from_text(text: str) -> Optional[list[str]]:
    """Return the JIDs on the ``allowed_jids = […]`` line, or None if absent.

    Scoped to the single-line array (``[^\\]\\n]*`` — no DOTALL) so a
    trailing comment on the line, or the next ``]`` further down the
    file, can never leak a quoted token into the parse."""
    import re

    m = re.search(r"(?m)^[ \t]*allowed_jids[ \t]*=[ \t]*\[([^\]\n]*)\]", text)
    if not m:
        return None
    return re.findall(r'"([^"]*)"', m.group(1))

def rewrite_allowed_jids(path: Any, jids: list[str]) -> tuple[bool, str]:
    """Rewrite (or insert) ``whitelist.allowed_jids``, preserving the rest
    of daemon.toml byte-for-byte — never touches ``password_env``.

    Only the ``[…]`` array on the ``allowed_jids`` line is rewritten:
    ``[^\\]\\n]*`` keeps the match on that one line, so a trailing
    comment (and any following section header such as ``[rate_limit]``)
    is preserved verbatim rather than swallowed by a DOTALL over-match."""
    import re

    try:
        text = Path(path).read_text()
    except OSError:
        return False, "no daemon.toml — copy daemon.toml.example there first"
    inner = ", ".join(f'"{j}"' for j in jids)
    line = f"allowed_jids = [{inner}]"
    # Replace ONLY the single-line array; the callable keeps the
    # ``allowed_jids = `` prefix (group 1) and everything after the
    # closing ``]`` (a trailing comment) untouched.
    new_text, n = re.subn(
        r"(?m)^([ \t]*allowed_jids[ \t]*=[ \t]*)\[[^\]\n]*\]",
        lambda m: f"{m.group(1)}[{inner}]",
        text,
        count=1,
    )
    if n == 0:
        # Insert under an existing [whitelist] header, or create the section.
        new_text, n = re.subn(
            r"(?m)^\[whitelist\][ \t]*$",
            f"[whitelist]\n{line}",
            text,
            count=1,
        )
    if n == 0:
        # No [whitelist] at all — append a section (preserve prior bytes).
        sep = "" if text.endswith("\n") or not text else "\n"
        new_text = text + f"{sep}\n[whitelist]\n{line}\n"
    from xlii.atomicio import write_text_atomic

    write_text_atomic(Path(path), new_text, mode=0o600)
    return True, f"whitelist → {len(jids)} JID(s) (restart the daemon to apply)"

def whitelist_add_jid(path: Any, jid: str) -> tuple[bool, str]:
    from xlii.daemon_gate import valid_bare_jid

    if not valid_bare_jid(jid):
        return False, f"invalid JID: {jid!r} — bare user@domain, no /resource"
    try:
        text = Path(path).read_text()
    except OSError:
        return False, "no daemon.toml — copy daemon.toml.example there first"
    current = parse_allowed_jids_from_text(text) or []
    if jid in current:
        return False, f"{jid} already allowed"
    return rewrite_allowed_jids(path, current + [jid])

def whitelist_remove_jid(path: Any, jid: str) -> tuple[bool, str]:
    try:
        text = Path(path).read_text()
    except OSError:
        return False, "no daemon.toml — copy daemon.toml.example there first"
    current = parse_allowed_jids_from_text(text) or []
    if jid not in current:
        return False, f"{jid} not in whitelist"
    return rewrite_allowed_jids(
        path, [j for j in current if j != jid]
    )
