"""HTTP execution for plugin manifest actions (L2 ``plugin_call`` tool).

Beyond the raw HTTP call (:func:`execute_http_action`), this module owns the
*output-mode* resolution the-fold Vector B rung 2 adds: an action declares
``raw | schema | interpret`` and :func:`invoke_action` returns a structured
:class:`ActionResult` that the caller (direct ``/plugin call``, a pipe step, or
the agent tool) honours identically —

* ``raw``       — the body is the user's; the model gets :meth:`ActionResult.receipt`.
* ``schema``    — the body is rendered deterministically via ``output_renderer``.
* ``interpret`` — the body returns to the model (today's behaviour).

The renderers are pure string functions with NO Rich dependency, so the same
rendering serves the REPL, a pipe carry, and a headless test. Untrusted API text
is never interpolated into console markup — callers print the returned string
through ``Text()``.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from xlii.url_safe import validate_url_host
from xlii.plugin_manifest import (
    OUTPUT_INTERPRET,
    OUTPUT_RAW,
    OUTPUT_SCHEMA,
    ActionSpec,
    ParamSpec,
    parse_manifest,
)

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# Path-segment placeholders in action URL templates: ``/page/{title}``.
# Params consumed here are stripped from the query string / POST body.
_PATH_PARAM_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_WHOLE_URL_PARAM_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


def _url_origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port is None:
        if parsed.scheme == "http":
            port = 80
        elif parsed.scheme == "https":
            port = 443
    return parsed.scheme.lower(), host, port


def _same_origin(a: str, b: str) -> bool:
    return _url_origin(a) == _url_origin(b)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Preserve manifest headers only across same-origin redirects.

    Plugin manifests commonly attach vault-expanded API tokens in headers, and
    urllib's default redirect handling carries non-content headers to the next
    request. A compromised service or proxy must not be able to redirect a
    credentialed plugin call to a different origin and receive those headers.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and not _same_origin(req.full_url, newurl):
            redirected.headers.clear()
            redirected.unredirected_hdrs.clear()
        return redirected


def _open_plugin_url(req: urllib.request.Request, timeout: int, *, check_host: bool):
    """Fetch with header-stripping redirects, and optional SSRF host checks."""
    if check_host:
        hop_err = validate_url_host(req.full_url)
        if hop_err:
            raise urllib.error.URLError(hop_err)

    class _Handler(_SafeRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
            if check_host:
                nxt = validate_url_host(newurl)
                if nxt:
                    raise urllib.error.URLError(nxt)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(_Handler)
    return opener.open(req, timeout=timeout)


def _expand_env(text: str, env: dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        key = m.group(1)
        return env.get(key, os.environ.get(key, ""))
    return _ENV_RE.sub(repl, text)


def _apply_path_params(url: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Substitute ``{name}`` placeholders from *params*; return (url, remaining).

    When the entire URL template is a single placeholder (e.g. ``{feed_url}``
    for a user-supplied RSS endpoint), the value is inserted as-is so schemes
    and query strings stay intact. Segment-style placeholders are percent-
    encoded so titles with spaces / slashes stay valid path components.
    """
    keys = _PATH_PARAM_RE.findall(url)
    if not keys:
        return url, dict(params)

    # Whole-URL substitution: ``url: "{feed_url}"`` — value is a complete URL.
    if _WHOLE_URL_PARAM_RE.fullmatch(url.strip()):
        key = keys[0]
        if key in params and params[key] is not None:
            rest = {k: v for k, v in params.items() if k != key}
            return str(params[key]), rest
        return url, dict(params)

    used: set[str] = set()

    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key in params and params[key] is not None:
            used.add(key)
            return urllib.parse.quote(str(params[key]), safe="")
        return m.group(0)

    out = _PATH_PARAM_RE.sub(repl, url)
    rest = {k: v for k, v in params.items() if k not in used}
    return out, rest


