"""House JID mint — the rider creates addresses, not an agent over SSH."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.jid_house import (
    JidError,
    add_account,
    face_localpart,
    house_domain,
    list_accounts,
    register_command,
    set_house,
)


def _cfg(**extra):
    box = {"xmpp": dict(extra.pop("xmpp", {})), "saves": 0}

    def save():
        box["saves"] += 1

    return SimpleNamespace(xmpp=box["xmpp"], save=save, _box=box)


def test_node_setup_refuses_hostname(monkeypatch):
    from argparse import Namespace
    from xlii.cmds.nodes import cmd_node_setup

    cfg = _cfg(xmpp={"domain": "home.xlii-remote.com"})
    cfg.fabric_nodes = {}
    monkeypatch.setattr("xlii.cmds.nodes.GlobalConfig", SimpleNamespace(load=lambda: cfg))
    rc = cmd_node_setup(Namespace(
        name="acer", remote="", gig="", mint_xai=False, new_key=False,
    ))
    assert rc == 1


def test_next_node_name_skips_taken_limbs():
    from xlii.jid_house import next_node_name

    cfg = _cfg(xmpp={"accounts": {
        "node1": {"jid": "node1@h", "role": "node", "node": "node1"},
    }})
    cfg.fabric_nodes = {"node2": {"remote": "node2"}}
    assert next_node_name(cfg) == "node3"
    assert next_node_name(_cfg()) == "node1"


def test_face_localpart_is_the_body_not_a_second_account():
    assert face_localpart("node1") == "node1"
    assert "desk" not in face_localpart("node1")
    with pytest.raises(JidError):
        face_localpart("2acer")


def test_register_command_is_quoted_and_noninteractive():
    cmd = register_command("node1", "home.xlii-remote.com", "p a'ss")
    assert cmd.startswith("sudo -n prosodyctl register ")
    assert "node1" in cmd
    assert "home.xlii-remote.com" in cmd
    assert "in-band" not in cmd.lower()


def test_set_house_persists_domain_and_remote():
    cfg = _cfg()
    set_house(cfg, domain="home.xlii-remote.com", admin_remote="xliiec2")
    assert house_domain(cfg) == "home.xlii-remote.com"
    assert cfg.xmpp["admin_remote"] == "xliiec2"
    assert cfg._box["saves"] == 1


def test_add_without_admin_remote_ledgers_and_returns_command(monkeypatch):
    cfg = _cfg(xmpp={"domain": "home.xlii-remote.com"})
    monkeypatch.setattr("xlii.jid_house._store_password", lambda *_a, **_k: True)
    mint = add_account(cfg, "me", role="me")
    assert mint.jid == "me@home.xlii-remote.com"
    assert mint.registered is False
    assert "prosodyctl register" in mint.command
    assert "me" in mint.command
    assert list_accounts(cfg)[0].jid == mint.jid


def test_add_runs_exec_when_provided(monkeypatch):
    cfg = _cfg(xmpp={"domain": "home.xlii-remote.com", "admin_remote": "xliiec2"})
    seen = []
    monkeypatch.setattr("xlii.jid_house._store_password", lambda *_a, **_k: True)
    mint = add_account(
        cfg, "node1", role="node", node="node1",
        exec_fn=lambda cmd: seen.append(cmd) or b"ok",
    )
    assert mint.registered is True
    assert mint.node == "node1"
    assert mint.role == "node"
    assert seen and "node1" in seen[0]
    assert "desk" not in seen[0]


def test_add_duplicate_refuses():
    cfg = _cfg(xmpp={
        "domain": "home.xlii-remote.com",
        "accounts": {"acer": {"jid": "acer@home.xlii-remote.com", "role": "node"}},
    })
    with pytest.raises(JidError, match="already"):
        add_account(cfg, "acer", role="node", register=False)


def test_adopt_does_not_call_exec(monkeypatch):
    cfg = _cfg(xmpp={"domain": "home.xlii-remote.com"})
    monkeypatch.setattr("xlii.jid_house._store_password", lambda *_a, **_k: False)
    mint = add_account(cfg, "throne", role="throne", adopt=True, exec_fn=lambda *_: (_ for _ in ()).throw(RuntimeError("no")))
    assert mint.registered is False
    assert mint.jid.endswith("@home.xlii-remote.com")


def test_form_seed_has_no_password():
    from xlii.jid_form import render_form_html

    html = render_form_html({
        "title": "XMPP addresses",
        "lead": "mint",
        "domain": "home.xlii-remote.com",
        "admin_remote": "",
        "remotes": ["xliiec2"],
        "roles": ["me", "node", "face"],
        "accounts": [],
    })
    assert "xlii jid house" in html
    assert "xlii jid add" in html
    assert "password" not in html.lower() or "Passwords are never" in html or "never" in html
    assert "--password" not in html


def test_install_form_seeds_both_mouths():
    from xlii.install_form import render_form_html

    html = render_form_html({
        "title": "Install node",
        "lead": "stamp",
        "domain": "home.xlii-remote.com",
        "jids": [],
        "remotes": ["acer"],
        "nodes": [],
    })
    assert "xlii jid add" in html
    assert "--role face" not in html
    assert "xlii node setup" in html
    assert "--journal" in html
    assert "--password" not in html


def test_face_jid_is_not_node_daemon():
    from xlii.face_remote import is_node_daemon_jid

    assert is_node_daemon_jid("daemon@home.xlii-remote.com")
    assert not is_node_daemon_jid("node1@home.xlii-remote.com")
    assert not is_node_daemon_jid("acer@home.xlii-remote.com")
    assert not is_node_daemon_jid("throne@home.xlii-remote.com")


def test_add_needs_domain():
    with pytest.raises(JidError, match="domain"):
        add_account(_cfg(), "me", role="me", register=False)
