"""/howto latency (howto-fast): the prompt weight, the pull tool, the palette.

The measured problem: the bare-/howto attachment was 26,237 chars (~6,500
tokens), 17,161 of them the whole ``get_repl_help`` block fenced into the
system prompt on EVERY howto turn — time-to-first-token paid again each turn.
These pins hold the four fixes in place: a compact command index, a
``command_help`` tool to pull the detail that left, a Q&A-shaped tool palette,
and an off-turn corpus warm so the first ``/howto <extended-topic>`` never waits
on GitHub mid-turn.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from xlii.repl_cmds import howto, register_all

register_all()


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Standing test-isolation rule: never touch the real ~/.cache (the help
    corpus etag/body cache and corpus.json live under XDG_CACHE_HOME)."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))


# The cap is a budget, not the measurement: the compact index took the
# attachment from 26,456 to ~11.2 KB. The floor under it is the BUNDLED content
# this command exists to deliver — xlii/prompts/howto.md (5.9 KB) plus the
# manifest's topic table (2.1 KB) plus the framing — so a ~6 KB total is not
# reachable without trimming the guide itself, which is a separate call. Both
# numbers drift as commands and topics are added; the cap has ~800 chars of
# headroom and the half-of-old pin below is the one that catches a regression.
_ATTACHMENT_CAP = 12_000
_OLD_SIZE = 26_456


class _Owner:
    """Quacks like the bits of REPLState that the handler touches."""

    def __init__(self):
        self.attached_docs: list[tuple[str, str]] = []
        self.howto_mode = False

    def attach_doc(self, name, content):
        self.attached_docs.append((name, content))

    def detach_doc(self, name):
        before = len(self.attached_docs)
        self.attached_docs = [(n, c) for n, c in self.attached_docs if n != name]
        return len(self.attached_docs) < before


def _ctx(owner):
    return {
        "console": Console(file=io.StringIO(), force_terminal=False),
        "state": owner,
        "project": None,
        "command_scope": "code",
    }


# --------------------------------------------------------------------------- #
# T1 — the attachment stopped carrying the full slash listing
# --------------------------------------------------------------------------- #


def test_attachment_is_under_the_prompt_budget():
    body = howto._local_content("code", None)
    assert len(body) < _ATTACHMENT_CAP
    assert len(body) < _OLD_SIZE // 2  # the regression that started this


def test_command_index_is_names_not_the_help_block():
    from xlii.commands import get_repl_help

    index = howto._live_commands("code")
    assert len(index) < 4_000
    # Complete vocabulary, no usage lines: every name present, no help block.
    assert "/plan" in index and "/howto" in index and "/describe" in index
    assert get_repl_help("code") not in index
    assert "SHELL" not in index          # a get_repl_help section header
    assert "<0.0..2.0>" not in index     # a usage-line fragment
    # And it points the model at the pull side rather than at guessing.
    assert "command_help" in index


def test_command_index_carries_aliases():
    index = howto._live_commands("code")
    assert "/attach (/doc)" in index


def test_framing_tells_the_model_to_call_command_help():
    assert "command_help" in howto._FRAMING


def test_topic_attachment_also_shrank():
    content, _ = howto._topic_content("install", "code", None)
    assert len(content) < _ATTACHMENT_CAP
    assert "xlii setup" in content       # the topic body still rides


# --------------------------------------------------------------------------- #
# T1 — command_help: the pull side of the compact index
# --------------------------------------------------------------------------- #


def _command_help(name):
    from xlii.tool_handlers import t_command_help

    return t_command_help(None, {"name": name})


def test_command_help_returns_usage_for_a_known_command():
    res = _command_help("plan")
    assert not res.is_error
    assert "/plan" in res.content
    assert "usage:" in res.content
    from xlii.commands import find_repl_command

    cmd = find_repl_command("/plan", repl="code")
    assert cmd.usage in res.content
    assert cmd.description in res.content


def test_command_help_accepts_a_leading_slash_and_an_alias():
    bare = _command_help("attach")
    slashed = _command_help("/attach")
    assert slashed.content == bare.content
    # /doc is an alias of /attach — resolving it must land on the real command.
    assert _command_help("doc").content == bare.content
    assert "/doc" in bare.content        # …and the aliases are reported


def test_command_help_misses_clearly():
    res = _command_help("zxqwv")
    assert res.is_error
    assert "no slash command named" in res.content
    assert "zxqwv" in res.content
    # A near-miss gets pointed at the real name instead of left to guess.
    near = _command_help("plna")
    assert near.is_error and "/plan" in near.content


def test_command_help_needs_a_name():
    res = _command_help("")
    assert res.is_error and "needs `name`" in res.content


def test_command_help_is_advertised_and_dispatchable():
    from xlii.tool_schemas import get_tool_fn, tool_schemas

    assert "command_help" in {s["function"]["name"] for s in tool_schemas()}
    assert get_tool_fn("command_help") is not None


# --------------------------------------------------------------------------- #
# T2 — the howto palette is Q&A-shaped
# --------------------------------------------------------------------------- #


def test_howto_palette_excludes_writers_and_includes_command_help():
    from xlii.mode_contract import howto_tools_policy

    policy = howto_tools_policy()
    for granted in ("read_file", "grep", "search_project", "xai_docs",
                    "command_help", "request_deep_search"):
        assert policy.permits(granted), granted
    for withheld in ("write_file", "edit_file", "bash", "dispatch_subagent",
                     "code_execute", "codex_run_task", "send_email",
                     "generate_image"):
        assert not policy.permits(withheld), withheld


def test_howto_keeps_only_the_self_doc_door():
    """K6 gave talk six door tools. A help turn keeps explain_xlii — reading the
    shipped self-doc IS the help question — and drops the ones that move the
    desk, open panes, or write."""
    import xlii.mode_contract as mc

    policy = mc.howto_tools_policy()
    doors = mc.get_mode("howto").capabilities.door_tools
    assert policy.permits("explain_xlii") and doors.permits("explain_xlii")
    for door in mc.CHAT_DOOR_TOOLS - {"explain_xlii"}:
        assert not policy.permits(door), door
        assert not doors.permits(door), door
    # And the set is derived from K6's, so a new door can't silently join.
    assert mc.HOWTO_DOOR_TOOLS <= mc.CHAT_DOOR_TOOLS


def test_howto_contract_matches_the_live_policy():
    import xlii.mode_contract as mc

    contract = mc.get_mode("howto")
    assert contract.capabilities.tools.allow == mc.HOWTO_TOOLS
    assert contract.capabilities.door_tools.allow == mc.HOWTO_DOOR_TOOLS
    # No writer, no shell ⇒ the overlay records read awareness (capability,
    # not posture — the axis mode_contract documents).
    assert contract.awareness == mc.AWARENESS_READ


def test_howto_gate_narrows_a_turn_and_survives_the_chat_surface():
    """The agent applies the howto policy ahead of chat's, only when no
    controller owns the turn — so a howto question keeps command_help on BOTH
    surfaces, and a controller mode's curated palette is untouched."""
    import re
    from pathlib import Path

    import xlii.agent as agent_mod

    src = Path(agent_mod.__file__).read_text()
    howto_gate = src.index("self.session.howto_mode:")
    chat_gate = src.index("elif self.active_mode is None and self.session.conversational")
    assert howto_gate < chat_gate           # narrower contract wins
    gate_src = src[howto_gate - 200:chat_gate]
    assert "self.active_mode is None" in gate_src
    assert re.search(r"howto_tools_policy", gate_src)
    # K1/K6 stay intact for NON-howto turns: hire still re-adds the worker on a
    # talk turn, and the door gate still picks chat's / code's posture.
    assert 'if self.session.hire != "none":' in src
    assert 'get_mode("chat").capabilities.door_tools' in src
    assert 'get_mode("code").capabilities.door_tools' in src
    assert 'get_mode("howto").capabilities.door_tools' in src


