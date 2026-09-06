"""Vector A1 — Context Tabs & Kinds: the seam-level (front-end-free) tests.

Covers the three seams A1 publishes for Interaction Layer II:
  • seam #7 — `skills.partition_attached_docs` (the one home of the `skill:` split);
  • seam #1 — type-driven `status.frame_tabs` (the skill-miscount fix, the
    `(label, kind, payload)` shape, and the `register_frame_tab` provider registry);
  • seam #5 — the harness-mode key/color/stat stub (`placeholder_key`, `frame_mode`,
    `model`) that Vector C's harness modes ride.

All pure over a fake REPLState (SimpleNamespace + defensive getattr) — no Textual,
no prompt_toolkit — so they pin the contract other vectors consume. The click/key
interaction lives in test_tui_tabs_interaction.py (needs the Textual pilot).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.skills import SKILL_ATTACH_PREFIX, partition_attached_docs
from xlii.tui import status


def _st(**kw):
    base = dict(
        shell_cwd=None,
        project=SimpleNamespace(name="proj", project_root=None),
        plan_mode=False, persona=None, yolo=False, profile=None,
        agent=SimpleNamespace(active_mode=None, rail=None),
        attached_refs=[], attached_docs=[],
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _kinds(tabs):
    return [k for _, k, _ in tabs]


def _by_kind(tabs):
    return {k: (label, payload) for label, k, payload in tabs}


# --------------------------------------------------------------------------- #
#  seam #7 — partition_attached_docs: the single skill:/doc split
# --------------------------------------------------------------------------- #

def test_partition_splits_docs_and_skills_preserving_order():
    docs, skills = partition_attached_docs([
        ("api.md", "A"),
        (f"{SKILL_ATTACH_PREFIX}deploy", "S1"),
        ("rfc.md", "B"),
        (f"{SKILL_ATTACH_PREFIX}test", "S2"),
    ])
    assert docs == [("api.md", "A"), ("rfc.md", "B")]      # verbatim, skills removed
    # true partition: skills kept verbatim (prefix retained) so /doc can match them
    assert skills == [(f"{SKILL_ATTACH_PREFIX}deploy", "S1"), (f"{SKILL_ATTACH_PREFIX}test", "S2")]


def test_partition_empty_and_none():
    assert partition_attached_docs(None) == ([], [])
    assert partition_attached_docs([]) == ([], [])


def test_partition_no_skills_returns_docs_unchanged():
    docs, skills = partition_attached_docs([("a", "x"), ("b", "y")])
    assert docs == [("a", "x"), ("b", "y")]
    assert skills == []


def test_partition_malformed_entry_never_raises_and_lands_in_docs():
    docs, skills = partition_attached_docs([("only_name",), "weird", ("d", "x")])
    assert ("d", "x") in docs and ("only_name",) in docs and "weird" in docs
    assert skills == []


# --------------------------------------------------------------------------- #
#  seam #1 — frame_tabs: type-driven, the skill-miscount fix, payloads, registry
# --------------------------------------------------------------------------- #

# One doc per doorway kind → all five doors light up. Skills/bookmarks/wiki ride the /doc channel
# under their prefixes; images ride the locker (an attached file of kind "image").
_ALL_KINDS_DOCS = [("conventions", "d"), ("skill:deploy", "s"), ("point:thesis", "p"),
                   ("wiki:intro", "w")]
_ALL_KINDS_FILES = [{"name": "a.png", "path": "/x/a.png", "kind": "image"}]


def test_doorways_hidden_until_their_kind_is_attached():
    assert _kinds(status.frame_tabs(_st())) == []
    st = _st(attached_docs=_ALL_KINDS_DOCS, attached_files=_ALL_KINDS_FILES)
    doors = [(label.split()[0], payload) for label, k, payload in status.frame_tabs(st) if k == "door"]
    assert doors == [("skills", "skills"), ("docs", "docs"), ("bookmarks", "mark"),
                     ("images", "locker"), ("wiki", "wiki")]
    assert "files" not in _kinds(status.frame_tabs(st))  # no persistent files doorway


@pytest.mark.parametrize("docs,files,scheme", [
    ([("conventions", "d")], [], "docs"),                                   # a real doc
    ([("skill:deploy", "s")], [], "skills"),                                # a skill rider
    ([("point:thesis", "p")], [], "mark"),                                  # a /ref bookmark
    ([("wiki:intro", "w")], [], "wiki"),                                    # a wiki page
    ([], [{"name": "a.png", "path": "/x/a.png", "kind": "image"}], "locker"),  # a locker image
])
def test_only_the_attached_kind_gets_a_doorway(docs, files, scheme):
    # Prefix isolation: a skill:/point:/wiki: entry lights ONLY its own doorway, never leaking into
    # the plain docs count; an image lights only the images door.
    st = _st(attached_docs=docs, attached_files=files)
    assert [p for _, k, p in status.frame_tabs(st) if k == "door"] == [scheme]


def test_tab_payloads_per_kind():
    st = _st(active_role="ada", attached_docs=_ALL_KINDS_DOCS, attached_files=_ALL_KINDS_FILES)
    by = _by_kind(status.frame_tabs(st))
    assert by["role"][1] == "ada"
    door_payloads = [p for _, k, p in status.frame_tabs(st) if k == "door"]
    assert door_payloads == ["skills", "docs", "mark", "locker", "wiki"]  # a door's payload = its scheme


def test_kind_order_mode_role_then_attached_doors():
    st = _st(active_role="ada", attached_docs=_ALL_KINDS_DOCS)
    assert _kinds(status.frame_tabs(st)) == [
        "role", "door", "door", "door", "door"
    ]


def test_images_doorway_reads_the_locker():
    st = _st(attached_files=[
        {"name": "a.png", "path": "/x/a.png", "kind": "image", "enabled": True},
        {"name": "b.png", "path": "/x/b.png", "kind": "image", "enabled": True},
    ])
    img_door = next(label for label, k, p in status.frame_tabs(st) if k == "door" and p == "locker")
    assert img_door == "images 2"  # the images door counts locker images


def test_register_frame_tab_provider_appends_after_builtins():
    seen = {}

    def provider(state):
        seen["called"] = True
        return ("cursor:s1", "session", {"name": "s1"})

    status.register_frame_tab(provider)
    try:
        tabs = status.frame_tabs(_st())
        assert seen.get("called")
        assert ("cursor:s1", "session", {"name": "s1"}) in tabs
    finally:
        status.unregister_frame_tab(provider)
    # unregister drops it cleanly (test isolation / plugin teardown)
    assert all(k != "session" for _, k, _ in status.frame_tabs(_st()))


def test_provider_two_tuple_normalized_and_list_supported():
    def provider(state):
        return [("x", "kx"), ("y", "ky", "py")]   # 2-tuple → payload None; 3-tuple kept

    status.register_frame_tab(provider)
    try:
        tabs = status.frame_tabs(_st())
        assert ("x", "kx", None) in tabs
        assert ("y", "ky", "py") in tabs
    finally:
        status.unregister_frame_tab(provider)


def test_a_raising_provider_cannot_blank_the_frame():
    def bad(state):
        raise RuntimeError("boom")

    status.register_frame_tab(bad)
    try:
        tabs = status.frame_tabs(_st())
        assert tabs == []   # built-ins intact despite the raise
    finally:
        status.unregister_frame_tab(bad)


# --------------------------------------------------------------------------- #
#  seam #5 — harness-mode key / color / stat (the day-1 stub C's modes ride)
# --------------------------------------------------------------------------- #

def _cfg():
    return SimpleNamespace(
        orchestrator=lambda: "grok-4",
        get_model_for_role=lambda role="orchestrator": "grok-4",
    )


def _agent():
    return SimpleNamespace(
        rail=None, active_mode=None, model_override=None,
        session=SimpleNamespace(conversational=False),
    )


def test_no_harness_keeps_base_mode_dormant_seam():
    # the seam stays inert until C sets state.harness_foreground.
    assert status.placeholder_key(_st()) == "code"
    assert status.frame_mode(_st()) == ("code", "green")


def test_harness_foreground_drives_placeholder_and_frame_color():
    spec = SimpleNamespace(name="cursor", label="cursor",
                           model_selectable=True, model="composer-2.5")
    st = _st(harness_foreground=spec)
    assert status.placeholder_key(st) == "cursor"
    label, color = status.frame_mode(st)
    assert label == "cursor"
    assert color == status._HARNESS_RICH["cursor"].split()[-1]


def test_harness_label_can_be_multiword():
    spec = SimpleNamespace(name="claude", label="claude code", model_selectable=False)
    st = _st(harness_foreground=spec)
    assert status.placeholder_key(st) == "claude code"
    assert status.frame_mode(st)[0] == "claude code"


def test_harness_foreground_accepts_bare_string():
    st = _st(harness_foreground="grok")
    assert status.placeholder_key(st) == "grok"
    assert status.frame_mode(st) == ("grok", status._HARNESS_RICH["grok"].split()[-1])


def test_unknown_harness_uses_the_neutral_fallback_color():
    spec = SimpleNamespace(name="acme", label="acme", model_selectable=False)
    st = _st(harness_foreground=spec)
    assert status.frame_mode(st)[1] == status._HARNESS_FALLBACK.split()[-1]


def test_model_appends_hosted_model_only_for_model_selectable():
    sel = SimpleNamespace(name="cursor", label="cursor",
                          model_selectable=True, model="composer-2.5")
    st = _st(agent=_agent(), cfg=_cfg(), harness_foreground=sel)
    assert status.model(st) == "grok-4 · composer-2.5"


def test_model_does_not_append_for_non_selectable_harness():
    # the harness IS the model (its label names it) — nothing appended.
    nonsel = SimpleNamespace(name="claude", label="claude code",
                             model_selectable=False, default_model="claude-sonnet-4-6")
    st = _st(agent=_agent(), cfg=_cfg(), harness_foreground=nonsel)
    assert status.model(st) == "grok-4"


def test_frame_color_token_is_valid_textual_border():
    # the harness palette feeds the Textual frame border verbatim, so every tone
    # must parse as a border color (a hex, like the howto fix from the foundation).
    from textual.color import Color

    for name in ("cursor", "claude", "grok", "codex"):
        spec = SimpleNamespace(name=name, label=name, model_selectable=False)
        _, color = status.frame_mode(_st(harness_foreground=spec))
        assert Color.parse(color)
