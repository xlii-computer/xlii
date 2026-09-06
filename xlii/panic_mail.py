"""Panic mail — deny-only ingest. Not mojo, not ``$``, not an agent.

Three gates (all must pass, else silent drop): known From, DKIM+SPF pass,
subject is one of three owner phrases. Kill is one mail. Destroy needs round 2
(TOTP default) then the same ``run_destroy`` handler via an in-process
``PanicProof`` — not a forgeable context flag.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from xlii.atomicio import write_text_atomic
from xlii.email import (
    UnseenMail,
    authentication_results_ok,
    parse_from_addr,
)
from xlii.project_paths import user_home

log = logging.getLogger(__name__)

PANIC_VAULT_ID = "__xlii_panic__"
PHRASE_KEYS = ("phrase_1", "phrase_2", "phrase_3")
_PROOFS: set[str] = set()

PHRASE_IDEAS = (
    "the yellow teapot behind the oats",
    "purple socks on a tuesday",
    "cabinet left of the sink, third shelf",
    "the ugly lamp from the yard sale",
    "wallet in the winter coat",
    "blue crate in the garage",
    "the plant that always dies",
    "movie we walked out of",
    "wrong-key drawer in the kitchen",
    "the dog's second bowl",
    "red notebook under the maps",
    "that one parking garage",
    "soup we burned in 2019",
    "the chair nobody sits in",
    "spare glasses in the glove box",
    "password on a sticky we threw away",
    "train we missed on purpose",
    "the loud clock in the hall",
    "jar of screws in the junk drawer",
    "nickname only one person uses",
)

_KILL_RE = re.compile(
    r"(?im)(?:^|\n)\s*kill(?:\s+(\S+))?\s*(?:\n|$)"
)
_DESTROY_RE = re.compile(
    r"(?im)(?:^|\n)\s*destroy(?:\s+(all|throne\S*|node\S*))?\s*(?:\n|$)"
)
_TOTP_RE = re.compile(r"^\s*(\d{6})\s*$")


@dataclass(frozen=True)
class PanicProof:
    """One-shot in-process token. Tasks cannot mint this."""
    nonce: str
    aim: str
    target: str
    minted_at: float


@dataclass
class PanicResult:
    status: str  # dropped | kill | destroy_pending | destroy_run | skipped
    reason: str = ""
    aim: str = ""
    target: str = ""


def mint_panic_proof(*, aim: str, target: str = "") -> PanicProof:
    proof = PanicProof(
        nonce=secrets.token_hex(16),
        aim=aim,
        target=target,
        minted_at=time.time(),
    )
    _PROOFS.add(proof.nonce)
    return proof


def consume_panic_proof(proof: Any) -> bool:
    if not isinstance(proof, PanicProof):
        return False
    if proof.nonce not in _PROOFS:
        return False
    _PROOFS.discard(proof.nonce)
    return True


def pending_path() -> Path:
    override = (os.environ.get("XLII_PANIC_PENDING") or "").strip()
    if override:
        return Path(override)
    return user_home() / ".local" / "share" / "xlii" / "panic-pending.json"


def load_pending() -> Optional[dict[str, Any]]:
    path = pending_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def save_pending(aim: str, target: str, *, fails: int = 0) -> None:
    write_text_atomic(
        pending_path(),
        json.dumps(
            {"aim": aim, "target": target, "fails": fails, "ts": time.time()},
            indent=2,
        )
        + "\n",
        mode=0o600,
    )


def clear_pending() -> None:
    path = pending_path()
    try:
        path.unlink()
    except FileNotFoundError:
        # Nothing pending — the common case, not an error.
        pass
    except OSError as e:
        log.debug("panic: could not clear %s: %s", path, e)


def load_phrases() -> list[str]:
    try:
        from xlii.vault import Vault

        vault = Vault.unlock(create_if_missing=False)
    except Exception:
        return []
    rec = vault.get(PANIC_VAULT_ID)
    out: list[str] = []
    for key in PHRASE_KEYS:
        val = (rec.get(key) or "").strip()
        if val:
            out.append(val)
    return out


def save_phrases(phrases: list[str]) -> None:
    from xlii.vault import Vault

    vault = Vault.unlock(create_if_missing=True)
    cleaned = [(p or "").strip() for p in phrases]
    if len(cleaned) != 3 or not all(cleaned):
        raise ValueError("need exactly three non-empty phrases")
    for key, val in zip(PHRASE_KEYS, cleaned):
        vault.set(PANIC_VAULT_ID, key, val)


def load_allowlist() -> list[str]:
    env = (os.environ.get("XLII_PANIC_FROM") or "").strip()
    if env:
        return [x.strip().lower() for x in env.split(",") if x.strip()]
    path = Path(os.environ.get("XLII_DAEMON_TOML") or "").expanduser()
    if not path.is_file():
        from xlii.daemon_gate import DEFAULT_CONFIG_PATH

        path = DEFAULT_CONFIG_PATH
    if not path.is_file():
        return []
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return []
    panic = data.get("panic") if isinstance(data, dict) else None
    raw = panic.get("from") if isinstance(panic, dict) else None
    if not isinstance(raw, list):
        return []
    return [str(x).strip().lower() for x in raw if str(x).strip()]


def subject_matches(subject: str, phrases: list[str]) -> bool:
    subj = (subject or "").strip().casefold()
    if not subj:
        return False
    return any(subj == (p or "").strip().casefold() for p in phrases if (p or "").strip())


def parse_kill_target(body: str) -> Optional[str]:
    m = _KILL_RE.search(body or "")
    if not m:
        return None
    return (m.group(1) or "throne").strip()


def parse_destroy_aim(body: str) -> Optional[tuple[str, str]]:
    m = _DESTROY_RE.search(body or "")
    if not m:
        return None
    raw = (m.group(1) or "throne").strip()
    low = raw.lower()
    if low == "all":
        return "all", ""
    if low.startswith("throne"):
        return "throne", ""
    if low.startswith("node"):
        name = raw[4:].lstrip("@: ").strip() or raw
        return "node", name
    return "throne", ""


def gates_ok(
    *,
    sender: str,
    subject: str,
    auth_results: str,
    allowlist: list[str],
    phrases: list[str],
) -> bool:
    addr = parse_from_addr(sender)
    if not addr or addr not in {a.lower() for a in allowlist}:
        return False
    if not authentication_results_ok(auth_results):
        return False
    if not subject_matches(subject, phrases):
        return False
    return True


def _totp_ok(code: str, secret: str, now: float) -> bool:
    if not secret:
        return False
    from xlii import totp

    return totp.verify(secret, (code or "").strip(), now)


def _other_phrase_ok(body: str, phrases: list[str], subject: str) -> bool:
    blob = (body or "").strip().casefold()
    subj = (subject or "").strip().casefold()
    for p in phrases:
        t = (p or "").strip()
        if not t or t.casefold() == subj:
            continue
        if blob == t.casefold():
            return True
    return False


def execute_kill() -> str:
    from xlii.daemon_kill import disable_systemd_restart

    return disable_systemd_restart()


def handle_panic(
    mail: UnseenMail,
    *,
    allowlist: list[str],
    phrases: list[str],
    now: Optional[float] = None,
    totp_secret: str = "",
    kill_fn: Optional[Callable[[], str]] = None,
    send_fn: Optional[Callable[[str, str, str], None]] = None,
    destroy_fn: Optional[Callable[..., Any]] = None,
) -> PanicResult:
    """Pure ingest. Silent drop is ``dropped``. Never bounces a reject."""
    clock = time.time() if now is None else now
    if not gates_ok(
        sender=mail.sender,
        subject=mail.subject,
        auth_results=mail.auth_results,
        allowlist=allowlist,
        phrases=phrases,
    ):
        return PanicResult(status="dropped", reason="gate")

    pending = load_pending()
    if pending:
        totp_match = _TOTP_RE.match(mail.body or "")
        ok = False
        if totp_secret and totp_match:
            ok = _totp_ok(totp_match.group(1), totp_secret, clock)
        elif not totp_secret:
            ok = _other_phrase_ok(mail.body, phrases, mail.subject)
        if not ok:
            fails = int(pending.get("fails") or 0) + 1
            if fails >= 5:
                clear_pending()
            else:
                save_pending(
                    str(pending.get("aim") or "throne"),
                    str(pending.get("target") or ""),
                    fails=fails,
                )
            return PanicResult(status="dropped", reason="round2")
        aim = str(pending.get("aim") or "throne")
        target = str(pending.get("target") or "")
        clear_pending()
        proof = mint_panic_proof(aim=aim, target=target)
        fn = destroy_fn
        if fn is None:
            from xlii.destroy import run_destroy

            fn = run_destroy
        fn(aim, target=target, level=1, dry_run=False, proof=proof)
        return PanicResult(status="destroy_run", aim=aim, target=target)

    destroy_aim = parse_destroy_aim(mail.body)
    if destroy_aim:
        aim, target = destroy_aim
        save_pending(aim, target)
        if send_fn is not None:
            to = parse_from_addr(mail.sender)
            if totp_secret:
                body = (
                    "xlii panic: reply with a 6-digit authenticator code in the body.\n"
                    "Do not repeat the subject line."
                )
            else:
                body = (
                    "xlii panic: reply with phrase 2 of 3 in the body "
                    "(not the subject).\n"
                    "Do not repeat the subject line."
                )
            try:
                send_fn(to, "xlii panic", body)
            except Exception as e:
                log.warning("panic challenge send failed: %s", e)
        return PanicResult(status="destroy_pending", aim=aim, target=target)

    if parse_kill_target(mail.body) is not None:
        fn = kill_fn or execute_kill
        note = fn()
        return PanicResult(status="kill", reason=note)

    return PanicResult(status="dropped", reason="no-verb")


def check_on_wake() -> PanicResult:
    """Poison pill: fetch unseen panic mail. No phrases / no allowlist → skip.

    Never raises into daemon/Face start.
    """
    try:
        phrases = load_phrases()
        allowlist = load_allowlist()
        if len(phrases) < 3 or not allowlist:
            return PanicResult(status="skipped", reason="unconfigured")
        from xlii.config import GlobalConfig
        from xlii.email import (
            EmailError,
            fetch_unseen_mail,
            mark_uid_seen,
            resolve_account,
            send_message,
        )

        cfg = GlobalConfig.load()
        try:
            account = resolve_account(cfg, None)
        except Exception as e:
            log.warning("panic wake: no mail account (%s)", e)
            return PanicResult(status="skipped", reason="no-account")
        try:
            unseen = fetch_unseen_mail(account)
        except EmailError as e:
            log.warning("panic wake: fetch failed (%s)", e)
            return PanicResult(status="skipped", reason="fetch")
        totp_secret = (os.environ.get("XLII_DAEMON_TOTP_SECRET") or "").strip()
        last = PanicResult(status="skipped", reason="empty")

        def _send(to: str, subject: str, body: str) -> None:
            send_message(account, to=to, subject=subject, body=body)

        for mail in unseen:
            last = handle_panic(
                mail,
                allowlist=allowlist,
                phrases=phrases,
                totp_secret=totp_secret,
                send_fn=_send,
            )
            try:
                mark_uid_seen(account, mail.uid)
            except Exception as e:
                # Advisory flag only — a mailbox that refuses \Seen must not
                # stall the panic sweep (worst case the mail is re-read).
                log.debug("panic wake: mark_uid_seen(%s) failed: %s", mail.uid, e)
            if last.status in ("kill", "destroy_run"):
                return last
        return last
    except Exception as e:
        log.warning("panic wake: %s: %s", type(e).__name__, e)
        return PanicResult(status="skipped", reason="error")
