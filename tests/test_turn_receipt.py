"""Turn receipts (P1): evidence harvest, claim tiers, gate, recording."""

from __future__ import annotations

import json
from types import SimpleNamespace

from xlii.agent import SessionState
from xlii.turn_receipt import build_receipt, record_receipt


class _Console:
    def __init__(self):
        self.lines = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    @property
    def text(self):
        return "\n".join(self.lines)


def _state(tmp_path, history=None):
    xli = tmp_path / ".xlii"
    xli.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        console=_Console(),
        project=SimpleNamespace(xli_dir=xli, project_root=tmp_path),
        agent=SimpleNamespace(history=history or [], session=SessionState()),
    )


def _stats(tool_calls=0):
    return SimpleNamespace(tool_calls=tool_calls)


def _bash_entry(cmd):
    return {"role": "assistant", "tool_calls": [
        {"id": "t1", "type": "function",
         "function": {"name": "bash", "arguments": json.dumps({"command": cmd})}}]}


def test_clean_explain_turn_is_ok(tmp_path):
    st = _state(tmp_path, history=[{"role": "user", "content": "why?"},
                                   {"role": "assistant", "content": "because X."}])
    r = build_receipt(st, "why?", "because X.", set(), _stats())
    assert r.gate == "ok"
    assert r.classes == []
    assert "explain" in r.compact_line()


def test_edit_claim_without_writes_is_unsubstantiated_but_quiet(tmp_path):
    # The edit lane is P0's to yell about — the receipt records it silently.
    st = _state(tmp_path, history=[{"role": "user", "content": "fix it"}])
    r = build_receipt(st, "fix it", "I fixed the bug in main.py.", set(), _stats())
    assert r.gate == "unsubstantiated"
    assert "edit" in r.claims and "edit" not in r.classes
    record_receipt(st, r)
    assert st.console.text == ""              # quiet: P0 already covers this lane


def test_verify_claim_without_test_command_yells(tmp_path):
    st = _state(tmp_path, history=[{"role": "user", "content": "check"}])
    r = build_receipt(st, "check", "All tests pass now.", set(), _stats())
    assert r.gate == "unsubstantiated"
    assert any("verify claim" in w for w in r.warnings)
    record_receipt(st, r)
    assert "receipt: unsubstantiated" in st.console.text


def test_verify_claim_with_pytest_evidence_is_ok(tmp_path):
    history = [{"role": "user", "content": "check"},
               _bash_entry("python -m pytest -q"),
               {"role": "tool", "tool_call_id": "t1",
                "content": "2 passed\n--- exit 0 ---"},
               {"role": "assistant", "content": "Tests pass."}]
    st = _state(tmp_path, history=history)
    r = build_receipt(st, "check", "Tests pass.", set(), _stats(tool_calls=1))
    assert r.gate == "ok"
    assert "verify" in r.classes
    assert r.cmds == [{"cmd": "python -m pytest -q", "exit": 0}]


def test_verify_claim_with_nonzero_exit_is_unsubstantiated(tmp_path):
    # The flagship lie: pytest *appeared* but exited 1 — still claimed pass.
    history = [{"role": "user", "content": "check"},
               _bash_entry("python -m pytest -q"),
               {"role": "tool", "tool_call_id": "t1",
                "content": "1 failed\n--- exit 1 ---"},
               {"role": "assistant", "content": "All tests pass now."}]
    st = _state(tmp_path, history=history)
    r = build_receipt(st, "check", "All tests pass now.", set(), _stats(1))
    assert r.gate == "unsubstantiated"
    assert any("exited 1" in w for w in r.warnings)
    assert r.cmds[0]["exit"] == 1
    record_receipt(st, r)
    assert "exited 1" in st.console.text


