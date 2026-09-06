"""email E0–E5 — vault accounts, faked IMAP/SMTP, tools, gate (no live servers)."""

from __future__ import annotations

from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytest

import xlii.email as em
from xlii.multimodal import _MAX_TEXT_BYTES
from xlii.tool_handlers import t_read_email, t_send_email
from xlii.tool_schemas import BUILTIN_TOOLS, PARALLEL_SAFE, WORKER_REGISTRY
from tests.helpers import FakeConsole, make_tool_ctx


def _mime_bytes(
    *,
    subject: str = "Hello",
    body: str = "World",
    html: str | None = None,
    attachment: bool = False,
) -> bytes:
    if attachment:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body, "plain"))
        part = MIMEApplication(b"binary-data", Name="file.bin")
        part.add_header("Content-Disposition", "attachment", filename="file.bin")
        msg.attach(part)
    elif html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain"))
        msg.attach(MIMEText(html, "html"))
    else:
        msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = "sender@example.com"
    msg["To"] = "me@example.com"
    msg["Date"] = "Mon, 1 Jan 2024 12:00:00 +0000"
    return msg.as_bytes()


class FakeIMAP:
    mailbox: dict[bytes, bytes] = {b"1": _mime_bytes(), b"2": _mime_bytes(subject="Billing")}

    def __init__(self, host, port):
        self.host = host
        self.port = port

    def login(self, user, password):
        if password != "s3cret":
            raise RuntimeError("auth failed")

    def select(self, folder, readonly=True):
        return "OK", [b"2"]

    def search(self, charset, *criteria):
        return "OK", [b"1 2"]

    def fetch(self, uid, parts):
        uid_s = uid.decode() if isinstance(uid, bytes) else str(uid)
        raw = self.mailbox.get(uid if isinstance(uid, bytes) else uid.encode(), self.mailbox[b"1"])
        parts_s = parts.decode() if isinstance(parts, bytes) else str(parts)
        if "RFC822" in parts_s:
            return "OK", [(f"{uid_s} (RFC822 {{{len(raw)}}})".encode(), raw)]
        meta = f"{uid_s} (UID {uid_s} FLAGS (\\Seen))".encode()
        if "HEADER" in parts_s:
            header = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
            return "OK", [(meta, header)]
        return "OK", [(meta, b"")]

    def logout(self):
        pass


class FakeSMTP:
    sent: list[tuple] = []

    def __init__(self, host, port):
        self.host = host
        self.port = port

    def login(self, user, password):
        if password != "s3cret":
            raise RuntimeError("auth failed")

    def sendmail(self, from_addr, to_addrs, message):
        self.sent.append((from_addr, to_addrs, message))

    def quit(self):
        pass


@pytest.fixture(autouse=True)
def _fake_mail_wire(monkeypatch):
    FakeSMTP.sent.clear()
    monkeypatch.setattr(em, "_IMAP_FACTORY", FakeIMAP)
    monkeypatch.setattr(em, "_SMTP_FACTORY", FakeSMTP)


@pytest.fixture(autouse=True)
def _email_vault_teardown():
    yield
    from xlii.vault import VAULT_FILE

    try:
        VAULT_FILE.unlink(missing_ok=True)
    except TypeError:
        if VAULT_FILE.exists():
            VAULT_FILE.unlink()


@pytest.fixture
def personal_account(monkeypatch):
    from cryptography.fernet import Fernet

    from xlii.vault import ENV_VAR, VAULT_FILE

    monkeypatch.setenv(ENV_VAR, Fernet.generate_key().decode())
    try:
        VAULT_FILE.unlink(missing_ok=True)
    except TypeError:
        if VAULT_FILE.exists():
            VAULT_FILE.unlink()
    entry = em.add_account(
        "personal",
        imap_host="imap.test",
        smtp_host="smtp.test",
        user="me@test.com",
        password="s3cret",
        default=True,
    )
    return entry


def test_add_account_stores_descriptor_and_vault(personal_account):
    from xlii.config import GlobalConfig
    from xlii.vault import Vault

    cfg = GlobalConfig.load()
    assert "personal" in cfg.email_accounts
    assert cfg.email_accounts["personal"]["vault_ns"] == "email:personal"
    assert Vault.unlock().get("email:personal")["PASSWORD"] == "s3cret"


