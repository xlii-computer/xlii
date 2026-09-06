"""Episode continuity (/session — code-session-resume P0)."""

from __future__ import annotations

from types import SimpleNamespace

from xlii import episode as ep
from xlii.repl_cmds import register_all
from xlii.repl_cmds.episode import _cmd_session

register_all()


class _Console:
    def __init__(self):
        self.lines = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    @property
    def text(self):
        return "\n".join(self.lines)


def _state(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        project=SimpleNamespace(xli_dir=xli, project_root=tmp_path,
                                conversation_id="conv-abc123"),
        agent=SimpleNamespace(history=[
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
        ]),
        cfg=SimpleNamespace(orchestrator_model="grok-build-0.1", model="grok-build-0.1"),
        episode_id=None,
    )


def _ctx(state):
    return {"console": _Console(), "state": state}


# --- the store ----------------------------------------------------------------


def test_new_update_list_load_roundtrip(tmp_path):
    st = _state(tmp_path)
    eid = ep.new_episode(st)
    assert eid and st.episode_id == eid

    st.agent.history.append({"role": "user", "content": "q2"})
    ep.update_episode(st)

    records = ep.list_episodes(st.project.xli_dir)
    assert [r["id"] for r in records] == [eid]
    assert records[0]["turns"] == 2
    assert "history" not in records[0]           # summaries carry no payload

    full = ep.load_episode(st.project.xli_dir, eid)
    assert full["conversation_id"] == "conv-abc123"
    assert len(full["history"]) == 3
    assert full["started_at"] <= full["updated_at"]


def test_update_is_noop_without_active_episode(tmp_path):
    st = _state(tmp_path)
    ep.update_episode(st)
    assert ep.list_episodes(st.project.xli_dir) == []


def test_resume_restores_history_and_conversation_id(tmp_path):
    st = _state(tmp_path)
    eid = ep.new_episode(st)
    saved_len = len(st.agent.history)

    # a "restart": fresh state, new conversation id, empty history
    st2 = _state(tmp_path)
    st2.project.conversation_id = "conv-FRESH"
    st2.agent.history = []

    record = ep.resume_episode(st2, eid)
    assert record is not None
    assert len(st2.agent.history) == saved_len
    assert st2.project.conversation_id == "conv-abc123"   # the cache-warm key
    assert st2.episode_id == eid


def test_resume_missing_id_returns_none(tmp_path):
    st = _state(tmp_path)
    assert ep.resume_episode(st, "zzzz") is None
    assert st.episode_id is None


# --- the /session command -------------------------------------------------------


def test_session_registered_code_only():
    from xlii.commands import find_repl_command
    assert find_repl_command("/session", "code") is not None
    assert find_repl_command("/session", "chat") is None   # chat keeps zero ceremony


def test_session_on_off_list_resume_flow(tmp_path):
    st = _state(tmp_path)

    ctx = _ctx(st)
    assert _cmd_session("/session on", ctx) is True
    eid = st.episode_id
    assert eid and f"sess {eid}" in ctx["console"].text

    ctx = _ctx(st)
    assert _cmd_session("/session", ctx) is True            # bare = status
    assert f"sess {eid}" in ctx["console"].text

    ctx = _ctx(st)
    assert _cmd_session("/session off", ctx) is True
    assert st.episode_id is None
    assert "record kept" in ctx["console"].text

    ctx = _ctx(st)
    assert _cmd_session("/session list", ctx) is True
    assert eid in ctx["console"].text

    st.agent.history = []
    ctx = _ctx(st)
    assert _cmd_session("/session resume", ctx) is True     # bare resume = latest
    assert st.episode_id == eid
    assert len(st.agent.history) == 2
    assert "resumed" in ctx["console"].text


def test_session_bare_without_episode_explains_default(tmp_path):
    ctx = _ctx(_state(tmp_path))
    assert _cmd_session("/session", ctx) is True
    assert "no episode" in ctx["console"].text


# --- the spine snapshot + the chip ---------------------------------------------