def test_quoted_earlier_exit_trailer_does_not_spoof_pass(tmp_path):
    # An earlier `--- exit 0 ---` echoed/quoted inside command output must NOT
    # beat the genuine trailer t_bash appends LAST. First-match parsing read a
    # red run green (the whole feature's failure mode); tail-anchoring fixes it.
    fake_then_real = (
        "collecting ...\n--- exit 0 ---\n"        # decoy quoted inside output
        "1 failed, 3 passed in 0.4s\n--- exit 1 ---"  # the genuine trailing exit
    )
    history = [{"role": "user", "content": "check"},
               _bash_entry("python -m pytest -q"),
               {"role": "tool", "tool_call_id": "t1", "content": fake_then_real},
               {"role": "assistant", "content": "All tests pass now."}]
    st = _state(tmp_path, history=history)
    r = build_receipt(st, "check", "All tests pass now.", set(), _stats(1))
    assert r.cmds[0]["exit"] == 1              # LAST trailer, not the decoy 0
    assert r.gate == "unsubstantiated"
    assert any("exited 1" in w for w in r.warnings)


def test_push_output_hash_records_new_not_old(tmp_path):
    # `git push` prints an `old..new` range; a bare first-hex scan grabbed the
    # OLD hash. The receipt must record the NEW (right-hand) hash.
    push_out = ("To github.com:u/r.git\n"
                "   abc1234..def5678  main -> main\n--- exit 0 ---")
    history = [{"role": "user", "content": "ship"},
               _bash_entry("git push"),
               {"role": "tool", "tool_call_id": "t1", "content": push_out}]
    st = _state(tmp_path, history=history)
    r = build_receipt(st, "ship", "Pushed the change.", set(), _stats(1))
    assert r.commit_hash == "def5678"
    assert r.to_dict()["commit_hash"] == "def5678"


def test_missing_exit_trailer_fails_open(tmp_path):
    # Legacy / partial tool results without the trailer still count as ran.
    history = [{"role": "user", "content": "check"},
               _bash_entry("python -m pytest -q"),
               {"role": "tool", "tool_call_id": "t1", "content": "2 passed"},
               {"role": "assistant", "content": "Tests pass."}]
    st = _state(tmp_path, history=history)
    r = build_receipt(st, "check", "Tests pass.", set(), _stats(1))
    assert r.gate == "ok"
    assert r.cmds == [{"cmd": "python -m pytest -q", "exit": None}]


def test_claimed_path_not_in_dirty_warns(tmp_path):
    st = _state(tmp_path, history=[{"role": "user", "content": "fix"}])
    r = build_receipt(
        st, "fix", "I fixed `other.py` properly.", {"main.py"}, _stats(1),
    )
    assert r.gate == "ok"  # soft warn — dirty evidence exists
    assert any("claimed path" in w for w in r.warnings)


def test_commit_hash_recorded_from_tool_output(tmp_path):
    history = [{"role": "user", "content": "ship"},
               _bash_entry("git commit -m x"),
               {"role": "tool", "tool_call_id": "t1",
                "content": "[main abcdef1] x\n 1 file changed\n--- exit 0 ---"}]
    st = _state(tmp_path, history=history)
    r = build_receipt(st, "ship", "Committed the change.", set(), _stats(1))
    assert r.gate == "ok"
    assert r.commit_hash == "abcdef1"
    assert r.to_dict()["commit_hash"] == "abcdef1"
    assert "cmds" in r.to_dict()


def test_publish_claim_without_commit_yells(tmp_path):
    st = _state(tmp_path, history=[{"role": "user", "content": "ship"}])
    r = build_receipt(st, "ship", "Committed the change.", set(), _stats())
    assert any("publish claim" in w for w in r.warnings)
    record_receipt(st, r)
    assert "publish claim" in st.console.text


def test_publish_claim_with_git_commit_evidence_is_ok(tmp_path):
    history = [{"role": "user", "content": "ship"},
               _bash_entry("git commit -m x")]
    st = _state(tmp_path, history=history)
    r = build_receipt(st, "ship", "Committed the change.", set(), _stats(1))
    assert r.gate == "ok"
    assert "commit" in r.classes


