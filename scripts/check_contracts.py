#!/usr/bin/env python3
"""Ban-grep ratchets for the brain/face convergence (godzilla-mothra V0a).

Four machine-checked contracts, frozen against today's tree:

1. ``Path.home()`` is allowed only in the designated path module
   (``xlii/project_paths.py`` — the shared path-resolution seam). Every
   current offender is frozen in the baseline; new ones fail CI.
2. ``isatty`` is allowed only in face/terminal modules (``xlii/tui/``,
   ``xlii/panes/``, ``xlii/tui_textual.py``, ``xlii/terminal_image.py``,
   ``xlii/console_prompt.py``). Core logic probing the terminal is
   brain-matter-in-faces — frozen baseline, no new entries.
3. ``import keyring`` is allowed only in ``xlii/vault.py``, whose key
   chain is verified env var -> OS keyring -> key file (each layer falls
   through on failure). Anywhere else, keyring would be assumed present
   without that fallback — headless bodies break.
4. The pyproject import-linter contract stays complete: every top-level
   kernel module under ``xlii/`` must appear in ``source_modules`` (so a
   NEW kernel module cannot dodge the faces->kernel import contract), no
   stale entries, and ``xlii/__init__.py`` — uncoverable by the contract
   without dragging in its descendants — must not import faces. Every
   ``.py`` file must also sit under an unbroken ``__init__.py`` chain:
   a module in a PEP 420 namespace dir (e.g. dropped into a data dir
   like ``xlii/help/``) is runtime-importable yet invisible to grimp,
   so lint-imports could never analyze it — flagged here instead.

Baselines live in ``scripts/contract_baselines.json`` (exact paths, never
wildcards). The ratchet: no new entries ever; Stage 1 burns them down.
Failure output always includes ``new violation vs baseline: <file>``.

Detection is token/AST-based, not regex-over-text, so mentions in comments
and docstrings do not count — only code does.

Usage: ``python scripts/check_contracts.py [--root DIR] [--baseline FILE]``
(defaults: the repo this script lives in, and the JSON next to it).
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import sys
import tokenize
import tomllib
from pathlib import Path

FACES = ("xlii.tui", "xlii.panes", "xlii.tui_textual")
FACE_TOPLEVEL = {"tui", "panes", "tui_textual"}

PATH_HOME_DESIGNATED = {"xlii/project_paths.py"}
ISATTY_ALLOWED_PREFIXES = ("xlii/tui/", "xlii/panes/")
ISATTY_ALLOWED_FILES = {
    "xlii/tui_textual.py",
    "xlii/terminal_image.py",
    "xlii/console_prompt.py",
}
KEYRING_ALLOWED_FILES = {"xlii/vault.py"}

BASELINE_KEYS = ("path_home", "isatty", "keyring")

HINTS = {
    "path_home": (
        "Path.home() outside the designated path module "
        "(xlii/project_paths.py) — derive the path from xlii.config's "
        "config-dir seam or route it through the path module"
    ),
    "isatty": (
        "isatty outside face/terminal modules — core logic must not probe "
        "the terminal; take the answer from the face via a parameter"
    ),
    "keyring": (
        "keyring imported outside xlii/vault.py — secrets go through "
        "xlii.vault, whose chain falls back env var -> keyring -> key file"
    ),
    "import_contract": (
        "kernel module not covered by the [tool.importlinter] contract — "
        "add it to source_modules so lint-imports guards it"
    ),
}


def _iter_py(root: Path):
    pkg = root / "xlii"
    for path in sorted(pkg.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _code_name_op_tokens(text: str, filename: str):
    """NAME/OP tokens only — comments, strings, and layout tokens dropped,
    so prose mentions can't false-positive."""
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in (tokenize.NAME, tokenize.OP):
                out.append(tok)
    except tokenize.TokenError as exc:  # pragma: no cover - unparseable file
        raise SystemExit(f"check_contracts: cannot tokenize {filename}: {exc}")
    return out


def scan_path_home(text: str, filename: str) -> list[int]:
    """Lines calling ``Path.home(`` (also matches ``pathlib.Path.home(``)."""
    toks = _code_name_op_tokens(text, filename)
    hits = []
    for i in range(len(toks) - 3):
        if (
            toks[i].type == tokenize.NAME
            and toks[i].string == "Path"
            and toks[i + 1].string == "."
            and toks[i + 2].string == "home"
            and toks[i + 3].string == "("
        ):
            hits.append(toks[i].start[0])
    return hits


def scan_isatty(text: str, filename: str) -> list[int]:
    """Lines with an ``isatty`` name token (attribute or bare)."""
    return [
        tok.start[0]
        for tok in _code_name_op_tokens(text, filename)
        if tok.type == tokenize.NAME and tok.string == "isatty"
    ]


def scan_keyring(text: str, filename: str) -> list[int]:
    """Lines importing the ``keyring`` module (``import`` or ``from``)."""
    tree = ast.parse(text, filename=filename)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "keyring" or a.name.startswith("keyring.") for a in node.names):
                hits.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "keyring" or node.module.startswith("keyring."):
                hits.append(node.lineno)
    return hits


def _face_imports(text: str, filename: str, importer_pkg: str = "xlii") -> list[int]:
    """Lines where *filename* imports a face module (absolute or relative)."""
    tree = ast.parse(text, filename=filename)
    hits = []

    def face(mod: str) -> bool:
        return any(mod == f or mod.startswith(f + ".") for f in FACES)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(face(a.name) for a in node.names):
                hits.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                mod = f"{importer_pkg}.{mod}" if mod else importer_pkg
            if face(mod) or any(face(f"{mod}.{a.name}") for a in node.names):
                hits.append(node.lineno)
    return hits