def _build_url(
    action: ActionSpec, params: dict[str, Any], env: dict[str, str]
) -> tuple[str, dict[str, Any]]:
    """Expand env + path params; return ``(final_url, remaining_params)``.

    Remaining params become the GET query string or the POST body. Path-
    consumed names are not double-sent.
    """
    base = _expand_env(action.url, env)
    base, rest = _apply_path_params(base, params)
    if action.method == "GET":
        qs = urllib.parse.urlencode({k: v for k, v in rest.items() if v is not None})
        if qs:
            sep = "&" if "?" in base else "?"
            return f"{base}{sep}{qs}", rest
        return base, rest
    return base, rest


def _host_is_env_controlled(url_template: str) -> bool:
    """True when the manifest URL's host comes from ``${ENV}``, not a static literal.

    ``${ARCHIVEBOX_BASE}/path`` and ``https://${API_HOST}/path`` are user/vault
    picks; ``http://169.254.169.254/${X}`` is not — the host is still static.
    """
    template = (url_template or "").strip()
    if not template:
        return False
    if template.startswith("${"):
        return True
    parsed = urllib.parse.urlparse(template)
    if "${" in (parsed.netloc or ""):
        return True
    return False


def _validate_action_url(url: str, *, user_supplied_full_url: bool,
                         skip_host_check: bool = False) -> str:
    """Return an error when a manifest action resolves to an unsafe fetch URL.

    Host checks run for every URL except vault-controlled templates
    (``${ENV}`` in the original template — the user picked the host).
    User-supplied full URLs (RSS ``{feed_url}``) always check.
    """
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return f"unsupported url scheme: {scheme or '(none)'}"
    if user_supplied_full_url or not skip_host_check:
        return validate_url_host(url) or ""
    return ""


def _wants_json_body(headers: dict[str, str]) -> bool:
    """True when the manifest already declared a JSON content type.

    Flat string params used to be urlencoded even with
    ``Content-Type: application/json`` — Bluesky then 400s
    ``Unexpected token 'i'`` on ``identifier=…``.
    """
    for key, val in headers.items():
        if key.lower() == "content-type" and "json" in str(val).lower():
            return True
    return False


def _subst_bound(val: Any, bound: dict[str, Any], env: dict[str, str]) -> Any:
    """Replace ``$name`` tokens and ``${ENV}`` in compose maps."""
    if isinstance(val, str):
        if val.startswith("$") and len(val) > 1 and "{" not in val:
            key = val[1:]
            if key in bound:
                return bound[key]
        return _expand_env(val, env)
    if isinstance(val, dict):
        return {k: _subst_bound(v, bound, env) for k, v in val.items()}
    if isinstance(val, list):
        return [_subst_bound(v, bound, env) for v in val]
    return val


