"""Doctor engine — install + project health checks as a pure collector.

Kernel home of the engine that used to live in the CLI face ``cmds/diag.py``
(godzilla-mothra Stage 1, B3): the finding/report model, the safe-fix
whitelist, the fix executor, and ``collect_doctor_findings`` — every health
check with zero printing. The CLI face keeps the presenter; ``/howto fix``
and any future body consume the report directly. Offline-safe: network checks
only run with ``online=True``.
"""

from __future__ import annotations

import os
import queue
import re
import shlex
import stat
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from xlii import __version__
from xlii.client import Clients
from xlii.config import GLOBAL_CONFIG_FILE, GlobalConfig, ProjectConfig


@dataclass(frozen=True)
class DoctorFinding:
    """One doctor check result.

    Two fix channels so we keep explanatory prose *and* an exact runnable
    command instead of flattening one into the other:

    * ``fix`` — the human-facing hint printed under the finding (prose, may name
      the command in context).
    * ``fix_cmd`` — the *exact* config/CLI command a whitelist entry auto-applies
      via :func:`apply_doctor_fix` (never codebase edits). Empty for advisory
      findings.

    ``runnable_fix`` is the command to match/execute: ``fix_cmd`` when set, else
    ``fix`` (back-compat for legacy findings whose ``fix`` *was* the bare command).
    """

    severity: str  # "ok" | "warn" | "bad"
    message: str
    fix: str = ""
    fix_cmd: str = ""

    @property
    def runnable_fix(self) -> str:
        """The exact command for is_runnable_fix / apply_doctor_fix."""
        return self.fix_cmd or self.fix


@dataclass
class DoctorReport:
    """Structured doctor output for CLI exit codes and ``/howto fix``."""

    findings: list[DoctorFinding] = field(default_factory=list)
    problems: int = 0
    warn_count: int = 0

    @property
    def exit_code(self) -> int:
        return 1 if self.problems else 0

    def runnable(self) -> list[DoctorFinding]:
        """Findings whose command (``runnable_fix``) is on the safe-apply whitelist."""
        return [
            f for f in self.findings
            if f.severity in ("warn", "bad") and is_runnable_fix(f.runnable_fix)
        ]


# Config/CLI repairs only — never ``sudo``, never free-form prose, never
# codebase edits. Doctor emits these exact strings when a fix is auto-applicable.
_RE_CHMOD_600 = re.compile(r"^chmod 600 (\S+)$")
# One *or more* space-separated paths (so a fix that repairs N inert hooks
# actually touches all N), tolerating a trailing "…" display marker.
_RE_CHMOD_X = re.compile(r"^chmod \+x (\S+(?: \S+)*?)(?: …)?$")
_RE_XLII_CLI = re.compile(r"^xlii (keys migrate|sync(?: --dry-run)?)$")
_GITIGNORE_FIX = "echo '.xlii/' >> .gitignore"
_RENAME_XLIIGNORE = "rename to .xliiignore"
_IN_PROCESS_SYNC_TIMEOUT_S = 120


def is_runnable_fix(fix: str) -> bool:
    """True when ``fix`` is a config/CLI repair we will execute (after confirm)."""
    if not fix or not fix.strip():
        return False
    f = fix.strip()
    if f in (_GITIGNORE_FIX, _RENAME_XLIIGNORE):
        return True
    if _RE_CHMOD_600.match(f) or _RE_CHMOD_X.match(f):
        return True
    return bool(_RE_XLII_CLI.match(f))


