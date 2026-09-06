"""Render-prep transforms for plugin schema output (the-fold Vector B follow-up).

A CLOSED, finite vocabulary — deliberately NOT an expression language. Each
transform is a named op that reshapes a parsed JSON body BEFORE the
deterministic renderer runs, so a stock plugin's RAW upstream body can match its
renderer args. Ops apply in order; a raising or unknown op is skipped (fail-safe,
mirroring the renderers' degrade contract — a transform bug never blanks or
raises the call).

Why this exists: stock `output_renderer_args` were authored against the
model-EXTRACTED shape documented in each action's `output_schema`, not the raw
body. These ops bridge the two deterministically, no model in the loop, so a
read-only stock plugin can declare `output: schema` and light up the `/get`
zero-token fast path.

The op set (each optionally scoped to a `list:` path — absent = the root object):
    rename       {from, to, list?}          copy a (possibly nested) field to a new flat name
    derive       {to, template, list?}      set a new field from a {field} template over the item
    lift         {from, list?}              merge a nested object's keys up into its parent
    zip          {from:[paths], names:[], into}   zip parallel arrays into a list of row-objects
    flatten_map  {from?, into, key_as, value_as}  a {k:v} object -> list of {key_as:k, value_as:v}
    cast         {fields:[], to, strip?, list?}    coerce values (number|string), stripping chars
    concat       {from:[], to, sep?, list?}  join fields into one
    first_of     {from:[paths], to, list?}   first non-empty value

Paths accept a dotted string ("a.b.0.c") OR an explicit segment list
(["Global Quote", "05. price"]) — the list form is the escape hatch for
literal-dot / space keys, so the addressing helper needs no mini-grammar.
"""
from __future__ import annotations

from typing import Any, Optional


# --------------------------------------------------------------------------- #
#  Path addressing — dotted string or explicit segment list
# --------------------------------------------------------------------------- #

def _segments(path: Any) -> list[str]:
    if isinstance(path, (list, tuple)):
        return [str(p) for p in path]
    return str(path).split(".")


def _get(obj: Any, path: Any) -> Any:
    """Navigate ``obj`` by a dotted string or a segment list. Returns None on any
    miss (a falsy-but-present value like 0 or "" is preserved)."""
    cur = obj
    for part in _segments(path):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def _scope_items(body: Any, spec: dict) -> list:
    """The objects an op applies to: ``list:`` → that list's items; absent → the
    root object itself (as a one-item list)."""
    lk = spec.get("list")
    if lk:
        target = _get(body, lk)
        return target if isinstance(target, list) else []
    return [body] if isinstance(body, dict) else []


# --------------------------------------------------------------------------- #
#  Small numeric/scalar helpers (kept local to avoid a plugin_call import cycle)
# --------------------------------------------------------------------------- #