# --------------------------------------------------------------------------- #
# T3 — the corpus warm rides a thread, once, and never raises
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _reset_warm(monkeypatch):
    monkeypatch.setattr(howto, "_WARMED", False)


def _sync_threads(monkeypatch):
    """Run the warm thread inline so the assertion isn't a race."""
    import threading

    class _Now:
        def __init__(self, target, name=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(threading, "Thread", _Now)


def test_warm_is_invoked_once_per_process(monkeypatch):
    _sync_threads(monkeypatch)
    calls = []
    monkeypatch.setattr(howto, "sync_corpus_cache", lambda: calls.append(1))

    owner = _Owner()
    howto._howto_handler("/howto", _ctx(owner))
    howto._howto_handler("/howto install", _ctx(owner))
    howto._howto_handler("/howto", _ctx(owner))
    assert calls == [1]


def test_warm_swallows_network_failure(monkeypatch):
    _sync_threads(monkeypatch)

    def boom():
        raise OSError("no network")

    monkeypatch.setattr(howto, "sync_corpus_cache", boom)
    owner = _Owner()
    # The REPL must not notice: no raise, mode still entered, guide attached.
    assert howto._howto_handler("/howto", _ctx(owner)) is True
    assert owner.howto_mode is True
    assert dict(owner.attached_docs).get("howto")


def test_warm_is_off_the_turn_path(monkeypatch):
    """A daemon thread, never joined — the REPL returns while the fetch runs."""
    started = {}

    def _capture(target, name=None, daemon=None):
        started["daemon"] = daemon
        started["target"] = target
        return type("T", (), {"start": lambda self: None})()

    import threading

    monkeypatch.setattr(threading, "Thread", _capture)
    monkeypatch.setattr(howto, "sync_corpus_cache", lambda: None)
    howto._howto_handler("/howto", _ctx(_Owner()))
    assert started["daemon"] is True


def test_warm_does_not_fire_when_leaving_the_mode(monkeypatch):
    _sync_threads(monkeypatch)
    calls = []
    monkeypatch.setattr(howto, "sync_corpus_cache", lambda: calls.append(1))
    howto._howto_handler("/howto off", _ctx(_Owner()))
    assert calls == []


def test_cached_topic_body_never_touches_the_network(monkeypatch):
    """The mid-turn read is cache-FIRST: a warmed slot must not even attempt a
    conditional GET, because a 304 is still a wait the operator feels."""
    import xlii.help_corpus as hc

    # A synthetic extended-tier topic: no bundled copy, no docs/help copy (this
    # tree ships both for every real topic), so the read reaches the remote leg.
    manifest = hc._parse_manifest({
        "version": 2,
        "repo": "n3r4-life/iXaac-lab",
        "ref": "main",
        "base_path": "docs/help",
        "topics": {"ghost": {"title": "Ghost", "path": "topics/ghost.md",
                             "tier": "extended"}},
    })
    hc.cache_dir().mkdir(parents=True, exist_ok=True)
    hc._cache_body_path(hc._remote_url(manifest, "topics/ghost.md")).write_text("# warm body")

    def boom(*a, **k):
        raise AssertionError("load_topic_body hit the network with a warm cache")

    monkeypatch.setattr(hc, "http_get_cached", boom)
    assert hc.load_topic_body(manifest, "ghost") == ("# warm body", "cache")
    # Without the warm slot it still reaches out (the leg wasn't removed).
    hc._cache_body_path(hc._remote_url(manifest, "topics/ghost.md")).unlink()
    with pytest.raises(AssertionError, match="hit the network"):
        hc.load_topic_body(manifest, "ghost")


# --------------------------------------------------------------------------- #
# T4 — the shipped help ref is pinned to the shipped version
# --------------------------------------------------------------------------- #


def _stamp_module():
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "xlii_stamp_version", root / "scripts" / "stamp_version.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_stamp_rewrites_the_bundled_manifest_ref(tmp_path):
    stamp = _stamp_module()
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("version: 2\nrepo: n3r4-life/iXaac-lab\nref: main\nbase_path: docs/help\n")
    assert stamp.pin_help_ref("0.26229.5188", path=manifest) == "v0.26229.5188"
    text = manifest.read_text()
    assert "ref: v0.26229.5188" in text
    assert "ref: main" not in text
    # Everything else is untouched — this is a one-key rewrite, not a re-emit.
    assert "repo: n3r4-life/iXaac-lab" in text
    assert "base_path: docs/help" in text
    # …and help_ref reads what was written (XLII_HELP_REF still overrides).
    import xlii.help_corpus as hc

    parsed = hc._parse_manifest(__import__("yaml").safe_load(text))
    assert hc.help_ref(parsed) == "v0.26229.5188"


def test_stamp_main_pins_the_bundle_it_stamps(monkeypatch, tmp_path):
    """The pin follows INIT, so `main()` rewrites the manifest next to the
    package it just stamped — never the real tree from under a test run."""
    stamp = _stamp_module()
    fake_init = tmp_path / "__init__.py"
    fake_init.write_text('__version__ = "0.5.0"\n')
    bundled = tmp_path / "help" / "manifest.yaml"
    bundled.parent.mkdir()
    bundled.write_text("version: 2\nrepo: n3r4-life/iXaac-lab\nref: main\n")

    monkeypatch.setattr(stamp, "INIT", fake_init)
    monkeypatch.setattr(stamp, "run_suite", lambda: (0, 4241))
    monkeypatch.setattr("sys.argv", ["stamp_version.py", "--set", "1"])
    assert stamp.main() == 0
    version = stamp.current_version(fake_init.read_text())
    assert f"ref: v{version}" in bundled.read_text()
    assert stamp.help_manifest_path() == bundled


def test_stamp_pin_is_never_fatal(tmp_path):
    stamp = _stamp_module()
    # No bundle at all, and a manifest with no ref: key — both are None, not a
    # raise. A missing pin must never fail a stamp.
    assert stamp.pin_help_ref("1.2.3", path=tmp_path / "nope.yaml") is None
    refless = tmp_path / "m.yaml"
    refless.write_text("version: 2\nrepo: x/y\n")
    assert stamp.pin_help_ref("1.2.3", path=refless) is None
    assert refless.read_text() == "version: 2\nrepo: x/y\n"


def test_dev_tree_manifest_still_tracks_main():
    """docs/help/manifest.yaml is the source of truth a checkout reads; only the
    bundled copy gets pinned, so working trees keep fetching the newest shards."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    docs_manifest = root / "docs" / "help" / "manifest.yaml"
    if not docs_manifest.is_file():
        pytest.skip("no dev docs/help tree in this install")
    assert "\nref: main\n" in docs_manifest.read_text()