def apply_doctor_fix(fix: str, *, cwd: Optional[Path] = None) -> str:
    """Apply one whitelisted doctor fix. Returns a short success summary.

    Raises ``ValueError`` when the fix is not on the whitelist (codebase edits,
    sudo, free-form advice). Raises on OS/subprocess failure.
    """
    if not is_runnable_fix(fix):
        raise ValueError(f"fix is not auto-applicable: {fix!r}")
    f = fix.strip()
    root = (cwd or Path(".")).resolve()

    m600 = _RE_CHMOD_600.match(f)
    if m600:
        path = Path(m600.group(1)).expanduser().resolve()
        os.chmod(path, 0o600)
        return f"chmod 600 {path}"

    mx = _RE_CHMOD_X.match(f)
    if mx:
        done: list[str] = []
        for raw in shlex.split(mx.group(1)):
            path = Path(raw).expanduser().resolve()
            mode = os.stat(path).st_mode
            os.chmod(path, mode | 0o111)
            done.append(str(path))
        return "chmod +x " + " ".join(done)

    if f == _GITIGNORE_FIX:
        gi = root / ".gitignore"
        line = ".xlii/\n"
        existing = gi.read_text() if gi.exists() else ""
        if ".xlii" not in existing:
            with gi.open("a") as fh:
                if existing and not existing.endswith("\n"):
                    fh.write("\n")
                fh.write(line)
        return f"appended .xlii/ to {gi}"

    if f == _RENAME_XLIIGNORE:
        legacy = root / ".xliignore"
        dest = root / ".xliiignore"
        if not legacy.exists():
            raise FileNotFoundError(str(legacy))
        if dest.exists():
            raise FileExistsError(str(dest))
        legacy.rename(dest)
        return f"renamed {legacy.name} → {dest.name}"

    mcli = _RE_XLII_CLI.match(f)
    if mcli:
        sub = mcli.group(1)
        if sub.startswith("sync"):
            # In-process: a body has no `xlii` binary on PATH. confirm_deletes
            # stays None — an auto-fix never approves remote destruction
            # (above-threshold deletes are skipped with a note, not executed).
            from xlii.sync import sync_project
            dry_run = sub.endswith("--dry-run")
            cfg = GlobalConfig.load()
            project = ProjectConfig.load(root)
            if project is None:
                raise RuntimeError(f"not an xlii project: {root}")
            # Daemon thread, not ThreadPoolExecutor: the executor's
            # non-daemon worker gets joined both by __exit__ and at
            # interpreter exit, so a hung sync would outlive the timeout.
            outcome: queue.Queue = queue.Queue(maxsize=1)

            def _run_sync() -> None:
                try:
                    outcome.put(("ok", sync_project(Clients.from_config(cfg), project, cfg, dry_run=dry_run)))
                except BaseException as e:  # noqa: BLE001 — relayed to the caller below
                    outcome.put(("err", e))

            threading.Thread(target=_run_sync, name="doctor-sync", daemon=True).start()
            try:
                kind, value = outcome.get(timeout=_IN_PROCESS_SYNC_TIMEOUT_S)
            except queue.Empty:
                raise RuntimeError(f"xlii sync timed out after {_IN_PROCESS_SYNC_TIMEOUT_S}s") from None
            if kind == "err":
                raise value
            stats = value
            return f"ran xlii {sub}: {stats.summary()}"
        # `xlii keys migrate` has no kernel home yet (B6 owns migrate_chat_keys);
        # keep the CLI re-entry for that one fix until it lands.
        argv = ["xlii", *shlex.split(sub)]
        proc = subprocess.run(argv, cwd=str(root), capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
            raise RuntimeError(f"{' '.join(argv)} failed: {err}")
        return f"ran {' '.join(argv)}"

    raise ValueError(f"fix is not auto-applicable: {fix!r}")


def collect_doctor_findings(
    project_root: Optional[Path] = None,
    *,
    online: bool = False,
    migrate_legacy: bool = False,
    dry_run: bool = False,
    config_file: Optional[Path] = None,
    emit: Optional[Callable[[str, object], None]] = None,
) -> DoctorReport:
    """Check install + project health; return structured findings. Prints nothing.

    The pure engine behind ``xlii doctor`` / ``/howto fix``. ``project_root``
    defaults to the CWD (CLI behavior — bodies pass their own root);
    ``config_file`` defaults to the standard global config path.

    ``emit`` (optional) receives render events for a face to present, in check
    order: ``("header", str)`` section titles, ``("note", str)`` preformatted
    lines, and ``("finding", DoctorFinding)`` as each check lands. With
    ``emit=None`` the collector is pure — the report carries everything.
    """
    report = DoctorReport()
    cfg_file = config_file or GLOBAL_CONFIG_FILE

    def _emit(kind: str, payload: object) -> None:
        if emit is not None:
            emit(kind, payload)

    def ok(msg: str) -> None:
        finding = DoctorFinding("ok", msg)
        report.findings.append(finding)
        _emit("finding", finding)

    def warn(msg: str, fix: str = "", fix_cmd: str = "") -> None:
        report.warn_count += 1
        finding = DoctorFinding("warn", msg, fix, fix_cmd)
        report.findings.append(finding)
        _emit("finding", finding)

    def bad(msg: str, fix: str = "", fix_cmd: str = "") -> None:
        report.problems += 1
        finding = DoctorFinding("bad", msg, fix, fix_cmd)
        report.findings.append(finding)
        _emit("finding", finding)

    if migrate_legacy:
        from xlii.legacy_migrate import migrate_all
        _emit("header", "legacy migration")
        lines = migrate_all(dry_run=dry_run)
        if not lines:
            _emit("note", "  [dim](nothing to migrate)[/dim]")
        else:
            for ln in lines:
                _emit("note", f"  [green]✓[/green] {ln}")
        if dry_run:
            _emit("note", "[dim]dry run — re-run without --dry-run to apply[/dim]")

    _emit("header", "install")
    if not cfg_file.exists():
        bad("no config.json", "run `xlii config` then add your key")
    else:
        mode = stat.S_IMODE(os.stat(cfg_file).st_mode)
        if mode != 0o600:
            bad(f"config.json perms are {oct(mode)}",
                f"tighten to owner-only read/write: chmod 600 {cfg_file}",
                f"chmod 600 {cfg_file}")
        else:
            ok("config.json present, perms 0600")
        if GlobalConfig.mgmt_key_in_file():
            bad("management key is persisted in config.json (env-only invariant)",
                "remove it from the file; export XAI_MANAGEMENT_API_KEY instead")
        cfg = GlobalConfig.load()
        try:
            pairs = cfg.key_pairs()
        except RuntimeError as e:
            pairs = None
            bad(f"chat keys unreadable: {e}", "check the vault (`xlii auth list`) or restore keys[]")
        if pairs is None:
            pass
        elif not pairs:
            bad("no API keys configured", "add keys[] to config.json or run `xlii setup`")
        else:
            ok(f"{len(pairs)} API key(s) configured")
            plain = cfg.plaintext_key_count()
            if plain:
                warn(f"{plain} chat key(s) stored in plaintext in config.json",
                     "move them into the encrypted vault: xlii keys migrate",
                     "xlii keys migrate")
            else:
                ok("chat keys encrypted at rest (vault-backed)")

    # Stale-shim check: the xlii on PATH may import a different tree.
    import shutil as _shutil
    shim = _shutil.which("xlii")
    if shim and Path(shim).resolve() != Path(sys.argv[0]).resolve():
        try:
            out = subprocess.run([shim, "--version"], capture_output=True, text=True, timeout=10)
            shim_ver = (out.stdout or "").strip().split()[-1]
            if shim_ver != __version__:
                bad(f"`xlii` on PATH ({shim}) is version {shim_ver}, this install is {__version__}",
                    f"sudo ln -sf {Path(sys.argv[0]).resolve()} {shim}")
            else:
                ok(f"PATH xlii matches ({shim_ver})")
        except Exception:
            warn(f"could not check version of {shim}")

    # Vault: resolvable without creating anything.
    try:
        from xlii.vault import (
            ENV_VAR,
            ENV_VAR_LEGACY,
            KEYRING_SERVICE,
            master_key_backend,
        )
        key, backend = master_key_backend()
        ok(f"vault master key via {backend}" if key else "no vault yet (created on first `xlii auth set`)")
        # Grades Phase 2: surface intentional legacy aliases so operators know
        # they are contracted, not bugs (see docs/LEGACY.md).
        ok(
            f"legacy aliases OK — vault env ${ENV_VAR} (alias ${ENV_VAR_LEGACY}); "
            f"keyring service {KEYRING_SERVICE!r}; "
            f"collection/key prefixes xli/* and xli- still accepted"
        )
    except Exception as e:
        warn(f"vault check failed: {e}")

    # Gigwork providers (proposals/gigwork.md): list what's configured and
    # whether each key env is set — the value, never the secret.
    if cfg_file.exists():
        try:
            from xlii.chat_backend import GigError, gig_allowlist, gig_providers
            gig_cfg = GlobalConfig.load()
            providers = gig_providers(gig_cfg)
            if providers:
                allow = set(gig_allowlist(gig_cfg))
                for p in sorted(providers.values(), key=lambda p: p.name):
                    hire = "agent-hireable" if p.name in allow else "slash-only"
                    if p.key_optional:
                        where = "local endpoint" if p.is_local else "no key needed"
                        ok(f"gigwork[{p.name}] {p.model} · {where} · {hire}")
                    elif p.key_set:
                        ok(f"gigwork[{p.name}] {p.model} · ${p.api_key_env} set · {hire}")
                    else:
                        warn(
                            f"gigwork[{p.name}] configured but ${p.api_key_env} is unset",
                            f"export {p.api_key_env}=… (key comes from the environment only)",
                        )
        except GigError as e:
            bad(f"gigwork config invalid: {e}")
        except Exception as e:
            warn(f"gigwork check failed: {e}")

    root = Path(project_root if project_root is not None else ".").resolve()
    project = ProjectConfig.load(root)
    if project is None:
        _emit("note", "[dim](not inside an xlii project — project checks skipped)[/dim]")
    else:
        _emit("header", f"project: {project.name}")
        legacy = project.project_root / ".xliignore"
        if legacy.exists():
            warn("legacy .xliignore name",
                 "rename it to the current .xliiignore spelling",
                 "rename to .xliiignore")
        gi = project.project_root / ".gitignore"
        if gi.exists() and ".xlii" not in gi.read_text():
            warn(".xlii/ not in .gitignore (private state could be committed)",
                 "add it so private state stays uncommitted: echo '.xlii/' >> .gitignore",
                 "echo '.xlii/' >> .gitignore")
        else:
            ok(".xlii/ ignored by git")
        from xlii.storage import LocalIndex
        if LocalIndex(project).exists():
            ok("local search index present (offline search works)")
        else:
            warn("no local search index yet",
                 "build it so offline search works: xlii sync",
                 "xlii sync")
        # Orphaned manifest entries: remembered remotely, gone locally.
        try:
            from xlii.manifest import Manifest
            manifest = Manifest.load(project.manifest_path)
            orphans = [rel for rel in manifest.entries if not (project.project_root / rel).exists()]
            if orphans:
                warn(f"{len(orphans)} manifest entr(ies) have no local file (will delete remotely on next sync)",
                     "preview the remote deletions first: xlii sync --dry-run",
                     "xlii sync --dry-run")
            else:
                ok("manifest matches the local tree")
        except Exception as e:
            warn(f"manifest unreadable: {e}", "delete .xlii/manifest.json and re-sync")
        # Hooks hygiene: files that look like hooks but aren't executable.
        from xlii.hooks import inert_hooks
        inert = inert_hooks(project.xli_dir)
        if inert:
            # Repair ALL inert hooks, not just the first — the count in the
            # warning promises every one is fixed. (Paths with spaces would
            # break the \S+ whitelist; such a fix simply prints as advice.)
            listed = " ".join(str(p) for p in inert)
            warn(f"{len(inert)} hook file(s) not executable (silently skipped)",
                 f"make them executable: chmod +x {listed}",
                 f"chmod +x {listed}")

        if online and not project.local_only:
            try:
                from xlii.sync import fetch_collection_state
                clients = Clients.from_config(GlobalConfig.load())
                state = fetch_collection_state(clients, project.collection_id)
                ok(f"collection reachable ({len(state)} docs)")
            except Exception as e:
                bad(f"collection unreachable: {type(e).__name__}: {e}",
                    "check key expiry (`xlii keys list`) / network; REPL still works degraded")
        elif not project.local_only:
            _emit("note", "[dim](network checks skipped — pass --online to test the Collection)[/dim]")

        cfg = GlobalConfig.load()
        judges = cfg.effective_judges()
        if not any(
            isinstance(j, dict) and j.get("kind") == "cross_vendor" for j in judges.values()
        ):
            warn(
                "no cross_vendor judge profile configured",
                'add e.g. "judges": {"anthropic": {"kind": "cross_vendor", '
                '"provider": "anthropic", "model": "claude-sonnet-4-6", '
                '"api_key_env": "ANTHROPIC_API_KEY"}} for /loop fresh-eyes checks',
            )
        else:
            ok("cross_vendor judge profile configured")

    return report