def _run_compose(
    action: ActionSpec,
    params: dict[str, Any],
    *,
    env: dict[str, str],
    timeout: int,
) -> tuple[int, str, str]:
    """Run ``compose.steps`` then POST the action URL with ``compose.body``.

    Closed vocab: each step is GET/POST ``url`` + ``query`` + optional
    ``headers`` + ``take: {bound: dotted.path}`` + ``skip_if: bound_key``.
    ``$name`` in query/body pulls from form params or prior ``take``.
    """
    spec = getattr(action, "compose", None) or {}
    bound: dict[str, Any] = dict(params)
    to = str(bound.get("to") or "").strip().lstrip("@")
    if to:
        bound["to"] = to
        if to.startswith("did:"):
            bound.setdefault("did", to)

    from xlii.plugin_form import extract_store_value

    for step in spec.get("steps") or []:
        if not isinstance(step, dict):
            continue
        skip = str(step.get("skip_if") or "").strip()
        if skip and bound.get(skip):
            continue
        url = _expand_env(str(step.get("url") or ""), env)
        if not url:
            return 0, "", "compose step has no url"
        method = str(step.get("method") or "GET")
        method = "exec" if method.lower() == "exec" else method.upper()
        headers = {}
        raw_headers = step.get("headers") or {}
        if isinstance(raw_headers, dict):
            headers = {str(k): _expand_env(str(v), env) for k, v in raw_headers.items()}
        query: dict[str, Any] = {}
        raw_query = step.get("query") or {}
        if isinstance(raw_query, dict):
            query = {str(k): _subst_bound(v, bound, env) for k, v in raw_query.items()}
        step_action = ActionSpec(
            id=str(step.get("id") or "step"),
            description="",
            method=method,
            url=url,
            headers=headers,
            params={k: ParamSpec(name=k) for k in query},
        )
        code, body, err = execute_http_action(
            step_action, query, env=env, timeout=timeout
        )
        if err or not (200 <= code < 300):
            return code, body, err or f"compose step HTTP {code}"
        try:
            parsed = json.loads(body) if body else {}
        except (ValueError, TypeError):
            parsed = {}
        take = step.get("take") or {}
        if isinstance(take, dict):
            for dest, path in take.items():
                got = extract_store_value(parsed, str(path))
                if got is None:
                    from xlii.plugin_form import _dotted
                    raw = _dotted(parsed, str(path))
                    got = None if raw is None else str(raw)
                if got:
                    bound[str(dest)] = got

    raw_body = spec.get("body")
    if isinstance(raw_body, dict):
        final = _subst_bound(raw_body, bound, env)
    else:
        final = {k: v for k, v in bound.items() if k in action.params}
    send = ActionSpec(
        id=action.id,
        description=action.description,
        method=action.method,
        url=action.url,
        headers=action.headers,
        params={
            k: ParamSpec(name=k) for k in (final if isinstance(final, dict) else {})
        },
    )
    return execute_http_action(send, final if isinstance(final, dict) else {}, env=env, timeout=timeout)


def execute_http_action(
    action: ActionSpec,
    params: dict[str, Any],
    *,
    env: Optional[dict[str, str]] = None,
    timeout: int = 30,
) -> tuple[int, str, str]:
    """Run one HTTP manifest action. Returns (status_code, body_text, error_note)."""
    if action.is_exec:
        return 0, "", "exec actions are not supported by plugin_call yet"
    if getattr(action, "is_store_only", False):
        return 200, "", ""
    if not action.url:
        return 0, "", "action has no url"

    env = {**os.environ, **(env or {})}
    merged, errors = action.resolve_params(params)
    if errors:
        return 0, "", "; ".join(errors)

    base_template = _expand_env(action.url, env)
    user_supplied_full_url = bool(_WHOLE_URL_PARAM_RE.fullmatch(base_template.strip()))
    # Vault-controlled hosts (`${ARCHIVEBOX_BASE}`) are the user's pick; static
    # manifest URLs and user-supplied full URLs still get the SSRF host check.
    skip_host_check = _host_is_env_controlled(action.url) and not user_supplied_full_url
    url, body_params = _build_url(action, merged, env)
    url_error = _validate_action_url(
        url,
        user_supplied_full_url=user_supplied_full_url,
        skip_host_check=skip_host_check,
    )
    if url_error:
        return 0, "", url_error
    headers = {k: _expand_env(v, env) for k, v in action.headers.items()}

    data = None
    if action.method not in ("GET", "HEAD"):
        wants_json = _wants_json_body(headers) or any(
            isinstance(v, (dict, list)) for v in body_params.values()
        )
        if wants_json:
            data = json.dumps(body_params).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        elif body_params:
            data = urllib.parse.urlencode(body_params).encode("utf-8")
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=action.method,
    )
    try:
        check_host = user_supplied_full_url or not skip_host_check
        with _open_plugin_url(req, timeout, check_host=check_host) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            code = getattr(resp, "status", None) or getattr(resp, "code", 0) or 0
            return int(code), body, ""
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, err_body[:8000], f"HTTP {e.code}"
    except TimeoutError:
        return 0, "", f"timeout after {timeout}s"
    except urllib.error.URLError as e:
        return 0, "", f"network error: {e.reason}"