def test_spine_snapshots_active_episode(tmp_path):
    from xlii.agent import SessionState
    from xlii.conversation import Conversation, drive_turn

    xli = tmp_path / ".xlii"
    (xli / "turns").mkdir(parents=True, exist_ok=True)
    st = _state(tmp_path)
    st.console = _Console()
    st.agent.session = SessionState()
    st.conversation = Conversation(turns_dir=xli / "turns")
    st.journal = None
    st.loop = None
    st.profile = SimpleNamespace(memory=SimpleNamespace(persist=lambda h, q, d: d))
    st.live_attachment_paths = lambda: []

    eid = ep.new_episode(st)

    def fake_run(q, **kw):
        st.agent.history.append({"role": "user", "content": q})
        st.agent.history.append({"role": "assistant", "content": "done"})
        return ("done", set(), SimpleNamespace(tool_calls=0, total_cost=None))

    drive_turn(st, "next task", fake_run,
               render=lambda r, p: None, on_error=lambda e: None)

    full = ep.load_episode(xli, eid)
    assert full["turns"] == 2                    # snapshot grew with the turn
    assert any(e.get("content") == "next task" for e in full["history"])


def test_status_bar_shows_sess_chip_only_when_active(tmp_path):
    from xlii.tui.status import profile_bar

    st = _state(tmp_path)
    st.no_sync = False
    bar = profile_bar(st).plain
    assert "sess" not in bar                     # default: chrome-free

    st.episode_id = "a3f2"
    bar = profile_bar(st).plain
    assert "sess a3f2" in bar


# --- P1: unclean lifecycle + the soft offer + CLI flags -------------------------


def test_unclean_lifecycle_and_latest_unclean(tmp_path):
    st = _state(tmp_path)
    eid = ep.new_episode(st)

    # Running episode = crash residue if we vanish now.
    assert ep.load_episode(st.project.xli_dir, eid)["unclean"] is True
    residue = ep.latest_unclean(st.project.xli_dir)
    assert residue is not None and residue["id"] == eid

    # A clean stop clears the residue signal.
    ep.mark_clean(st)
    assert ep.load_episode(st.project.xli_dir, eid)["unclean"] is False
    assert ep.latest_unclean(st.project.xli_dir) is None


def test_session_off_marks_clean(tmp_path):
    st = _state(tmp_path)
    ctx = _ctx(st)
    _cmd_session("/session on", ctx)
    eid = st.episode_id
    _cmd_session("/session off", _ctx(st))
    assert ep.load_episode(st.project.xli_dir, eid)["unclean"] is False


def test_resume_makes_episode_live_again_hence_unclean(tmp_path):
    st = _state(tmp_path)
    eid = ep.new_episode(st)
    ep.mark_clean(st)
    st.episode_id = None

    st2 = _state(tmp_path)
    ep.resume_episode(st2, eid)
    # resumed = running again → residue signal is back until the next clean exit
    assert ep.load_episode(st.project.xli_dir, eid)["unclean"] is True


def test_cli_parser_accepts_keep_session_and_resume():
    from xlii.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["code", ".", "--keep-session"])
    assert args.keep_session is True
    assert args.resume_episode is None

    args = parser.parse_args(["code", ".", "--resume"])
    assert args.resume_episode == ""          # sentinel: most recent episode

    args = parser.parse_args(["code", ".", "--resume", "a3f2"])
    assert args.resume_episode == "a3f2"


# --- sticky keep-session (the persisted preference + the launch offer) ----------


def test_keep_session_pref_roundtrip(tmp_path):
    st = _state(tmp_path)
    assert ep.read_keep_session(st.project.xli_dir) is False
    ep.write_keep_session(st.project.xli_dir, True)
    assert ep.read_keep_session(st.project.xli_dir) is True
    ep.write_keep_session(st.project.xli_dir, False)
    assert ep.read_keep_session(st.project.xli_dir) is False


def test_continue_kept_is_none_when_pref_off(tmp_path):
    st = _state(tmp_path)
    ep.new_episode(st)                        # a record exists…
    assert ep.continue_kept(_state(tmp_path)) is None  # …but pref off → fall through


def test_continue_kept_starts_fresh_when_no_records(tmp_path):
    st = _state(tmp_path)
    ep.write_keep_session(st.project.xli_dir, True)
    action, eid, _turns = ep.continue_kept(st)
    assert action == "new" and eid and st.episode_id == eid


