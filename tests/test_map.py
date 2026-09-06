"""The repo-map vector (proposals/map.md): one engine, four thin clients.

Covers the engine's locked decisions (determinism, degrade-not-raise, LOUD
budget truncation with the cap winning over everything, scope containment,
missing-path honesty), BOTH tree sources (git ls-files — unicode names,
gitignore, the empty-ls-files fallback — and the os.walk fallback), the ``map``
agent tool, the ``/map`` attach/detach round-trip through the production
attach seam, the read-only ``map://`` provider, and that both command surfaces
are registered.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from xlii.repo_map import TRUNCATION_MARKER, build_map

_MARKER_STEM = TRUNCATION_MARKER.split("{")[0]  # "… truncated ("


# --------------------------------------------------------------------------- #
#  Fixture tree — nested packages, a non-.py file, a deliberately broken .py
# --------------------------------------------------------------------------- #


@pytest.fixture
def tree(tmp_path) -> Path:
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "pkg" / "__init__.py").write_text('"""Package one-liner.\n\nMore prose."""\n')
    (tmp_path / "pkg" / "mod.py").write_text(
        '"""Module doc first line."""\n\n'
        "class Widget(Base):\n"
        '    """A widget."""\n'
        "    def render(self, width: int = 80) -> str:\n"
        '        """Render it."""\n'
        '        return ""\n\n'
        "async def main(argv=None, *, verbose: bool = False):\n"
        '    """Entry point."""\n'
    )
    (tmp_path / "pkg" / "sub" / "deep.py").write_text("def deep_fn(x):\n    return x\n")
    (tmp_path / "notes.txt").write_text("just text\n")
    (tmp_path / "broken.py").write_text("def broken(:\n    nope\n")
    return tmp_path


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def git_tree(tree) -> Path:
    """The same fixture tree, made a real git repo — so the ls-files source
    (what every real project takes) is what's exercised: gitignore respected,
    non-ASCII filenames surviving un-C-quoted."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    _git(tree, "init", "-q")
    (tree / ".gitignore").write_text("ignored-secret.txt\n")
    (tree / "ignored-secret.txt").write_text("shh\n")
    (tree / "héllø wörld.py").write_text('"""Ünïcode module."""\n\ndef greet(name):\n    pass\n')
    return tree


# --------------------------------------------------------------------------- #
#  Engine — walk source
# --------------------------------------------------------------------------- #


def test_outline_has_signatures_and_doclines(tree):
    out = build_map(tree)
    assert "class Widget(Base)" in out
    assert "def render(self, width: int=80) -> str" in out
    assert "async def main(argv=None, *, verbose: bool=False)" in out
    assert "Module doc first line." in out
    assert "A widget." in out
    assert "Render it." in out
    assert "notes.txt" in out  # non-Python: tree entry + size
    assert "(10 B)" in out


def test_two_runs_are_byte_identical(tree):
    assert build_map(tree) == build_map(tree)  # locked decision #2


def test_broken_python_degrades_to_unparseable(tree):
    out = build_map(tree)  # must not raise
    assert "broken.py" in out
    assert "(unparseable)" in out


def test_detail_files_is_tree_only(tree):
    out = build_map(tree, detail="files")
    assert "pkg/mod.py" in out
    assert "class Widget" not in out
    with pytest.raises(ValueError, match="detail"):
        build_map(tree, detail="everything")


# --------------------------------------------------------------------------- #
#  Engine — git source (what every real project takes)
# --------------------------------------------------------------------------- #


def test_git_source_respects_gitignore(git_tree):
    out = build_map(git_tree)
    assert "pkg/mod.py" in out and "class Widget(Base)" in out
    assert "ignored-secret.txt" not in out   # ls-files path taken — walk would include it
    assert ".gitignore" in out               # untracked-but-not-ignored files ride


def test_git_unicode_filename_survives_unquoted(git_tree):
    # core.quotepath=off + -z: a non-ASCII name must come back verbatim, stat,
    # parse, and appear — not C-quoted ("h\303\251…") and silently dropped.
    out = build_map(git_tree)
    assert "héllø wörld.py" in out
    assert "Ünïcode module." in out and "def greet(name)" in out
    assert "\\303" not in out and '"h' not in out