def call_plugin_action(
    plugin_id: str,
    raw_markdown: str,
    action_id: str,
    params: dict[str, Any],
    *,
    env: Optional[dict[str, str]] = None,
    timeout: int = 30,
) -> str:
    """Parse manifest from plugin text and invoke one action. Raises ValueError on config errors."""
    manifest = parse_manifest(raw_markdown)
    if manifest is None:
        raise ValueError(f"plugin {plugin_id!r} has no structured actions manifest")
    action = manifest.get_action(action_id)
    if action is None:
        raise ValueError(
            f"unknown action {action_id!r} for plugin {plugin_id!r}; "
            f"available: {', '.join(manifest.action_ids)}"
        )
    code, body, err = execute_http_action(action, params, env=env, timeout=timeout)
    if err and not body:
        raise RuntimeError(err)
    prefix = f"[{plugin_id}.{action_id} HTTP {code}]"
    if action.post_call_instruction:
        return f"{prefix}\n{body}\n\n{action.post_call_instruction}"
    return f"{prefix}\n{body}"


# --------------------------------------------------------------------------- #
#  CLI parsing — the `/plugin call <plugin>.<action> k=v …` surface (rung 1)
# --------------------------------------------------------------------------- #

def split_plugin_action(spec: str) -> tuple[str, str]:
    """Split ``weather.current`` → ``("weather", "current")``. Raises ValueError
    on a missing dot so the user gets a usage nudge, not a silent misfire. The
    plugin id may itself contain dots/dashes (``alpha-vantage``), so we split on
    the LAST dot — the action id is a bare token."""
    spec = spec.strip()
    if "." not in spec:
        raise ValueError(
            f"expected <plugin>.<action> (e.g. open-meteo.geocode), got {spec!r}"
        )
    plugin_id, _, action_id = spec.rpartition(".")
    if not plugin_id or not action_id:
        raise ValueError(f"expected <plugin>.<action>, got {spec!r}")
    return plugin_id, action_id


def split_call_tokens(s: str) -> list[str]:
    """Split a call tail, honouring ``title="ben franklin"`` quotes."""
    import shlex

    raw = (s or "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw, posix=True)
    except ValueError:
        return raw.split()


def parse_kv(tokens: list[str]) -> dict[str, Any]:
    """Parse ``key=value`` tokens into a params dict. Values are literal strings —
    which is exactly what every real plugin's request params want (see the
    param-richness decision in the-fold Decision log) — EXCEPT a value that opens
    with ``{`` or ``[`` is parsed as JSON, so a nested-object param (Bluesky
    ``send_message message={"text":"hi"}``) reaches ``resolve_params`` as a real
    dict. A bare ``true``/``3`` stays the literal string it was typed as: coercing
    it to Python ``True``/``3`` would re-stringify to ``"True"``/``"3"`` and
    diverge from the literal the API expects.

    A token without ``=`` is appended to the previous value so
    ``title=ben franklin`` (no quotes) still binds.
    """
    params: dict[str, Any] = {}
    last_key = ""
    for tok in tokens:
        if "=" not in tok:
            if last_key and isinstance(params.get(last_key), str):
                params[last_key] = f"{params[last_key]} {tok}".strip()
                continue
            raise ValueError(f"bad param {tok!r} — expected key=value")
        key, _, value = tok.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"bad param {tok!r} — empty key")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if value[:1] in ("{", "["):
            try:
                params[key] = json.loads(value)
                last_key = key
                continue
            except ValueError:
                pass  # not valid JSON — keep the literal string
        params[key] = value
        last_key = key
    return params


