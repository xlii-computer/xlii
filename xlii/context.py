"""
DeepContext — the core abstraction for bidirectional bridging between xlii and Grok Build.

A DeepContext is a portable, durable snapshot of an xlii workspace (attachments + custom tools)
that can be:

- Attached by Grok Build agents as long-term memory and custom capabilities
- Modified by those agents
- Synced back into the original xlii workspace (write-back)

Storage:
    Global DeepContexts live at ~/.config/xlii/contexts/<name>.json.
    A legacy ~/.xli/contexts/ tree is drained into it on read
    (_auto_migrate_legacy) — mirrors xlii/loadout_paths.py.

MCP Exposure (from day 1):
    - Resource: xlii://contexts
    - Resource: xlii://contexts/{name}
    - Tool:     xlii_list_contexts()
    - Tool:     xlii_load_context(name)
    - Tool:     xlii_sync_context(name, updates)
    - Tool:     xlii_get_live_context(project_path?, workspace?)   # live, no prior /context save needed

See also:
    - examples/deep-contexts.py
    - xlii/mcp_deep_context.py  (MCP server definition)
    - REPLState.create_deep_context() in xlii/repl.py
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from xlii.atomicio import write_text_atomic
from xlii.config import GLOBAL_CONFIG_DIR


@dataclass
class DeepContext:
    """A portable, durable snapshot of an xlii workspace that can be attached
    by external agents (especially Grok Build) as long-term memory + capabilities.

    This is the core abstraction for the bidirectional bridge.
    """

    # Identity
    id: str
    name: str

    # Required fields must stay contiguous for Python 3.12 dataclass init generation.
    # Source of truth in xlii
    source_project_path: str
    source_workspace_name: str
    description: str | None = None

    # The actual durable state
    attached_refs: list[tuple[str, str]] = field(default_factory=list)
    attached_docs: list[tuple[str, str]] = field(default_factory=list)

    # Custom agent tools that were available when the context was created
    tools: list[dict[str, Any]] = field(default_factory=list)

    # Metadata
    version: int = 1
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_synced_at: str | None = None
    tags: list[str] = field(default_factory=list)
    origin: str = "xlii"   # "xlii" or "grok-build" (for write-back tracking)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeepContext":
        return cls(**data)


# ------------------------------------------------------------------
# Global storage for DeepContexts — canonical location + legacy fallback.
#
# Mirrors xlii/loadout_paths.py: the canonical store is ~/.config/xlii/contexts/.
# A legacy ~/.xli/contexts/ tree is drained into it on read (_auto_migrate_legacy),
# then reads/writes are canonical-only — no permanent fallback, and legacy data is
# absorbed (moved), so a delete can't be undone by a lingering legacy shadow.
# ------------------------------------------------------------------

GLOBAL_CONTEXTS_DIR = GLOBAL_CONFIG_DIR / "contexts"
LEGACY_GLOBAL_CONTEXTS_DIR = Path.home() / ".xli" / "contexts"

def _auto_migrate_legacy() -> None:
    """Best-effort one-way DRAIN of legacy ``~/.xli/contexts`` into the canonical
    dir: each legacy file is moved over (canonical wins on a name clash, dropping
    the shadow), then the emptied legacy dir is removed. Draining — rather than
    copying — is what lets a deleted context stay deleted (a lingering legacy copy
    would otherwise resurface on the next read). A no-op (one stat) when there is
    no legacy dir, so it's safe to call on every read."""
    src = LEGACY_GLOBAL_CONTEXTS_DIR
    if not src.is_dir():
        return
    try:
        GLOBAL_CONTEXTS_DIR.mkdir(parents=True, exist_ok=True)
        for path in src.glob("*.json"):
            target = GLOBAL_CONTEXTS_DIR / path.name
            if target.exists():
                path.unlink()                       # canonical wins — drop shadow
            else:
                shutil.move(str(path), str(target))  # absorb legacy-only name
        try:
            src.rmdir()                              # remove now-empty legacy dir
        except OSError:
            # The legacy dir isn't empty (or won't go) -- the files above were still migrated.
            pass
    except Exception:
        # Auto-migration is opportunistic: a partial or failed migration must not block resolving the context
        # dir.
        pass