def test_connect_imap_missing_password_hint(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    from xlii.config import GlobalConfig
    from xlii.vault import ENV_VAR, VAULT_FILE

    monkeypatch.setenv(ENV_VAR, Fernet.generate_key().decode())
    try:
        VAULT_FILE.unlink(missing_ok=True)
    except TypeError:
        if VAULT_FILE.exists():
            VAULT_FILE.unlink()
    cfg = GlobalConfig.load()
    cfg.email_accounts = {
        "x": {
            "imap_host": "imap.test",
            "smtp_host": "smtp.test",
            "user": "u@test.com",
            "vault_ns": "email:x",
        }
    }
    cfg.save()
    account = em.resolve_account(cfg, "x")
    with pytest.raises(em.MissingCredentialError, match="xlii email accounts add"):
        em.connect_imap(account)


def test_connect_imap_faked(personal_account):
    from xlii.config import GlobalConfig

    account = em.resolve_account(GlobalConfig.load(), "personal")
    conn = em.connect_imap(account)
    conn.logout()


def test_parse_html_only_body():
    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText("<p>Hi <b>there</b></p>", "html"))
    msg["Subject"] = "HTML only"
    msg["From"] = "sender@example.com"
    raw = msg.as_bytes()
    parsed = em.parse_raw_message(raw, account="personal", folder="INBOX", uid="1")
    assert "there" in parsed.body


def test_parse_attachment_metadata_only():
    raw = _mime_bytes(body="plain", attachment=True)
    parsed = em.parse_raw_message(raw, account="personal", folder="INBOX", uid="1")
    assert parsed.body == "plain"
    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].name == "file.bin"
    assert parsed.attachments[0].size == len(b"binary-data")


def test_body_cap_truncation_note():
    huge = "x" * (_MAX_TEXT_BYTES + 1000)
    raw = _mime_bytes(body=huge)
    parsed = em.parse_raw_message(raw, account="personal", folder="INBOX", uid="9")
    assert parsed.truncated
    assert "truncated" in parsed.body


def test_list_and_read_cache(tmp_path, personal_account):
    from xlii.config import GlobalConfig

    account = em.resolve_account(GlobalConfig.load(), "personal")
    rows = em.list_messages(account, project_root=tmp_path)
    assert len(rows) >= 1
    msg = em.read_message(account, rows[0].id, project_root=tmp_path)
    assert "World" in msg.body
    cache = tmp_path / ".xlii" / "mail" / f"{msg.id.replace(':', '_')}.json"
    assert cache.is_file()


def test_search_messages(tmp_path, personal_account):
    from xlii.config import GlobalConfig

    account = em.resolve_account(GlobalConfig.load(), "personal")
    rows = em.search_messages(account, "Billing", project_root=tmp_path)
    assert any("Billing" in r.subject for r in rows)


def test_send_message_faked_smtp(personal_account):
    from xlii.config import GlobalConfig

    account = em.resolve_account(GlobalConfig.load(), "personal")
    em.send_message(account, to="dest@example.com", subject="Hi", body="Body")
    assert FakeSMTP.sent
    assert FakeSMTP.sent[0][1] == ["dest@example.com"]


def test_read_email_tool_no_mime_blob(tmp_path, personal_account):
    from xlii.config import GlobalConfig

    account = em.resolve_account(GlobalConfig.load(), "personal")
    rows = em.list_messages(account, project_root=tmp_path)
    ctx = make_tool_ctx(tmp_path)
    out = t_read_email(ctx, {"id": rows[0].id})
    assert not out.is_error
    assert "Content-Transfer-Encoding" not in out.content
    assert "base64" not in out.content.lower()


def test_search_email_tool_flags():
    names = {t.name for t in BUILTIN_TOOLS}
    assert "read_email" in names
    assert "search_email" in names
    assert "send_email" in names
    assert "read_email" in PARALLEL_SAFE
    assert "search_email" in PARALLEL_SAFE
    assert "read_email" in WORKER_REGISTRY
    assert "send_email" not in WORKER_REGISTRY


