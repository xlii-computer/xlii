"""Local media artifacts — binary store under .xlii/artifacts/ (media-artifacts M0).

Artifacts are never synced to Collections and never marked dirty. Session JSON
tracks the last /imagine turn for --redo / --editprompt iteration.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from xlii.atomicio import write_bytes_atomic, write_text_atomic
from xlii.media_client import (
    DEFAULT_ASPECT_RATIO,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_RESOLUTION,
    GeneratedImage,
    _ext_for_mime,
    edit_image,
    estimate_image_cost,
    generate_image,
)

_KEEP = 200
_SESSION_NAME = "last-session.json"
_VIDEO_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")


@dataclass
class ArtifactMeta:
    rel_path: str
    abs_path: Path
    mime_type: str
    bytes: int
    model: str
    prompt: str
    aspect_ratio: str = DEFAULT_ASPECT_RATIO
    resolution: str = DEFAULT_RESOLUTION


@dataclass
class ImagineSession:
    prompt: str
    model: str = DEFAULT_IMAGE_MODEL
    aspect_ratio: str = DEFAULT_ASPECT_RATIO
    resolution: str = DEFAULT_RESOLUTION
    paths: list[str] = field(default_factory=list)
    provider: str = "xai"
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImagineSession:
        return cls(
            prompt=str(data.get("prompt") or ""),
            model=str(data.get("model") or DEFAULT_IMAGE_MODEL),
            aspect_ratio=str(data.get("aspect_ratio") or DEFAULT_ASPECT_RATIO),
            resolution=str(data.get("resolution") or DEFAULT_RESOLUTION),
            paths=[str(p) for p in (data.get("paths") or []) if p],
            provider=str(data.get("provider") or "xai"),
            created_at=str(data.get("created_at") or ""),
        )


def artifacts_dir(project_root: Path) -> Path:
    return Path(project_root) / ".xlii" / "artifacts"


def artifact_rel(name: str) -> str:
    """Project-relative posix path for a store filename (``.xlii/artifacts/<name>``)."""
    return f".xlii/artifacts/{Path(name).name}"


def locate_made_file(path: Path, project_root: Path | None = None) -> tuple[Path, str]:
    """Prefer the artifacts-store copy of a delivered file.

    ``send_file`` copies into a temp outbox; the durable file is still
    ``.xlii/artifacts/<name>``. Returns ``(path, vfs_address)``.
    """
    p = Path(path)
    root = Path(project_root) if project_root else None
    if root is not None:
        store = artifacts_dir(root)
        names = [p.name]
        # Duplicate sends land as ``1-<name>`` in the outbox.
        if "-" in p.name and p.name.split("-", 1)[0].isdigit():
            names.append(p.name.split("-", 1)[1])
        for name in names:
            cand = store / name
            try:
                if cand.is_file():
                    return cand, f"artifacts://{cand.name}"
            except OSError:
                continue
        try:
            resolved = p.resolve()
            resolved.relative_to(store.resolve())
            return resolved, f"artifacts://{resolved.name}"
        except (ValueError, OSError):
            # Not inside the artifacts store, or unresolvable -- fall through to the plain file:// form below.
            pass
    return p, f"file://{p}"


def display_store_path(path: Path, project_root: Path | None = None) -> str:
    """Short label: ``.xlii/artifacts/name`` when we can, else the given path."""
    p = Path(path)
    if project_root:
        try:
            return p.resolve().relative_to(Path(project_root).resolve()).as_posix()
        except (ValueError, OSError):
            # Outside the project root, or unresolvable -- fall through to the absolute display below.
            pass
    text = str(p)
    marker = ".xlii/artifacts/"
    i = text.find(marker)
    return text[i:] if i >= 0 else text


def session_path(project_root: Path) -> Path:
    return artifacts_dir(project_root) / _SESSION_NAME


def _prune(d: Path, keep: int = _KEEP, *, protect: Path | set[Path] | None = None) -> None:
    try:
        files = sorted(
            (p for p in d.iterdir() if p.is_file() and p.name != _SESSION_NAME),
            key=lambda p: p.stat().st_mtime,
        )
    except OSError:
        return
    protected = {protect} if isinstance(protect, Path) else set(protect or ())
    candidates = [f for f in files if f not in protected]
    for old in candidates[: max(0, len(files) - keep)]:
        try:
            old.unlink()
        except OSError:
            # Best-effort pruning: ignore per-file delete failures to avoid
            # failing artifact writes due to transient/permission filesystem errors.
            pass


def _path_inside(child: Path, parent: Path) -> bool:
    """True when resolved ``child`` is under resolved ``parent``."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def resolve_artifact_path(project_root: Path, rel_artifact: str) -> Path:
    """Resolve a project-relative artifact path; reject escapes outside the store."""
    root = Path(project_root).resolve()
    store = artifacts_dir(root).resolve()
    src = (root / rel_artifact).resolve()
    if not _path_inside(src, store):
        raise ValueError(f"artifact path escapes store: {rel_artifact!r}")
    if not src.is_file():
        raise FileNotFoundError(rel_artifact)
    return src


