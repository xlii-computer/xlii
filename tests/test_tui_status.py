"""Unit tests for the shared session-status readers (xlii.tui.status).

Pure functions over REPLState — no textual, no prompt_toolkit. These back BOTH
the inline bottom toolbar (skinned in prompt_toolkit) and the Textual --tui top
strip, so they're the one place mode/cwd/attachments truth is tested.
"""

from __future__ import annotations

from types import SimpleNamespace

from xlii.debug_mode import DebugController
from xlii.mode_controller import PlanController
from xlii.rail import RailController, RailStage
from xlii.tui import status


def _st(**kw):
    base = dict(
        shell_cwd=None,
        project=SimpleNamespace(name="proj", project_root=None),
        plan_mode=False, persona=None, yolo=False,
        agent=SimpleNamespace(active_mode=None, rail=None),
        attached_refs=[], attached_docs=[],
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _agent_with_mode(mode):
    if isinstance(mode, RailController):
        return SimpleNamespace(active_mode=mode, rail=mode)
    if isinstance(mode, DebugController):
        return SimpleNamespace(active_mode=mode, debug=mode)
    if isinstance(mode, PlanController):
        return SimpleNamespace(active_mode=mode)
    return SimpleNamespace(active_mode=mode)


def test_mode_defaults_to_shell():
    assert status.mode(_st()) == ("SHELL", "SHELL")


def test_mode_plan():
    assert status.mode(_st(agent=_agent_with_mode(PlanController()))) == ("PLAN", "PLAN")


def test_primary_axes_exclusive_mode_plan():
    st = _st(agent=_agent_with_mode(PlanController()))
    assert status.exclusive_mode(st) == "plan"
    assert status.trust_axis(st) == "safe"
    assert status.surface_axis(st) == "code"
    assert status.primary_axes(st) == ("plan", "safe", "code")


def test_primary_axes_trust_separate_from_mode():
    st = _st(yolo=True)
    # No controller → exclusive mode empty; trust is yolo; surface code.
    assert status.exclusive_mode(st) == "—"
    assert status.trust_axis(st) == "yolo"
    assert status.surface_axis(st) == "code"
    assert "mode: —" in status.format_primary_axes(st)
    assert "trust: yolo" in status.format_primary_axes(st)


def test_primary_axes_one_exclusive_mode_with_yolo():
    """Plan + yolo → still a single exclusive mode word (plan), trust separate."""
    st = _st(agent=_agent_with_mode(PlanController()), yolo=True)
    assert status.exclusive_mode(st) == "plan"
    assert status.trust_axis(st) == "yolo"
    plain = status.profile_bar(st).plain
    # Lead is plan (frame); yolo appears as trust segment — not a second plan.
    assert plain.startswith("plan")
    assert plain.count("plan") == 1
    assert "yolo" in plain


def test_surface_axis_chat():
    st = _st(persona=SimpleNamespace(name="ada"))
    assert status.surface_axis(st) == "chat"


def test_mode_yolo():
    assert status.mode(_st(yolo=True)) == ("YOLO", "YOLO")


def test_mode_chat_when_persona_present():
    assert status.mode(_st(persona=SimpleNamespace(name="ada"))) == ("CHAT", "CHAT")


def test_mode_howto():
    assert status.mode(_st(howto_mode=True)) == ("HOWTO", "HOWTO")


def test_mode_howto_wins_over_chat():
    # /howto overlays a chat surface too — the active intent shows
    st = _st(howto_mode=True, persona=SimpleNamespace(name="ada"))
    assert status.mode(st) == ("HOWTO", "HOWTO")


def test_profile_bar_howto_leads():
    assert status.profile_bar(_st(howto_mode=True)).plain == "howto · safe · jrnl○  ·  proj"


def test_profile_bar_howto_leads_over_chat_profile():
    st = _st(howto_mode=True, profile=SimpleNamespace(mode="chat"),
             persona=SimpleNamespace(name="ada"))
    assert status.profile_bar(st).plain == "howto · safe · jrnl○  ·  ada"


def test_mode_rail():
    rail = RailController()
    label, key = status.mode(_st(agent=_agent_with_mode(rail)))
    assert key == "RAIL"
    assert label.startswith("RAIL 0/")


def test_mode_debug():
    dbg = DebugController()
    label, key = status.mode(_st(agent=_agent_with_mode(dbg)))
    assert key == "DEBUG"
    assert label.startswith("DEBUG 0/")


def test_attachments_counts():
    assert status.attachments(_st()) == ""
    st = _st(attached_refs=[("a", "c")], attached_docs=[("d", "x"), ("e", "y")])
    assert status.attachments(st) == "refs:1 docs:2"


def test_doorway_counts_are_attached_not_store():
    # the chip badges what's RIDING (hidden at zero), one channel per kind — skills under skill:,
    # bookmarks (recalled via /ref) under point:, real docs are the rest.
    from xlii.tui.status import _doc_attached_count, _mark_attached_count, _skill_rider_count

    st = _st(attached_docs=[("conventions", "x"), ("skill:grounded-analysis", "y"), ("point:thesis", "z")])
    assert _doc_attached_count(st) == 1     # only the real doc
    assert _skill_rider_count(st) == 1      # the skill: entry
    assert _mark_attached_count(st) == 1    # the point: (recalled bookmark) entry
    # nothing attached → all zero (the chips render bare, no number)
    assert _doc_attached_count(_st()) == 0 and _mark_attached_count(_st()) == 0


def test_cwd_falls_back_to_project_name():
    # no shell_cwd → format_shell_cwd returns "" → project name
    assert status.cwd(_st()) == "proj"


# --------------------------------------------------------------------------- #
#  location (Vector E) — the project-anchored "where am I": the project name is
#  never lost to a cd, and the segment collapses when there's nothing to say.


def test_location_at_root_and_inside(tmp_path):
    proj = SimpleNamespace(name="proj", project_root=tmp_path)
    (tmp_path / "sub").mkdir()
    assert status.location(_st(project=proj, shell_cwd=tmp_path)) == "proj"
    assert status.location(_st(project=proj, shell_cwd=tmp_path / "sub")) == "proj/sub"


def test_location_outside_keeps_the_project_name(tmp_path, monkeypatch):
    # the old bare "(outside ~/x)" form hid WHICH project you were in
    proj = SimpleNamespace(name="proj", project_root=tmp_path / "root")
    (tmp_path / "root").mkdir()
    (tmp_path / "elsewhere").mkdir()
    loc = status.location(_st(project=proj, shell_cwd=tmp_path / "elsewhere"))
    assert loc.startswith("proj (outside ") and loc.endswith(")")


def test_location_collapses_without_project_or_cwd():
    assert status.location(_st(project=None)) == ""
    assert status.location(_st()) == "proj"          # project name alone, no cwd


def test_profile_bar_chat_without_project_stays_lean():
    st = _st(persona=SimpleNamespace(name="ada"), project=None)
    assert status.profile_bar(st).plain == "chat · safe · jrnl○  ·  ada"


# --------------------------------------------------------------------------- #
#  frame_mode (tui-frame) — (label, border-color) for the input frame; label is
#  the placeholder_key surface word, color the _MODE_RICH tone for that mode.
# --------------------------------------------------------------------------- #

def test_frame_mode_default_is_code_green():
    assert status.frame_mode(_st()) == ("code", "green")


def test_frame_mode_plan_is_yellow():
    assert status.frame_mode(_st(agent=_agent_with_mode(PlanController()))) == ("plan", "yellow")


def test_frame_mode_chat_is_cyan():
    assert status.frame_mode(_st(persona=SimpleNamespace(name="ada"))) == ("chat", "cyan")


def test_frame_mode_howto_is_legible_blue():
    # A hex blue, not ANSI `blue` (#0000ff): the latter is unreadable on the dark
    # bg and `bright_blue` doesn't parse as a Textual border color. The hex is the
    # one spelling that's legible AND valid on both the Rich bar and the frame.
    label, color = status.frame_mode(_st(howto_mode=True))
    assert label == "howto"
    assert color == "#5f9bff"
    from textual.color import Color  # frame feeds this straight into the border
    assert Color.parse(color)


def test_frame_mode_rail_keeps_base_surface():
    # rail/debug aren't bare-input modes of their own, so the frame keeps the
    # base surface word + color (it mirrors placeholder_key / the bar's lead).
    assert status.frame_mode(_st(agent=_agent_with_mode(RailController()))) == ("code", "green")


def test_frame_tabs_doorways_hidden_until_attached():
    # A content doorway rides the frame ONLY while its kind is attached; an empty session has
    # no tabs. Attach a doc → the docs doorway appears.
    assert [k for _, k, _ in status.frame_tabs(_st())] == []
    tabs = status.frame_tabs(_st(attached_docs=[("conventions", "x")]))
    assert [(l.split()[0], p) for l, k, p in tabs if k == "door"] == [("docs", "docs")]


def test_frame_tabs_role_then_attached_doorway():
    st = _st(active_role="ada", attached_docs=[("conventions", "x")])
    tabs = status.frame_tabs(st)
    assert [k for _, k, _ in tabs] == ["role", "door"]
    assert ("role:ada", "role", "ada") in tabs  # role payload = the bare role name
    assert [p for _, k, p in tabs if k == "door"] == ["docs"]


def test_frame_tabs_one_tab_per_attached_file(tmp_path):
    f1 = tmp_path / "a.md"
    f2 = tmp_path / "b.py"
    st = _st(attached_files=[
        {"name": "a.md", "path": str(f1), "kind": "text"},
        {"name": "b.py", "path": str(f2), "kind": "text"},
    ])
    tabs = status.frame_tabs(st)
    assert ("a.md", "file", {"path": str(f1), "kind": "text"}) in tabs
    assert ("b.py", "file", {"path": str(f2), "kind": "text"}) in tabs
    assert "files" not in [k for _, k, _ in tabs]  # no persistent files doorway on the row


def test_frame_tabs_role_silent_in_chat_become():
    st = _st(active_role="ada", persona=SimpleNamespace(name="ada"))
    assert [k for _, k, _ in status.frame_tabs(st)] == []


def test_frame_mode_color_token_matches_mode_rich_palette():
    # the color is the bare token of the same _MODE_RICH style the bar uses, so
    # the frame never drifts from the profile bar's tone for a mode.
    _, color = status.frame_mode(_st(agent=_agent_with_mode(PlanController())))
    assert status._MODE_RICH["PLAN"].endswith(color)


# --------------------------------------------------------------------------- #
#  profile_bar (RP3) — mode · id · loadout · model · meter, segments collapse
# --------------------------------------------------------------------------- #

def test_profile_bar_unbound_code_is_lean():
    # no profile, no persona, no attachments, no cfg → mode · trust · id
    assert status.profile_bar(_st()).plain == "code · safe · jrnl○  ·  proj"


def test_profile_bar_chat_shows_persona():
    st = _st(persona=SimpleNamespace(name="ada"))
    assert status.profile_bar(st).plain == "chat · safe · jrnl○  ·  ada"


def test_profile_bar_profile_mode_takes_precedence():
    # an explicit live Profile.mode wins over persona-presence inference
    st = _st(profile=SimpleNamespace(mode="chat"), persona=None)
    assert status.profile_bar(st).plain == "chat · safe · jrnl○  ·  proj"


def test_profile_bar_loadout_and_model_and_meter():
    st = _st(
        attached_docs=[("d", "x")], attached_refs=[("a", "c")],
        cfg=SimpleNamespace(
            orchestrator=lambda: "grok-4",
            get_model_for_role=lambda role="orchestrator": "grok-4",
        ),
    )
    assert status.profile_bar(st, meter="90K / 256K").plain == \
        "code · safe · jrnl○  ·  proj  ·  1 doc · 1 ref  ·  grok-4  ·  90K / 256K"


def test_profile_bar_model_override_wins():
    st = _st(agent=SimpleNamespace(rail=None, model_override="pinned-x",
                                  session=SimpleNamespace(conversational=False)),
             cfg=SimpleNamespace(
                 orchestrator=lambda: "grok-4",
                 get_model_for_role=lambda role="orchestrator": "grok-4",
             ))
    assert status.profile_bar(st).plain == "code · safe · jrnl○  ·  proj  ·  pinned-x"


def test_profile_bar_chat_surface_uses_chat_role_model():
    st = _st(
        persona=SimpleNamespace(name="ada"),
        agent=SimpleNamespace(
            rail=None,
            model_override=None,
            session=SimpleNamespace(conversational=True),
        ),
        cfg=SimpleNamespace(
            orchestrator=lambda: "grok-build-0.1",
            chat=lambda: "grok-4",
            get_model_for_role=lambda role="orchestrator": (
                "grok-4" if role == "chat" else "grok-build-0.1"
            ),
        ),
    )
    assert status.profile_bar(st).plain == "chat · safe · jrnl○  ·  ada  ·  grok-4"


def test_profile_bar_rail_flag():
    rail = RailController()
    rail.current_stage = RailStage.EDGE_CASES
    plain = status.profile_bar(_st(agent=_agent_with_mode(rail))).plain
    assert plain.startswith("code · rail 2/")     # affordance flag preserved
    assert "proj" in plain


def test_profile_bar_plan_flag():
    assert status.profile_bar(_st(agent=_agent_with_mode(PlanController()))).plain == "plan · safe · jrnl○  ·  proj"


def test_profile_bar_debug_flag():
    dbg = DebugController()
    plain = status.profile_bar(_st(agent=_agent_with_mode(dbg))).plain
    assert "code · debug 0/" in plain


def test_profile_mode_tag_for_prompt_prefix():
    rail = RailController()
    rail.current_stage = RailStage.EDGE_CASES
    assert status.profile_mode_tag(_agent_with_mode(rail)) == "[rail 2/5]"
    assert status.profile_mode_tag(_agent_with_mode(PlanController())) == "[plan]"
    dbg = DebugController()
    assert status.profile_mode_tag(_agent_with_mode(dbg)) == "[debug 0/5]"


def test_status_modules_have_no_mode_ladders():
    import xlii.profile as profile_mod

    status_src = status.__file__
    assert status_src
    from pathlib import Path

    status_text = Path(status_src).read_text()
    profile_text = Path(profile_mod.__file__).read_text()
    for label, src in (("status.py", status_text), ("profile.py", profile_text)):
        assert "elif state.plan_mode" not in src, label
        assert "agent.rail is not None" not in src, label
        assert "getattr(state, \"plan_mode\"" not in src, label


def test_identity_code_shows_project_not_chat_persona():
    # RP7: code is isolated — the bar shows the project, never the chat-persona
    # preference. `bound_persona` is only which persona bare /chat opens.
    st = _st(project=SimpleNamespace(name="iXaac-lab", project_root=None, bound_persona="bob"),
             persona=None)
    assert status.identity(st) == "iXaac-lab"


def test_profile_bar_code_shows_project_only():
    st = _st(project=SimpleNamespace(name="iXaac-lab", project_root=None, bound_persona="bob"),
             persona=None)
    assert status.profile_bar(st).plain == "code · safe · jrnl○  ·  iXaac-lab"


# --------------------------------------------------------------------------- #
#  role badge (roles R3) — `role:<name>` when equipped in code, silent in chat
# --------------------------------------------------------------------------- #

def test_role_empty_when_none():
    assert status.role(_st()) == ""
    assert status.role(_st(active_role=None)) == ""


def test_role_equipped_in_code():
    assert status.role(_st(active_role="debugger")) == "role:debugger"


def test_role_silent_in_chat_become():
    # chat 'become': the persona name IS the role, so identity already shows it
    st = _st(active_role="debugger", persona=SimpleNamespace(name="debugger"))
    assert status.role(st) == ""


def test_profile_bar_shows_equipped_role():
    st = _st(active_role="debugger")
    assert status.profile_bar(st).plain == "code · safe · jrnl○  ·  proj  ·  role:debugger"


# --------------------------------------------------------------------------- #
#  Track G0 — always-show trust + neutral bulk ink; never mute danger
# --------------------------------------------------------------------------- #

def test_profile_bar_always_shows_safe_trust():
    plain = status.profile_bar(_st()).plain
    assert "safe" in plain
    assert "yolo" not in plain and "FREEBALL" not in plain


def test_profile_bar_yolo_and_freeball_stay_loud():
    yolo_bar = status.profile_bar(_st(yolo=True))
    assert yolo_bar.plain == "code · yolo · jrnl○  ·  proj"
    assert any(span.style == status._MODE_RICH["YOLO"] for span in yolo_bar.spans)

    fb_bar = status.profile_bar(_st(yolo=True, freeball=True))
    assert fb_bar.plain == "code · FREEBALL · jrnl○  ·  proj"
    assert any(span.style == status._MODE_RICH["FREEBALL"] for span in fb_bar.spans)


def test_profile_bar_plan_plus_yolo_shows_both():
    """Exclusive mode + trust are separate segments — both visible."""
    st = _st(agent=_agent_with_mode(PlanController()), yolo=True)
    plain = status.profile_bar(st).plain
    assert plain.startswith("plan")
    assert "yolo" in plain
    assert plain.count("plan") == 1


def test_profile_bar_bulk_segments_use_dim_ink():
    st = _st(
        episode_id="abc",
        no_sync=True,
        session_cost=0.5,
        budget_usd=10.0,
        attached_docs=[("d", "x")],
    )
    bar = status.profile_bar(st)
    plain = bar.plain
    styles_by_plain = [(plain[start:end], str(style)) for start, end, style in bar.spans]
    assert any(t == "safe" and s == "dim" for t, s in styles_by_plain)
    assert any(t == "no-sync" and s == "dim" for t, s in styles_by_plain)
    assert any(t.startswith("sess ") and s == "cyan" for t, s in styles_by_plain)
    assert any("$" in t and "/" in t and s == "dim" for t, s in styles_by_plain)


# --- chat tiers as a faux-mode suffix (chat-tiers Vector D) ---------------- #

def _chat(**kw):
    return _st(persona=SimpleNamespace(name="ada"), **kw)


def test_frame_mode_suffixes_tier_on_chat():
    assert status.frame_mode(_chat(chat_tier="expert"))[0] == "chat · expert"
    assert status.frame_mode(_chat(chat_tier="heavy"))[0] == "chat · heavy"
    # no tier → bare chat word, unchanged
    assert status.frame_mode(_chat())[0] == "chat"


def test_frame_mode_no_tier_suffix_off_chat():
    # chat_tier set but surface is code (no persona) → tier is inert, no suffix
    assert status.frame_mode(_st(chat_tier="expert"))[0] == "code"


def test_mode_suffixes_tier_for_inline_toolbar():
    assert status.mode(_chat(chat_tier="auto")) == ("CHAT · auto", "CHAT")
    assert status.mode(_chat()) == ("CHAT", "CHAT")  # unchanged without a tier


def test_chat_tier_reads_through_agent_session():
    st = _chat(agent=SimpleNamespace(
        active_mode=None, rail=None, session=SimpleNamespace(chat_tier="fast")))
    assert status.chat_tier(st) == "fast"
    assert status.frame_mode(st)[0] == "chat · fast"


def test_profile_bar_leads_with_tiered_chat():
    # a persona chat shows the persona name as the id segment
    assert status.profile_bar(_chat(chat_tier="auto")).plain == "chat · auto · safe · jrnl○  ·  ada"

# --- V3 chrome-status -------------------------------------------------------- #

def test_affordance_plan_gigwork_label_passthrough():
    from xlii.mode_controller import PlanController
    backend = type("B", (), {"label": "haiku"})()
    label, _style = status._affordance_from_mode_tag(PlanController(chat_backend=backend).status_tag())
    assert label == "plan·gigwork[haiku]"


def test_profile_bar_plan_gigwork_shows_hired_label():
    from xlii.mode_controller import PlanController
    backend = type("B", (), {"label": "kimi"})()
    st = _st(agent=_agent_with_mode(PlanController(chat_backend=backend)))
    assert "plan·gigwork[kimi]" in status.profile_bar(st).plain


def test_journal_glyph_always_visible(tmp_path):
    from tests.test_journal import _journal

    j, _proj, _c = _journal(tmp_path, code_on=True)
    assert status.journal(_st(journal=j)) == "jrnl●"
    j.code_on = False
    assert status.journal(_st(journal=j)) == "jrnl○"
    # D8: no omission-as-off — a journal-less state (chat, pre-boot) still
    # shows the dim off glyph.
    assert status.journal(_st()) == "jrnl○"


def test_profile_bar_mode_word_neutral_for_code_and_chat():
    code_bar = status.profile_bar(_st())
    styles = {code_bar.plain[start:end]: str(style) for start, end, style in code_bar.spans}
    assert styles.get("code") == "dim"
    chat_bar = status.profile_bar(_st(persona=SimpleNamespace(name="ada")))
    styles = {chat_bar.plain[start:end]: str(style) for start, end, style in chat_bar.spans}
    assert styles.get("chat") == "dim"
    howto_bar = status.profile_bar(_st(howto_mode=True))
    styles = {howto_bar.plain[start:end]: str(style) for start, end, style in howto_bar.spans}
    assert "bold" in styles.get("howto", "")


def test_status_kernel_has_no_rich_imports():
    import ast
    from pathlib import Path
    import xlii.status as kernel_status
    tree = ast.parse(Path(kernel_status.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("rich") for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("rich")
