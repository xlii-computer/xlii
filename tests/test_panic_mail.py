"""Panic mail gates — silent drop, kill one-shot, destroy round 2."""

from types import SimpleNamespace

from xlii.email import UnseenMail, authentication_results_ok, parse_from_addr
from xlii.panic_mail import (
    PanicProof,
    consume_panic_proof,
    handle_panic,
    mint_panic_proof,
)


AUTH = "mx.example; dkim=pass header.i=@ex.test; spf=pass smtp.mailfrom=a@ex.test"
PHRASES = ["yellow teapot", "purple socks", "ugly lamp"]
ALLOW = ["owner@ex.test"]


def _mail(*, sender="Owner <owner@ex.test>", subject="yellow teapot",
          body="hello", auth=AUTH) -> UnseenMail:
    return UnseenMail(
        uid="1", sender=sender, subject=subject, body=body, auth_results=auth,
    )


def test_parse_from_and_auth_results():
    assert parse_from_addr("Nick <owner@ex.test>") == "owner@ex.test"
    assert authentication_results_ok(AUTH) is True
    assert authentication_results_ok("") is False
    assert authentication_results_ok("dkim=pass") is False  # no spf
    assert authentication_results_ok("dkim=fail; spf=pass") is False
    assert authentication_results_ok("dkim=pass; spf=fail") is False


def test_unknown_from_drops():
    r = handle_panic(
        _mail(sender="evil@ex.test"),
        allowlist=ALLOW, phrases=PHRASES,
    )
    assert r.status == "dropped"
    assert r.reason == "gate"


def test_bad_dkim_drops():
    r = handle_panic(
        _mail(auth="dkim=fail; spf=pass"),
        allowlist=ALLOW, phrases=PHRASES,
    )
    assert r.status == "dropped"


def test_wrong_subject_drops():
    r = handle_panic(
        _mail(subject="not a phrase"),
        allowlist=ALLOW, phrases=PHRASES,
    )
    assert r.status == "dropped"


def test_kill_one_shot(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_PANIC_PENDING", str(tmp_path / "pending.json"))
    killed = []
    r = handle_panic(
        _mail(body="please\nkill throne\n"),
        allowlist=ALLOW, phrases=PHRASES,
        kill_fn=lambda: killed.append("k") or "masked",
    )
    assert r.status == "kill"
    assert killed == ["k"]


def test_destroy_needs_round2(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_PANIC_PENDING", str(tmp_path / "pending.json"))
    ran = []
    sent = []
    r = handle_panic(
        _mail(body="destroy throne\n"),
        allowlist=ALLOW, phrases=PHRASES,
        totp_secret="MFRGGZDFMZTWQ2LK",  # dummy; not verified this step
        send_fn=lambda to, s, b: sent.append((to, s, b)),
        destroy_fn=lambda *a, **k: ran.append(k),
    )
    assert r.status == "destroy_pending"
    assert ran == []
    assert sent and "authenticator" in sent[0][2]
    assert "yellow teapot" not in sent[0][2]


def test_destroy_round2_totp(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_PANIC_PENDING", str(tmp_path / "pending.json"))
    monkeypatch.setattr(
        "xlii.totp.verify", lambda secret, code, now, **k: code == "123456"
    )
    ran = []
    handle_panic(
        _mail(body="destroy all\n"),
        allowlist=ALLOW, phrases=PHRASES,
        totp_secret="ANYSECRET", now=1.0,
        send_fn=lambda *a: None,
        destroy_fn=lambda *a, **k: ran.append((a, k)),
    )
    assert ran == []
    r = handle_panic(
        _mail(body="123456"),
        allowlist=ALLOW, phrases=PHRASES,
        totp_secret="ANYSECRET", now=1.0,
        destroy_fn=lambda *a, **k: ran.append((a, k)),
    )
    assert r.status == "destroy_run"
    assert ran and ran[0][1].get("proof") is not None
    assert ran[0][1]["level"] == 1
    assert ran[0][1]["dry_run"] is False


def test_round2_fail_drops(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_PANIC_PENDING", str(tmp_path / "pending.json"))
    from xlii import totp

    secret = totp.generate_secret()
    handle_panic(
        _mail(body="destroy throne"),
        allowlist=ALLOW, phrases=PHRASES,
        totp_secret=secret, now=1_700_000_000,
        send_fn=lambda *a: None,
    )
    ran = []
    r = handle_panic(
        _mail(body="000000"),
        allowlist=ALLOW, phrases=PHRASES,
        totp_secret=secret, now=1_700_000_000,
        destroy_fn=lambda *a, **k: ran.append(1),
    )
    assert r.status == "dropped"
    assert r.reason == "round2"
    assert ran == []


def test_proof_not_forgeable():
    assert consume_panic_proof(None) is False
    assert consume_panic_proof(SimpleNamespace(nonce="x")) is False
    fake = PanicProof(nonce="deadbeef", aim="throne", target="", minted_at=0)
    assert consume_panic_proof(fake) is False
    real = mint_panic_proof(aim="throne")
    assert consume_panic_proof(real) is True
    assert consume_panic_proof(real) is False  # one shot


def test_run_destroy_accepts_proof(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_DESTROY_JOURNAL", str(tmp_path / "j.jsonl"))
    monkeypatch.setenv("XLII_MINTED_PATH", str(tmp_path / "minted.json"))
    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    from xlii.destroy import run_destroy
    from xlii.panic_mail import mint_panic_proof

    proof = mint_panic_proof(aim="throne")
    report = run_destroy(
        "throne",
        level=1,
        dry_run=False,
        local_only=True,
        console=None,
        proof=proof,
        journal_path=tmp_path / "j.jsonl",
    )
    assert not any(f.get("kind") == "gate" for f in report.failed)


def test_run_destroy_rejects_forged_proof(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_DESTROY_JOURNAL", str(tmp_path / "j.jsonl"))
    from xlii.destroy import run_destroy

    report = run_destroy(
        "throne",
        level=1,
        dry_run=False,
        console=None,
        proof=object(),
        journal_path=tmp_path / "j.jsonl",
    )
    assert any("panic proof" in f.get("error", "") for f in report.failed)