def write_artifact(project_root: Path, data: bytes, *, ext: str, prune: bool = True,
                   prefix: str = "img") -> str:
    """Write binary artifact; return project-relative posix path."""
    root = Path(project_root)
    d = artifacts_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    ext = ext.lstrip(".") or "png"
    name = f"{prefix}-{uuid.uuid4().hex[:10]}.{ext}"
    path = d / name
    write_bytes_atomic(path, data, mode=0o644)
    if prune:
        _prune(d, protect=path)
    try:
        from xlii.active_session import active_session

        hook = getattr(active_session(), "on_artifact_written", None)
        if callable(hook):
            hook(path)
    except Exception:
        # best-effort: a broken session hook must not break the artifact write
        pass
    return path.relative_to(root).as_posix()


def _meta_from_written(
    project_root: Path,
    rel_path: str,
    img: GeneratedImage,
    *,
    prompt: str,
    aspect_ratio: str,
    resolution: str,
) -> ArtifactMeta:
    abs_path = Path(project_root) / rel_path
    return ArtifactMeta(
        rel_path=rel_path,
        abs_path=abs_path,
        mime_type=img.mime_type,
        bytes=len(img.data),
        model=img.model,
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
    )


def read_session(project_root: Path) -> Optional[ImagineSession]:
    path = session_path(project_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    sess = ImagineSession.from_dict(data)
    return sess if sess.prompt or sess.paths else None


def write_session(project_root: Path, session: ImagineSession) -> None:
    path = session_path(project_root)
    if not session.created_at:
        session.created_at = datetime.now(timezone.utc).isoformat()
    write_text_atomic(path, json.dumps(session.to_dict(), indent=2) + "\n", mode=0o644)


def video_record_path(project_root: Path, request_id: str) -> Path:
    """Location of the resumable record for one async video job."""
    if not isinstance(request_id, str) or not _VIDEO_REQUEST_ID_RE.fullmatch(request_id):
        raise ValueError("invalid video request_id")
    d = artifacts_dir(project_root)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"video-{request_id}.json"


def read_video_record(project_root: Path, request_id: str) -> dict[str, Any]:
    """Load one video job's record; a pending skeleton when none exists yet."""
    p = video_record_path(project_root, request_id)
    if p.exists():
        return json.loads(p.read_text())
    return {"request_id": request_id, "prompt": "", "model": "", "status": "pending"}


def write_video_record(project_root: Path, record: dict[str, Any]) -> None:
    """Persist a video job record (the poll/kickoff lifecycle lives in
    ``xlii.imagine_run``; this module owns only the on-disk format)."""
    write_text_atomic(
        video_record_path(project_root, record["request_id"]),
        json.dumps(record, indent=1) + "\n",
    )


def imagine_and_store(
    project_root: Path,
    prompt: str,
    *,
    api_key: str,
    model: str = DEFAULT_IMAGE_MODEL,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    resolution: str = DEFAULT_RESOLUTION,
    n: int = 1,
) -> tuple[list[ArtifactMeta], str, ImagineSession]:
    """Generate, write artifacts, update session. Returns (metas, cost_line, session)."""
    images = generate_image(
        prompt,
        api_key=api_key,
        model=model,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        n=n,
    )
    metas: list[ArtifactMeta] = []
    paths: list[str] = []
    root = Path(project_root)
    store = artifacts_dir(root)
    written: list[Path] = []
    for img in images:
        rel = write_artifact(root, img.data, ext=_ext_for_mime(img.mime_type), prune=False)
        written.append((root / rel).resolve())
        paths.append(rel)
        metas.append(
            _meta_from_written(
                root,
                rel,
                img,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
            )
        )
    if written:
        _prune(store, protect=set(written))
    session = ImagineSession(
        prompt=prompt,
        model=model,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        paths=paths,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    write_session(project_root, session)
    return metas, estimate_image_cost(model, len(metas)), session


def edit_and_store(
    project_root: Path,
    prompt: str,
    reference_paths: list[Path] | list[str],
    *,
    api_key: str,
    model: str = DEFAULT_IMAGE_MODEL,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    resolution: str = DEFAULT_RESOLUTION,
    n: int = 1,
) -> tuple[list[ArtifactMeta], str, ImagineSession]:
    """Edit via reference images, write artifacts, update session (M5).

    Same return shape as :func:`imagine_and_store`. Wire shape is faked until
    the operator live pass — callers should say so in UX/docs.
    """
    images = edit_image(
        prompt,
        reference_paths,
        api_key=api_key,
        model=model,
        n=n,
    )
    metas: list[ArtifactMeta] = []
    paths: list[str] = []
    root = Path(project_root)
    store = artifacts_dir(root)
    written: list[Path] = []
    for img in images:
        rel = write_artifact(root, img.data, ext=_ext_for_mime(img.mime_type), prune=False)
        written.append((root / rel).resolve())
        paths.append(rel)
        metas.append(
            _meta_from_written(
                root,
                rel,
                img,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
            )
        )
    if written:
        _prune(store, protect=set(written))
    session = ImagineSession(
        prompt=prompt,
        model=model,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        paths=paths,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    write_session(project_root, session)
    return metas, estimate_image_cost(model, len(metas)), session


def save_artifact_to(project_root: Path, rel_artifact: str, dest: str) -> Path:
    """Copy a stored artifact into the project tree."""
    root = Path(project_root).resolve()
    src = resolve_artifact_path(root, rel_artifact)
    dest_path = Path(dest).expanduser()
    if not dest_path.is_absolute():
        dest_path = (root / dest_path).resolve()
    else:
        dest_path = dest_path.resolve()
    if not _path_inside(dest_path, root):
        raise ValueError(f"destination escapes project: {dest!r}")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest_path)
    return dest_path
