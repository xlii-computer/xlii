"""U1 of the upload locker (proposals/upload-locker.md): the `attached_files`
model + the `/locker` command. Disk-only, no network, no GUI.

Gate: toggling a file off drops it from the live set (and thus the next turn);
`--once` entries auto-disable after one turn; the locker persists across restarts.
"""

import xlii.multimodal as M
from xlii.commands import dispatch_repl_command
from xlii.config import GlobalConfig
from xlii.repl import REPLState
from xlii.repl_cmds import register_all
from tests.helpers import FakeConsole, make_agent

register_all()  # built-in slash commands (incl. /locker) registered explicitly


def _state(tmp_path, *, sub="proj"):
    root = tmp_path / sub
    (root / ".xlii").mkdir(parents=True, exist_ok=True)
    cfg = GlobalConfig()
    agent = make_agent(root, cfg=cfg)
    return REPLState(console=FakeConsole(), agent=agent, project=agent.project,
                     cfg=cfg, pool=agent.pool)


def _img(tmp_path, name="a.png", data=b"PNG"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #

def test_classify_kind(tmp_path):
    assert M.classify_kind("a.png") == "image"
    assert M.classify_kind("a.JPG") == "image"
    assert M.classify_kind("notes.md") == "text"
    assert M.classify_kind("doc.pdf") == "pdf"
    assert M.classify_kind("blob.bin") == "other"


def test_estimate_live_cost(tmp_path):
    img = _img(tmp_path, "x.png", b"0123456789")
    assert M.estimate_live_cost([]) == "nothing enabled"
    line = M.estimate_live_cost([str(img)])
    assert "1 file" in line and "approx" in line


# --------------------------------------------------------------------------- #
#  REPLState locker model
# --------------------------------------------------------------------------- #

def test_attach_appears_in_live_and_list(tmp_path):
    state = _state(tmp_path)
    img = _img(tmp_path)
    e = state.attach_file(img)
    assert e["kind"] == "image" and e["enabled"] is True
    assert state.live_attachment_paths() == [e["path"]]
    assert state.list_attachments()["files"][0]["name"] == "a.png"


def test_toggle_off_drops_from_live_then_back(tmp_path):
    state = _state(tmp_path)
    e = state.attach_file(_img(tmp_path))
    assert state.set_file_enabled("a.png", False) is True
    assert state.live_attachment_paths() == []          # the gate: held → not sent
    assert any(e2["name"] == "a.png" for e2 in state.attached_files)  # still present
    state.set_file_enabled("a.png", True)
    assert state.live_attachment_paths() == [e["path"]]


def test_remove_file(tmp_path):
    state = _state(tmp_path)
    state.attach_file(_img(tmp_path))
    assert state.remove_file("a.png") is True
    assert state.attached_files == []
    assert state.remove_file("a.png") is False          # idempotent


def test_once_consumed_after_a_turn(tmp_path):
    state = _state(tmp_path)
    state.attach_file(_img(tmp_path), once=True)
    assert state.live_attachment_paths() != []          # live for the next turn
    state.consume_once_attachments()                    # the turn ran
    assert state.live_attachment_paths() == []          # auto-disabled
    assert state.attached_files[0]["once"] is True      # entry kept, just held


def test_readd_known_path_reenables(tmp_path):
    state = _state(tmp_path)
    img = _img(tmp_path)
    state.attach_file(img)
    state.set_file_enabled("a.png", False)
    state.attach_file(img)                               # re-add same path
    assert state.live_attachment_paths() != []
    assert len(state.attached_files) == 1               # not duplicated


def test_persistence_roundtrip(tmp_path):
    s1 = _state(tmp_path)
    s1.attach_file(_img(tmp_path, "p.png"))
    s1.attach_file(_img(tmp_path, "n.txt", b"hi"), once=True)
    s1.set_file_enabled("p.png", False)
    s1.save()

    s2 = _state(tmp_path)                               # fresh agent/session, same dir
    assert s2.load() is True
    by_name = {e["name"]: e for e in s2.attached_files}
    assert by_name["p.png"]["enabled"] is False
    assert by_name["n.txt"]["once"] is True
    assert by_name["n.txt"]["kind"] == "text"


def test_clear_attachments_clears_files(tmp_path):
    state = _state(tmp_path)
    state.attach_file(_img(tmp_path))
    state.clear_attachments()
    assert state.attached_files == []


# --------------------------------------------------------------------------- #
#  /locker command
# --------------------------------------------------------------------------- #

def test_locker_command_add_toggle_remove(tmp_path):
    state = _state(tmp_path)
    img = _img(tmp_path, "shot.png")
    ctx = state.as_context_dict()

    dispatch_repl_command(f"/locker add {img}", ctx)
    assert any(e["name"] == "shot.png" and e["enabled"] for e in state.attached_files)

    dispatch_repl_command("/locker off shot.png", ctx)
    assert all(not e["enabled"] for e in state.attached_files if e["name"] == "shot.png")

    dispatch_repl_command("/locker on shot.png", ctx)
    assert state.live_attachment_paths() != []

    dispatch_repl_command("/locker remove shot.png", ctx)
    assert state.attached_files == []


def test_locker_warns_when_model_cannot_see_images(tmp_path):
    # Regression for the "attached an image, asked about it, hung" report: the
    # fake cfg's model ("orch-model") isn't vision-capable, so adding an image
    # should warn up front rather than letting an image turn stall.
    state = _state(tmp_path)
    dispatch_repl_command(f"/locker add {_img(tmp_path)}", state.as_context_dict())
    assert "can't see images" in state.console.text


def test_locker_no_warning_when_vision_profile_applied(tmp_path):
    from xlii.model_profiles import apply_model_profile

    state = _state(tmp_path)
    apply_model_profile(state.cfg, "vision", persist=False)
    dispatch_repl_command(f"/locker add {_img(tmp_path)}", state.as_context_dict())
    assert "can't see" not in state.console.text.lower()


def test_locker_uses_chat_role_on_conversational_surface(tmp_path):
    state = _state(tmp_path)
    state.agent.session.conversational = True
    state.cfg.orchestrator_model = "grok-build-0.1"
    state.cfg.chat_model = "grok-4"
    dispatch_repl_command(f"/locker add {_img(tmp_path)}", state.as_context_dict())
    assert "can't see" not in state.console.text.lower()


def test_locker_command_add_once_and_nonfile(tmp_path):
    state = _state(tmp_path)
    img = _img(tmp_path, "once.png")
    ctx = state.as_context_dict()

    dispatch_repl_command(f"/locker add {img} --once", ctx)
    assert state.attached_files[0]["once"] is True

    # a non-file is rejected, not added
    dispatch_repl_command("/locker add /no/such/file.png", ctx)
    assert len(state.attached_files) == 1


# --------------------------------------------------------------------------- #
#  two-pane rewire: the file-tab panel tree is another producer for this locker
# --------------------------------------------------------------------------- #

def test_locker_list_footer_points_at_panel_tree(tmp_path):
    # The /locker listing names the file-tab panel tree as an in-TUI producer
    # (the supersede note — same attach_file path), beside /upload + /locker add.
    state = _state(tmp_path)
    state.attach_file(_img(tmp_path, "shot.png"))
    dispatch_repl_command("/locker", state.as_context_dict())
    assert "/file-tab" in state.console.text


def test_locker_empty_hint_points_at_panel_tree(tmp_path):
    # Even with an empty locker, the hint surfaces the in-TUI panel-tree producer.
    state = _state(tmp_path)
    dispatch_repl_command("/locker", state.as_context_dict())
    assert "/file-tab" in state.console.text