def test_email_tools_hidden_when_no_account():
    from types import SimpleNamespace

    from xlii.tool_schemas import apply_email_account_gate, tool_schemas

    names = lambda schemas: {s["function"]["name"] for s in schemas}
    full = tool_schemas()
    assert {"read_email", "search_email", "send_email"} <= names(full)

    stripped = apply_email_account_gate(full, SimpleNamespace(email_accounts={}))
    assert not {"read_email", "search_email", "send_email"} & names(stripped)

    kept = apply_email_account_gate(
        full, SimpleNamespace(email_accounts={"personal": {"imap_host": "imap.test"}})
    )
    assert {"read_email", "search_email", "send_email"} <= names(kept)
    # Gate never injects: a chat-blind list without send stays without send.
    blind = [s for s in full if s["function"]["name"] in {"read_email", "web_search"}]
    gated = apply_email_account_gate(
        blind, SimpleNamespace(email_accounts={"personal": {}})
    )
    assert names(gated) == {"read_email", "web_search"}


def test_send_email_refuses_worker(tmp_path, personal_account):
    ctx = make_tool_ctx(tmp_path, is_worker=True)
    out = t_send_email(
        ctx,
        {"to": "a@b.com", "subject": "s", "body": "b"},
    )
    assert out.is_error
    assert "worker" in out.content.lower()


def test_send_email_refuses_headless(tmp_path, personal_account):
    ctx = make_tool_ctx(tmp_path, console=None)
    out = t_send_email(
        ctx,
        {"to": "a@b.com", "subject": "s", "body": "b"},
    )
    assert out.is_error
    assert "headless" in out.content.lower()


def test_send_email_typed_confirm(tmp_path, personal_account, monkeypatch):
    prompts: list[str] = []
    monkeypatch.setattr("xlii.tools._confirm", lambda p: prompts.append(p) or "n")
    ctx = make_tool_ctx(tmp_path, console=FakeConsole())
    out = t_send_email(ctx, {"to": "a@b.com", "subject": "s", "body": "b"})
    assert out.is_error
    assert "cancelled" in out.content.lower()
    assert prompts and "send" in prompts[0].lower()


def test_send_email_yolo_sends(tmp_path, personal_account):
    ctx = make_tool_ctx(tmp_path, yolo=True)
    out = t_send_email(ctx, {"to": "a@b.com", "subject": "s", "body": "b"})
    assert not out.is_error
    assert FakeSMTP.sent


def test_gate_send_requires_send_token(tmp_path, monkeypatch):
    ctx = make_tool_ctx(tmp_path, console=FakeConsole())
    monkeypatch.setattr("xlii.tools._confirm", lambda p: "y")
    refusal = em.gate_send_email(ctx, to="a@b.com", subject="s", body="b")
    assert refusal is not None
    assert "cancelled" in refusal.content.lower()


def test_resolve_account_requires_flag_when_ambiguous(personal_account, monkeypatch):
    from xlii.config import GlobalConfig

    cfg = GlobalConfig.load()
    cfg.email_accounts["personal"].pop("default", None)
    cfg.email_accounts["work"] = {
        "imap_host": "imap.work",
        "smtp_host": "smtp.work",
        "user": "work@test.com",
        "vault_ns": "email:work",
    }
    cfg.save()
    with pytest.raises(em.EmailError, match="--account"):
        em.resolve_account(cfg, None)


# --------------------------------------------------------------------------- #
#  Defect 1 — cache path traversal via a hostile message-id uid
# --------------------------------------------------------------------------- #

def test_parse_message_id_rejects_non_numeric_uid():
    # IMAP uids are numeric; a traversal payload must be refused, not parsed.
    with pytest.raises(em.EmailError, match="uid must be numeric"):
        em._parse_message_id("personal:INBOX:../../../x")


def test_cache_message_contains_traversal_id(tmp_path):
    hostile = em.ParsedMessage(
        id="personal:INBOX:../../../../pwned",
        uid="1",
        account="personal",
        folder="INBOX",
        subject="s",
        sender="from@x",
        to="to@x",
        date="",
        body="b",
    )
    rel = em.cache_message(tmp_path, hostile)
    mail_dir = em.mail_cache_dir(tmp_path).resolve()
    written = (tmp_path / rel).resolve()
    # The cache file stays directly inside .xlii/mail/ …
    assert written.parent == mail_dir
    # … and nothing escaped up to the project root.
    assert not (tmp_path / "pwned.json").exists()


