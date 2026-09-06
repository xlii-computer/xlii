"""House XMPP accounts — the rider mints JIDs, not an agent over SSH.

In-band registration stays OFF. This box (the throne) either:

- runs ``prosodyctl register`` on the configured admin remote (owned
  Prosody, e.g. home.xlii-remote.com), or
- prints the exact command for a BYO server.

Passwords never land in config.json. They go in the vault under
``xlii:xmpp`` (JID localpart as the slot). A node cannot self-enroll.
"""

from __future__ import annotations

import re
import secrets
import shlex
from dataclasses import dataclass
from typing import Any, Callable, Optional

from xlii.daemon_gate import valid_bare_jid

VAULT_XMPP_NS = "xlii:xmpp"

ROLES = ("me", "throne", "daemon", "node", "face")

_LOCAL = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_NODE_N = re.compile(r"^node([0-9]+)$")


class JidError(ValueError):
    """Bad house config, bad localpart, or Prosody refused the mint."""


@dataclass
class Mint:
    """One minted (or adopted) account."""

    jid: str
    localpart: str
    domain: str
    role: str
    node: str = ""
    registered: bool = False
    command: str = ""
    password_stored: bool = False


def xmpp_cfg(cfg: Any) -> dict[str, Any]:
    raw = getattr(cfg, "xmpp", None) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def house_domain(cfg: Any) -> str:
    return str(xmpp_cfg(cfg).get("domain") or "").strip().lower()


def house_admin_remote(cfg: Any) -> str:
    return str(xmpp_cfg(cfg).get("admin_remote") or "").strip()


def accounts(cfg: Any) -> dict[str, dict[str, Any]]:
    raw = xmpp_cfg(cfg).get("accounts") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, row in raw.items():
        k = str(key).strip().lower()
        if not k:
            continue
        if isinstance(row, str):
            out[k] = {"jid": row.strip().lower(), "role": "", "node": ""}
            continue
        if not isinstance(row, dict):
            continue
        out[k] = {
            "jid": str(row.get("jid") or "").strip().lower(),
            "role": str(row.get("role") or "").strip().lower(),
            "node": str(row.get("node") or "").strip().lower(),
        }
    return out


def normalize_localpart(raw: str) -> str:
    s = (raw or "").strip().lower()
    if not _LOCAL.fullmatch(s):
        raise JidError(
            f"localpart {raw!r} must be lowercase letters/digits/hyphens, "
            "start with a letter, max 32"
        )
    return s


def face_localpart(node: str) -> str:
    """The body's JID localpart. One account per limb — not a second ``*desk``."""
    return normalize_localpart(node)


def daemon_localpart(node: str) -> str:
    return normalize_localpart(node)


def is_limb_name(name: str) -> bool:
    """True for ``node1``, ``node2``, … — not a hostname, not the throne."""
    return bool(_NODE_N.fullmatch((name or "").strip().lower()))


def taken_limb_names(cfg: Any) -> set[str]:
    """Roster keys + ledger ``node`` fields that are already limbs."""
    taken: set[str] = set()
    try:
        for key in (getattr(cfg, "fabric_nodes", None) or {}):
            k = str(key).strip().lower()
            if is_limb_name(k):
                taken.add(k)
    except Exception:
        # An unreadable roster contributes no taken names; whatever was collected so far still counts.
        pass
    for row in accounts(cfg).values():
        n = str(row.get("node") or "").strip().lower()
        if is_limb_name(n):
            taken.add(n)
        lp = str(row.get("jid") or "").split("@", 1)[0].strip().lower()
        if is_limb_name(lp):
            taken.add(lp)
    return taken


def next_node_name(cfg: Any) -> str:
    """The next ``nodeN`` that is not on the roster or ledger."""
    taken = taken_limb_names(cfg)
    n = 1
    while f"node{n}" in taken:
        n += 1
    return f"node{n}"


def split_jid(jid: str) -> tuple[str, str]:
    s = (jid or "").strip().lower()
    if not valid_bare_jid(s):
        raise JidError(f"not a bare JID: {jid!r}")
    local, _, domain = s.partition("@")
    return local, domain


def register_command(localpart: str, domain: str, password: str) -> str:
    """The operator-facing Prosody mint. Password is quoted. Never log it."""
    return (
        "sudo -n prosodyctl register "
        f"{shlex.quote(localpart)} {shlex.quote(domain)} {shlex.quote(password)}"
    )


def new_password() -> str:
    return secrets.token_urlsafe(18)


