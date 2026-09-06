"""/remote-control sitting and /mute me@ — desk-owned teeth, not /kill."""

from __future__ import annotations

import time
from typing import Any

from xlii.commands import REPLCommand, register_repl_command
from xlii.daemon_gate import DEFAULT_CONFIG_PATH
from xlii.daemon_toml import parse_allowed_jids_from_text, rewrite_allowed_jids
from xlii.occupancy_store import load_live, mutate


def _device_flag(name: str) -> str:
    try:
        from xlii.tailnet import peer_online

        online = peer_online(name)
    except Exception:
        online = None
    if online is True:
        return "online"
    if online is False:
        return "offline"
    return "unknown"


def _status_line(occ) -> str:
    rl = occ.remote_lab
    if not rl.open:
        return "sitting closed — phone is not this Face"
    bound = (getattr(rl, "device", "") or "").strip()
    if rl.locked:
        if bound:
            return (
                f"sitting locked for {bound} — phone refused; "
                "/remote-control unlock or drop"
            )
        return "sitting locked — phone refused; /remote-control unlock or drop"
    tier = (getattr(rl, "tier", "") or "door").strip().lower()
    glass = " · tailnet glass" if tier == "glass" else ""
    if bound:
        return (
            f"sitting open for {bound} ({_device_flag(bound)}){glass} — "
            "Conversations is this glass: Mojo + $ lab"
        )
    return f"sitting open{glass} — Conversations is this glass: Mojo + $ lab"


def _face_remote_jid() -> str:
    try:
        from xlii.face_remote import load_bridge_config

        cfg, err = load_bridge_config()
        if cfg is not None:
            return (getattr(cfg, "jid", "") or "")
        return ""
    except Exception:
        return ""


def _how_to_lines(occ) -> list[str]:
    jid = _phone_jid()
    lines = [
        _status_line(occ),
    ]
    try:
        from xlii.face_remote import bridge_status

        lines.append(bridge_status())
    except Exception:
        # No bridge available -- the how-to prints without the status line.
        pass
    lines.append(
        "Bare text from the phone is Mojo on this desk — no ? prefix. "
        "Mojo may send a lab worker (create/edit files) while the sitting "
        "is open. Wait for the reply; it can take a minute."
    )
    if jid:
        lines.append(f"From Conversations, DM [bold]{jid}[/bold] as me@.")
        lines.append("One JID per body. Sitting turns that contact into Mojo + $.")
    else:
        lines.append(
            "No body JID on this box yet (daemon.toml or face.toml [remote])."
        )
    lines.extend([
        "what is this project?     — Mojo answers (talk)",
        "create text.txt hello world — Mojo hires the lab worker",
        "/whoami                   — which body is listening",
        "Idle 5 min with no activity autolocks. /remote-control drop hangs up.",
        "From the phone: /xsu <code> then /remote-control. Face open skips /xsu.",
        "Live only — DMs from while Face was down or sitting closed are discarded, never queued.",
    ])
    return lines


def _print_how_to(console: Any, occ) -> None:
    for line in _how_to_lines(occ):
        console.print(line)


def sitting_live(occ, *, now: float) -> bool:
    """Open, unlocked, and inside the idle window."""
    return bool(occ.remote_lab_allows_dollar(now=now))


def apply_remote_control(
    action: str, *, now: float, source: str = "face", device: str = "",
    tier: str = "door",
):
    """Mutate occupancy for one remote-control sub-verb. Returns the live occ."""
    act = (action or "status").strip().lower()
    if act in ("start",):
        act = "open"
    if act in ("close",):
        act = "drop"
    if act in ("show",):
        act = "status"
    if act == "open":
        return mutate(
            lambda o: o.open_remote_lab(
                now=now, source=source, device=device, tier=tier,
            ),
            now=now,
        )
    if act == "lock":
        return mutate(lambda o: o.lock_remote_lab(), now=now)
    if act == "unlock":
        def _unlock(o):
            if o.remote_lab.open:
                o.remote_lab.locked = False
                o.record_remote_lab_agent(now=now)

        return mutate(_unlock, now=now)
    if act == "drop":
        return mutate(lambda o: o.drop_remote_lab(), now=now)
    return load_live(now=now)


def format_daemon_sitting_reply(occ, action: str) -> str:
    """Plain XMPP reply for a daemon /remote-control."""
    line = _status_line(occ)
    act = (action or "").strip().lower()
    if act in ("drop", "close"):
        return line + " — mojo remains; $ gone"
    if occ.remote_lab.open and not occ.remote_lab.locked:
        return (
            f"{line}\n"
            "Idle 5 min with no activity autolocks."
        )
    return line


def sitting_open_notice(*, node: str, persona: str, jid: str) -> str:
    """What me@ reads when any body opens remote-control (whoami + $)."""
    n = (node or "").strip() or "node"
    p = (persona or "").strip() or "mojo"
    j = (jid or "").strip() or n
    return (
        f"{n} opened remote-control\n"
        f"[node] {n} · persona: {p} · {j}\n"
        f"Conversations DM to {j} is this glass: Mojo talk + $ lab "
        f"(edit/write/bash) until /remote-control drop."
    )