def required_param_names(action: ActionSpec) -> list[str]:
    """User-supplied params that must be present (no const, no default)."""
    return [
        spec.name
        for spec in action.params.values()
        if spec.const is None and spec.required and spec.default is None
    ]


def missing_required(action: ActionSpec, params: dict[str, Any]) -> list[str]:
    """Required names that are absent or blank in *params*."""
    out: list[str] = []
    for name in required_param_names(action):
        if name not in params:
            out.append(name)
            continue
        val = params[name]
        if val is None or (isinstance(val, str) and not val.strip()):
            out.append(name)
    return out


def seed_call_line(plugin_id: str, action_id: str, action: ActionSpec) -> str:
    """A `/plugin call` line with empty `name=` holes for required params."""
    bits = [f"/plugin call {plugin_id}.{action_id}"]
    for name in required_param_names(action):
        bits.append(f"{name}=")
    return " ".join(bits)


def parse_call_line(text: str) -> tuple[str, str, dict[str, Any]] | None:
    """Parse a face-seeded call: `/plugin call id.action k=v` or `__plugin_call__:id:action k=v`.

    Returns ``None`` when *text* is not a plugin-call line.
    """
    t = (text or "").strip()
    if t.startswith("__plugin_call__:"):
        rest = t[len("__plugin_call__:"):].strip()
        plugin_id, sep, rest2 = rest.partition(":")
        if not sep or not plugin_id:
            return None
        action_id, _, kv = rest2.partition(" ")
        action_id = action_id.strip()
        if not action_id:
            return None
        tokens = split_call_tokens(kv) if kv.strip() else []
        return plugin_id.strip(), action_id, parse_kv(tokens) if tokens else {}
    if t.startswith("/plugin call"):
        rest = t[len("/plugin call"):].strip()
        if not rest:
            raise ValueError("usage: /plugin call <plugin>.<action> [k=v …]")
        parts = split_call_tokens(rest)
        plugin_id, action_id = split_plugin_action(parts[0])
        return plugin_id, action_id, parse_kv(parts[1:]) if len(parts) > 1 else {}
    return None


# --------------------------------------------------------------------------- #
#  Deterministic output rendering (rung 2: schema mode)
# --------------------------------------------------------------------------- #
#
# Renderers turn a parsed JSON body into plain text WITHOUT a model. They are the
# deterministic half of "schema" mode: where the API body already matches the
# renderer's expected shape (a top-level list for table/message_list, matching
# keys for a template) it renders exactly; where it doesn't, it degrades to a
# note + the raw body rather than raising — the model-constrained forced-schema
# path stays the extraction fallback, never a crash.

def _dotted_get(obj: Any, path: str) -> Any:
    """Navigate ``obj`` by a dotted path — ``a.b`` into dicts, ``items.0.name``
    into lists. Returns None on any miss so a template placeholder degrades to ""
    instead of raising on partial API responses."""
    cur = obj
    for part in str(path).split("."):
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