def set_house(
    cfg: Any,
    *,
    domain: str = "",
    admin_remote: str = "",
) -> dict[str, Any]:
    block = xmpp_cfg(cfg)
    if domain:
        d = domain.strip().lower()
        if "." not in d and d != "localhost":
            raise JidError(f"domain {domain!r} looks incomplete")
        block["domain"] = d
    if admin_remote:
        block["admin_remote"] = admin_remote.strip()
    cfg.xmpp = block
    save = getattr(cfg, "save", None)
    if callable(save):
        save()
    return block


def _store_password(localpart: str, password: str) -> bool:
    try:
        from xlii.vault import Vault

        Vault.unlock().set(VAULT_XMPP_NS, localpart, password)
        return True
    except Exception:
        return False


def password_for(localpart: str) -> str:
    try:
        from xlii.vault import Vault

        slot = Vault.unlock(create_if_missing=False).get(VAULT_XMPP_NS) or {}
        return str(slot.get(localpart) or "")
    except Exception:
        return ""


def _write_ledger(cfg: Any, mint: Mint) -> None:
    block = xmpp_cfg(cfg)
    acc = dict(block.get("accounts") or {}) if isinstance(block.get("accounts"), dict) else {}
    acc[mint.localpart] = {
        "jid": mint.jid,
        "role": mint.role,
        "node": mint.node,
    }
    block["accounts"] = acc
    cfg.xmpp = block
    save = getattr(cfg, "save", None)
    if callable(save):
        save()


def add_account(
    cfg: Any,
    localpart: str,
    *,
    role: str = "node",
    node: str = "",
    domain: str = "",
    password: str = "",
    adopt: bool = False,
    register: bool = True,
    exec_fn: Optional[Callable[[str], bytes]] = None,
) -> Mint:
    """Mint or adopt a house JID. ``adopt`` records without touching Prosody."""
    role_n = (role or "node").strip().lower()
    if role_n not in ROLES:
        raise JidError(f"role must be one of {', '.join(ROLES)}")
    local = normalize_localpart(localpart)
    node_n = (node or "").strip().lower()
    if node_n:
        node_n = normalize_localpart(node_n)
    if role_n == "face" and not node_n:
        if local.endswith("desk") and len(local) > 4:
            node_n = local[:-4]
        else:
            raise JidError("face role needs --node (the limb this glass sits on)")
    if role_n == "node" and not node_n:
        node_n = local
    dom = (domain or house_domain(cfg) or "").strip().lower()
    if not dom:
        raise JidError("no XMPP domain — run `xlii jid house --domain …` first")
    jid = f"{local}@{dom}"
    if not valid_bare_jid(jid):
        raise JidError(f"not a bare JID: {jid}")

    have = accounts(cfg).get(local)
    if have and have.get("jid") == jid and not adopt:
        raise JidError(f"{jid} is already on the ledger — `xlii jid ls`")

    pw = (password or "").strip() or new_password()
    cmd = register_command(local, dom, pw)
    minted = Mint(
        jid=jid,
        localpart=local,
        domain=dom,
        role=role_n,
        node=node_n,
        command=cmd,
    )
    if adopt:
        minted.password_stored = _store_password(local, pw) if password else False
        _write_ledger(cfg, minted)
        return minted

    ran = False
    if register:
        runner = exec_fn if exec_fn is not None else _default_exec(cfg)
        if runner is not None:
            try:
                runner(cmd)
                ran = True
            except Exception as e:
                raise JidError(
                    f"Prosody register failed ({type(e).__name__}: {e}). "
                    f"Run this on the XMPP host:\n  {cmd}"
                ) from e
    minted.registered = ran
    minted.password_stored = _store_password(local, pw)
    _write_ledger(cfg, minted)
    return minted


def _default_exec(cfg: Any) -> Optional[Callable[[str], bytes]]:
    remote = house_admin_remote(cfg)
    if not remote:
        return None

    def _run(command: str) -> bytes:
        from xlii.remotefs import manager

        conn = manager.get(remote)
        try:
            return conn.run(command)
        finally:
            manager.close(remote)

    return _run


def list_accounts(cfg: Any) -> list[Mint]:
    dom = house_domain(cfg)
    out: list[Mint] = []
    for local, row in sorted(accounts(cfg).items()):
        jid = row.get("jid") or (f"{local}@{dom}" if dom else local)
        try:
            lp, d = split_jid(jid) if "@" in jid else (local, dom)
        except JidError:
            lp, d = local, dom
        out.append(Mint(
            jid=jid,
            localpart=lp,
            domain=d,
            role=str(row.get("role") or ""),
            node=str(row.get("node") or ""),
            registered=True,
            password_stored=bool(password_for(lp)),
        ))
    return out
