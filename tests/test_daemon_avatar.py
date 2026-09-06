"""Packaged 42-brain avatar + XMPP vCard/PEP publish (no live slixmpp)."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

from xlii.daemon_avatar import (
    AVATAR_MIME,
    avatar_path,
    load_avatar,
    mark_path,
    publish_daemon_avatar,
)


def test_avatar_is_square_png():
    avatar = load_avatar()
    assert avatar.path == avatar_path()
    assert avatar.path.is_file()
    assert avatar.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert avatar.mime == AVATAR_MIME
    assert avatar.width == avatar.height == 512
    assert avatar.sha1 == hashlib.sha1(avatar.data).hexdigest()
    assert len(avatar.sha1) == 40
    assert mark_path().is_file()


def test_publish_skipped_without_plugins():
    xmpp = _FakeXMPP(plugins={})
    assert asyncio.run(publish_daemon_avatar(xmpp)) == "skipped"
    assert xmpp.hashes == []


def test_publish_unchanged_when_vcard_already_ours():
    avatar = load_avatar()
    xmpp = _FakeXMPP(
        plugins={
            "xep_0054": _Fake0054(binval=avatar.data),
            "xep_0153": _Fake0153(),
            "xep_0084": _Fake0084(),
        }
    )
    assert asyncio.run(publish_daemon_avatar(xmpp)) == "unchanged"
    assert xmpp["xep_0153"].set_calls == []
    assert xmpp["xep_0084"].published == []
    assert xmpp.hashes == [avatar.sha1]


def test_publish_sets_vcard_and_pep_when_missing():
    avatar = load_avatar()
    xmpp = _FakeXMPP(
        plugins={
            "xep_0054": _Fake0054(binval=None),
            "xep_0153": _Fake0153(),
            "xep_0084": _Fake0084(),
        }
    )
    assert asyncio.run(publish_daemon_avatar(xmpp)) == "set"
    assert xmpp["xep_0153"].set_calls == [(avatar.data, avatar.mime)]
    assert xmpp["xep_0084"].published == [avatar.data]
    meta = xmpp["xep_0084"].metadata[0]
    assert meta["id"] == avatar.sha1
    assert meta["type"] == avatar.mime
    assert meta["bytes"] == str(len(avatar.data))
    assert meta["width"] == "512"
    assert meta["height"] == "512"


def test_publish_sets_when_get_vcard_errors():
    xmpp = _FakeXMPP(
        plugins={
            "xep_0054": _Fake0054(boom=True),
            "xep_0153": _Fake0153(),
        }
    )
    assert asyncio.run(publish_daemon_avatar(xmpp)) == "set"
    assert xmpp["xep_0153"].set_calls


def test_daemon_registers_avatar_plugins():
    src = Path(__file__).resolve().parents[1] / "xlii" / "daemon.py"
    text = src.read_text()
    assert 'register_plugin("xep_0054")' in text
    assert 'register_plugin("xep_0153")' in text
    assert 'register_plugin("xep_0084")' in text
    assert "await self._publish_avatar()" in text
    assert "from xlii.daemon_avatar import publish_daemon_avatar" in text


class _Fake0054:
    def __init__(self, *, binval: bytes | None = None, boom: bool = False) -> None:
        self.binval = binval
        self.boom = boom

    async def get_vcard(self, cached: bool = True, **_kwargs):
        if self.boom:
            raise RuntimeError("no vcard")
        return {"vcard_temp": {"PHOTO": {"BINVAL": self.binval}}}


class _Fake0153:
    def __init__(self) -> None:
        self.set_calls: list[tuple[bytes, str]] = []
        self.api = {"set_hash": self._set_hash}
        self._xmpp: _FakeXMPP | None = None

    async def _set_hash(self, jid, args=None, **_kwargs) -> None:
        if self._xmpp is not None:
            self._xmpp.hashes.append(args)

    def set_avatar(self, jid=None, avatar=None, mtype=None, **_kwargs):
        self.set_calls.append((avatar, mtype))

        async def _done():
            return None

        return _done()


class _Fake0084:
    def __init__(self) -> None:
        self.published: list[bytes] = []
        self.metadata: list[dict] = []

    async def publish_avatar(self, data: bytes, **_kwargs) -> None:
        self.published.append(data)

    async def publish_avatar_metadata(self, items=None, **_kwargs) -> None:
        self.metadata.append(dict(items or {}))


class _FakeXMPP:
    def __init__(self, *, plugins: dict) -> None:
        self.boundjid = SimpleNamespace(bare="daemon@example.test")
        self._plugins = plugins
        self.hashes: list[str] = []
        plug = plugins.get("xep_0153")
        if isinstance(plug, _Fake0153):
            plug._xmpp = self

    def __getitem__(self, key: str):
        return self._plugins[key]
