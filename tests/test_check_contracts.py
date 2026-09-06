"""Tests for scripts/check_contracts.py — the Stage-0 convergence ratchets
(godzilla-mothra V0a).

Each ratchet is proven in both directions against synthetic trees: green when
only baselined/allowed violations exist, red (with the ``new violation vs
baseline: <file>`` message) on a synthetic NEW violation — so the synthetic
never touches the real tree. The real tree itself is checked once, pinning
the frozen baselines as exact.

The import contract proper is import-linter's job; here we prove the
lint-imports mechanics red/green on a synthetic package (skipped when the
tool isn't installed — CI always has it via the dev extras).
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_contracts.py"

_spec = importlib.util.spec_from_file_location("check_contracts", SCRIPT)
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #

def make_tree(tmp_path, files, baseline=None, source_modules=None, pyproject=None):
    """Build a synthetic repo root: xlii/ files, a baseline JSON, and a
    minimal pyproject import contract (source_modules auto-derived unless
    given). Returns (root, baseline_path)."""
    root = tmp_path / "repo"
    (root / "xlii").mkdir(parents=True, exist_ok=True)
    (root / "xlii" / "__init__.py").touch()
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    base = {"path_home": [], "isatty": [], "keyring": []}
    base.update(baseline or {})
    baseline_path = root / "baseline.json"
    baseline_path.write_text(json.dumps(base), encoding="utf-8")

    if pyproject is None:
        if source_modules is None:
            source_modules = sorted(cc.kernel_toplevel_modules(root))
        mods = ", ".join(f'"{m}"' for m in source_modules)
        pyproject = (
            '[tool.importlinter]\nroot_packages = ["xlii"]\n\n'
            "[[tool.importlinter.contracts]]\n"
            'name = "faces import kernel, never the reverse"\n'
            'type = "forbidden"\n'
            'forbidden_modules = ["xlii.tui", "xlii.panes", "xlii.tui_textual"]\n'
            f"source_modules = [{mods}]\n"
            "ignore_imports = []\n"
        )
    (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    return root, baseline_path


def run(root, baseline_path, capsys):
    rc = cc.main(["--root", str(root), "--baseline", str(baseline_path)])
    captured = capsys.readouterr()
    return rc, captured.out + captured.err


# --------------------------------------------------------------------------- #
# the real tree: frozen baselines are exact, today's tree is green
# --------------------------------------------------------------------------- #

def test_real_tree_is_green(capsys):
    assert cc.main([]) == 0


# --------------------------------------------------------------------------- #
# path_home ratchet
# --------------------------------------------------------------------------- #

def test_new_path_home_goes_red_with_baseline_message(tmp_path, capsys):
    root, base = make_tree(
        tmp_path, {"xlii/evil.py": "from pathlib import Path\nX = Path.home() / 'x'\n"}
    )
    rc, out = run(root, base, capsys)
    assert rc == 1
    assert "new violation vs baseline: xlii/evil.py" in out
    assert "[path_home]" in out


def test_baselined_path_home_stays_green(tmp_path, capsys):
    root, base = make_tree(
        tmp_path,
        {"xlii/old.py": "from pathlib import Path\nX = Path.home()\n"},
        baseline={"path_home": ["xlii/old.py"]},
    )
    assert run(root, base, capsys)[0] == 0


def test_designated_path_module_is_exempt(tmp_path, capsys):
    root, base = make_tree(
        tmp_path,
        {"xlii/project_paths.py": "from pathlib import Path\nHOME = Path.home()\n"},
    )
    assert run(root, base, capsys)[0] == 0


def test_qualified_pathlib_call_is_caught(tmp_path, capsys):
    root, base = make_tree(
        tmp_path, {"xlii/evil.py": "import pathlib\nX = pathlib.Path.home()\n"}
    )
    assert run(root, base, capsys)[0] == 1


def test_comment_and_docstring_mentions_do_not_count(tmp_path, capsys):
    root, base = make_tree(
        tmp_path,
        {
            "xlii/prose.py": (
                '"""Never call Path.home() or isatty here."""\n'
                "# Path.home() and isatty are banned; import keyring is too\n"
                "X = 1\n"
            )
        },
    )
    assert run(root, base, capsys)[0] == 0


# --------------------------------------------------------------------------- #
# isatty ratchet
# --------------------------------------------------------------------------- #

def test_new_isatty_goes_red(tmp_path, capsys):
    root, base = make_tree(
        tmp_path, {"xlii/core_logic.py": "import sys\nTTY = sys.stdout.isatty()\n"}
    )
    rc, out = run(root, base, capsys)
    assert rc == 1
    assert "new violation vs baseline: xlii/core_logic.py" in out
    assert "[isatty]" in out


def test_isatty_in_face_and_terminal_modules_is_allowed(tmp_path, capsys):
    root, base = make_tree(
        tmp_path,
        {
            "xlii/tui/__init__.py": "",
            "xlii/tui/chrome.py": "import sys\nTTY = sys.stdout.isatty()\n",
            "xlii/terminal_image.py": "import sys\nTTY = sys.stdout.isatty()\n",
        },
    )
    assert run(root, base, capsys)[0] == 0


# --------------------------------------------------------------------------- #
# keyring ratchet
# --------------------------------------------------------------------------- #

def test_new_keyring_import_goes_red(tmp_path, capsys):
    root, base = make_tree(tmp_path, {"xlii/creds.py": "import keyring\n"})
    rc, out = run(root, base, capsys)
    assert rc == 1
    assert "new violation vs baseline: xlii/creds.py" in out
    assert "[keyring]" in out


def test_keyring_from_import_goes_red(tmp_path, capsys):
    root, base = make_tree(
        tmp_path, {"xlii/creds.py": "from keyring.errors import KeyringError\n"}
    )
    assert run(root, base, capsys)[0] == 1


def test_keyring_in_vault_is_allowed(tmp_path, capsys):
    root, base = make_tree(tmp_path, {"xlii/vault.py": "import keyring\n"})
    assert run(root, base, capsys)[0] == 0


# --------------------------------------------------------------------------- #
# the ratchet only tightens: stale entries fail too
# --------------------------------------------------------------------------- #

def test_stale_baseline_entry_goes_red(tmp_path, capsys):
    root, base = make_tree(
        tmp_path,
        {"xlii/clean.py": "X = 1\n"},
        baseline={"isatty": ["xlii/clean.py"]},
    )
    rc, out = run(root, base, capsys)
    assert rc == 1
    assert "stale baseline entry: xlii/clean.py" in out


def test_unknown_baseline_key_goes_red(tmp_path, capsys):
    root, base = make_tree(tmp_path, {})
    base.write_text(json.dumps({"path_home": [], "isatty": [], "keyring": [], "typo": []}))
    assert run(root, base, capsys)[0] == 1


# --------------------------------------------------------------------------- #
# import-contract completeness (new kernel modules cannot dodge lint-imports)
# --------------------------------------------------------------------------- #

def test_new_kernel_module_missing_from_source_modules_goes_red(tmp_path, capsys):
    root, base = make_tree(
        tmp_path, {"xlii/newborn.py": "X = 1\n"}, source_modules=[]
    )
    rc, out = run(root, base, capsys)
    assert rc == 1
    assert "new violation vs baseline: xlii/newborn.py" in out
    assert "[import_contract]" in out


def test_stale_source_modules_entry_goes_red(tmp_path, capsys):
    root, base = make_tree(tmp_path, {}, source_modules=["xlii.ghost"])
    rc, out = run(root, base, capsys)
    assert rc == 1
    assert "stale source_modules entry: xlii.ghost" in out


def test_py_file_in_namespace_dir_goes_red(tmp_path, capsys):
    # PEP 420 makes xlii/help_data/mod.py importable, but grimp never analyzes
    # it — the completeness check must flag the escape hatch.
    root, base = make_tree(tmp_path, {"xlii/help_data/mod.py": "X = 1\n"})
    rc, out = run(root, base, capsys)
    assert rc == 1
    assert "new violation vs baseline: xlii/help_data/mod.py" in out
    assert "namespace dir" in out


def test_py_file_under_nested_namespace_dir_goes_red(tmp_path, capsys):
    # The chain check is recursive: cmds/ is a real package here, but the
    # new subdir below it lacks __init__.py.
    root, base = make_tree(
        tmp_path,
        {
            "xlii/cmds/__init__.py": "",
            "xlii/cmds/newdir/mod.py": "X = 1\n",
        },
    )
    rc, out = run(root, base, capsys)
    assert rc == 1
    assert "new violation vs baseline: xlii/cmds/newdir/mod.py" in out


def test_root_init_importing_a_face_goes_red(tmp_path, capsys):
    root, base = make_tree(tmp_path, {})
    (root / "xlii" / "__init__.py").write_text("from xlii.tui import console\n")
    rc, out = run(root, base, capsys)
    assert rc == 1
    assert "new violation vs baseline: xlii/__init__.py" in out


def test_missing_import_contract_goes_red(tmp_path, capsys):
    root, base = make_tree(tmp_path, {}, pyproject="[tool.other]\nx = 1\n")
    rc, out = run(root, base, capsys)
    assert rc == 1
    assert "import contract missing" in out


# --------------------------------------------------------------------------- #
# lint-imports mechanics, proven on a synthetic package (never the real tree)
# --------------------------------------------------------------------------- #

LINT_IMPORTS = shutil.which("lint-imports")


@pytest.mark.skipif(LINT_IMPORTS is None, reason="import-linter not installed (CI has it)")
def test_lint_imports_goes_red_on_new_kernel_to_face_import_and_green_when_baselined(tmp_path):
    root = tmp_path / "synth"
    (root / "xlii" / "tui").mkdir(parents=True)
    (root / "xlii" / "__init__.py").touch()
    (root / "xlii" / "tui" / "__init__.py").write_text("console = object()\n")
    (root / "xlii" / "kernel_mod.py").write_text("from xlii.tui import console\n")

    contract = (
        '[tool.importlinter]\nroot_packages = ["xlii"]\n\n'
        "[[tool.importlinter.contracts]]\n"
        'name = "faces import kernel, never the reverse"\n'
        'type = "forbidden"\n'
        'source_modules = ["xlii.kernel_mod"]\n'
        'forbidden_modules = ["xlii.tui"]\n'
        "ignore_imports = [{edges}]\n"
    )
    env = {**os.environ, "PYTHONPATH": str(root)}

    (root / "pyproject.toml").write_text(contract.format(edges=""))
    red = subprocess.run(
        [LINT_IMPORTS], cwd=root, env=env, capture_output=True, text=True, timeout=60
    )
    assert red.returncode != 0
    assert "xlii.kernel_mod" in red.stdout

    (root / "pyproject.toml").write_text(
        contract.format(edges='"xlii.kernel_mod -> xlii.tui"')
    )
    green = subprocess.run(
        [LINT_IMPORTS], cwd=root, env=env, capture_output=True, text=True, timeout=60
    )
    assert green.returncode == 0, green.stdout + green.stderr
