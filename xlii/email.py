"""Email core — IMAP read/search + gated SMTP send (proposals/email.md).

Stdlib only (imaplib, smtplib, email). Credentials live in the encrypted vault;
``config.json`` holds curated account descriptors (host/port/user + vault_ns).
Fetched mail is cached under ``<project>/.xlii/mail/`` (ignored, pruned).
"""

from __future__ import annotations

import html as html_module
import imaplib
import json
import re
import smtplib
from dataclasses import dataclass, field
from email import message_from_bytes
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, unquote

from xlii.atomicio import write_text_atomic
from xlii.multimodal import _MAX_TEXT_BYTES

PASSWORD_ENV_VAR = "PASSWORD"
DEFAULT_IMAP_PORT = 993
DEFAULT_SMTP_PORT = 465
DEFAULT_FOLDER = "INBOX"
DEFAULT_LIST_LIMIT = 50
_KEEP_CACHE = 200

# Test seams — never hit real servers in unit tests.
_IMAP_FACTORY: Callable[..., Any] | None = None
_SMTP_FACTORY: Callable[..., Any] | None = None


class EmailError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class MissingCredentialError(EmailError):
    pass


@dataclass
class EmailAccount:
    name: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    user: str
    vault_ns: str
    default: bool = False


@dataclass
class AttachmentMeta:
    name: str
    size: int
    content_type: str = ""


@dataclass
class MailSummary:
    id: str
    uid: str
    subject: str
    sender: str
    date: str
    unread: bool
    account: str
    folder: str = DEFAULT_FOLDER


@dataclass
class ParsedMessage:
    id: str
    uid: str
    account: str
    folder: str
    subject: str
    sender: str
    to: str
    date: str
    body: str
    truncated: bool = False
    unread: bool = False
    attachments: list[AttachmentMeta] = field(default_factory=list)


def _imap_class() -> type:
    return _IMAP_FACTORY or imaplib.IMAP4_SSL


def _smtp_class() -> type:
    return _SMTP_FACTORY or smtplib.SMTP_SSL


def parse_account(name: str, entry: dict[str, Any]) -> EmailAccount:
    if not isinstance(entry, dict):
        raise EmailError(f"account {name!r}: descriptor must be an object")
    imap_host = str(entry.get("imap_host") or "").strip()
    smtp_host = str(entry.get("smtp_host") or "").strip()
    user = str(entry.get("user") or "").strip()
    vault_ns = str(entry.get("vault_ns") or f"email:{name}").strip()
    if not imap_host or not smtp_host or not user:
        raise EmailError(
            f"account {name!r}: imap_host, smtp_host, and user are required"
        )
    return EmailAccount(
        name=name,
        imap_host=imap_host,
        imap_port=int(entry.get("imap_port") or DEFAULT_IMAP_PORT),
        smtp_host=smtp_host,
        smtp_port=int(entry.get("smtp_port") or DEFAULT_SMTP_PORT),
        user=user,
        vault_ns=vault_ns,
        default=bool(entry.get("default")),
    )


def list_accounts(cfg) -> dict[str, EmailAccount]:
    raw = getattr(cfg, "email_accounts", None) or {}
    out: dict[str, EmailAccount] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not name.strip():
            continue
        out[name.strip()] = parse_account(name.strip(), entry)
    return out


def resolve_account(cfg, name: str | None = None) -> EmailAccount:
    accounts = list_accounts(cfg)
    if not accounts:
        raise EmailError(
            "no email accounts configured — run `xlii email accounts add <name>`"
        )
    if name:
        key = name.strip()
        if key not in accounts:
            raise EmailError(f"unknown email account {key!r}")
        return accounts[key]
    defaults = [a for a in accounts.values() if a.default]
    if len(defaults) == 1:
        return defaults[0]
    if len(accounts) == 1:
        return next(iter(accounts.values()))
    raise EmailError(
        "multiple email accounts — pass --account <name> "
        "(or mark one descriptor with \"default\": true)"
    )


def credential_hint(account: EmailAccount) -> str:
    return (
        f"no password in vault for {account.vault_ns!r} — "
        f"run `xlii auth set {account.vault_ns} {PASSWORD_ENV_VAR}` "
        f"or `xlii email accounts add {account.name}`"
    )


