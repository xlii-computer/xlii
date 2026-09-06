"""Email the webcode magic link — same door as XMPP, other mouth.

``webcode email`` / ``xlii serve mint --email`` mint the grant, then send
``base_url/?code=…`` to the owner inbox. The pairing page is unchanged
(GET peeks, POST consumes). Not a new face.

To: ``serve.public.owner_email`` if set, else the default email account's
``user``. SMTP uses that account. Elevation / ``--email`` is the consent;
this path does not ask ``type send``.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from xlii.daemon_gate import WEBCODE_REPLY_SEP, format_webcode_reply


def webcode_magic_link(code: str, base_url: str) -> str:
    return f"{base_url.rstrip('/')}/?code={code}"


def code_from_webcode_reply(reply: str) -> str:
    first = (reply or "").split(WEBCODE_REPLY_SEP, 1)[0]
    return first.removeprefix("code: ").strip()


def owner_inbox(cfg: Any) -> tuple[Any, str]:
    """``(account, to_address)`` for the owner inbox.

    Raises the same ``EmailError`` as ``resolve_account`` when nothing is
    configured.
    """
    from xlii.email import resolve_account

    account = resolve_account(cfg)
    to = _configured_owner_email(cfg) or (account.user or "").strip()
    if not to:
        from xlii.email import EmailError

        raise EmailError(
            "no owner inbox — set serve.public.owner_email or "
            "an email account user"
        )
    return account, to


def _configured_owner_email(cfg: Any) -> str:
    serve = getattr(cfg, "serve", None) or {}
    if not isinstance(serve, dict):
        return ""
    pub = serve.get("public")
    if not isinstance(pub, dict):
        return ""
    return str(pub.get("owner_email") or "").strip()


def magic_link_mail(*, code: str, base_url: str, ttl_s: int) -> tuple[str, str]:
    """Subject + plain body for the pairing mail."""
    link = webcode_magic_link(code, base_url)
    if ttl_s >= 60 and ttl_s % 60 == 0:
        exp = f"{ttl_s // 60}m"
    else:
        exp = f"{ttl_s}s"
    subject = "xlii face"
    body = f"{link}\nexpires in {exp}\n"
    return subject, body


def deliver_webcode_email(
    *,
    code: str,
    base_url: str,
    ttl_s: int,
    cfg: Any = None,
    send: Optional[Callable[..., None]] = None,
) -> str:
    """Send the magic link. Returns the To: address. Does not mint."""
    from xlii.config import GlobalConfig
    from xlii.email import send_message

    cfg = cfg if cfg is not None else GlobalConfig.load()
    account, to = owner_inbox(cfg)
    subject, body = magic_link_mail(code=code, base_url=base_url, ttl_s=ttl_s)
    sender = send if send is not None else send_message
    sender(account, to=to, subject=subject, body=body)
    return to


def append_email_note(reply: str, *, to: str = "", err: str = "") -> str:
    """Keep the XMPP/CLI mint line; add one dry email status line."""
    if err:
        return f"{reply}\n[daemon] email failed: {err}"
    return f"{reply}\n[daemon] emailed {to}"


def mint_reply_and_email(
    *,
    code: str,
    base_url: str,
    ttl_s: int,
    cfg: Any = None,
    send: Optional[Callable[..., None]] = None,
) -> str:
    """Format the usual mint reply and try to email the link."""
    reply = format_webcode_reply(code, base_url, ttl_s)
    try:
        to = deliver_webcode_email(
            code=code, base_url=base_url, ttl_s=ttl_s, cfg=cfg, send=send,
        )
    except Exception as e:  # noqa: BLE001 — mint already happened
        return append_email_note(reply, err=f"{type(e).__name__}: {e}")
    return append_email_note(reply, to=to)
