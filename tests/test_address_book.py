"""Project cards may share a git URL. The address book cannot ride along."""

from __future__ import annotations

from types import SimpleNamespace

from xlii.address_book import (
    FORBIDDEN_CATALOG_KEYS,
    files_root_if_known,
    is_known_connection,
    public_catalog_row,
    sanitize_repo,
    scrub_catalog_row,
)


def test_sanitize_repo_accepts_public_git_urls():
    assert sanitize_repo("https://github.com/n3r4-life/iXaac-lab.git")
    assert sanitize_repo("git@github.com:n3r4-life/iXaac-lab.git")
    assert sanitize_repo("ssh://git@host.example/src/app.git")


def test_sanitize_repo_refuses_credentials_and_junk():
    assert sanitize_repo("https://user:secret@github.com/x/y.git") == ""
    assert sanitize_repo("https://ghp_abcdefghijklmnopqrstuvwxyz@github.com/x/y") == ""
    assert sanitize_repo("-----BEGIN OPENSSH PRIVATE KEY-----\nxxx") == ""
    assert sanitize_repo("vault_ref:foo") == ""
    assert sanitize_repo("") == ""


def test_scrub_drops_address_book_keys_keeps_repo():
    row = scrub_catalog_row({
        "name": "app",
        "path": "/vm/app",
        "collection_id": "",
        "created_at": "t",
        "node": "node1",
        "repo": "https://github.com/acme/app.git",
        "password": "hunter2",
        "vault_ref": "box",
        "ftp_connections": {"box": {"host": "evil"}},
        "key_path": "/home/.ssh/id_rsa",
    })
    assert row["repo"] == "https://github.com/acme/app.git"
    assert row["name"] == "app"
    assert "password" not in row
    assert "vault_ref" not in row
    assert "ftp_connections" not in row
    assert "key_path" not in row
    assert FORBIDDEN_CATALOG_KEYS.isdisjoint(row)


def test_unknown_files_root_is_not_a_way_in():
    cfg = SimpleNamespace(ftp_connections={"homebox": {"host": "a"}})
    assert files_root_if_known("sftp://homebox/src/app", cfg=cfg) == "sftp://homebox/src/app"
    assert files_root_if_known("sftp://evil/src/app", cfg=cfg) == ""
    assert not is_known_connection("evil", cfg=cfg)


def test_public_catalog_row_never_emits_secrets():
    entry = SimpleNamespace(
        name="app", path="/local/app", collection_id="", created_at="t",
        node="node1", remote_path="app", repo="https://github.com/acme/app.git",
        password="nope",
    )
    row = public_catalog_row(entry)
    assert row["repo"].startswith("https://")
    assert FORBIDDEN_CATALOG_KEYS.isdisjoint(row)
    assert "password" not in row