def get_password(account: EmailAccount, vault=None) -> str:
    from xlii.vault import Vault, VaultError

    try:
        v = vault if vault is not None else Vault.unlock()
        data = v.get(account.vault_ns) or {}
    except VaultError as e:
        raise MissingCredentialError(f"vault unavailable: {e}") from e
    pwd = data.get(PASSWORD_ENV_VAR) or data.get("password")
    if not pwd:
        raise MissingCredentialError(credential_hint(account))
    return str(pwd)


def add_account(
    name: str,
    *,
    imap_host: str,
    smtp_host: str,
    user: str,
    password: str,
    imap_port: int = DEFAULT_IMAP_PORT,
    smtp_port: int = DEFAULT_SMTP_PORT,
    default: bool = False,
) -> dict[str, Any]:
    from xlii.config import GlobalConfig
    from xlii.vault import Vault

    key = (name or "").strip()
    if not key or "/" in key:
        raise ValueError(f"invalid account name {name!r}")
    if not imap_host.strip() or not smtp_host.strip() or not user.strip():
        raise ValueError("imap_host, smtp_host, and user are required")
    if not password:
        raise ValueError("password is required")

    vault_ns = f"email:{key}"
    Vault.unlock(create_if_missing=True).set(vault_ns, PASSWORD_ENV_VAR, password)
    entry: dict[str, Any] = {
        "imap_host": imap_host.strip(),
        "imap_port": int(imap_port),
        "smtp_host": smtp_host.strip(),
        "smtp_port": int(smtp_port),
        "user": user.strip(),
        "vault_ns": vault_ns,
    }
    if default:
        entry["default"] = True
    cfg = GlobalConfig.load()
    accounts = dict(cfg.email_accounts or {})
    accounts[key] = entry
    cfg.email_accounts = accounts
    cfg.save()
    return entry


def connect_imap(account: EmailAccount, *, vault=None):
    password = get_password(account, vault=vault)
    try:
        conn = _imap_class()(account.imap_host, account.imap_port)
        conn.login(account.user, password)
        return conn
    except MissingCredentialError:
        raise
    except Exception as e:
        raise EmailError(f"IMAP connect failed for {account.name}: {e}") from e


def connect_smtp(account: EmailAccount, *, vault=None):
    password = get_password(account, vault=vault)
    try:
        conn = _smtp_class()(account.smtp_host, account.smtp_port)
        conn.login(account.user, password)
        return conn
    except MissingCredentialError:
        raise
    except Exception as e:
        raise EmailError(f"SMTP connect failed for {account.name}: {e}") from e


def mail_cache_dir(project_root: Path) -> Path:
    return Path(project_root) / ".xlii" / "mail"


def message_id(account: str, folder: str, uid: str) -> str:
    # Percent-encode the folder so ``:`` (our separator) and any other odd
    # character round-trips losslessly — a lossy ``:``⇄``_`` swap mangled
    # folders like ``My_Stuff`` into ``My:Stuff``.
    safe_folder = quote(folder, safe="")
    return f"{account}:{safe_folder}:{uid}"


def _parse_message_id(msg_id: str) -> tuple[str, str, str]:
    parts = msg_id.split(":", 2)
    if len(parts) != 3:
        raise EmailError(f"invalid message id {msg_id!r} (expected account:folder:uid)")
    account, folder, uid = parts
    folder = unquote(folder)
    # IMAP UIDs are non-negative integers. Reject anything else up front so a
    # hostile/compromised server (or a bad caller) can't smuggle a path
    # traversal payload through the uid into the on-disk cache filename.
    if not uid.isdigit():
        raise EmailError(f"invalid message id {msg_id!r}: uid must be numeric")
    return account, folder, uid


def _decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    chunks: list[str] = []
    for part, charset in decode_header(value):
        if isinstance(part, bytes):
            chunks.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            chunks.append(str(part))
    return "".join(chunks).strip()


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html_module.unescape(text).strip()


def _cap_text(text: str, max_bytes: int = _MAX_TEXT_BYTES) -> tuple[str, bool]:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return text, False
    clipped = raw[:max_bytes].decode("utf-8", errors="ignore")
    note = f"\n\n…(body truncated at {max_bytes // 1024} KiB)"
    return clipped + note, True


