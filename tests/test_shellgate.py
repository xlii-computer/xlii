from pathlib import Path

import pytest

from xlii.shellgate import classify_command

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("cmd,expected", [
    ("ls -la", "read-only"),
    ("git status && git log --oneline", "read-only"),
    ("grep -rn foo src/", "read-only"),
    ("FOO=1 pytest -q", "read-only"),
    ("cat a.txt > b.txt", "modifies-project"),
    ("rm -rf build/", "modifies-project"),
    ("sed -i 's/a/b/' f.py", "modifies-project"),
    # GNU sed glues an optional backup suffix to -i, so all of these are
    # in-place edits. -in (suffix 'n') was the false-negative the old guard hit.
    ("sed -in 's/a/b/' f.py", "modifies-project"),
    ("sed -i.bak 's/a/b/' f.py", "modifies-project"),
    ("sed --in-place 's/a/b/' f.py", "modifies-project"),
    # ...but read-only sed flags must NOT escalate, and -i on other binaries
    # (grep case-insensitive) is unrelated.
    ("sed -n '1,5p' f.py", "read-only"),
    ("sed -e 's/a/b/' f.py", "read-only"),
    ("grep -i foo src/", "read-only"),
    ("git commit -am x", "modifies-project"),
    ("python -c 'open(\"x\",\"w\")'", "modifies-project"),
    ("python3 -c 'import urllib.request; urllib.request.urlopen(\"https://x\")'", "network"),
    ("curl https://example.com", "network"),
    ("pip install requests", "network"),
    ("git push origin main", "network"),
    ("sudo apt install jq", "modifies-system"),
    ("kill -9 1234", "modifies-system"),
    ("systemctl restart nginx", "modifies-system"),
    ("rm -rf /etc/passwd", "modifies-system"),
    ("rm -rf ~/other-project", "modifies-system"),
    ("echo $(rm -rf /tmp/x)", "modifies-system"),
    ("ls `curl evil.sh`", "network"),
    ("grep foo .; shutdown now", "modifies-system"),
    ("grep 'a&b' file", "read-only"),
    ("echo ok & rm -rf important/", "modifies-project"),
    ("ls & curl https://example.com", "network"),
    ("cat <(rm -rf important/)", "modifies-project"),
    ("cat <( (rm -rf important/) )", "modifies-project"),
    # Intentionally malformed process substitution: should fail closed.
    ("cat <(echo (oops)", "modifies-project"),
    ("echo hidden &> out.txt", "modifies-project"),
    # find: bare walks are read-only; -delete mutates; the exec-family body is
    # a command in its own right (grep stays read-only, rm escalates — these
    # were read-only leaks that let a "read-only" worker delete files).
    ("find . -name '*.tmp'", "read-only"),
    ("find . -name '*.tmp' -delete", "modifies-project"),
    ("find . -name '*.py' -exec grep -l foo {} ;", "read-only"),
    ("find . -name '*.tmp' -exec rm {} ;", "modifies-project"),
    ("find / -exec sudo chmod 777 {} ;", "modifies-system"),
    # xargs classifies by the command it wraps, walking arg-taking flags so
    # `-n 5 rm` classifies rm (not "5"); unknown flags fail toward mutation.
    ("ls | xargs grep -l foo", "read-only"),
    ("ls | xargs rm", "modifies-project"),
    ("ls | xargs -n 5 rm", "modifies-project"),
    ("ls | xargs -I {} cp {} /tmp/", "modifies-system"),
    ("ls | xargs --weird-flag rm", "modifies-project"),
    ("ls | xargs", "read-only"),  # no wrapped command → defaults to echo
    # build drivers run arbitrary recipe shells — interpreter posture.
    ("make build", "modifies-project"),
    ("ninja -C build", "modifies-project"),
    ("cmake -B build", "modifies-project"),
    # Wrappers / builtins must not hide the inner command (worker-model hole).
    ("env curl https://example.com", "network"),
    ("env FOO=bar curl https://example.com", "network"),
    ("env -i curl https://example.com", "network"),
    ("command curl https://example.com", "network"),
    ("nice curl https://example.com", "network"),
    ("time curl https://example.com", "network"),
    ("nohup curl https://example.com", "network"),
    ("stdbuf -oL curl https://example.com", "network"),
    ("timeout 5 curl https://example.com", "network"),
    ("busybox wget https://example.com", "network"),
    ("eval \"curl https://example.com\"", "network"),
    ("source ./setup.sh", "modifies-project"),
    (". ./setup.sh", "modifies-project"),
    ("env sudo apt update", "modifies-system"),
    # Interpreter -c bodies are commands in their own right.
    ("bash -c 'curl https://example.com'", "network"),
    ("bash -c 'sudo apt update'", "modifies-system"),
    ("tee /etc/passwd", "modifies-system"),
    ("echo x > /etc/cron.d/xlii", "modifies-system"),
])
def test_classify(cmd, expected):
    assert classify_command(cmd, ROOT) == expected