def resolve_global_contexts_dir(*, for_write: bool = False) -> Path:
    """Return the canonical directory for global DeepContext JSON files."""
    if for_write:
        GLOBAL_CONTEXTS_DIR.mkdir(parents=True, exist_ok=True)
    else:
        _auto_migrate_legacy()
    return GLOBAL_CONTEXTS_DIR


def global_context_read_dirs() -> list[Path]:
    """Return the global context directory (canonical only). Legacy contexts are
    auto-migrated into it on read."""
    _auto_migrate_legacy()
    return [GLOBAL_CONTEXTS_DIR]


def find_global_context_path(name: str) -> Path | None:
    """Find a context JSON by name in the canonical location."""
    for root in global_context_read_dirs():
        path = root / f"{name}.json"
        if path.exists():
            return path
    return None


def migrate_global_contexts(*, dry_run: bool = False) -> list[str]:
    """Copy legacy ``~/.xli/contexts/*.json`` into ``~/.config/xlii/contexts/``.

    Returns human-readable log lines. Skips names that already exist in the target.
    """
    lines: list[str] = []
    src = LEGACY_GLOBAL_CONTEXTS_DIR
    if not src.is_dir():
        return lines
    dst = GLOBAL_CONTEXTS_DIR
    if not dry_run:
        dst.mkdir(parents=True, exist_ok=True)
    for path in sorted(src.glob("*.json")):
        target = dst / path.name
        if target.exists():
            lines.append(f"skip {path.name} (already in {dst})")
            continue
        if dry_run:
            lines.append(f"would migrate {path} → {target}")
        else:
            shutil.copy2(path, target)
            lines.append(f"migrated {path.name} → {dst}/")
    return lines


def _ensure_contexts_dir() -> None:
    GLOBAL_CONTEXTS_DIR.mkdir(parents=True, exist_ok=True)


def context_path(name: str) -> Path:
    """Canonical write path for a context (reads use find_global_context_path)."""
    return GLOBAL_CONTEXTS_DIR / f"{name}.json"


def _project_xli_dir(root: Path) -> Path:
    """Resolve a project's state dir (``.xlii/``), auto-migrating a pre-rename
    ``.xli/`` first so MCP callers that never opened the project still see it."""
    try:
        from xlii.legacy_migrate import migrate_project_state
        migrate_project_state(root)
    except Exception:
        # Per the docstring this migration is a convenience -- resolution below works either way.
        pass
    return root / ".xlii"


def save_deep_context(ctx: DeepContext) -> None:
    """Save a DeepContext to the canonical global store."""
    _ensure_contexts_dir()
    path = context_path(ctx.name)
    write_text_atomic(path, json.dumps(ctx.to_dict(), indent=2, ensure_ascii=False), mode=0o644)


def load_deep_context(name: str) -> DeepContext | None:
    """Load a DeepContext by name (drain legacy into canonical, then read)."""
    path = find_global_context_path(name)
    if path is None:
        return None
    data = json.loads(path.read_text())
    return DeepContext.from_dict(data)


def list_deep_contexts() -> list[str]:
    """Return names of all saved DeepContexts (canonical store after legacy drain)."""
    names: set[str] = set()
    for root in global_context_read_dirs():
        if root.exists():
            names.update(p.stem for p in root.glob("*.json"))
    return sorted(names)


def delete_deep_context(name: str) -> bool:
    """Delete a DeepContext from every global root it lives in.

    Removes the name from all roots, not just the first match: a context present
    in both the canonical and legacy dirs would otherwise survive in the legacy
    shadow while delete reported success and list_deep_contexts() (a union across
    roots) kept showing it.
    """
    deleted = False
    for root in global_context_read_dirs():
        path = root / f"{name}.json"
        if path.exists():
            path.unlink()
            deleted = True
    return deleted


# ------------------------------------------------------------------
# MCP-friendly helpers (exposed from day 1)
# ------------------------------------------------------------------