def test_git_empty_ls_files_falls_back_to_walk(tmp_path):
    # A root that is itself gitignored inside an enclosing repo: ls-files
    # succeeds but lists nothing — the engine must fall back to the walk, not
    # return an empty map.
    if shutil.which("git") is None:
        pytest.skip("git not available")
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("inner/\n")
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "mod.py").write_text("def f():\n    pass\n")
    out = build_map(inner)
    assert "mod.py" in out and "def f()" in out


# --------------------------------------------------------------------------- #
#  Budget — the cap is HARD and truncation is LOUD, in every branch
# --------------------------------------------------------------------------- #


def test_budget_truncates_loudly_symbols_before_tree(tree):
    # Base = the files-only render + the marker + the " (unparseable)" head note
    # (which stays when symbols strip — it's head metadata, not symbol detail),
    # sized so the tree fits only with ALL THREE symbol layers dropped.
    marker = TRUNCATION_MARKER.format(elided=0, stripped=3)
    budget = len(build_map(tree, detail="files").encode()) + len(marker.encode()) + 2 + 25
    out = build_map(tree, max_bytes=budget)
    assert len(out.encode()) <= budget                    # hard cap holds
    assert _MARKER_STEM in out                            # LOUD truncation
    for rel in ("pkg/mod.py", "pkg/sub/deep.py", "broken.py", "notes.txt"):
        assert rel in out                                 # tree survives …
    assert "class Widget" not in out                      # … symbol detail dropped first
    assert "0 files elided, 3 stripped" in out            # both counts, each honest


def test_budget_trims_tree_after_symbols(tree):
    out = build_map(tree, max_bytes=120)
    assert len(out.encode()) <= 120                       # hard cap holds
    assert _MARKER_STEM in out
    assert "5 files elided, 0 stripped" in out            # honest counts
    assert "broken.py" not in out                         # tree levels trimmed


def test_budget_degenerate_cap_still_wins_and_is_loud(tree):
    # A cap smaller than the marker itself: the output is the truncated marker
    # alone — the cap wins over EVERYTHING, loudness survives.
    out = build_map(tree, max_bytes=40)
    assert len(out.encode()) <= 40                        # hard cap holds even here
    assert out.startswith("… truncated")
    assert "pkg" not in out and "notes.txt" not in out


def test_budget_default_is_capped(tree):
    out = build_map(tree)
    assert len(out.encode()) <= 16_384


# --------------------------------------------------------------------------- #
#  Scope / depth
# --------------------------------------------------------------------------- #


def test_scope_filters_to_subtree(tree):
    out = build_map(tree, scope="pkg")
    assert "mod.py" in out and "sub/deep.py" in out
    assert "notes.txt" not in out and "broken.py" not in out


def test_scope_single_file_outline(tree):
    out = build_map(tree, scope="pkg/mod.py")
    assert "class Widget(Base)" in out
    assert "deep_fn" not in out


def test_depth_caps_tree_levels(tree):
    out = build_map(tree, depth=1)
    assert "notes.txt" in out and "broken.py" in out
    assert "pkg/mod.py" not in out           # one level down — capped
    scoped = build_map(tree, scope="pkg", depth=1)
    assert "mod.py" in scoped and "sub/deep.py" not in scoped


def test_scope_outside_root_rejected(tree):
    with pytest.raises(ValueError, match="escapes"):
        build_map(tree, scope="../elsewhere")
    with pytest.raises(ValueError, match="escapes"):
        build_map(tree, scope="pkg/../../elsewhere")


def test_missing_scope_and_root_raise_not_empty_success(tree):
    # An empty map for a missing path would read as "the directory is empty".
    with pytest.raises(ValueError, match="no such path"):
        build_map(tree, scope="no/such/dir")
    with pytest.raises(ValueError, match="not a directory"):
        build_map(tree / "nowhere")


# --------------------------------------------------------------------------- #
#  Agent tool — t_map
# --------------------------------------------------------------------------- #