def kernel_toplevel_modules(root: Path) -> set[str]:
    """Every top-level module/package under xlii/ except the faces —
    what the import-linter contract's source_modules must list."""
    pkg = root / "xlii"
    mods = set()
    for p in sorted(pkg.iterdir()):
        if p.name == "__pycache__" or p.stem in FACE_TOPLEVEL:
            continue
        if p.is_dir() and (p / "__init__.py").is_file():
            mods.add(f"xlii.{p.name}")
        elif p.suffix == ".py" and p.stem != "__init__":
            mods.add(f"xlii.{p.stem}")
    return mods


def check_ratchet(name, violations, baseline, problems):
    """Apply the ratchet: new violation -> fail; stale baseline entry -> fail."""
    baseline_set = set(baseline)
    for path in sorted(set(violations) - baseline_set):
        lines = ",".join(str(n) for n in violations[path])
        problems.append(
            f"new violation vs baseline: {path}:{lines} [{name}] {HINTS[name]}"
        )
    for path in sorted(baseline_set - set(violations)):
        problems.append(
            f"stale baseline entry: {path} [{name}] no longer violates — "
            f"delete it from scripts/contract_baselines.json (the ratchet only tightens)"
        )


def namespace_orphans(root: Path) -> list[str]:
    """``.py`` files whose ancestor chain up to ``xlii/`` is missing an
    ``__init__.py`` somewhere — PEP 420 makes them importable, but grimp
    (and therefore lint-imports) never analyzes them, so they would dodge
    the import contract silently."""
    pkg = root / "xlii"
    orphans = []
    for py in _iter_py(root):
        d = py.parent
        while d != pkg.parent:
            if not (d / "__init__.py").is_file():
                orphans.append(_rel(py, root))
                break
            d = d.parent
    return orphans


def check_import_contract(root: Path, problems: list[str]) -> None:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        problems.append("import contract missing: no pyproject.toml at root")
        return
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    contracts = data.get("tool", {}).get("importlinter", {}).get("contracts", [])
    contract = next(
        (
            c
            for c in contracts
            if c.get("type") == "forbidden" and "xlii.tui" in c.get("forbidden_modules", [])
        ),
        None,
    )
    if contract is None:
        problems.append(
            "import contract missing: no [tool.importlinter] forbidden contract "
            "for xlii.tui in pyproject.toml"
        )
        return

    listed = set(contract.get("source_modules", []))
    expected = kernel_toplevel_modules(root)
    for mod in sorted(expected - listed):
        rel = "xlii/" + mod.removeprefix("xlii.")
        rel = rel + ("/" if (root / rel).is_dir() else ".py")
        problems.append(
            f"new violation vs baseline: {rel} [import_contract] {HINTS['import_contract']}"
        )
    for mod in sorted(listed - expected):
        problems.append(
            f"stale source_modules entry: {mod} [import_contract] no such module "
            f"under xlii/ — delete it from the pyproject import-linter contract"
        )

    for rel in namespace_orphans(root):
        problems.append(
            f"new violation vs baseline: {rel} [import_contract] .py file inside "
            f"a namespace dir (missing __init__.py on the path to xlii/) — grimp "
            f"cannot analyze it, so lint-imports would never see its imports; add "
            f"__init__.py and list the package in source_modules"
        )

    init = root / "xlii" / "__init__.py"
    if init.is_file():
        for line in _face_imports(init.read_text(encoding="utf-8"), str(init)):
            problems.append(
                f"new violation vs baseline: xlii/__init__.py:{line} [import_contract] "
                f"the xlii root package must not import faces (it sits outside "
                f"source_modules, so lint-imports cannot guard it)"
            )


def main(argv: list[str] | None = None) -> int:
    script_dir = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=script_dir.parent, help="repo root to scan")
    ap.add_argument(
        "--baseline",
        type=Path,
        default=script_dir / "contract_baselines.json",
        help="frozen baseline JSON",
    )
    args = ap.parse_args(argv)
    root = args.root.resolve()

    baselines = json.loads(args.baseline.read_text(encoding="utf-8"))
    unknown = set(baselines) - set(BASELINE_KEYS) - {k for k in baselines if k.startswith("_")}
    if unknown:
        print(f"check_contracts: unknown baseline keys: {sorted(unknown)}", file=sys.stderr)
        return 1

    scans = {"path_home": scan_path_home, "isatty": scan_isatty, "keyring": scan_keyring}
    allowed = {
        "path_home": lambda rel: rel in PATH_HOME_DESIGNATED,
        "isatty": lambda rel: rel.startswith(ISATTY_ALLOWED_PREFIXES) or rel in ISATTY_ALLOWED_FILES,
        "keyring": lambda rel: rel in KEYRING_ALLOWED_FILES,
    }

    violations: dict[str, dict[str, list[int]]] = {k: {} for k in BASELINE_KEYS}
    for path in _iter_py(root):
        rel = _rel(path, root)
        text = path.read_text(encoding="utf-8")
        for name, scan in scans.items():
            if allowed[name](rel):
                continue
            lines = scan(text, rel)
            if lines:
                violations[name][rel] = lines

    problems: list[str] = []
    for name in BASELINE_KEYS:
        check_ratchet(name, violations[name], baselines.get(name, []), problems)
    check_import_contract(root, problems)

    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        print(f"check_contracts: FAIL ({len(problems)} problem(s))", file=sys.stderr)
        return 1

    for name in BASELINE_KEYS:
        print(f"OK {name}: {len(baselines.get(name, []))} baselined, 0 new")
    print(
        "OK import_contract: source_modules complete, no namespace-dir escapes, "
        "root __init__ face-free"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
