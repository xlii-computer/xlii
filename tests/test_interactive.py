"""Full-screen / interactive program handling: the registry, the captured-shell
freeze fix (stdin=DEVNULL), the reactive detector, the routing decision, and the
/interactive command. No real terminal needed; capture() spawns tiny subprocesses.

Run with `python -m pytest` (imports tests.helpers).
"""

from types import SimpleNamespace

import xlii.interactive as I
from tests.helpers import FakeConsole


# --------------------------------------------------------------------------- #
#  program_token / is_interactive — parsing + the bare-REPL rule
# --------------------------------------------------------------------------- #

def test_program_token_peels_wrappers_and_paths():
    assert I.program_token("mc") == "mc"
    assert I.program_token("/usr/bin/htop") == "htop"
    assert I.program_token("sudo vim /etc/hosts") == "vim"
    assert I.program_token("env FOO=1 BAR=2 htop") == "htop"
    assert I.program_token("nice -n 10 top") == "top"
    assert I.program_token("command less file.txt") == "less"
    assert I.program_token("FOO=bar python") == "python"


def test_program_token_none_for_pipelines_and_empty():
    assert I.program_token("echo hi | less") is None
    assert I.program_token("vim && ls") is None
    assert I.program_token("cat > out.txt") is None
    assert I.program_token("") is None


def test_is_interactive_always_fullscreen():
    assert I.is_interactive("mc")
    assert I.is_interactive("vim file.py")          # editors are full-screen with args too
    assert I.is_interactive("sudo nano /etc/hosts")
    assert I.is_interactive("htop")
    assert I.is_interactive("less big.log")
    assert I.is_interactive("man ls")


def test_is_interactive_negatives():
    assert not I.is_interactive("ls -la")
    assert not I.is_interactive("git log")           # pager-spawner; token=git → captures fine
    assert not I.is_interactive("echo hi | less")    # pipeline
    assert not I.is_interactive("grep foo *.py")


def test_repl_when_bare_rule():
    assert I.is_interactive("python")                # bare REPL → interactive
    assert I.is_interactive("python -i")             # only flags → still a REPL
    assert not I.is_interactive("python script.py")  # positional script → batch, capture
    assert not I.is_interactive("python -c 'print(1)'")  # -c value is positional-ish → batch
    assert I.is_interactive("node")
    assert not I.is_interactive("node app.js")


# --------------------------------------------------------------------------- #
#  user list: defaults ∪ additions, add/remove
# --------------------------------------------------------------------------- #

def test_user_list_add_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(I, "USER_LIST_FILE", tmp_path / "interactive.txt")
    assert "foobar-tui" not in I.interactive_programs()
    assert I.add_program("foobar-tui") is True
    assert "foobar-tui" in I.interactive_programs()
    assert I.is_interactive("foobar-tui --watch")     # user-added → always interactive
    assert I.add_program("foobar-tui") is False        # idempotent
    assert I.add_program("vim") is False               # already a default
    assert I.remove_program("foobar-tui") is True
    assert "foobar-tui" not in I.interactive_programs()
    assert I.remove_program("vim") is False             # can't remove a default


# --------------------------------------------------------------------------- #
#  the freeze fix: captured commands get a closed stdin → interactive EXITS
# --------------------------------------------------------------------------- #

def test_capture_closes_stdin_so_stdin_reader_exits(tmp_path):
    from xlii.tui.shell import capture
    # `cat` with no args reads stdin forever on a tty; with DEVNULL it hits EOF
    # immediately and exits — proving a mis-captured interactive program can't
    # hang the session.
    cap = capture("cat", tmp_path, timeout=10)
    assert cap.timed_out is False
    assert cap.returncode == 0


# --------------------------------------------------------------------------- #
#  reactive detector
# --------------------------------------------------------------------------- #

def test_looks_interactive():
    from xlii.tui.shell import looks_interactive
    assert looks_interactive("\x1b[?1049hgarbage", "")        # alt-screen enter
    assert looks_interactive("", "error: stdout is not a terminal")
    assert looks_interactive("", "TERM environment variable not set")
    assert not looks_interactive("hello\nworld\n", "")
    assert not looks_interactive("", "permission denied")


# --------------------------------------------------------------------------- #
#  inline routing: !! / allowlist → raw handover; plain → capture
# --------------------------------------------------------------------------- #

