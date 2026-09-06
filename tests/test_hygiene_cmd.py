""" /hygiene REPL command """

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from xlii.commands import find_repl_command
from xlii.repl_cmds import register_all
from xlii.repl_cmds import hygiene as H
from xlii.text_hygiene import UTF8_BOM

register_all()


class _Console:
    def __init__(self):
        self.lines: list[str] = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _ctx(tmp_path: Path, cwd: Path | None = None):
    root = tmp_path
    project = SimpleNamespace(project_root=root, xli_dir=root / ".xlii")
    state = SimpleNamespace(project=project, shell_cwd=cwd or root)
    return {"console": _Console(), "project": project, "state": state}


def test_command_registered_code_only():
    assert find_repl_command("/hygiene", "code") is not None
    assert find_repl_command("/sanitize", "code") is not None
    # code console tool — not on chat surface
    assert find_repl_command("/hygiene", "chat") is None


def test_help_usage():
    ctx = _ctx(Path("/tmp"))
    assert H._handler("/hygiene", ctx) is True
    assert "scan" in ctx["console"].text and "strip" in ctx["console"].text


def test_scan_clean_file(tmp_path: Path):
    p = tmp_path / "ok.txt"
    p.write_text("hello\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    assert H._handler(f"/hygiene scan {p}", ctx) is True
    out = ctx["console"].text
    assert "credibility: 0" in out
    assert "take note" not in out


def test_scan_dirty_warns_take_note(tmp_path: Path):
    p = tmp_path / "dirty.txt"
    p.write_bytes(UTF8_BOM + f"a{chr(0x200B)}b\r\n".encode("utf-8"))
    ctx = _ctx(tmp_path)
    assert H._handler(f"/hygiene scan {p.name}", ctx) is True
    out = ctx["console"].text
    assert "credibility: 1" in out
    assert "ZWSP" in out
    assert "take note" in out
    assert "UTF-8 BOM" in out or "bom:" in out.lower()


def test_strip_cleans_file(tmp_path: Path):
    p = tmp_path / "fixme.txt"
    p.write_bytes(UTF8_BOM + f"x{chr(0x200B)}y\r\n".encode("utf-8"))
    ctx = _ctx(tmp_path)
    assert H._handler(f"/hygiene strip {p}", ctx) is True
    out = ctx["console"].text
    assert "stripped" in out
    data = p.read_bytes()
    assert not data.startswith(UTF8_BOM)
    assert b"\r\n" not in data
    assert chr(0x200B).encode() not in data
    assert p.read_text(encoding="utf-8") == "xy\n"


def test_strip_already_clean(tmp_path: Path):
    p = tmp_path / "clean.txt"
    p.write_text("just fine\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    assert H._handler(f"/hygiene strip {p}", ctx) is True
    assert "already clean" in ctx["console"].text


def test_unknown_flag(tmp_path: Path):
    p = tmp_path / "a.txt"
    p.write_text("a\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    assert H._handler(f"/hygiene strip {p} --explode", ctx) is True
    assert "unknown flag" in ctx["console"].text


def test_missing_path(tmp_path: Path):
    ctx = _ctx(tmp_path)
    assert H._handler("/hygiene scan missing-nope.txt", ctx) is True
    assert "not a file" in ctx["console"].text or "error" in ctx["console"].text


def test_bare_paths_default_to_scan(tmp_path: Path):
    p = tmp_path / "b.txt"
    p.write_text("z\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    assert H._handler(f"/hygiene {p}", ctx) is True
    assert "credibility: 0" in ctx["console"].text


def test_skip_binary(tmp_path: Path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"\x00\x01")
    ctx = _ctx(tmp_path)
    assert H._handler(f"/hygiene strip {p}", ctx) is True
    assert "binary" in ctx["console"].text.lower()
