"""The content-class (`type`) facet — derive a Node's semantic class, distinct from kind."""

from __future__ import annotations

from xlii.addressing import (
    Node,
    classify,
    content_types,
    register_content_type,
    unregister_content_type,
)


def _leaf(name: str, **extra) -> Node:
    return Node(address=f"file://{name}", name=name, kind="leaf", extra=extra)


def _dir(name: str) -> Node:
    return Node(address=f"file://{name}", name=name, kind="container")


def test_image_by_extension_case_insensitive():
    assert classify(_leaf("pic.PNG")) == "image"
    assert classify(_leaf("a.jpeg")) == "image"
    assert classify(_leaf("logo.svg")) == "image"


def test_doc_by_extension():
    assert classify(_leaf("README.md")) == "doc"
    assert classify(_leaf("spec.pdf")) == "pdf"


def test_container_defaults_to_dir():
    assert classify(_dir("src")) == "dir"


def test_unknown_leaf_defaults_to_file():
    assert classify(_leaf("blob.bin")) == "file"
    assert classify(_leaf("Makefile")) == "file"


def test_extra_type_overrides_derivation():
    # a provider that already knows its class (e.g. a future mark://) wins over extension.
    assert classify(Node(address="mark://x", name="x.md", kind="leaf", extra={"type": "mark"})) == "mark"


def test_builtins_are_registered():
    assert {"image", "pdf", "doc"} <= set(content_types())


def test_register_custom_type_then_clean_up():
    register_content_type("notebook", lambda n: n.name.endswith(".ipynb"))
    try:
        assert "notebook" in content_types()
        assert classify(_leaf("analysis.ipynb")) == "notebook"
    finally:
        unregister_content_type("notebook")
    assert "notebook" not in content_types()  # registry restored — no leak across tests


def test_first_true_outranks_builtins():
    # a scheme/name rule that should win over the extension guess.
    register_content_type("screenshot", lambda n: n.name.startswith("Screenshot"), first=True)
    try:
        assert classify(_leaf("Screenshot.png")) == "screenshot"  # not "image"
    finally:
        unregister_content_type("screenshot")
    assert classify(_leaf("Screenshot.png")) == "image"  # back to the built-in