def _fmt_scalar(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        import json
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _to_number(v: Any, strip: list) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    for ch in strip:
        s = s.replace(str(ch), "")
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
#  The ops
# --------------------------------------------------------------------------- #

def _op_rename(body: Any, spec: dict) -> Any:
    src, dst = spec.get("from"), spec.get("to")
    if src is None or dst is None:
        return body
    for item in _scope_items(body, spec):
        if isinstance(item, dict):
            val = _get(item, src)
            if val is not None:
                item[str(dst)] = val
    return body


def _op_derive(body: Any, spec: dict) -> Any:
    to, tmpl = spec.get("to"), spec.get("template")
    if not to or tmpl is None:
        return body
    from xlii.plugin_call import _apply_template  # lazy: avoid import cycle
    for item in _scope_items(body, spec):
        if isinstance(item, dict):
            item[str(to)] = _apply_template(str(tmpl), item)
    return body


def _op_lift(body: Any, spec: dict) -> Any:
    src = spec.get("from")
    if src is None:
        return body
    for item in _scope_items(body, spec):
        if isinstance(item, dict):
            nested = _get(item, src)
            if isinstance(nested, dict):
                for k, v in nested.items():
                    item.setdefault(k, v)
    return body


def _op_zip(body: Any, spec: dict) -> Any:
    froms, names, into = spec.get("from"), spec.get("names"), spec.get("into")
    if not (isinstance(froms, list) and isinstance(names, list) and into):
        return body
    arrays = [_get(body, p) for p in froms]
    arrays = [a if isinstance(a, list) else [] for a in arrays]
    n = min((len(a) for a in arrays), default=0)
    cols = min(len(names), len(arrays))
    rows = [{str(names[j]): arrays[j][i] for j in range(cols)} for i in range(n)]
    if isinstance(body, dict):
        body[str(into)] = rows
        return body
    return {str(into): rows}


def _op_flatten_map(body: Any, spec: dict) -> Any:
    src, into = spec.get("from"), spec.get("into")
    if not into:
        return body
    key_as = str(spec.get("key_as", "key"))
    value_as = str(spec.get("value_as", "value"))
    obj = body if src is None else _get(body, src)
    if not isinstance(obj, dict):
        return body
    rows = [{key_as: k, value_as: v} for k, v in obj.items()]
    if isinstance(body, dict):
        body[str(into)] = rows
        return body
    return {str(into): rows}


def _op_cast(body: Any, spec: dict) -> Any:
    fields = spec.get("fields") or []
    to = str(spec.get("to", "number"))
    strip = spec.get("strip") or []
    if not isinstance(strip, list):
        strip = [strip]
    for item in _scope_items(body, spec):
        if not isinstance(item, dict):
            continue
        for f in fields:
            val = item[f] if f in item else _get(item, f)
            if val is None:
                continue
            if to == "number":
                num = _to_number(val, strip)
                if num is not None:
                    item[str(f)] = int(num) if num.is_integer() else num
            else:
                item[str(f)] = _fmt_scalar(val)
    return body


def _op_concat(body: Any, spec: dict) -> Any:
    froms, to = spec.get("from") or [], spec.get("to")
    sep = str(spec.get("sep", " "))
    if not to:
        return body
    for item in _scope_items(body, spec):
        if not isinstance(item, dict):
            continue
        parts = []
        for f in froms:
            s = _fmt_scalar(_get(item, f))
            if s:
                parts.append(s)
        item[str(to)] = sep.join(parts)
    return body


def _op_first_of(body: Any, spec: dict) -> Any:
    froms, to = spec.get("from") or [], spec.get("to")
    if not to:
        return body
    for item in _scope_items(body, spec):
        if not isinstance(item, dict):
            continue
        for f in froms:
            v = _get(item, f)
            if v not in (None, "", [], {}):
                item[str(to)] = v
                break
    return body


def _op_scale(body: Any, spec: dict) -> Any:
    """Multiply numeric field(s) by a constant (e.g. a 0.0052 dividend fraction →
    0.52 percent via ``by: 100``). Coerces string-numbers first; a non-numeric
    value is left untouched."""
    fields = spec.get("fields") or []
    try:
        factor = float(spec.get("by", 1))
    except (TypeError, ValueError):
        return body
    for item in _scope_items(body, spec):
        if not isinstance(item, dict):
            continue
        for f in fields:
            v = item[f] if f in item else _get(item, f)
            n = _to_number(v, []) if v is not None else None
            if n is not None:
                item[str(f)] = n * factor
    return body


def _op_code_map(body: Any, spec: dict) -> Any:
    """Map an enum/code field to a label via a fixed lookup table (e.g. WMO
    weather codes → English). Table keys may be ints or strings in YAML; a code
    is matched by identity or its string form. Misses → ``default``."""
    field_, to = spec.get("field"), spec.get("to")
    table = spec.get("table")
    if not field_ or not to or not isinstance(table, dict):
        return body
    default = spec.get("default", "")
    for item in _scope_items(body, spec):
        if not isinstance(item, dict):
            continue
        code = _get(item, field_)
        if code is None:
            continue
        if code in table:
            item[str(to)] = table[code]
        elif str(code) in table:
            item[str(to)] = table[str(code)]
        else:
            item[str(to)] = default
    return body


_OPS = {
    "rename": _op_rename,
    "derive": _op_derive,
    "lift": _op_lift,
    "zip": _op_zip,
    "flatten_map": _op_flatten_map,
    "cast": _op_cast,
    "concat": _op_concat,
    "first_of": _op_first_of,
    "scale": _op_scale,
    "code_map": _op_code_map,
}


def apply_transforms(body: Any, transforms: Any) -> Any:
    """Apply an ordered list of ``{op: name, …}`` transforms to a parsed body.
    Unknown/malformed ops and any op that raises are skipped — the body is
    returned as-is in the worst case, so a bad transform degrades to today's
    raw-body behaviour rather than blanking or raising."""
    if not transforms or not isinstance(transforms, list):
        return body
    for t in transforms:
        if not isinstance(t, dict):
            continue
        fn = _OPS.get(str(t.get("op", "")))
        if fn is None:
            continue
        try:
            body = fn(body, t)
        except Exception:  # noqa: BLE001 — a transform bug must never break the call
            continue
    return body


# --------------------------------------------------------------------------- #
#  Input adapter — the one non-JSON stock case (google-news RSS)
# --------------------------------------------------------------------------- #

def unwrap_article_url(url: str) -> str:
    """Turn a Google News redirect / CBMi blob into the publisher URL.

    ``news.google.com/rss/articles/CBMi…`` encodes the real article in the
    path. Dumping the redirect is why a schema-aware news plugin still looks
    like garbage. Falls back to *url* when nothing decodes.
    """
    raw = (url or "").strip()
    if not raw:
        return raw
    from urllib.parse import parse_qs, unquote, urlparse

    parsed = urlparse(raw)
    qs = parse_qs(parsed.query)
    for key in ("url", "q"):
        for cand in qs.get(key) or []:
            c = unquote(cand)
            if c.startswith("http") and "news.google.com" not in c:
                return c
    host = (parsed.netloc or "").lower()
    if "news.google.com" not in host:
        return raw
    import base64
    import re

    m = re.search(r"/(?:rss/)?articles/([^/?]+)", parsed.path or "")
    if not m:
        return raw
    blob = m.group(1)
    pad = "=" * (-len(blob) % 4)
    try:
        data = base64.urlsafe_b64decode(blob + pad)
    except Exception:
        return raw
    found = re.findall(rb"https?://[^\x00-\x1f\s\"'<>\\]+", data)
    for hit in reversed(found):
        u = hit.decode("utf-8", errors="replace").rstrip(").,]\"'")
        if u.startswith("http") and "news.google.com" not in u:
            return u
    return raw


def _strip_title_source(title: str, source: str) -> str:
    t = (title or "").strip()
    s = (source or "").strip()
    if not t or not s:
        return t
    for sep in (" - ", " — ", " | "):
        if t.endswith(sep + s):
            return t[: -len(sep + s)].rstrip()
    return t


def parse_rss(text: str) -> Optional[dict]:
    """Minimal RSS/Atom → ``{items:[{title, link, published, source}]}`` using
    stdlib only. Returns None on a parse failure so the caller falls back to the
    raw body. A named adapter, not a general XML mapper.

    Google News links are unwrapped to the publisher URL; title suffixes
    matching ``source`` are stripped.
    """
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    items: list[dict] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1].lower()
        if tag not in ("item", "entry"):
            continue
        row: dict = {}
        for child in el:
            ctag = child.tag.rsplit("}", 1)[-1].lower()
            text_val = (child.text or "").strip()
            if ctag == "title":
                row["title"] = text_val
            elif ctag == "link":
                row["link"] = (child.get("href") or child.text or "").strip()
            elif ctag in ("pubdate", "published", "updated"):
                row.setdefault("published", text_val)
            elif ctag == "source":
                row["source"] = text_val
                href = (child.get("url") or child.get("href") or "").strip()
                if href:
                    row["source_url"] = href
        if row.get("link"):
            row["link"] = unwrap_article_url(row["link"])
        if row.get("title"):
            row["title"] = _strip_title_source(row["title"], row.get("source") or "")
        items.append(row)
        if len(items) >= 20:
            break
    return {"items": items}
