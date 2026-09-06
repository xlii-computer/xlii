"""Vector A2 — the preview registry + built-in providers (seam #2, read side).

These pin the registry contract (register / dispatch / fallback / isolation) and
that each built-in provider renders the natural payload shapes A1's tab-click
hands it, plus the bare-name / dict / tuple shapes it should also tolerate while
sibling vectors are still landing their exact encodings.
"""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

from rich.console import Console, RenderableType

from xlii.tui import preview, preview_providers


def _render(renderable: RenderableType, width: int = 80) -> str:
    buf = StringIO()
    Console(file=buf, width=width, no_color=True, highlight=False).print(renderable)
    return buf.getvalue()


# -- registry -------------------------------------------------------------

def test_register_and_dispatch():
    preview.register_preview("xkind", lambda payload, state=None: __import__("rich.text", fromlist=["Text"]).Text(f"got:{payload}"))
    assert preview.get_preview_provider("xkind") is not None
    assert "got:hi" in _render(preview.preview_for("xkind", "hi"))


def test_last_registration_wins():
    preview.register_preview("dupe", lambda p, state=None: __import__("rich.text", fromlist=["Text"]).Text("first"))
    preview.register_preview("dupe", lambda p, state=None: __import__("rich.text", fromlist=["Text"]).Text("second"))
    assert "second" in _render(preview.preview_for("dupe", None))


def test_unknown_kind_falls_back_not_raises():
    out = _render(preview.preview_for("does-not-exist", None))
    assert "no preview for 'does-not-exist'" in out


def test_provider_error_is_contained():
    def _boom(payload, state=None):
        raise RuntimeError("kaboom")

    preview.register_preview("boom", _boom)
    out = _render(preview.preview_for("boom", None))
    assert "preview failed" in out and "kaboom" in out


def test_builtins_register_lazily():
    # A fresh dispatch for a built-in kind must work without an explicit
    # register_builtin_previews() call (the surface/tab-click path relies on it).
    assert "doc" in preview.registered_preview_kinds()
    assert "image" in preview.registered_preview_kinds()


def test_provider_called_without_state_kwarg():
    # A provider that doesn't accept state must still be invokable.
    preview.register_preview("nostate", lambda payload: __import__("rich.text", fromlist=["Text"]).Text(f"p={payload}"))
    assert "p=42" in _render(preview.preview_for("nostate", 42))


# -- doc ------------------------------------------------------------------

def test_doc_preview_tuple_and_dict():
    preview_providers.register_builtin_previews()
    assert "hello world" in _render(preview.preview_for("doc", ("notes", "hello world")))
    assert "hello world" in _render(preview.preview_for("doc", {"name": "notes", "text": "hello world"}))


def test_doc_preview_empty():
    assert "empty doc" in _render(preview.preview_for("doc", ("n", "")))


# -- skill ----------------------------------------------------------------

def test_skill_preview_from_skill_object():
    from xlii.skills import Skill

    sk = Skill(name="grounded", description="be grounded", body="1. cite\n2. verify", path=__import__("pathlib").Path("x"), scope="stock")
    out = _render(preview.preview_for("skill", sk))
    assert "Skill: grounded" in out
    assert "cite" in out


def test_skill_preview_attached_tuple_uses_body():
    # An attached skill rides the /doc channel as (skill:<name>, <rendered body>).
    out = _render(preview.preview_for("skill", ("skill:foo", "# step one\nbody")))
    assert "step one" in out


# -- role -----------------------------------------------------------------

def test_role_preview_from_role_like():
    role_like = SimpleNamespace(
        name="architect",
        identity=lambda: "You are a senior architect.",
        description=lambda: "designs systems",
    )
    out = _render(preview.preview_for("role", role_like))
    assert "architect" in out
    assert "senior architect" in out


# -- ref (seam #6 dispatch) ----------------------------------------------

def test_ref_collection_legacy_2tuple():
    out = _render(preview.preview_for("ref", ("docs-ref", "collection-abc")))
    assert "collection" in out
    assert "collection-abc" in out


def test_ref_collection_3tuple():
    out = _render(preview.preview_for("ref", ("docs-ref", "collection", "col-9")))
    assert "collection" in out and "col-9" in out


def test_ref_bookmark_with_saved_turn():
    payload = ("my-mark", "bookmark", {"body": "## recalled\n\nthe saved turn text"})
    out = _render(preview.preview_for("ref", payload))
    assert "bookmark" in out
    assert "the saved turn text" in out


def test_ref_bookmark_identifier_only_shows_card():
    out = _render(preview.preview_for("ref", ("my-mark", "bookmark", "persona:my-mark")))
    assert "bookmark" in out
    assert "persona:my-mark" in out


def test_ref_name_resolved_against_state():
    state = SimpleNamespace(attached_refs=[("known", "col-77")])
    out = _render(preview.preview_for("ref", "known", state=state))
    assert "col-77" in out


# -- image (dormant stub) -------------------------------------------------

def test_image_preview_missing_path_shows_path_note(tmp_path):
    p = tmp_path / "shot.png"
    out = _render(preview.preview_for("image", str(p)))
    # No chafa / no real image bytes → falls back to the path note (the stub).
    assert "shot.png" in out