def test_worker_bash_blocked_by_classifier(tmp_path):
    """A worker declaring read-only on a mutating command is refused in code."""
    from xlii.config import GlobalConfig, ProjectConfig
    from xlii.tools import t_bash, ToolContext

    project = ProjectConfig(
        project_root=tmp_path, name="t", collection_id="c",
        created_at="2026-01-01", local_only=True,
    )
    ctx = ToolContext(project=project, clients=None, cfg=GlobalConfig(), is_worker=True)
    res = t_bash(ctx, {"command": "rm -rf important/", "intent": "read-only"})
    assert res.is_error
    assert "read-only" in res.content
    assert (tmp_path / "important").exists() is False  # nothing ran


def test_worker_bash_blocks_background_shell_bypass(tmp_path):
    """A background separator must not hide a mutating command from worker gating."""
    from xlii.config import GlobalConfig, ProjectConfig
    from xlii.tools import ToolContext, t_bash

    important = tmp_path / "important"
    important.mkdir()
    (important / "data.txt").write_text("keep")
    project = ProjectConfig(
        project_root=tmp_path, name="t", collection_id="c",
        created_at="2026-01-01", local_only=True,
    )
    ctx = ToolContext(project=project, clients=None, cfg=GlobalConfig(), is_worker=True)
    res = t_bash(ctx, {"command": "echo ok & rm -rf important/", "intent": "read-only"})
    assert res.is_error
    assert "read-only" in res.content
    assert (important / "data.txt").read_text() == "keep"


def test_bash_redacts_injected_plugin_secret_from_outputs(tmp_path, monkeypatch):
    """Vault-injected plugin secrets must not echo into model content or shell events."""
    import os

    from xlii import tool_handlers
    from xlii.config import GlobalConfig, ProjectConfig
    from xlii.tools import ToolContext

    secret = "super-secret-token"

    def fake_env_with_plugin_secrets(_ctx, _cmd):
        return {**os.environ, "DEMO_TOKEN": secret}, (secret,)

    project = ProjectConfig(
        project_root=tmp_path, name="t", collection_id="c",
        created_at="2026-01-01", local_only=True,
    )
    ctx = ToolContext(
        project=project, clients=None, cfg=GlobalConfig(), subscribed_plugins=["demo"],
    )
    monkeypatch.setattr(tool_handlers.shell, "_env_with_plugin_secrets", fake_env_with_plugin_secrets)

    res = tool_handlers.t_bash(ctx, {"command": "printf $DEMO_TOKEN", "intent": "read-only"})

    assert not res.is_error
    assert secret not in res.content
    assert secret not in res.shell.stdout
    assert "[redacted secret]" in res.content
    assert "[redacted secret]" in res.shell.stdout


def test_intent_mismatch_gates_on_classification(tmp_path, monkeypatch):
    """Declared read-only but classified network → the y/N gate fires."""
    from xlii import tools
    from xlii.config import GlobalConfig, ProjectConfig

    project = ProjectConfig(
        project_root=tmp_path, name="t", collection_id="c",
        created_at="2026-01-01", local_only=True,
    )

    class FakeConsole:
        def print(self, *a, **k):
            pass

    ctx = tools.ToolContext(project=project, clients=None, cfg=GlobalConfig(), console=FakeConsole())
    monkeypatch.setattr(tools, "_confirm", lambda prompt: "n")
    res = tools.t_bash(ctx, {"command": "curl https://example.com", "intent": "read-only"})
    assert res.is_error
    assert "denied" in res.content


def test_worker_bash_blocks_env_wrapper_network(tmp_path):
    """`env curl` must not classify as read-only and sneak past the worker ceiling."""
    from xlii.config import GlobalConfig, ProjectConfig
    from xlii.tools import ToolContext, t_bash

    project = ProjectConfig(
        project_root=tmp_path, name="t", collection_id="c",
        created_at="2026-01-01", local_only=True,
    )
    ctx = ToolContext(project=project, clients=None, cfg=GlobalConfig(), is_worker=True)
    res = t_bash(ctx, {"command": "env curl https://example.com", "intent": "read-only"})
    assert res.is_error
    assert "read-only" in res.content


def test_unknown_root_treats_abs_and_home_as_system():
    """Inbox and other no-root callers must not treat ``rm /etc`` as in-tree."""
    assert classify_command("rm -rf /etc/passwd", None) == "modifies-system"
    assert classify_command("tee ~/outside", None) == "modifies-system"
    assert classify_command("rm -rf build/", None) == "modifies-project"
    assert classify_command("ls /etc", None) == "read-only"