def test_t_map_returns_toolresult_honoring_path(tree):
    from tests.helpers import make_tool_ctx
    from xlii.tool_handlers import t_map

    ctx = make_tool_ctx(tree)
    res = t_map(ctx, {"path": "pkg"})
    assert not res.is_error
    assert "class Widget(Base)" in res.content
    assert "notes.txt" not in res.content     # scoped out

    res_all = t_map(ctx, {})
    assert not res_all.is_error and "notes.txt" in res_all.content

    res_files = t_map(ctx, {"detail": "files", "depth": 1})
    assert not res_files.is_error
    assert "class Widget" not in res_files.content

    assert t_map(ctx, {"path": "../.."}).is_error
    assert t_map(ctx, {"depth": "many"}).is_error
    missing = t_map(ctx, {"path": "no/such/dir"})     # error, never empty success
    assert missing.is_error and "no such path" in missing.content


def test_map_tool_registered_and_in_explore_palette():
    from xlii.tool_schemas import _EXPLORE_TOOLS, REGISTRY, worker_tool_schemas

    assert "map" in REGISTRY
    assert "map" in _EXPLORE_TOOLS
    explore = {s["function"]["name"] for s in worker_tool_schemas(role="explore")}
    assert "map" in explore


# --------------------------------------------------------------------------- #
#  /map — attach/detach round-trip through the PRODUCTION attach seam
# --------------------------------------------------------------------------- #


class _FakeState:
    """Mirrors REPLState's attach seam exactly (attach_doc no-ops on a duplicate
    name; detach_doc rebuilds the list and reports removal) so the production
    attach_doc/detach_doc path in repl_cmds.map is what's tested — not the
    legacy attached_docs-list fallback."""

    def __init__(self) -> None:
        self.attached_docs: list[tuple[str, str]] = []

    def attach_doc(self, name: str, content: str) -> None:
        if not any(n == name for n, _ in self.attached_docs):
            self.attached_docs.append((name, content))

    def detach_doc(self, name: str) -> bool:
        before = len(self.attached_docs)
        self.attached_docs = [(n, c) for n, c in self.attached_docs if n != name]
        return len(self.attached_docs) < before


def _ctx(tree):
    from tests.helpers import FakeConsole

    state = _FakeState()
    return {
        "console": FakeConsole(),
        "state": state,
        "project": SimpleNamespace(project_root=tree),
    }, state


def test_repl_map_attach_replace_and_off(tree):
    from xlii.repl_cmds.map import _handler

    ctx, state = _ctx(tree)
    assert _handler("/map attach", ctx) is True
    assert [n for n, _ in state.attached_docs] == ["map"]
    full = state.attached_docs[0][1]
    assert "class Widget(Base)" in full

    # re-attach with a scope REPLACES the prior copy (still exactly one "map"),
    # through the real attach_doc (which no-ops on duplicates) + detach_doc.
    assert _handler("/map attach pkg", ctx) is True
    assert [n for n, _ in state.attached_docs] == ["map"]
    assert "notes.txt" not in state.attached_docs[0][1]

    assert _handler("/map off", ctx) is True
    assert state.attached_docs == []


def test_repl_map_bare_renders_engine_bytes(tree):
    from xlii.repl_cmds.map import _handler

    ctx, state = _ctx(tree)
    assert _handler("/map", ctx) is True
    assert "class Widget(Base)" in ctx["console"].text
    assert state.attached_docs == []          # bare render attaches nothing

    ctx2, _ = _ctx(tree)
    assert _handler("/map ../nope", ctx2) is True   # error surfaced, not raised
    assert "escapes" in ctx2["console"].text

    ctx3, _ = _ctx(tree)
    assert _handler("/map no/such/dir", ctx3) is True
    assert "no such path" in ctx3["console"].text


# --------------------------------------------------------------------------- #
#  map:// provider — read-only, computed, round-tripping addresses
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _no_session(monkeypatch):
    from xlii import active_session

    monkeypatch.setattr(active_session, "_ACTIVE", None)


def _session_at(monkeypatch, cwd):
    from xlii import active_session

    monkeypatch.setattr(
        active_session, "_ACTIVE",
        SimpleNamespace(shell_cwd=Path(cwd),
                        project=SimpleNamespace(project_root=Path(cwd), xli_dir=Path(cwd) / ".xlii")),
    )


