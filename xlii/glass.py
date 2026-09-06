"""Face glass proofs — lock / unlock. Fail counter is occupancy.face, never daemon ``/xsu``."""

from __future__ import annotations

import hmac
import os
import time
from typing import Optional

from xlii.occupancy import Occupancy, _face_toml_path
from xlii.occupancy_store import (
    apply_face_idle,
    glass_wire,
    mutate,
    mutate_bundle,
)


def _glass_pin() -> str:
    path = _face_toml_path()
    if not path.is_file():
        return ""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        data = tomllib.loads(raw)
    except (ValueError, tomllib.TOMLDecodeError):
        return ""
    glass = data.get("glass")
    if not isinstance(glass, dict):
        return ""
    pin = glass.get("pin")
    return str(pin).strip() if pin else ""


def totp_secret() -> str:
    env = (os.environ.get("XLII_DAEMON_TOTP_SECRET") or "").strip()
    return env


def live_chrome(*, now: Optional[float] = None) -> tuple[str, str, str]:
    """``(glass, mouth, via)`` for ChromeState after idle tick."""
    clock = time.time() if now is None else now
    chrome: dict[str, str] = {}

    def _tick(occ: Occupancy, last: float) -> tuple[Optional[float], None]:
        apply_face_idle(occ, last, now=clock)
        chrome["glass"] = glass_wire(occ)
        chrome["mouth"] = occ.mouth
        chrome["via"] = getattr(occ, "via", "") or ""
        return None, None

    mutate_bundle(_tick, now=clock)
    return chrome["glass"], chrome["mouth"], chrome["via"]


def lock_face(*, now: Optional[float] = None) -> Occupancy:
    clock = time.time() if now is None else now

    def _lock(occ: Occupancy) -> None:
        occ.lock_face()

    return mutate(_lock, now=clock)


def unlock_face(*, code: str = "", now: Optional[float] = None) -> tuple[bool, str]:
    """Unlock this Face. Desk unlock does not need the phone.

    Café/black: TOTP (same secret as ``/xsu``) or ``[glass] pin``. Fail
    counter lives on occupancy.face — never ElevationGate._locked.
    Home, or café with no factor configured: just the verb (no hostage).
    """
    clock = time.time() if now is None else now
    proof = (code or "").strip()
    released = {"was_me": False}

    def _unlock(occ: Occupancy, last: float) -> tuple[Optional[float], tuple[bool, str]]:
        level = occ.face.level
        ok = False
        reason = "ok"
        if level == "home":
            ok = True
        else:
            secret = totp_secret()
            pin = _glass_pin()
            if secret:
                from xlii import totp

                ok = totp.verify(secret, proof, clock)
                reason = "ok" if ok else "bad totp"
            elif pin:
                ok = hmac.compare_digest(proof, pin)
                reason = "ok" if ok else "bad pin"
            else:
                ok = True
                reason = "ok"
        if ok:
            released["was_me"] = occ.mouth == "me"
            occ.take_mouth("desk")
            occ.unlock_face()
            return clock, (ok, reason)
        occ.note_face_unlock_fail()
        return last, (ok, reason)

    ok, reason = mutate_bundle(_unlock, now=clock)
    if ok and released["was_me"]:
        try:
            from xlii.occ_bus import notify, publish_release, this_node

            publish_release(this_node())
            notify()
        except Exception:
            # occ_bus is optional fan-out: release is already persisted.
            pass
    return ok, reason


def face_may_accept(text: str, *, now: Optional[float] = None) -> tuple[bool, str]:
    """Whether this Face may take *text*. Updates mouth/idle on yes."""
    clock = time.time() if now is None else now
    t = (text or "").strip()
    released = {"was_me": False}

    def _accept(occ: Occupancy, last: float) -> tuple[Optional[float], tuple[bool, str]]:
        apply_face_idle(occ, last, now=clock)
        if t in ("/exit", "/quit"):
            return None, (True, "")
        if occ.face_accepts_input(t):
            released["was_me"] = occ.mouth == "me"
            occ.take_mouth("desk")
            return clock, (True, "")
        if occ.face.locked:
            return None, (False, "face locked — unlock on this glass")
        via = getattr(occ, "via", "") or ""
        where = f" on {via}" if via else ""
        return None, (False, f"me@ has the mouth{where} — lock or unlock only")

    ok, reason = mutate_bundle(_accept, now=clock)
    if ok and released["was_me"]:
        try:
            from xlii.occ_bus import notify, publish_release, this_node

            publish_release(this_node())
            notify()
        except Exception:
            # occ_bus is an optional fan-out: the release is already persisted,
            # so peers converge on their next occupancy poll.
            pass
    return ok, reason


def rider_agent_allowed(
    *,
    rider: bool,
    persona: str,
    now: Optional[float] = None,
    node: str = "",
) -> tuple[bool, str]:
    """Daemon agent from a rider JID.

    Sitting open+unlocked → ``dollar`` (lab). Else persona/mojo → ``mojo``.
    No persona and no sitting → refuse (that *is* ``$``).
    One me: a granted rider turn claims this limb and tells the house.
    """
    clock = time.time() if now is None else now
    published = {"here": ""}

    def _rider(occ: Occupancy, last: float) -> tuple[Optional[float], tuple[bool, str]]:
        occ.tick(now=clock)
        kind = "mojo"
        allowed = True
        if rider and occ.remote_lab_allows_dollar(now=clock):
            occ.record_remote_lab_agent(now=clock)
            kind = "dollar"
        elif rider and not (persona or "").strip():
            allowed = False
            kind = "sitting closed"
        if rider and allowed:
            from xlii.occ_bus import this_node

            here = this_node(node)
            occ.take_mouth("me", via=here)
            published["here"] = here
        return None, (allowed, kind)

    allowed, kind = mutate_bundle(_rider, now=clock)
    if rider and allowed:
        from xlii.occ_bus import notify, publish_claim

        here = published["here"]
        if here:
            publish_claim(here)
        notify()
    return allowed, kind


def claim_this_limb(*, node: str = "") -> None:
    """This box is the live mouth. Siblings will lock when the claim hits the MUC."""
    from xlii.occ_bus import notify, publish_claim, this_node

    here = this_node(node)

    def _claim(occ: Occupancy) -> None:
        occ.take_mouth("me", via=here)

    mutate(_claim)
    if here:
        publish_claim(here)
    notify()


def apply_remote_claim(node: str, *, self_node: str = "") -> None:
    """Sibling published me@ on *node*. Lock this glass unless we *are* that limb."""
    n = (node or "").strip()
    here = (self_node or "").strip()
    if not n or (here and n == here):
        return

    def _claim(occ: Occupancy) -> None:
        occ.take_mouth("me", via=n)

    mutate(_claim)
    try:
        from xlii.occ_bus import notify

        notify()
    except Exception:
        # The claim is already written; the notify only shortens how long peers
        # take to notice.
        pass


def apply_remote_release(node: str, *, self_node: str = "") -> None:
    """Sibling released me@. Unlock if we were mirroring that limb."""
    n = (node or "").strip()
    here = (self_node or "").strip()
    if here and n == here:
        return

    def _rel(occ: Occupancy) -> None:
        via = getattr(occ, "via", "") or ""
        if occ.mouth == "me" and (not n or via == n or not via):
            occ.take_mouth("desk")

    mutate(_rel)
    try:
        from xlii.occ_bus import notify

        notify()
    except Exception:
        # The release is already written; the notify only shortens how long
        # peers take to notice.
        pass
