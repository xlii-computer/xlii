"""Human-only destroy ladder — L0 dry-run inventory, L1 manifest-bound wipe.

Wave 1: this body only. The only function that mutates is ``run_destroy``.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from xlii.config import JOURNAL_COLLECTION_PREFIX, global_config_dir
from xlii.project_paths import user_home, xlii_user_root
from xlii.human_gate import (
    HumanGateError,
    confirm_typed_phrase,
    require_human,
    verify_fresh_admin,
)
from xlii.minted import Manifest, load as load_minted
from xlii.registry import REGISTRY_FILE, Registry
from xlii.vault import KEY_FILE, VAULT_FILE

PHRASE_L1 = "DESTROY THIS INSTALL"
AIMS = ("all", "throne", "node")

_MGMT_ENV = "XAI_MANAGEMENT_API_KEY"


def _state_root() -> Path:
    """``~/.xlii`` at call time (honors test HOME). Never freeze at import."""
    return xlii_user_root()


@dataclass
class DestroyReport:
    aim: str
    level: int
    dry_run: bool
    local_only: bool
    planned: list[dict]
    done: list[dict]
    failed: list[dict]
    residuals: list[str]
    journal_path: Path | None = None


def destroy_journal_path() -> Path:
    override = os.environ.get("XLII_DESTROY_JOURNAL")
    if override:
        return Path(override)
    return user_home() / ".local/share/xlii/destroy-journal.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_journal(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def _is_provisioned_collection(name: str) -> bool:
    return name.startswith(("xlii/", "xli/", JOURNAL_COLLECTION_PREFIX))


def _list_tree_paths(root: Path) -> list[str]:
    if not root.exists():
        return []
    out: list[str] = []
    try:
        for p in root.rglob("*"):
            if p.is_symlink():
                continue
            out.append(str(p))
    except OSError:
        out.append(str(root))
    if root.is_dir() and not root.is_symlink():
        out.insert(0, str(root))
    return sorted(set(out))


def _node_on_paper(target: str, manifest: Manifest) -> bool:
    if not target:
        return False
    reg = Registry.load()
    if any((e.node or "").strip() == target for e in reg.entries):
        return True
    return any(
        (getattr(c, "team_id", "") or "").strip() and target in (getattr(c, "name", "") or "")
        for c in manifest.collections
    )


def _gate_level1(console: Any) -> Optional[str]:
    if console is None:
        return "level >= 1 requires a foreground console"
    try:
        require_human({"console": console})
    except HumanGateError as exc:
        return str(exc)
    if not verify_fresh_admin(console):
        return "fresh admin proof failed or admin secret not set"
    if not confirm_typed_phrase(console, PHRASE_L1):
        return f"typed phrase mismatch (expected {PHRASE_L1!r})"
    return None


def _plan_local_paths() -> list[dict]:
    planned: list[dict] = []
    for root in (_state_root(), global_config_dir()):
        for path in _list_tree_paths(root):
            planned.append({"kind": "path", "path": path, "action": "delete"})
    reg = Registry.load()
    for entry in reg.entries:
        planned.append(
            {
                "kind": "project",
                "path": entry.path,
                "collection_id": entry.collection_id,
                "name": entry.name,
                "action": "remove",
            }
        )
    return planned


def _plan_manifest(manifest: Manifest) -> list[dict]:
    planned: list[dict] = []
    for key in manifest.keys:
        planned.append(
            {
                "kind": "key",
                "id": key.key_id,
                "team_id": key.team_id,
                "label": key.label,
                "action": "revoke",
            }
        )
    for coll in manifest.collections:
        planned.append(
            {
                "kind": "collection",
                "id": coll.collection_id,
                "team_id": coll.team_id,
                "name": coll.name,
                "kind_hint": coll.kind,
                "action": "delete",
            }
        )
    return planned


def _prefix_residuals(
    manifest: Manifest,
    mgmt_key: str,
    team_id: str,
    *,
    clients: Any = None,
) -> list[str]:
    """Prefix-matching cloud ids not in minted → honest residuals."""
    from xlii.bootstrap import extract_api_key_id, is_provisioned_name, list_api_keys
    from xlii.storage_backend import CollectionsBackend

    minted_key_ids = {k.key_id for k in manifest.keys}
    minted_coll_ids = {c.collection_id for c in manifest.collections}
    residuals: list[str] = []

    try:
        server_keys = list_api_keys(mgmt_key, team_id)
    except Exception:
        return residuals

    extra_keys = []
    for row in server_keys:
        name = row.get("name") or ""
        kid = extract_api_key_id(row)
        if not kid or not is_provisioned_name(name):
            continue
        if kid not in minted_key_ids:
            extra_keys.append(kid)
    if extra_keys:
        residuals.append(
            f"{len(extra_keys)} key(s) match xlii-/xli- prefix but are not on minted paper "
            f"(reported, not deleted): {', '.join(extra_keys[:5])}"
            + (" …" if len(extra_keys) > 5 else "")
        )

    if clients is None:
        return residuals

    try:
        cloud = CollectionsBackend.list_collections(clients)
    except Exception:
        return residuals

    extra_colls = []
    for cid, cname in cloud.items():
        if not _is_provisioned_collection(cname):
            continue
        if cid not in minted_coll_ids:
            extra_colls.append(cid)
    if extra_colls:
        residuals.append(
            f"{len(extra_colls)} collection(s) match xlii prefix but are not on minted paper "
            f"(reported, not deleted): {', '.join(extra_colls[:5])}"
            + (" …" if len(extra_colls) > 5 else "")
        )
    return residuals


def _safe_unlink(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _wipe_local_tree(
    *,
    dry_run: bool,
    journal_path: Path,
    done: list[dict],
    failed: list[dict],
) -> None:
    reg = Registry.load()
    for entry in list(reg.entries):
        proj_root = Path(entry.path)
        xli_dir = proj_root / ".xlii"
        item = {"kind": "project", "path": str(proj_root), "name": entry.name}
        if dry_run:
            continue
        try:
            if xli_dir.exists():
                _safe_unlink(xli_dir)
            done.append({**item, "status": "local_removed"})
        except OSError as exc:
            failed.append({**item, "error": str(exc)})

    if not dry_run:
        reg.entries = []
        try:
            reg.save()
        except OSError as exc:
            failed.append({"kind": "registry", "path": str(REGISTRY_FILE), "error": str(exc)})

    for root in (_state_root(),):
        if dry_run:
            continue
        try:
            if root.exists():
                _safe_unlink(root)
                done.append({"kind": "path", "path": str(root), "status": "removed"})
        except OSError as exc:
            failed.append({"kind": "path", "path": str(root), "error": str(exc)})

    if dry_run:
        return

    preserve = {journal_path.resolve(), VAULT_FILE.resolve(), KEY_FILE.resolve()}
    cfg_dir = global_config_dir()
    if cfg_dir.exists():
        for child in list(cfg_dir.iterdir()):
            try:
                resolved = child.resolve()
            except OSError:
                resolved = child
            if resolved in preserve:
                continue
            try:
                _safe_unlink(child)
                done.append({"kind": "path", "path": str(child), "status": "removed"})
            except OSError as exc:
                failed.append({"kind": "path", "path": str(child), "error": str(exc)})


def _remote_revoke(
    manifest: Manifest,
    mgmt_key: str,
    *,
    dry_run: bool,
    done: list[dict],
    failed: list[dict],
) -> bool:
    """Return False when any remote revoke failed (caller must halt local wipe)."""
    from xlii.bootstrap import BootstrapError, delete_api_key
    from xlii.client import Clients
    from xlii.config import GlobalConfig
    from xlii.storage_backend import CollectionsBackend
    from xlii.xai_mgmt import list_teams

    if dry_run:
        return True

    try:
        teams = list_teams(mgmt_key)
    except Exception as exc:
        failed.append({"kind": "remote", "error": f"list_teams failed: {exc}"})
        return False
    if len(teams) > 1:
        failed.append(
            {
                "kind": "remote",
                "error": "multiple teams visible — refuse without explicit team scope",
            }
        )
        return False

    cfg = GlobalConfig.load()
    cfg.management_api_key = mgmt_key
    try:
        clients = Clients.from_config(cfg)
    except Exception as exc:
        failed.append({"kind": "remote", "error": f"cloud client unavailable: {exc}"})
        return False

    ok = True
    for coll in manifest.collections:
        item = {
            "kind": "collection",
            "id": coll.collection_id,
            "team_id": coll.team_id,
        }
        try:
            CollectionsBackend.delete_collection(clients, coll.collection_id)
            done.append({**item, "status": "deleted"})
        except Exception as exc:
            failed.append({**item, "error": str(exc)})
            ok = False

    for key in manifest.keys:
        item = {"kind": "key", "id": key.key_id, "team_id": key.team_id}
        try:
            delete_api_key(mgmt_key, key.team_id, key.key_id)
            done.append({**item, "status": "revoked"})
        except BootstrapError as exc:
            failed.append({**item, "error": str(exc)})
            ok = False

    return ok


def run_destroy(
    aim: str,
    *,
    target: str = "",
    level: int = 0,
    dry_run: bool | None = None,
    local_only: bool = False,
    console: Any = None,
    journal_path: Path | None = None,
    proof: Any = None,
) -> DestroyReport:
    aim = (aim or "all").strip().lower()
    if aim not in AIMS:
        aim = "all"

    if dry_run is None:
        dry_run = level == 0

    journal = journal_path or destroy_journal_path()
    report = DestroyReport(
        aim=aim,
        level=level,
        dry_run=dry_run,
        local_only=local_only,
        planned=[],
        done=[],
        failed=[],
        residuals=[],
        journal_path=journal,
    )

    if aim == "node" and not _node_on_paper(target, load_minted()):
        report.residuals.append("not on paper")
        return report

    if level >= 1 and not dry_run:
        if proof is not None:
            from xlii.panic_mail import consume_panic_proof

            if not consume_panic_proof(proof):
                report.failed.append({"kind": "gate", "error": "invalid panic proof"})
                return report
        else:
            refusal = _gate_level1(console)
            if refusal:
                report.failed.append({"kind": "gate", "error": refusal})
                return report

    manifest = load_minted()
    report.planned = _plan_manifest(manifest) + _plan_local_paths()

    if aim == "all":
        report.residuals.append(
            "Wave 1: destroy all = this install only; child fan-out is not built."
        )

    mgmt_key = os.environ.get(_MGMT_ENV) or ""
    team_id = ""
    if manifest.keys:
        team_id = manifest.keys[0].team_id
    elif manifest.collections:
        team_id = manifest.collections[0].team_id

    clients = None
    if mgmt_key and team_id and not local_only:
        try:
            from xlii.client import Clients
            from xlii.config import GlobalConfig

            cfg = GlobalConfig.load()
            cfg.management_api_key = mgmt_key
            clients = Clients.from_config(cfg)
        except Exception:
            clients = None

    if mgmt_key and team_id:
        report.residuals.extend(
            _prefix_residuals(manifest, mgmt_key, team_id, clients=clients)
        )

    plan_record = {
        "ts": _now_iso(),
        "phase": "plan",
        "aim": aim,
        "target": target,
        "level": level,
        "dry_run": dry_run,
        "local_only": local_only,
        "planned": report.planned,
        "residuals": list(report.residuals),
    }
    if local_only and level >= 1:
        surviving = {
            "keys": [{"id": k.key_id, "team_id": k.team_id} for k in manifest.keys],
            "collections": [
                {"id": c.collection_id, "team_id": c.team_id} for c in manifest.collections
            ],
        }
        plan_record["surviving_cloud_ids"] = surviving
    _append_journal(journal, plan_record)

    if level == 0 or dry_run:
        return report

    remote_ok = True
    if not local_only:
        if not mgmt_key:
            report.failed.append(
                {"kind": "remote", "error": "XAI_MANAGEMENT_API_KEY not set — cannot revoke cloud"}
            )
            remote_ok = False
        else:
            remote_ok = _remote_revoke(
                manifest, mgmt_key, dry_run=False, done=report.done, failed=report.failed
            )

    if not remote_ok:
        report.residuals.append("halted before local wipe due to remote failure")
        _append_journal(
            journal,
            {
                "ts": _now_iso(),
                "phase": "halt",
                "failed": report.failed,
                "residuals": report.residuals,
            },
        )
        return report

    if mgmt_key:
        os.environ.pop(_MGMT_ENV, None)

    _wipe_local_tree(
        dry_run=False,
        journal_path=journal,
        done=report.done,
        failed=report.failed,
    )

    from xlii.vault import clear_admin_secret, wipe_vault_verified

    wipe = wipe_vault_verified()
    if wipe.wiped:
        report.done.append({"kind": "vault", "status": "wiped"})
    else:
        report.residuals.extend(wipe.residuals)
    clear_admin_secret()

    if mgmt_key or os.environ.get(_MGMT_ENV):
        report.residuals.append(
            "XAI_MANAGEMENT_API_KEY is env-only and cannot self-revoke — remove from shell "
            "startup and revoke manually in the xAI console if intended"
        )

    _append_journal(
        journal,
        {
            "ts": _now_iso(),
            "phase": "complete",
            "done": report.done,
            "failed": report.failed,
            "residuals": report.residuals,
        },
    )
    return report