def _fmt_scalar(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _coerce_number(v: Any) -> Optional[float]:
    """Best-effort numeric coercion for a raw field value — so a numeric format
    spec (`{price:,.2f}`) works on a string-number API (Alpha Vantage returns
    `"190.50"`, CoinGecko a real float). Strips a trailing ``%`` and thousands
    commas. Returns None when the value isn't numeric-looking."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip().replace(",", "").rstrip("%").strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


class _Field:
    """A ``str.format_map`` value that formats the RAW field value rather than a
    pre-stringified one, so numeric format specs actually render. Degrades
    PER-FIELD (never voids the whole template): ``None`` → "", dict/list → JSON,
    a numeric spec on a numeric-looking string coerces then formats, and any
    residual format error falls back to the plain scalar. This is the fix for
    the old "stringify-then-format" path where `{p:,.2f}` raised ValueError and
    dropped the entire template to its literal form."""

    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value

    def __format__(self, spec: str) -> str:
        v = self.value
        if v is None:
            return ""
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        if not spec:
            return _fmt_scalar(v)
        try:
            return format(v, spec)
        except (ValueError, TypeError):
            coerced = _coerce_number(v)
            if coerced is not None:
                try:
                    return format(coerced, spec)
                except (ValueError, TypeError):
                    # The spec doesn't apply to the coerced number -- fall through to the default formatting
                    # below.
                    pass
            return _fmt_scalar(v)

    def __str__(self) -> str:  # bare {field} paths that stringify without a spec
        return _fmt_scalar(self.value)


class _TemplateFields:
    """A str.format_map mapping that yields a `_Field` for any missing/dotted
    field, so a single bad placeholder never blows up the whole render (partial
    API bodies) and numeric specs format the raw value."""

    __slots__ = ("_body",)

    def __init__(self, body: Any) -> None:
        self._body = body

    def __getitem__(self, key: str) -> "_Field":
        if isinstance(self._body, dict) and "." not in key and key in self._body:
            return _Field(self._body[key])
        return _Field(_dotted_get(self._body, key))


def _apply_template(template: str, body: Any) -> str:
    try:
        return template.format_map(_TemplateFields(body))
    except (ValueError, IndexError, KeyError):
        # A malformed template (stray brace) — show it literally rather than crash.
        return template


def _get_list(body: Any, list_key: str) -> list:
    target = _dotted_get(body, list_key) if list_key else body
    if isinstance(target, list):
        return target
    return []


def _render_text_template(body: Any, args: dict) -> str:
    template = str(args.get("template", ""))
    return _apply_template(template, body).strip("\n") if template else _fmt_scalar(body)


def _render_paste_command(body: Any, args: dict) -> str:
    parts: list[str] = []
    if args.get("preamble"):
        parts.append(str(args["preamble"]))
    cmd = args.get("command_template")
    if cmd:
        parts.append(_apply_template(str(cmd), body))
    if args.get("footer"):
        parts.append(str(args["footer"]))
    return "\n\n".join(p for p in parts if p).strip("\n")


def _md_link_text(s: str) -> str:
    return (s or "").replace("[", "\\[").replace("]", "\\]")


def _headline_md(item: Any) -> str | None:
    """Markdown bullet when the row has a title + http(s) link — clickable on the face."""
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or "").strip()
    # Only the RSS `link` field — plugins that use `url` keep their template
    # (GDELT etc. still want domain / country in the line).
    link = str(item.get("link") or "").strip()
    if not title or not link.startswith("http"):
        return None
    line = f"- [{_md_link_text(title)}]({link})"
    extra = []
    for key in ("source", "published", "author"):
        val = item.get(key)
        if val:
            extra.append(str(val).strip())
    if extra:
        line += " — " + " · ".join(extra)
    return line


def _render_message_list(body: Any, args: dict) -> str:
    items = _get_list(body, str(args.get("list_key", "")))
    header = str(args.get("header", ""))
    if not items:
        empty = str(args.get("empty_message", "(no results)"))
        return f"{header}\n{empty}".strip("\n") if header else empty
    md_lines = [_headline_md(it) for it in items]
    if md_lines and all(md_lines):
        # Schema already has title+link — render markdown so the face styles
        # it (clickable links) instead of dumping raw redirect URLs.
        head = f"### {header}" if header else ""
        body_text = "\n".join(md_lines)
        return f"{head}\n\n{body_text}".strip("\n") if head else body_text
    tmpl = str(args.get("item_template", "{title}"))
    lines = [_apply_template(tmpl, it) for it in items]
    body_text = "\n".join(lines)
    return f"{header}\n{body_text}".strip("\n") if header else body_text


def _render_table(body: Any, args: dict) -> str:
    items = _get_list(body, str(args.get("list_key", "")))
    header = str(args.get("header", ""))
    columns = args.get("columns") or []
    if not isinstance(columns, list) or not columns:
        return _render_message_list(body, args)
    cols = []
    for c in columns:
        if isinstance(c, dict) and c.get("key"):
            cols.append((str(c["key"]), str(c.get("label", c["key"])),
                         int(c.get("max_width", 0)) or None))
    if not items:
        empty = str(args.get("empty_message", "(no results)"))
        return f"{header}\n{empty}".strip("\n") if header else empty

    def cell(row: Any, key: str, cap: Optional[int]) -> str:
        s = " ".join(_fmt_scalar(_dotted_get(row, key)).split())
        if cap and len(s) > cap:
            s = s[: max(cap - 1, 1)] + "…"
        return s

    rendered_rows = [[cell(it, k, cap) for k, _lbl, cap in cols] for it in items]
    widths = [len(lbl) for _k, lbl, _cap in cols]
    for r in rendered_rows:
        for i, val in enumerate(r):
            widths[i] = max(widths[i], len(val))
    def line(vals: list[str]) -> str:
        return "  ".join(v.ljust(widths[i]) for i, v in enumerate(vals))
    out = []
    if header:
        out.append(header)
    out.append(line([lbl for _k, lbl, _cap in cols]))
    out.append(line(["─" * w for w in widths]))
    out.extend(line(r) for r in rendered_rows)
    return "\n".join(out)


_RENDERERS = {
    "text_template": _render_text_template,
    "paste_command": _render_paste_command,
    "message_list": _render_message_list,
    "table": _render_table,
}


def render_schema_output(action: ActionSpec, body: str) -> str:
    """Render an action's HTTP ``body`` deterministically: parse it (JSON, or RSS
    when ``body_format: rss``), apply the action's ordered ``output_transforms``
    to reshape the raw body into the renderer's expected shape, then dispatch its
    ``output_renderer``. Falls back to the raw body when the body won't parse, the
    renderer is unknown, or rendering yields nothing — schema mode degrades, it
    never blanks or raises."""
    from xlii.plugin_transforms import apply_transforms, parse_rss

    if getattr(action, "body_format", "json") == "rss":
        parsed = parse_rss(body)
        if parsed is None:
            return body
    else:
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            return body
    try:
        parsed = apply_transforms(parsed, getattr(action, "output_transforms", None))
    except Exception:  # noqa: BLE001 — a transform bug must never break the call
        pass
    fn = _RENDERERS.get(action.output_renderer)
    if fn is None:
        return body
    try:
        out = fn(parsed, dict(action.output_renderer_args or {}))
    except Exception:  # noqa: BLE001 — a renderer bug must never break the call
        return body
    return out if out.strip() else body


# --------------------------------------------------------------------------- #
#  Structured invocation — the one path that honours output mode
# --------------------------------------------------------------------------- #

@dataclass
class ActionResult:
    """The outcome of one action, carrying enough to serve every output mode.

    ``user_text`` is what a human sees (rendered for schema, the body for
    raw/interpret); ``model_text`` is what an agent-tool caller returns to the
    model — the full prefixed body for ``interpret`` (today's behaviour), but only
    :attr:`receipt` for ``raw``/``schema`` so an untrusted body never enters model
    context (the per-response prompt-injection surface closes by default)."""

    plugin_id: str
    action_id: str
    mode: str
    code: int
    body: str
    user_text: str
    model_text: str
    error: str = ""
    stored: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error and 200 <= self.code < 300

    @property
    def receipt(self) -> str:
        status = f"HTTP {self.code}" if self.code else "no HTTP response"
        if self.ok:
            return (f"[{self.plugin_id}.{self.action_id} {status}] — "
                    f"{self.mode} mode, output shown to the user (not entered into context)")
        detail = self.error or status
        return f"[{self.plugin_id}.{self.action_id} {status}] — failed: {detail}"


def invoke_action(
    plugin_id: str,
    raw_markdown: str,
    action_id: str,
    params: dict[str, Any],
    *,
    env: Optional[dict[str, str]] = None,
    timeout: int = 30,
) -> ActionResult:
    """Parse the manifest, run one action, and shape its output by the action's
    declared mode. Raises ValueError for config errors (bad plugin/action) so the
    caller can report them; HTTP/network failures come back on the result's
    ``error`` (with any partial body) rather than raising."""
    manifest = parse_manifest(raw_markdown)
    if manifest is None:
        raise ValueError(f"plugin {plugin_id!r} has no structured actions manifest")
    action = manifest.get_action(action_id)
    if action is None:
        raise ValueError(
            f"unknown action {action_id!r} for plugin {plugin_id!r}; "
            f"available: {', '.join(manifest.action_ids)}"
        )
    from xlii.plugin_form import apply_stores

    stored: list[str] = []
    if getattr(action, "is_store_only", False):
        merged, errors = action.resolve_params(params)
        if errors:
            code, body, err = 0, "", "; ".join(errors)
        else:
            try:
                stored = apply_stores(plugin_id, action, merged, None)
            except Exception as e:  # noqa: BLE001 — vault failure is the result
                code, body, err = 0, "", f"vault: {e}"
            else:
                code, body, err = 200, "", ""
        mode = OUTPUT_RAW
        user_text = (
            "stored " + ", ".join(stored) if stored
            else (err or "nothing stored")
        )
    elif getattr(action, "compose", None):
        env_map = {**os.environ, **(env or {})}
        merged, errors = action.resolve_params(params)
        if errors:
            code, body, err = 0, "", "; ".join(errors)
        else:
            code, body, err = _run_compose(
                action, merged, env=env_map, timeout=timeout
            )
        if not err:
            try:
                stored = apply_stores(plugin_id, action, merged, body)
            except Exception as e:  # noqa: BLE001
                err = f"vault: {e}"
        mode = action.output if action.output in (OUTPUT_RAW, OUTPUT_SCHEMA, OUTPUT_INTERPRET) else OUTPUT_INTERPRET
        dest = str((params or {}).get("to") or "").strip()
        if not err and dest:
            user_text = f"Sent to {dest}."
        elif mode == OUTPUT_SCHEMA and not err:
            user_text = render_schema_output(action, body)
        else:
            user_text = body
        if stored:
            note = "stored " + ", ".join(stored)
            user_text = f"{user_text.rstrip()}\n\n{note}" if user_text.strip() else note
    else:
        code, body, err = execute_http_action(action, params, env=env, timeout=timeout)
        if not err:
            try:
                stored = apply_stores(plugin_id, action, params, body)
            except Exception as e:  # noqa: BLE001
                err = f"vault: {e}"
        mode = action.output if action.output in (OUTPUT_RAW, OUTPUT_SCHEMA, OUTPUT_INTERPRET) else OUTPUT_INTERPRET
        if mode == OUTPUT_SCHEMA and not err:
            user_text = render_schema_output(action, body)
        else:
            user_text = body
        if stored:
            note = "stored " + ", ".join(stored)
            user_text = f"{user_text.rstrip()}\n\n{note}" if user_text.strip() else note

    result = ActionResult(
        plugin_id=plugin_id,
        action_id=action_id,
        mode=mode,
        code=code,
        body=body,
        user_text=user_text,
        model_text="",
        error=err,
        stored=stored,
    )
    # The model-facing text: interpret returns the prefixed body (today's tool
    # behaviour, incl. any post_call_instruction render directive); raw/schema
    # return only the receipt so the body stays out of context.
    if mode == OUTPUT_INTERPRET:
        prefix = f"[{plugin_id}.{action_id} HTTP {code}]"
        model_text = f"{prefix}\n{body}"
        if action.post_call_instruction:
            model_text += f"\n\n{action.post_call_instruction}"
        result.model_text = model_text
    else:
        result.model_text = result.receipt
    return result