def test_map_provider_resolve_list_read_round_trip(tree, monkeypatch):
    _session_at(monkeypatch, tree)
    from xlii.addressing import resolve, vfs_list, vfs_read

    assert resolve("map://").ok
    root_nodes = vfs_list("map://")
    names = {n.name for n in root_nodes}
    assert {"pkg", "notes.txt", "broken.py"} <= names
    pkg = next(n for n in root_nodes if n.name == "pkg")
    assert pkg.kind == "container" and pkg.address == "map://pkg"

    # every emitted address is a real map:// address that round-trips
    for n in root_nodes:
        assert n.address.startswith("map://")
        assert resolve(n.address).ok
    sub_nodes = vfs_list("map://pkg")
    assert {n.address for n in sub_nodes} == {"map://pkg/__init__.py", "map://pkg/mod.py",
                                              "map://pkg/sub"}

    # a subtree's outline and a single file's outline — the same engine bytes
    assert vfs_read("map://pkg").decode() == build_map(tree, scope="pkg")
    assert "class Widget(Base)" in vfs_read("map://pkg/mod.py").decode()
    # the root container reads as the whole project outline
    assert vfs_read("map://").decode() == build_map(tree)


def test_map_provider_list_agrees_with_engine_view(git_tree, monkeypatch):
    # client-#1: the pane view = the engine view (one file source). A
    # gitignored file neither browses nor maps; a unicode name does both.
    _session_at(monkeypatch, git_tree)
    from xlii.addressing import vfs_list, vfs_read

    names = {n.name for n in vfs_list("map://")}
    assert "ignored-secret.txt" not in names
    assert "héllø wörld.py" in names and "pkg" in names
    assert "def greet(name)" in vfs_read("map://héllø wörld.py").decode()


def test_map_provider_misses_and_containment(tree, monkeypatch):
    _session_at(monkeypatch, tree)
    from xlii.addressing import resolve, vfs_list, vfs_read, vfs_stat

    assert resolve("map://no/such/path").ok is False
    with pytest.raises(FileNotFoundError):
        vfs_read("map://no/such/path")
    assert vfs_list("map://no/such/path") == []
    # traversal is a miss on EVERY verb — read/stat raise, never leak ValueError
    assert resolve("map://../outside").ok is False
    with pytest.raises(FileNotFoundError):
        vfs_read("map://../outside")
    with pytest.raises(FileNotFoundError):
        vfs_stat("map://../outside")
    assert vfs_list("map://../outside") == []


def test_map_provider_is_read_only():
    from xlii.addressing import supports_vfs, supports_write

    assert supports_vfs("map") is True
    assert supports_write("map") is False     # no write/delete/mkdir — computed view


def test_map_pane_back_navigation_has_parents(tree, monkeypatch):
    # map is a subpath scheme for ExplorerPane._parent — two+ segments deep must
    # not dead-end (git:// had to learn the same lesson).
    from xlii.addressing import Address
    from xlii.panes.explorer import _SUBPATH_SCHEMES, ExplorerPane

    assert "map" in _SUBPATH_SCHEMES
    parent = ExplorerPane()._parent(Address.parse("map://pkg/sub/deep.py"))
    assert str(parent) == "map://pkg/sub"


# --------------------------------------------------------------------------- #
#  Command surfaces are registered
# --------------------------------------------------------------------------- #


def test_command_surfaces_registered():
    from xlii.cli import build_parser
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    cmd = find_repl_command("/map", "code")
    assert cmd is not None and cmd.name == "map"
    sub = next(a for a in build_parser()._actions
               if a.__class__.__name__ == "_SubParsersAction")
    assert "map" in sub.choices


def test_cli_map_prints_engine_bytes(tree, capsys, monkeypatch):
    from xlii.cmds.map import cmd_map

    monkeypatch.chdir(tree)
    args = SimpleNamespace(path="pkg", depth=None, detail="symbols")
    assert cmd_map(args) == 0
    assert capsys.readouterr().out == build_map(tree, scope="pkg")

    bad = SimpleNamespace(path="../nowhere", depth=None, detail="symbols")
    assert cmd_map(bad) == 1
    assert "map:" in capsys.readouterr().err

    missing = SimpleNamespace(path="no/such/dir", depth=None, detail="symbols")
    assert cmd_map(missing) == 1
    assert "no such path" in capsys.readouterr().err