def mcp_list_contexts() -> list[dict[str, Any]]:
    """Return lightweight metadata for all global DeepContexts (for MCP)."""
    names = list_deep_contexts()
    result = []
    for name in names:
        ctx = load_deep_context(name)
        if ctx:
            result.append({
                "name": ctx.name,
                "description": ctx.description,
                "source_project": ctx.source_project_path,
                "source_workspace": ctx.source_workspace_name,
                "ref_count": len(ctx.attached_refs),
                "doc_count": len(ctx.attached_docs),
                "tool_count": len(ctx.tools),
                "tags": ctx.tags,
            })
    return result


def mcp_get_context(name: str) -> dict[str, Any] | None:
    """Return the full DeepContext by name (for MCP consumption by Grok Build)."""
    ctx = load_deep_context(name)
    return ctx.to_dict() if ctx else None


def mcp_sync_context(name: str, updates: dict[str, Any]) -> dict[str, Any]:
    """
    True write-back (bidirectional bridge):

    1. Merge updates coming from an external agent (Grok Build, etc.)
       into the DeepContext.
    2. Immediately push those attachment changes back into the
       original source xlii workspace's session.json.

    This completes the round-trip.
    """
    ctx = load_deep_context(name)
    if not ctx:
        raise ValueError(f"DeepContext {name!r} not found")

    # 1. Apply updates to the DeepContext
    if "attached_refs" in updates:
        ctx.attached_refs = updates["attached_refs"]
    if "attached_docs" in updates:
        ctx.attached_docs = updates["attached_docs"]
    if "description" in updates:
        ctx.description = updates["description"]
    if "tags" in updates:
        ctx.tags = updates["tags"]

    ctx.last_synced_at = datetime.now(timezone.utc).isoformat()
    ctx.origin = "grok-build"

    save_deep_context(ctx)

    # 2. True write-back into the source workspace
    _sync_attachments_back_to_source_workspace(ctx)

    return ctx.to_dict()


def _sync_attachments_back_to_source_workspace(ctx: DeepContext) -> None:
    """
    Push the attachments from a DeepContext back into its source
    xlii project's session.json under the correct workspace.

    This is the "true write-back" step.
    """
    try:
        project_root = Path(ctx.source_project_path)
        session_path = _project_xli_dir(project_root) / "session.json"

        if not session_path.exists():
            # Source project/workspace no longer exists locally — nothing to sync to
            return

        session_data = json.loads(session_path.read_text())

        workspaces = session_data.setdefault("workspaces", {})
        ws = workspaces.setdefault(ctx.source_workspace_name, {})

        # Overwrite with the (possibly updated) attachments from the DeepContext
        ws["attached_refs"] = ctx.attached_refs
        ws["attached_docs"] = ctx.attached_docs
        ws["last_synced_from_deep_context"] = ctx.last_synced_at

        # Atomic write (+ fsync via the shared helper)
        write_text_atomic(
            session_path, json.dumps(session_data, indent=2, ensure_ascii=False), mode=0o644
        )

    except Exception as e:
        # Never crash an MCP call or REPL because of write-back failure
        logger.warning("Failed to write DeepContext changes back to source workspace: {}", e)


# ------------------------------------------------------------------
# Live context (the key for seamless Grok Build consumption)
# ------------------------------------------------------------------