def test_continue_kept_resumes_latest_when_noninteractive(tmp_path):
    """Piped/scripted launches (ask=None) resume silently — never a prompt."""
    st = _state(tmp_path)
    eid = ep.new_episode(st)
    ep.mark_clean(st)
    ep.write_keep_session(st.project.xli_dir, True)

    st2 = _state(tmp_path)
    st2.agent.history = []
    assert ep.continue_kept(st2, ask=None) == ("resumed", eid, 1)
    assert st2.episode_id == eid and len(st2.agent.history) == 2


def test_continue_kept_asks_and_decline_starts_fresh(tmp_path):
    st = _state(tmp_path)
    eid = ep.new_episode(st)
    ep.mark_clean(st)
    ep.write_keep_session(st.project.xli_dir, True)

    asked: list[str] = []

    def deny(line: str) -> bool:
        asked.append(line)
        return False

    st2 = _state(tmp_path)
    action, new_id, _ = ep.continue_kept(st2, ask=deny)
    assert action == "new" and new_id and new_id != eid
    # the typed question: what's waiting + the warm-KV smell + the ask
    assert f"sess {eid}" in asked[0] and "restart there?" in asked[0]
    assert "KV likely still warm" in asked[0]      # just-updated → inside horizon
    # declining resumes nothing but the preference stays on — /session off is the off-ramp
    assert ep.read_keep_session(st.project.xli_dir) is True


def test_kept_offer_line_omits_warmth_when_stale():
    rec = {"id": "abcd", "turns": 3, "updated_at": "2020-01-01T00:00:00+00:00"}
    line = ep.kept_offer_line(rec)
    assert "restart there?" in line and "KV" not in line and "d ago" in line


def test_session_on_persists_pref_and_off_clears_it(tmp_path):
    st = _state(tmp_path)
    ctx = _ctx(st)
    _cmd_session("/session on", ctx)
    assert ep.read_keep_session(st.project.xli_dir) is True
    assert "sticky" in ctx["console"].text

    ctx = _ctx(st)
    _cmd_session("/session off", ctx)
    assert ep.read_keep_session(st.project.xli_dir) is False
    assert "keep-session preference cleared" in ctx["console"].text


def test_session_bare_status_shows_sticky_pref(tmp_path):
    st = _state(tmp_path)
    ep.write_keep_session(st.project.xli_dir, True)
    ctx = _ctx(st)
    _cmd_session("/session", ctx)
    assert "keep-session is on for this project" in ctx["console"].text


# --- P2: trim tiers · pointers · cache instrumentation · picker ---------------


def test_prepare_history_trims_tool_bodies_and_caps_length():
    history = [{"role": "user", "content": "q"}]
    history.append({"role": "tool", "content": "x" * (ep.MAX_TOOL_BODY_CHARS + 50),
                    "tool_call_id": "t1"})
    history.append({"role": "assistant", "content": "a"})
    out = ep.prepare_history_for_snapshot(history)
    tool = next(e for e in out if e.get("role") == "tool")
    assert len(tool["content"]) < len(history[1]["content"])
    assert ep._TOOL_TRIM_MARK.strip() in tool["content"]
    assert out[-1]["content"] == "a"

    long = [{"role": "user", "content": f"q{i}"} for i in range(ep.MAX_HISTORY_MESSAGES + 20)]
    capped = ep.prepare_history_for_snapshot(long)
    assert len(capped) == ep.MAX_HISTORY_MESSAGES


def test_prepare_history_trims_to_valid_tool_boundary():
    """A >cap history whose naive ``[-cap:]`` cutoff lands on a tool result must
    snapshot from a VALID boundary — never an orphaned ``role=tool`` message,
    never a split assistant ``tool_calls``/result pair (would replay as a
    structurally invalid first post-resume turn)."""
    hist: list = [{"role": "user", "content": "start"}]
    hist.append({
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "call_a", "type": "function",
             "function": {"name": "f", "arguments": "{}"}},
        ],
    })
    hist.append({"role": "tool", "tool_call_id": "call_a", "content": "r"})
    # Pad so len == cap + 2: the naive history[-cap:] cutoff lands on index 2,
    # the orphaned tool result.
    while len(hist) < ep.MAX_HISTORY_MESSAGES + 2:
        hist.append({"role": "user", "content": f"q{len(hist)}"})
    assert hist[len(hist) - ep.MAX_HISTORY_MESSAGES]["role"] == "tool"

    out = ep.prepare_history_for_snapshot(hist)
    # never begin on an orphaned tool result …
    assert out[0].get("role") != "tool"
    # … the assistant carrying the tool_calls stays paired with its result …
    assert out[0].get("tool_calls")
    assert any(
        e.get("role") == "tool" and e.get("tool_call_id") == "call_a" for e in out
    )
    # … and the cap stays approximate: a few more than N to stay valid, never fewer.
    assert len(out) >= ep.MAX_HISTORY_MESSAGES