def _extract_body_and_attachments(msg) -> tuple[str, list[AttachmentMeta]]:
    attachments: list[AttachmentMeta] = []
    if msg.is_multipart():
        plain: str | None = None
        html_part: str | None = None
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disp = str(part.get("Content-Disposition", ""))
            filename = part.get_filename()
            if filename or "attachment" in disp.lower():
                payload = part.get_payload(decode=True) or b""
                attachments.append(
                    AttachmentMeta(
                        name=_decode_mime_header(filename) or "attachment",
                        size=len(payload),
                        content_type=part.get_content_type(),
                    )
                )
                continue
            ctype = part.get_content_type()
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if ctype == "text/plain" and plain is None:
                plain = text
            elif ctype == "text/html" and html_part is None:
                html_part = text
        body = plain or (_html_to_text(html_part) if html_part else "")
        return body, attachments
    payload = msg.get_payload(decode=True)
    if payload is None:
        return "", attachments
    charset = msg.get_content_charset() or "utf-8"
    text = payload.decode(charset, errors="replace")
    if msg.get_content_type() == "text/html":
        text = _html_to_text(text)
    return text, attachments


def parse_raw_message(
    raw: bytes,
    *,
    account: str,
    folder: str,
    uid: str,
    unread: bool = False,
) -> ParsedMessage:
    msg = message_from_bytes(raw)
    subject = _decode_mime_header(msg.get("Subject"))
    sender = _decode_mime_header(msg.get("From"))
    to = _decode_mime_header(msg.get("To"))
    date_hdr = msg.get("Date") or ""
    try:
        date = parsedate_to_datetime(date_hdr).isoformat() if date_hdr else ""
    except (TypeError, ValueError, OverflowError):
        date = date_hdr
    body, attachments = _extract_body_and_attachments(msg)
    body, truncated = _cap_text(body)
    mid = message_id(account, folder, uid)
    return ParsedMessage(
        id=mid,
        uid=str(uid),
        account=account,
        folder=folder,
        subject=subject,
        sender=sender,
        to=to,
        date=date,
        body=body,
        truncated=truncated,
        unread=unread,
        attachments=attachments,
    )


def _prune_cache(d: Path, keep: int = _KEEP_CACHE, *, protect: Path | None = None) -> None:
    try:
        files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    candidates = [f for f in files if f != protect]
    for old in candidates[: max(0, len(files) - keep)]:
        try:
            old.unlink()
        except OSError:
            # Best-effort prune: file may already be gone or unlink denied.
            pass


def _message_to_cache_dict(msg: ParsedMessage) -> dict[str, Any]:
    return {
        "id": msg.id,
        "uid": msg.uid,
        "account": msg.account,
        "folder": msg.folder,
        "subject": msg.subject,
        "sender": msg.sender,
        "to": msg.to,
        "date": msg.date,
        "body": msg.body,
        "truncated": msg.truncated,
        "unread": msg.unread,
        "attachments": [
            {"name": a.name, "size": a.size, "content_type": a.content_type}
            for a in msg.attachments
        ],
    }


def _cache_filename(msg_id: str) -> str:
    """A traversal-proof cache basename for ``msg_id``.

    Every character outside ``[A-Za-z0-9_.-]`` (crucially ``/`` and the ``:``
    separators) collapses to ``_``, so the result is always a single path
    component that cannot escape the mail cache directory.
    """
    return re.sub(r"[^A-Za-z0-9_.-]", "_", msg_id) + ".json"


def cache_message(project_root: Path, msg: ParsedMessage) -> str:
    d = mail_cache_dir(project_root)
    d.mkdir(parents=True, exist_ok=True)
    path = d / _cache_filename(msg.id)
    # Defense in depth: even after sanitizing, confirm the resolved target
    # still lands directly inside the mail cache dir before writing.
    if path.resolve().parent != d.resolve():
        raise EmailError(f"refusing to cache message with unsafe id {msg.id!r}")
    write_text_atomic(path, json.dumps(_message_to_cache_dict(msg), indent=2), mode=0o644)
    _prune_cache(d, protect=path)
    root = Path(project_root)
    return path.relative_to(root).as_posix()