def _parse_open_flags(parts: list[str]) -> tuple[str, str]:
    """Return ``(device, tier)`` from ``open [--glass] [--for <device>]``."""
    device = ""
    tier = "door"
    i = 0
    while i < len(parts):
        p = parts[i]
        if p == "--glass":
            tier = "glass"
        elif p == "--for" and i + 1 < len(parts):
            device = parts[i + 1].strip()
            i += 1
        elif p.startswith("--for="):
            device = p.split("=", 1)[1].strip()
        i += 1
    return device, tier


def h_remote_control(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    parts = line.split()
    sub = parts[1].lower() if len(parts) > 1 else "status"
    now = time.time()

    if sub in ("status", "show"):
        _print_how_to(console, load_live(now=now))
        return True

    if sub in ("open", "start"):
        device, tier = _parse_open_flags(parts[2:])
        occ = apply_remote_control(
            "open", now=now, source="face", device=device, tier=tier,
        )
        _print_how_to(console, occ)
        notice = _local_sitting_notice()
        if notice:
            console.print(f"[dim]me@ will hear:[/dim] {notice.splitlines()[0]}")
        return True

    if sub == "lock":
        occ = apply_remote_control("lock", now=now)
        console.print(_status_line(occ))
        return True

    if sub == "unlock":
        occ = apply_remote_control("unlock", now=now)
        console.print(_status_line(occ))
        return True

    if sub in ("drop", "close"):
        occ = apply_remote_control("drop", now=now)
        console.print(_status_line(occ) + " — mojo remains; $ gone")
        return True

    console.print(
        "usage: /remote-control [open [--glass] [--for <device>]|lock|unlock|drop|status]"
    )
    return True


def _phone_jid() -> str:
    """The one body JID the phone DMs — daemon on this box, else Face."""
    jid = _daemon_own_jid(DEFAULT_CONFIG_PATH)
    if jid:
        return jid
    return _face_remote_jid()


def _local_sitting_notice() -> str:
    """Whoami + $ line for this glass (any body: node, VM, throne)."""
    try:
        from xlii.config import GlobalConfig
        from xlii.farm import job_node_name
        from xlii.persona import resolve_default_persona

        cfg = GlobalConfig.load()
        node = job_node_name(cfg)
        jid = _phone_jid()
        if node in ("", "node"):
            node = (jid.split("@", 1)[0] if jid else "") or "throne"
        persona = resolve_default_persona(cfg=cfg)
        return sitting_open_notice(node=node, persona=persona, jid=jid)
    except Exception:
        return ""


def _daemon_own_jid(path) -> str:
    try:
        text = path.read_text()
    except OSError:
        return ""
    import re

    m = re.search(r'(?m)^[ \t]*jid[ \t]*=[ \t]*"([^"]+)"', text)
    return (m.group(1) if m else "").strip().lower()


def h_mute(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    parts = line.split()
    token = (line.split() or [""])[0].lstrip("/").lower()
    target = parts[1].strip() if len(parts) > 1 else ""
    unmute = token == "unmute"
    if not target:
        console.print("[dim]usage:[/dim] /mute me  ·  /mute <jid>  ·  /unmute <jid>")
        return True
    path = DEFAULT_CONFIG_PATH
    try:
        text = path.read_text()
    except OSError:
        console.print("[yellow]no daemon.toml — nothing to mute[/yellow]")
        return True
    allowed = parse_allowed_jids_from_text(text) or []
    own = _daemon_own_jid(path)
    if target.lower() == "me":
        drop = {j for j in allowed if j.strip().lower() != own}
    else:
        drop = {target.strip().lower()}
    if unmute:
        new = list(allowed)
        t = target.strip()
        if t.lower() != "me" and t not in new:
            new.append(t)
    else:
        new = [j for j in allowed if j.strip().lower() not in drop]
    ok, msg = rewrite_allowed_jids(path, new)
    if not ok:
        console.print(f"[yellow]{msg}[/yellow]")
        return True
    verb = "unmuted" if unmute else "muted"
    console.print(f"[dim]{verb}: {', '.join(sorted(drop)) or target}[/dim]")
    return True


def register() -> None:
    register_repl_command(REPLCommand(
        name="remote-control",
        aliases=["remote-lab"],
        handler=h_remote_control,
        description="Open this Face window to me@ — Mojo on the phone, lab worker for files.",
        usage="/remote-control [open [--glass] [--for <device>]|lock|unlock|drop|status]",
        category="session",
        repls=["code"],
    ))
    register_repl_command(REPLCommand(
        name="mute",
        handler=h_mute,
        description="Drop me@ (or a JID) from the daemon allowlist.",
        usage="/mute me | /mute <jid>",
        category="session",
        repls=["code"],
    ))
    register_repl_command(REPLCommand(
        name="unmute",
        handler=h_mute,
        description="Put a JID back on the daemon allowlist.",
        usage="/unmute <jid>",
        category="session",
        repls=["code"],
    ))