def get_live_context(
    project_path: str | None = None,
    workspace_name: str | None = None,
) -> dict[str, Any] | None:
    """
    Return a live DeepContext-like snapshot directly from an xlii project's
    on-disk session.json + currently loadable project tools.

    This is the primary path for Grok Build: no need for the user to run
    `/context save` first. Just point at a project directory (or omit to use
    CWD) and get the current attachments + custom tools for that workspace.

    Returns None if no .xlii/session.json is present.
    """
    if project_path is None:
        project_path = str(Path.cwd())

    root = Path(project_path).resolve()

    xli_dir = _project_xli_dir(root)

    session_path = xli_dir / "session.json"
    if not session_path.exists():
        return None

    try:
        session_data = json.loads(session_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    current_ws = session_data.get("current_workspace", "main")
    ws_name = workspace_name or current_ws
    workspaces = session_data.get("workspaces", {})
    ws = workspaces.get(ws_name, {})

    attached_refs = list(ws.get("attached_refs", []))
    attached_docs = list(ws.get("attached_docs", []))

    # Load whatever custom AgentTools the project currently exposes
    tools: list[dict[str, Any]] = []
    try:
        from xlii.tools import load_project_tools, _PROJECT_TOOLS

        load_project_tools(xli_dir)
        for t in _PROJECT_TOOLS.values():
            tools.append(
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                    "parallel_safe": getattr(t, "parallel_safe", True),
                    "plan_mode_safe": getattr(t, "plan_mode_safe", True),
                    "worker_safe": getattr(t, "worker_safe", True),
                    "source": getattr(t, "source", "project"),
                    "category": getattr(t, "category", None),
                }
            )
    except Exception:
        # Tools are nice-to-have; never break the live context over tool loading
        tools = []

    from datetime import datetime, timezone
    import uuid

    live_name = f"{root.name}:{ws_name}:live"

    return {
        "id": str(uuid.uuid4()),
        "name": live_name,
        "source_project_path": str(root),
        "source_workspace_name": ws_name,
        "description": f"Live view of '{ws_name}' workspace in {root.name} (fresh from disk)",
        "attached_refs": attached_refs,
        "attached_docs": attached_docs,
        "tools": tools,
        "version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_synced_at": ws.get("last_synced_from_deep_context"),
        "tags": ["live", "auto"],
        "origin": "live",
    }


# ------------------------------------------------------------------
# Project tool execution bridge (for Grok Build consumption of custom capabilities)
# ------------------------------------------------------------------

def call_project_tool(
    name: str,
    args: dict[str, Any] | None = None,
    project_path: str | None = None,
) -> dict[str, Any]:
    """
    Execute a user-defined project AgentTool by name.

    This is the bridge that turns the `tools[]` list returned by
    `get_live_context` / `xlii_load_context` into *callable* capabilities
    from Grok Build (via the `xlii_call_project_tool` MCP tool).

    A minimal headless ToolContext is constructed so that the great
    majority of custom tools (the ones that only need the project root,
    do subprocess work, file ops local to the tree, etc.) work without
    a full xlii REPL/agent runtime.

    Returns a dict with "content", "is_error", plus diagnostic fields.
    """
    args = args or {}

    if project_path is None:
        project_path = str(Path.cwd())

    root = Path(project_path).resolve()

    xli_dir = _project_xli_dir(root)

    if not xli_dir.exists():
        raise ValueError(
            f"No .xlii directory found under {root}. "
            "The project must have been initialized with 'xlii init'."
        )

    # Load (or reload) whatever custom tools the project registers
    from xlii.tools import load_project_tools, _PROJECT_TOOLS

    load_project_tools(xli_dir)

    tool = _PROJECT_TOOLS.get(name)
    if tool is None:
        available = sorted(_PROJECT_TOOLS.keys())
        raise ValueError(
            f"Project tool {name!r} is not registered for this project. "
            f"Available tools: {available}"
        )

    # Build a minimal but useful ToolContext.
    # We deliberately avoid network clients / cost tracking / full REPL state.
    from xlii.config import GlobalConfig, ProjectConfig
    from xlii.tools import ToolContext

    try:
        proj = ProjectConfig.load(root)
    except Exception:
        proj = None

    if proj is None:
        proj = ProjectConfig(
            project_root=root,
            name=root.name,
            collection_id="mcp-bridge-local",
            local_only=True,
        )

    cfg = GlobalConfig()  # safe defaults for most custom tools

    ctx = ToolContext(
        project=proj,
        clients=None,           # custom tools that need RAG/search_project will fail (expected)
        cfg=cfg,
        pool=None,
        console=None,
        yolo=False,
        is_worker=False,
        subscribed_plugins=[],
    )

    try:
        result = tool.handler(ctx, args)
        return {
            "content": result.content,
            "is_error": bool(getattr(result, "is_error", False)),
            "tool": name,
            "project": str(root),
            "workspace": "live",  # informational
        }
    except Exception as e:
        # Never let a project tool take down the MCP server
        return {
            "content": f"Project tool {name!r} raised {type(e).__name__}: {e}",
            "is_error": True,
            "tool": name,
            "project": str(root),
        }
