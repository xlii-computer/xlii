"""Built-in preview providers — the first client of the preview seam (#2).

Each provider turns a context *kind*'s payload into a Rich renderable for the
read view of ``PreviewSurface``. They are deliberately tolerant about payload
shape: A1's tab-click hands the natural object for each kind (a ``(name, text)``
doc, a ``Skill``/``Role``, a ref tuple, an image path), but a bare name, a dict,
or a tuple all resolve here too — so the surface degrades to *something legible*
rather than erroring while sibling vectors are still landing their exact shapes.

The kinds:

- ``doc``   — ``(name, text)`` → a Markdown render of the doc body.
- ``skill`` — a ``Skill`` (or name) → ``skills.render_skill`` as Markdown.
- ``role``  — a ``Role`` (or name) → the role identity body as Markdown.
- ``ref``   — dispatch on the ref target type (seam #6): a *collection* → an
  info card, a *bookmark* → the saved turn.
- ``image`` — a path → an inline image renderable (dormant stub: a soft dep on
  multimodal attachments, so it falls back to a path note).

Registering them is idempotent (``register_builtin_previews``); the registry's
last-write-wins lets a project or a later vector override any of these.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from xlii.tui.preview import register_preview


# --------------------------------------------------------------------------- #
#  small shape coercion helpers (tolerant of tuple / dict / obj / str)
# --------------------------------------------------------------------------- #

def _project_root(state: Any) -> Optional[Path]:
    project = getattr(state, "project", None)
    root = getattr(project, "project_root", None) if project is not None else None
    return Path(root) if root else None


def _name_and_text(payload: Any) -> tuple[str, str]:
    """Pull a ``(name, text)`` out of the common shapes."""
    if isinstance(payload, dict):
        return str(payload.get("name") or ""), str(payload.get("text") or payload.get("body") or "")
    if isinstance(payload, (tuple, list)) and len(payload) >= 2:
        return str(payload[0]), str(payload[1])
    if isinstance(payload, str):
        return "", payload
    name = getattr(payload, "name", "")
    text = getattr(payload, "text", None)
    if text is None:
        text = getattr(payload, "body", "")
    return str(name or ""), str(text or "")


def _header(name: str, label: str) -> Text:
    head = Text(label, style="bold cyan")
    if name:
        head.append("  ")
        head.append(name, style="bold")
    return head


# --------------------------------------------------------------------------- #
#  doc
# --------------------------------------------------------------------------- #

def doc_preview(payload: Any, *, state: Any = None) -> RenderableType:
    name, text = _name_and_text(payload)
    if not text:
        return Text("(empty doc)", style="dim italic")
    return Group(_header(name, "doc"), Text(""), Markdown(text))


# --------------------------------------------------------------------------- #
#  skill
# --------------------------------------------------------------------------- #

def skill_preview(payload: Any, *, state: Any = None) -> RenderableType:
    from xlii import skills as _skills

    skill = None
    if hasattr(payload, "body") and hasattr(payload, "name"):
        skill = payload  # already a Skill
    elif isinstance(payload, str):
        name = payload[len(_skills.SKILL_ATTACH_PREFIX):] if payload.startswith(
            _skills.SKILL_ATTACH_PREFIX) else payload
        skill = _skills.load_skills(_project_root(state)).get(name)
    elif isinstance(payload, (tuple, list)) and payload:
        raw = str(payload[0])
        name = raw[len(_skills.SKILL_ATTACH_PREFIX):] if raw.startswith(
            _skills.SKILL_ATTACH_PREFIX) else raw
        skill = _skills.load_skills(_project_root(state)).get(name)
        if skill is None and len(payload) >= 2 and payload[1]:
            # an attached skill carries its rendered body on the /doc channel
            return Group(_header(name, "skill"), Text(""), Markdown(str(payload[1])))

    if skill is None:
        name, text = _name_and_text(payload)
        if text:
            return Group(_header(name, "skill"), Text(""), Markdown(text))
        return Text(f"no such skill: {name or payload!r}", style="yellow")
    return Markdown(_skills.render_skill(skill))


# --------------------------------------------------------------------------- #
#  role
# --------------------------------------------------------------------------- #

def role_preview(payload: Any, *, state: Any = None) -> RenderableType:
    from xlii import role as _role

    role_obj = None
    if hasattr(payload, "identity"):
        role_obj = payload  # already a Role
    elif isinstance(payload, str):
        role_obj = _role.load_role(payload, _project_root(state))
    elif isinstance(payload, (tuple, list)) and payload:
        role_obj = _role.load_role(str(payload[0]), _project_root(state))

    if role_obj is None:
        name, text = _name_and_text(payload)
        if text:
            return Group(_header(name, "role"), Text(""), Markdown(text))
        return Text(f"no such role: {payload!r}", style="yellow")

    body = role_obj.identity()
    parts: list[RenderableType] = [_header(role_obj.name, "role")]
    desc = role_obj.description()
    if desc:
        parts.append(Text(desc, style="dim"))
    parts.append(Text(""))
    parts.append(Markdown(body or "_(no identity body)_"))
    return Group(*parts)


# --------------------------------------------------------------------------- #
#  ref — dispatch on target type (seam #6): collection | bookmark
# --------------------------------------------------------------------------- #

def _ref_parts(payload: Any, state: Any) -> tuple[str, str, Any]:
    """Normalize a ref into ``(name, target_type, target)``.

    Accepts D's future 3-tuple ``(name, target_type, target)``, today's 2-tuple
    ``(name, collection_id)`` (→ a collection), a dict, or a bare name resolved
    against ``state.attached_refs``."""
    if isinstance(payload, dict):
        return (
            str(payload.get("name") or ""),
            str(payload.get("target_type") or "collection"),
            payload.get("target", payload.get("collection_id")),
        )
    if isinstance(payload, (tuple, list)):
        if len(payload) >= 3:
            return str(payload[0]), str(payload[1] or "collection"), payload[2]
        if len(payload) == 2:
            return str(payload[0]), "collection", payload[1]
        if len(payload) == 1:
            payload = str(payload[0])
    if isinstance(payload, str):
        for entry in getattr(state, "attached_refs", []) or []:
            if isinstance(entry, (tuple, list)) and entry and str(entry[0]) == payload:
                return _ref_parts(tuple(entry), state)
        return payload, "collection", None
    return str(payload), "collection", None


def _render_saved_turn(target: Any) -> Optional[RenderableType]:
    """Render a bookmark's saved turn when the target carries one (a dict with a
    ``body``/``text`` string, or a list of ``{user, assistant, timestamp}`` turns).
    Returns None when the target is just an identifier (then we show a card)."""
    if isinstance(target, dict):
        body = target.get("body") or target.get("text")
        if body:
            return Markdown(str(body))
        turns = target.get("turns")
        if isinstance(turns, (list, tuple)) and turns:
            target = turns
    if isinstance(target, (list, tuple)) and target and isinstance(target[0], dict):
        parts: list[RenderableType] = []
        for t in target:
            ts = t.get("timestamp") or ""
            parts.append(Text(f"— {ts}", style="dim"))
            parts.append(Markdown(f"**User**\n\n{t.get('user', '')}\n\n**Assistant**\n\n{t.get('assistant', '')}"))
        return Group(*parts)
    return None


def ref_preview(payload: Any, *, state: Any = None) -> RenderableType:
    name, target_type, target = _ref_parts(payload, state)
    if target_type == "bookmark":
        turn = _render_saved_turn(target)
        if turn is not None:
            return Group(_header(name, "ref · bookmark"), Text(""), turn)
        tgt = "" if target is None else f"\n\nsaved turn: {target}"
        return Panel(
            Text(f"a bookmarked turn{tgt}", style="dim"),
            title=f"ref · bookmark · {name}",
            border_style="cyan",
        )
    # collection (today's default) — an info card
    tgt = "" if target is None else f"\ncollection: {target}"
    return Panel(
        Text(f"an xAI collection reference{tgt}", style="dim"),
        title=f"ref · collection · {name}",
        border_style="cyan",
    )


# --------------------------------------------------------------------------- #
#  image — dormant stub (soft dep on multimodal attachments)
# --------------------------------------------------------------------------- #

def image_preview(payload: Any, *, state: Any = None) -> RenderableType:
    from xlii import terminal_image

    path: Any
    if isinstance(payload, dict):
        path = payload.get("path") or payload.get("target")
    elif isinstance(payload, (tuple, list)) and payload:
        path = payload[-1]
    else:
        path = payload
    if not path:
        return Text("(no image)", style="dim italic")
    p = Path(str(path)).expanduser()
    rendered = None
    try:
        rendered = terminal_image.image_renderable(p)
    except Exception:
        rendered = None
    if rendered is not None:
        return Group(_header(p.name, "image"), Text(""), rendered)
    return Group(_header(p.name, "image"), Text(""), Text(str(p), style="dim"))


# --------------------------------------------------------------------------- #
#  registration
# --------------------------------------------------------------------------- #

def register_builtin_previews() -> None:
    """Register the built-in providers. Idempotent (last-write-wins registry)."""
    register_preview("doc", doc_preview)
    register_preview("skill", skill_preview)
    register_preview("role", role_preview)
    register_preview("ref", ref_preview)
    register_preview("image", image_preview)