def test_capture_pointers_reads_last_job_from_registry():
    """capture_pointers must call JobRegistry.jobs() (a method) and record the
    most recent job's kind/name — the old getattr(reg,'jobs') grabbed the bound
    method, list()'d it (TypeError), and silently dropped every job pointer."""
    from xlii.jobs import JobRegistry

    reg = JobRegistry(max_workers=1)
    reg.dispatch("task", "warmup", lambda: 1)
    jid = reg.dispatch("loop", "green-loop", lambda: 42)
    reg.wait(jid, timeout=5)

    st = SimpleNamespace(job_registry=reg)
    ptr = ep.capture_pointers(st)
    assert ptr["last_job_kind"] == "loop"
    assert ptr["last_job_name"] == "green-loop"


def test_update_episode_stores_cache_stats_and_pointers(tmp_path):
    st = _state(tmp_path)
    st.agent.rail = SimpleNamespace(stage="IMPLEMENTATION", seeded_from_plan=True)
    st.loop = SimpleNamespace(is_active=True, id="loop-1", status="paused")
    eid = ep.new_episode(st)
    stats = SimpleNamespace(context_tokens=58000, cached_tokens=36000)
    ep.update_episode(st, stats=stats)
    rec = ep.load_episode(st.project.xli_dir, eid)
    assert rec["history_tier"] == ep.HISTORY_TIER
    assert rec["last_cache_stats"]["context_tokens"] == 58000
    assert rec["last_cache_stats"]["cached_tokens"] == 36000
    assert rec["pointers"]["rail_stage"] == "IMPLEMENTATION"
    assert rec["pointers"]["loop_active"] is True


def test_resume_prints_cache_and_pointers_lines(tmp_path):
    st = _state(tmp_path)
    eid = ep.new_episode(st)
    ep.update_episode(st, stats=SimpleNamespace(context_tokens=1000, cached_tokens=600))
    st.episode_id = None

    st2 = _state(tmp_path)
    st2.project.conversation_id = "fresh"
    ctx = _ctx(st2)
    _cmd_session(f"/session resume {eid}", ctx)
    out = ctx["console"].text
    assert "last turn before exit" in out
    assert getattr(st2, "_episode_cache_baseline", None) is not None


def test_resume_without_id_lists_when_multiple(tmp_path):
    st = _state(tmp_path)
    ep.new_episode(st)
    st.episode_id = None
    st.agent.history = [{"role": "user", "content": "other"}]
    ep.new_episode(st)
    st.episode_id = None
    ctx = _ctx(st)
    _cmd_session("/session resume", ctx)
    assert "pick one" in ctx["console"].text
    assert "1." in ctx["console"].text and "2." in ctx["console"].text


def test_format_cache_delta_line():
    baseline = {"context_tokens": 1000, "cached_tokens": 600}
    warm = SimpleNamespace(context_tokens=1200, cached_tokens=900)
    cold = SimpleNamespace(context_tokens=1200, cached_tokens=100)
    assert "warm" in ep.format_cache_delta_line(baseline, warm)
    assert "cold" in ep.format_cache_delta_line(baseline, cold)


def test_palette_includes_session_resume_for_code_only():
    from xlii.tui.discover import collect_palette_items

    code = SimpleNamespace(command_scope="code", project=None)
    chat = SimpleNamespace(command_scope="chat", project=None)
    names_code = {i.name for i in collect_palette_items(code)}
    names_chat = {i.name for i in collect_palette_items(chat)}
    assert "session resume" in names_code
    assert "session resume" not in names_chat
    action = next(i.action for i in collect_palette_items(code) if i.name == "session resume")
    assert action == "__session_resume__"