class _FakeEv:
    stdout = ""
    stderr = ""
    returncode = 0


def test_passthrough_routes_interactive_to_raw(tmp_path, monkeypatch):
    import xlii.repl as R
    raw_calls: list[str] = []
    cap_calls: list[str] = []
    monkeypatch.setattr(R, "styled_enabled", lambda: True)   # capture is the default path
    monkeypatch.setattr(R, "_run_raw_shell", lambda cmd, cwd, printer: raw_calls.append(cmd))
    monkeypatch.setattr(R, "run_shell_captured",
                        lambda cmd, cwd, **k: cap_calls.append(cmd) or _FakeEv())
    monkeypatch.setattr(R, "renderer", SimpleNamespace(emit=lambda ev: None))

    R._run_shell_passthrough("!!ls", tmp_path)     # `!!` forces raw
    R._run_shell_passthrough("!mc", tmp_path)      # interactive → raw even in styled mode
    R._run_shell_passthrough("!ls", tmp_path)      # plain → captured

    assert raw_calls == ["ls", "mc"]
    assert cap_calls == ["ls"]


def test_passthrough_sudo_opens_elsewhere_on_face(tmp_path, monkeypatch):
    import xlii.repl as R

    opened = []
    monkeypatch.setattr(R, "styled_enabled", lambda: True)
    monkeypatch.setattr(R, "_surface_has_terminal", lambda st: False)
    monkeypatch.setattr(R, "_open_elsewhere", lambda st, cmd, cwd: opened.append(cmd))
    monkeypatch.setattr(R, "run_shell_captured", lambda *a, **k: (_ for _ in ()).throw(AssertionError("captured")))
    assert R._run_shell_passthrough("!sudo apt install slack", tmp_path) is True
    assert opened == ["sudo apt install slack"]


# --------------------------------------------------------------------------- #
#  /interactive command
# --------------------------------------------------------------------------- #

def test_interactive_command(tmp_path, monkeypatch):
    monkeypatch.setattr(I, "USER_LIST_FILE", tmp_path / "interactive.txt")
    from xlii.repl_cmds.registry_cmds import _cmd_interactive

    c = FakeConsole()
    _cmd_interactive("/interactive add foobar-tui", {"console": c})
    assert "foobar-tui" in I.interactive_programs()

    c = FakeConsole()
    _cmd_interactive("/interactive list", {"console": c})
    assert "foobar-tui" in c.text and "vim" in c.text

    c = FakeConsole()
    _cmd_interactive("/interactive remove vim", {"console": c})
    assert "built-in default" in c.text             # defaults are protected

    c = FakeConsole()
    _cmd_interactive("/interactive remove foobar-tui", {"console": c})
    assert "foobar-tui" not in I.interactive_programs()


def test_launch_in_external_terminal_runs_mc(monkeypatch, tmp_path):
    monkeypatch.setenv("DISPLAY", ":0")
    seen = {}

    def fake_popen(argv, **kw):
        seen["argv"] = list(argv)
        seen["cwd"] = kw.get("cwd")
        return object()

    ok, msg = I.launch_in_external_terminal(
        tmp_path, run="mc", preferred="",
        popen=fake_popen, which=lambda n: f"/usr/bin/{n}" if n == "kitty" else None,
    )
    assert ok
    assert seen["argv"][0] == "/usr/bin/kitty"
    assert seen["argv"][-1] == "mc"
    assert seen["cwd"] == str(tmp_path)
    assert "mc" in msg


def test_launch_preferred_appends_cmd(monkeypatch, tmp_path):
    monkeypatch.setenv("DISPLAY", ":0")
    seen = {}

    def fake_popen(argv, **kw):
        seen["argv"] = list(argv)
        return object()

    ok, msg = I.launch_in_external_terminal(
        tmp_path, run="mc", preferred="kitty --directory {cwd}",
        popen=fake_popen, which=lambda n: f"/bin/{n}",
    )
    assert ok
    assert seen["argv"][:3] == ["/bin/kitty", "--directory", str(tmp_path)]
    assert seen["argv"][-1] == "mc"


def test_launch_refuses_headless(monkeypatch, tmp_path):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    ok, msg = I.launch_in_external_terminal(tmp_path, run="mc")
    assert ok is False
    assert "graphical" in msg
