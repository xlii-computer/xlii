"""Email the webcode magic link — same grant, other mouth."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.daemon_gate import classify_dispatch, format_webcode_reply
from xlii.webcode_mail import (
    append_email_note,
    code_from_webcode_reply,
    deliver_webcode_email,
    magic_link_mail,
    mint_reply_and_email,
    owner_inbox,
    webcode_magic_link,
)


def _cfg(*, owner="", user="me@test.com"):
    return SimpleNamespace(
        email_accounts={
            "personal": {
                "imap_host": "imap.test",
                "smtp_host": "smtp.test",
                "user": user,
                "default": True,
            }
        },
        serve={"public": {"owner_email": owner} if owner else {}},
    )


def test_magic_link_is_the_pairing_url():
    assert webcode_magic_link("X7K2M9Q4", "https://xlii-code.com/") == (
        "https://xlii-code.com/?code=X7K2M9Q4"
    )


def test_code_round_trips_the_omemo_reply():
    reply = format_webcode_reply("X7K2M9Q4", "https://xlii-code.com", 300)
    assert code_from_webcode_reply(reply) == "X7K2M9Q4"


def test_owner_inbox_uses_account_user():
    account, to = owner_inbox(_cfg())
    assert to == "me@test.com"
    assert account.name == "personal"


def test_owner_inbox_prefers_serve_public_owner_email():
    _, to = owner_inbox(_cfg(owner="desk@work.test"))
    assert to == "desk@work.test"


def test_owner_inbox_missing_accounts_is_honest():
    from xlii.email import EmailError

    with pytest.raises(EmailError, match="no email accounts"):
        owner_inbox(SimpleNamespace(email_accounts={}, serve={}))


def test_deliver_sends_link_not_a_chat(monkeypatch):
    sent = []

    def fake_send(account, *, to, subject, body):
        sent.append((account.user, to, subject, body))

    to = deliver_webcode_email(
        code="ABCD1234",
        base_url="https://xlii-code.com",
        ttl_s=300,
        cfg=_cfg(owner="pc@work.test"),
        send=fake_send,
    )
    assert to == "pc@work.test"
    _from, dest, subject, body = sent[0]
    assert dest == "pc@work.test"
    assert subject == "xlii face"
    assert "https://xlii-code.com/?code=ABCD1234" in body
    assert "5m" in body


def test_mint_reply_and_email_keeps_the_xmpp_line_when_send_fails():
    def boom(*a, **k):
        raise RuntimeError("smtp down")

    reply = mint_reply_and_email(
        code="ABCD1234",
        base_url="https://xlii-code.com",
        ttl_s=300,
        cfg=_cfg(),
        send=boom,
    )
    assert reply.startswith("code: ABCD1234")
    assert "email failed" in reply
    assert "smtp down" in reply


def test_append_email_note_dry():
    assert append_email_note("code: X", to="a@b") == "code: X\n[daemon] emailed a@b"
    assert "failed" in append_email_note("code: X", err="no account")


def test_webcode_email_classifies():
    d = classify_dispatch(
        "webcode email",
        verbs_dir=__import__("pathlib").Path("/tmp/no-verbs"),
        fallback_enabled=True,
        fallback_workspace="",
        sender="",
    )
    assert d.kind == "webcode"
    assert d.webcode_action == "email"
    assert d.webcode_mode == "full"


def test_webcode_email_preview_classifies():
    d = classify_dispatch(
        "webcode email preview",
        verbs_dir=__import__("pathlib").Path("/tmp/no-verbs"),
        fallback_enabled=True,
        fallback_workspace="",
        sender="",
    )
    assert d.webcode_action == "email"
    assert d.webcode_mode == "preview"


def test_webcode_mail_alias():
    d = classify_dispatch(
        "webcode mail",
        verbs_dir=__import__("pathlib").Path("/tmp/no-verbs"),
        fallback_enabled=True,
        fallback_workspace="",
        sender="",
    )
    assert d.webcode_action == "email"


def test_magic_link_mail_seconds_when_ttl_not_even_minutes():
    subject, body = magic_link_mail(
        code="X", base_url="https://x", ttl_s=90,
    )
    assert subject == "xlii face"
    assert "90s" in body