def test_read_message_rejects_hostile_id(tmp_path, personal_account):
    from xlii.config import GlobalConfig

    account = em.resolve_account(GlobalConfig.load(), "personal")
    with pytest.raises(em.EmailError):
        em.read_message(
            account, "personal:INBOX:../../../evil", project_root=tmp_path
        )
    assert not (tmp_path / "evil.json").exists()


# --------------------------------------------------------------------------- #
#  Defect 3 — folder names must round-trip through the message id
# --------------------------------------------------------------------------- #

def test_message_id_folder_roundtrips():
    for folder in ("My_Stuff", "INBOX/Sent", "Weird:Folder", "INBOX"):
        mid = em.message_id("personal", folder, "42")
        account, parsed_folder, uid = em._parse_message_id(mid)
        assert account == "personal"
        assert parsed_folder == folder
        assert uid == "42"


# --------------------------------------------------------------------------- #
#  Defect 2 — piped/headless send must refuse cleanly (no EOFError traceback)
# --------------------------------------------------------------------------- #

def test_gate_send_eof_is_clean_cancel(tmp_path, monkeypatch):
    def _raise(_prompt):
        raise EOFError

    monkeypatch.setattr("xlii.tools._confirm", _raise)
    ctx = make_tool_ctx(tmp_path, console=FakeConsole())
    refusal = em.gate_send_email(ctx, to="a@b.com", subject="s", body="b")
    assert refusal is not None
    assert refusal.is_error
    assert "cancel" in refusal.content.lower()


def test_cmd_send_piped_stdin_refuses_without_yolo(
    tmp_path, personal_account, monkeypatch
):
    import argparse
    import io

    from xlii.cmds import email as email_cmd

    fake_console = FakeConsole()
    monkeypatch.setattr(email_cmd, "console", fake_console)

    # Piped (non-tty) stdin: the body read consumes it, so a later input()
    # would hit EOF. cmd_send must refuse up front rather than crash.
    fake_stdin = io.StringIO("piped body")
    fake_stdin.isatty = lambda: False
    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.chdir(tmp_path)

    args = argparse.Namespace(
        account="personal",
        to="dest@example.com",
        subject="Hi",
        body=None,
        html=None,
        yolo=False,
    )
    rc = email_cmd.cmd_send(args)  # must not raise EOFError
    assert rc == 1
    assert "refus" in fake_console.text.lower()
    assert not FakeSMTP.sent


# --------------------------------------------------------------------------- #
#  E4 — /mail REPL command: arg parser + registration
# --------------------------------------------------------------------------- #

def test_parse_mail_args_search_and_flags():
    from xlii.repl_cmds.mail import _parse_mail_args

    verb, rest, flags = _parse_mail_args(
        ["/mail", "search", "hello", "world", "--unread"]
    )
    assert verb == "search"
    assert rest == ["hello", "world"]
    assert flags == {"unread": "1"}


def test_parse_mail_args_send_flags():
    from xlii.repl_cmds.mail import _parse_mail_args

    verb, rest, flags = _parse_mail_args(
        ["/mail", "send", "--to", "a@b.com", "--subject", "Hi", "--body", "text"]
    )
    assert verb == "send"
    assert rest == []
    assert flags == {"to": "a@b.com", "subject": "Hi", "body": "text"}


def test_parse_mail_args_account_flag():
    from xlii.repl_cmds.mail import _parse_mail_args

    verb, rest, flags = _parse_mail_args(["/mail", "list", "--account", "work"])
    assert verb == "list"
    assert flags["account"] == "work"


def test_parse_mail_args_requires_verb():
    from xlii.repl_cmds.mail import _parse_mail_args

    with pytest.raises(ValueError):
        _parse_mail_args(["/mail"])


def test_mail_repl_command_registers():
    from xlii.commands import iter_repl_commands, unregister_repl_command
    import xlii.repl_cmds.mail as mailmod

    unregister_repl_command("mail")
    try:
        mailmod.register()
        cmds = {c.name: c for c in iter_repl_commands()}
        assert "mail" in cmds
        assert cmds["mail"].handler is mailmod._mail_handler
        assert cmds["mail"].category == "session"
    finally:
        unregister_repl_command("mail")