def load_cached_message(project_root: Path, msg_id: str) -> ParsedMessage | None:
    path = mail_cache_dir(project_root) / _cache_filename(msg_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    attachments = [
        AttachmentMeta(
            name=str(a.get("name") or "attachment"),
            size=int(a.get("size") or 0),
            content_type=str(a.get("content_type") or ""),
        )
        for a in (data.get("attachments") or [])
    ]
    return ParsedMessage(
        id=str(data.get("id") or msg_id),
        uid=str(data.get("uid") or ""),
        account=str(data.get("account") or ""),
        folder=str(data.get("folder") or DEFAULT_FOLDER),
        subject=str(data.get("subject") or ""),
        sender=str(data.get("sender") or ""),
        to=str(data.get("to") or ""),
        date=str(data.get("date") or ""),
        body=str(data.get("body") or ""),
        truncated=bool(data.get("truncated")),
        unread=bool(data.get("unread")),
        attachments=attachments,
    )


def _fetch_flags(conn, uid: bytes) -> tuple[bool, str]:
    typ, data = conn.fetch(uid, "(FLAGS UID)")
    if typ != "OK" or not data or not data[0]:
        return False, uid.decode()
    line = data[0]
    if isinstance(line, tuple):
        meta = line[0].decode(errors="replace") if isinstance(line[0], bytes) else str(line[0])
    else:
        meta = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
    unread = "\\Seen" not in meta
    m = re.search(r"UID (\d+)", meta)
    real_uid = m.group(1) if m else uid.decode()
    return unread, real_uid


def _fetch_header_summary(conn, uid: bytes) -> tuple[str, str, str]:
    typ, data = conn.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
    if typ != "OK" or not data or not data[0]:
        return "", "", ""
    chunk = data[0]
    raw = chunk[1] if isinstance(chunk, tuple) and len(chunk) > 1 else chunk
    if not isinstance(raw, bytes):
        return "", "", ""
    header_msg = message_from_bytes(raw + b"\r\n\r\n")
    return (
        _decode_mime_header(header_msg.get("From")),
        _decode_mime_header(header_msg.get("Subject")),
        _decode_mime_header(header_msg.get("Date")),
    )


def list_messages(
    account: EmailAccount,
    *,
    project_root: Path,
    folder: str = DEFAULT_FOLDER,
    unread_only: bool = False,
    limit: int = DEFAULT_LIST_LIMIT,
    vault=None,
) -> list[MailSummary]:
    conn = connect_imap(account, vault=vault)
    try:
        typ, _ = conn.select(folder, readonly=True)
        if typ != "OK":
            raise EmailError(f"could not open folder {folder!r}")
        criteria = "UNSEEN" if unread_only else "ALL"
        typ, data = conn.search(None, criteria)
        if typ != "OK" or not data or not data[0]:
            return []
        uids = [u for u in data[0].split() if u]
        if len(uids) > limit:
            uids = uids[-limit:]
        summaries: list[MailSummary] = []
        for uid in reversed(uids):
            unread, real_uid = _fetch_flags(conn, uid)
            sender, subject, date = _fetch_header_summary(conn, uid)
            summaries.append(
                MailSummary(
                    id=message_id(account.name, folder, real_uid),
                    uid=real_uid,
                    subject=subject or "(no subject)",
                    sender=sender or "(unknown)",
                    date=date,
                    unread=unread,
                    account=account.name,
                    folder=folder,
                )
            )
        return summaries
    finally:
        try:
            conn.logout()
        except Exception:
            # IMAP logout in a finally: the summaries are already built, so a failed logout must not mask them.
            pass


def _client_filter_messages(
    summaries: list[MailSummary], query: str
) -> list[MailSummary]:
    q = query.lower()
    return [
        s
        for s in summaries
        if q in s.subject.lower() or q in s.sender.lower()
    ]


def search_messages(
    account: EmailAccount,
    query: str,
    *,
    project_root: Path,
    folder: str = DEFAULT_FOLDER,
    unread_only: bool = False,
    limit: int = DEFAULT_LIST_LIMIT,
    vault=None,
) -> list[MailSummary]:
    q = (query or "").strip()
    if not q:
        raise EmailError("search query is required")
    conn = connect_imap(account, vault=vault)
    summaries: list[MailSummary] = []
    try:
        typ, _ = conn.select(folder, readonly=True)
        if typ != "OK":
            raise EmailError(f"could not open folder {folder!r}")
        if unread_only:
            typ, data = conn.search(None, "UNSEEN", "TEXT", q)
        else:
            typ, data = conn.search(None, "TEXT", q)
        if typ == "OK" and data and data[0]:
            uids = [u for u in data[0].split() if u]
            if len(uids) > limit:
                uids = uids[-limit:]
            for uid in reversed(uids):
                unread, real_uid = _fetch_flags(conn, uid)
                sender, subject, date = _fetch_header_summary(conn, uid)
                summaries.append(
                    MailSummary(
                        id=message_id(account.name, folder, real_uid),
                        uid=real_uid,
                        subject=subject or "(no subject)",
                        sender=sender or "(unknown)",
                        date=date,
                        unread=unread,
                        account=account.name,
                        folder=folder,
                    )
                )
    finally:
        try:
            conn.logout()
        except imaplib.IMAP4.error:
            # Logout is best-effort; connection may already be closed.
            pass
    if not summaries:
        summaries = _client_filter_messages(
            list_messages(
                account,
                project_root=project_root,
                folder=folder,
                unread_only=unread_only,
                limit=limit,
                vault=vault,
            ),
            q,
        )
    return summaries[:limit]


def read_message(
    account: EmailAccount,
    msg_id: str,
    *,
    project_root: Path,
    vault=None,
) -> ParsedMessage:
    acct_name, folder, uid = _parse_message_id(msg_id)
    if acct_name != account.name:
        raise EmailError(f"message {msg_id!r} belongs to account {acct_name!r}, not {account.name!r}")
    conn = connect_imap(account, vault=vault)
    try:
        typ, _ = conn.select(folder, readonly=True)
        if typ != "OK":
            raise EmailError(f"could not open folder {folder!r}")
        typ, data = conn.fetch(uid.encode(), "(RFC822)")
        if typ != "OK" or not data or not data[0]:
            raise EmailError(f"message {msg_id!r} not found")
        chunk = data[0]
        raw = chunk[1] if isinstance(chunk, tuple) and len(chunk) > 1 else None
        if not isinstance(raw, bytes):
            raise EmailError(f"could not fetch message {msg_id!r}")
        unread, _ = _fetch_flags(conn, uid.encode())
        parsed = parse_raw_message(
            raw,
            account=account.name,
            folder=folder,
            uid=uid,
            unread=unread,
        )
        cache_message(project_root, parsed)
        return parsed
    finally:
        try:
            conn.logout()
        except Exception:
            # IMAP logout in a finally: the message is already parsed, so a failed logout must not mask it.
            pass


def send_message(
    account: EmailAccount,
    *,
    to: str,
    subject: str,
    body: str,
    html: str | None = None,
    vault=None,
) -> None:
    to_addr = (to or "").strip()
    if not to_addr:
        raise EmailError("recipient (to) is required")
    subj = (subject or "").strip() or "(no subject)"
    text = body or ""

    if html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
    else:
        msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = subj
    msg["From"] = account.user
    msg["To"] = to_addr

    conn = connect_smtp(account, vault=vault)
    try:
        conn.sendmail(account.user, [to_addr], msg.as_string())
    finally:
        try:
            conn.quit()
        except Exception:
            # quit() during cleanup is best-effort; send already completed.
            pass


def parse_from_addr(raw: str) -> str:
    """Bare email address from a From header. Empty if unusable."""
    _name, addr = parseaddr(raw or "")
    return (addr or "").strip().lower()


def authentication_results_ok(header: str) -> bool:
    """True only when Authentication-Results shows both DKIM and SPF pass.

    Fail closed: missing header, empty, or any dkim/spf fail → False.
    """
    text = (header or "").strip().lower()
    if not text:
        return False
    if re.search(r"\bdkim\s*=\s*fail\b", text) or re.search(r"\bspf\s*=\s*fail\b", text):
        return False
    dkim_pass = bool(re.search(r"\bdkim\s*=\s*pass\b", text))
    spf_pass = bool(re.search(r"\bspf\s*=\s*pass\b", text))
    return dkim_pass and spf_pass


@dataclass
class UnseenMail:
    """One unseen message with the headers panic ingest needs."""
    uid: str
    sender: str
    subject: str
    body: str
    auth_results: str


def fetch_unseen_mail(
    account: EmailAccount,
    *,
    folder: str = DEFAULT_FOLDER,
    limit: int = 30,
    vault=None,
) -> list[UnseenMail]:
    """Fetch unseen RFC822. Does not mark seen (caller decides)."""
    conn = connect_imap(account, vault=vault)
    out: list[UnseenMail] = []
    try:
        typ, _ = conn.select(folder, readonly=True)
        if typ != "OK":
            raise EmailError(f"could not open folder {folder!r}")
        typ, data = conn.search(None, "UNSEEN")
        if typ != "OK" or not data or not data[0]:
            return []
        uids = [u for u in data[0].split() if u]
        if len(uids) > limit:
            uids = uids[-limit:]
        for uid in uids:
            typ, payload = conn.fetch(uid, "(RFC822)")
            if typ != "OK" or not payload or not payload[0]:
                continue
            chunk = payload[0]
            raw = chunk[1] if isinstance(chunk, tuple) and len(chunk) > 1 else None
            if not isinstance(raw, bytes):
                continue
            msg = message_from_bytes(raw)
            auth = " ".join(
                _decode_mime_header(h) for h in (msg.get_all("Authentication-Results") or [])
            )
            body, _atts = _extract_body_and_attachments(msg)
            body, _trunc = _cap_text(body)
            out.append(
                UnseenMail(
                    uid=uid.decode() if isinstance(uid, bytes) else str(uid),
                    sender=_decode_mime_header(msg.get("From")),
                    subject=_decode_mime_header(msg.get("Subject")),
                    body=body,
                    auth_results=auth,
                )
            )
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            # IMAP logout in a finally: the fetch already returned, so a failed logout must not mask it.
            pass


def mark_uid_seen(
    account: EmailAccount,
    uid: str,
    *,
    folder: str = DEFAULT_FOLDER,
    vault=None,
) -> None:
    """Best-effort \\Seen so a drop is not reprocessed. Not a bounce."""
    conn = connect_imap(account, vault=vault)
    try:
        typ, _ = conn.select(folder, readonly=False)
        if typ != "OK":
            return
        conn.store(uid.encode() if isinstance(uid, str) else uid, "+FLAGS", "\\Seen")
    except Exception:
        return
    finally:
        try:
            conn.logout()
        except Exception:
            # Best-effort cleanup: the \Seen flag is already stored server-side,
            # so a logout failure has nothing left to salvage.
            pass


def format_summary_line(s: MailSummary) -> str:
    flag = "●" if s.unread else " "
    return f"{flag} {s.id}  {s.date}  {s.sender}  {s.subject}"


def format_message_text(msg: ParsedMessage) -> str:
    attach = ""
    if msg.attachments:
        parts = [f"{a.name} ({a.size} bytes)" for a in msg.attachments]
        attach = "\nAttachments: " + ", ".join(parts)
    trunc = " [truncated]" if msg.truncated else ""
    return (
        f"Message: {msg.id}{trunc}\n"
        f"From: {msg.sender}\n"
        f"To: {msg.to}\n"
        f"Date: {msg.date}\n"
        f"Subject: {msg.subject}{attach}\n\n"
        f"{msg.body}"
    )


def gate_send_email(ctx, *, to: str, subject: str, body: str):
    """Return a refusal ToolResult, or None to proceed.

    Conservative default: require typing ``send`` (not just y/N). Honors
    ``ctx.yolo``; refuses workers and headless sessions without yolo.
    """
    from xlii.tool_context import ToolResult

    if ctx.is_worker:
        return ToolResult(
            "send_email refused: workers cannot send mail",
            is_error=True,
        )
    if ctx.yolo:
        return None
    if ctx.console is None:
        return ToolResult(
            "send_email refused: sending requires confirmation but no console "
            "is attached (headless). Use --yolo to bypass.",
            is_error=True,
        )
    preview = (body or "")[:500]
    if len(body or "") > 500:
        preview += "…"
    prompt = (
        f"Send email?\n"
        f"  To: {to}\n"
        f"  Subject: {subject}\n\n"
        f"{preview}\n\n"
        f"Type 'send' to confirm (anything else cancels): "
    )
    from xlii.tools import _confirm

    try:
        answer = _confirm(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        # No interactive input available (piped/closed stdin, or Ctrl-C) —
        # treat as a refusal rather than letting the bare traceback escape.
        return ToolResult(
            "send_email cancelled: no confirmation input available "
            "(use --yolo to send non-interactively)",
            is_error=True,
        )
    if answer != "send":
        return ToolResult("send_email cancelled", is_error=True)
    return None
