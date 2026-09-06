"""Shared YAML-ish frontmatter parser for markdown files.

Used by plugins (machine-readable manifest) and personas (loadout). Kept here,
not in plugin.py, so persona.py can use it without importing the plugin layer.
"""

from __future__ import annotations


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML-ish frontmatter from a markdown file.

    Supports the subset we need: `key: value` strings, inline lists
    (`key: [a, b, c]`), multi-line lists (`key:\\n  - a\\n  - b`), and block
    scalars (`key: >` folded / `key: |` literal, with the following more-indented
    lines as the value).

    Returns `(metadata_dict, body)`. If no frontmatter is present, returns
    `({}, full_text)`. Malformed frontmatter falls back to empty metadata.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ({}, text)

    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return ({}, text)  # unterminated, treat as no frontmatter

    fm_lines = lines[1:end]
    body = "\n".join(lines[end + 1:])

    metadata: dict = {}
    current_list: list | None = None

    i = 0
    total = len(fm_lines)
    while i < total:
        raw = fm_lines[i]
        stripped = raw.lstrip()
        indent = len(raw) - len(stripped)
        i += 1

        if not stripped or stripped.startswith("#"):
            continue

        # List item — must be inside a multi-line list context.
        if stripped.startswith("- "):
            if current_list is None:
                continue
            current_list.append(stripped[2:].strip())
            continue

        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()

        # Block scalar — `key: >` (folded) or `key: |` (literal), optionally with
        # chomping/indent indicators (>-, |-, |2, …). Consume the following
        # more-indented (or blank) lines as the value.
        if value and value[0] in ">|" and not value.rstrip(">|+-0123456789"):
            folded = value[0] == ">"
            block: list[str] = []
            while i < total:
                bl = fm_lines[i]
                if bl.strip() and (len(bl) - len(bl.lstrip())) <= indent:
                    break  # a non-blank line at/under the key's indent ends it
                block.append(bl)
                i += 1
            metadata[key] = _join_block(block, folded=folded)
            current_list = None
            continue

        if not value:
            # Multi-line value — start a list, attach it to the key.
            current_list = []
            metadata[key] = current_list
            continue

        if value.startswith("[") and value.endswith("]"):
            # Inline list: `[a, b, c]`.
            inner = value[1:-1]
            metadata[key] = [item.strip() for item in inner.split(",") if item.strip()]
            current_list = None
            continue

        metadata[key] = value
        current_list = None

    return (metadata, body.lstrip("\n"))


def _join_block(block: list[str], *, folded: bool) -> str:
    """Assemble a YAML block scalar. `folded` (`>`) joins lines into one
    space-separated string; literal (`|`) preserves line breaks. Common leading
    indentation is stripped and trailing blank lines dropped (chomping ignored)."""
    while block and not block[-1].strip():
        block.pop()
    if not block:
        return ""
    indents = [len(ln) - len(ln.lstrip()) for ln in block if ln.strip()]
    common = min(indents) if indents else 0
    dedented = [ln[common:] if len(ln) >= common else ln.strip() for ln in block]
    if folded:
        return " ".join(seg.strip() for seg in dedented if seg.strip())
    return "\n".join(dedented)