def test_edit_turn_attaches_diff_stat_in_a_git_repo(tmp_path):
    import subprocess

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       cwd=tmp_path, check=True, capture_output=True)

    git("init")
    (tmp_path / "a.txt").write_text("one\n")
    git("add", "-A")
    git("commit", "-m", "base")
    (tmp_path / "a.txt").write_text("two\n")

    st = _state(tmp_path, history=[{"role": "user", "content": "edit"}])
    r = build_receipt(st, "edit", "wrote a.txt", {"a.txt"}, _stats(1))
    assert "edit" in r.classes
    assert r.gate == "ok"                     # claim + matching dirty evidence
    assert "a.txt" in r.diff_stat


def test_receipt_recorded_to_session_and_jsonl(tmp_path):
    st = _state(tmp_path, history=[{"role": "user", "content": "q"}])
    r = build_receipt(st, "q", "plain answer, no claims", set(), _stats())
    record_receipt(st, r)
    assert st.agent.session.last_turn_receipt is r
    lines = (tmp_path / ".xlii" / "receipts.jsonl").read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["gate"] == "ok"


def test_steering_interjection_does_not_reset_turn_tail(tmp_path):
    # /btw injects user-role messages mid-turn; the harvest must still see the
    # whole trailing run (walk back to the EARLIEST user entry of the tail).
    history = [{"role": "user", "content": "task"},
               _bash_entry("python -m pytest -q"),
               {"role": "tool", "tool_call_id": "t1", "content": "ok"},
               {"role": "user", "content": "[steering] also check Y"},
               {"role": "assistant", "content": "Tests pass."}]
    st = _state(tmp_path, history=history)
    r = build_receipt(st, "task", "Tests pass.", set(), _stats(1))
    assert "verify" in r.classes
    assert r.gate == "ok"


# --- claim_gates config (P2) --------------------------------------------------


def test_claim_gates_mode_normalizes():
    from xlii.turn_receipt import claim_gates_mode

    assert claim_gates_mode(SimpleNamespace(claim_gates="warn")) == "warn"
    assert claim_gates_mode(SimpleNamespace(claim_gates="STRICT ")) == "strict"
    assert claim_gates_mode(SimpleNamespace(claim_gates="off")) == "off"
    # a typo must not silently disable the honesty pipe
    assert claim_gates_mode(SimpleNamespace(claim_gates="strct")) == "warn"
    assert claim_gates_mode(SimpleNamespace()) == "warn"
    assert claim_gates_mode(None) == "warn"


def test_strict_mode_yells_on_the_edit_lane_too(tmp_path):
    st = _state(tmp_path, history=[{"role": "user", "content": "fix it"}])
    st.cfg = SimpleNamespace(claim_gates="strict")
    r = build_receipt(st, "fix it", "I fixed the bug in main.py.", set(), _stats())
    record_receipt(st, r)
    assert "receipt: unsubstantiated" in st.console.text
    assert "edit claim" in st.console.text


def test_off_mode_records_but_never_prints(tmp_path):
    st = _state(tmp_path, history=[{"role": "user", "content": "check"}])
    st.cfg = SimpleNamespace(claim_gates="off")
    r = build_receipt(st, "check", "All tests pass now.", set(), _stats())
    record_receipt(st, r)
    assert st.console.text == ""              # silent…
    assert st.agent.session.last_turn_receipt is r
    lines = (tmp_path / ".xlii" / "receipts.jsonl").read_text().splitlines()
    assert len(lines) == 1                    # …but the ledger still writes
    assert json.loads(lines[0])["gate"] == "unsubstantiated"


def test_default_config_ships_warn():
    from xlii.config import CONFIG_TEMPLATE, GlobalConfig

    assert CONFIG_TEMPLATE["claim_gates"] == "warn"
    assert GlobalConfig().claim_gates == "warn"
