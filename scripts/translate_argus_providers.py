#!/usr/bin/env python
"""Translate argus-byo provider plugins into xlii provider manifests (S1).

argus's ~95 plugins (``providers/plugins/*.js``) are ``ProviderRegistry.register({...})``
objects: a declarative header (id, name, categories, authType, configFields,
rateLimit) plus a ``query(config, params)`` whose per-method branches each do
one ``fetch()`` and shape the JSON. xlii manifests are one endpoint each, so
one plugin becomes N manifests (``<id>-<method>.toml``).

This is a *pipeline + agent pass* tool (typed-workbenches.md S1), not a JS
parser. It line-scans each plugin and splits methods into two buckets:

- **clean** — single fetch, template-literal URL whose ``${...}`` holes are all
  simple params, ``resp.json()``, a plain ``.map(v => ({...}))`` record shape.
  Draft manifests are emitted for these (review before landing).
- **flagged** — multi-fetch (``Promise.all``), computed URL holes (symbol maps,
  slices), non-JSON bodies, OAuth flows, POSTs with constructed bodies.
  Reported with reasons; the agent pass handles them (or they're stock-pane
  material, per the proposal).

Usage:

    venv/bin/python scripts/translate_argus_providers.py \
        [--plugins DIR] [--out DIR] [--report PATH]

Defaults: plugins ``~/argus-byo/providers/plugins``, drafts ``--out`` only when
given, report to stdout (JSON with ``--report``).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PLUGINS = Path.home() / "argus-byo" / "providers" / "plugins"

_RE_ID = re.compile(r'\bid:\s*"([^"]+)"')
_RE_NAME = re.compile(r'\bname:\s*"([^"]+)"')
_RE_DESC = re.compile(r'\bdescription:\s*"([^"]+)"')
_RE_CATS = re.compile(r'\bcategories:\s*\[([^\]]*)\]')
_RE_AUTH = re.compile(r'\bauthType:\s*"([^"]+)"')
_RE_WEBSITE = re.compile(r'\bwebsite:\s*"([^"]+)"')
_RE_ENDPOINT = re.compile(r'(\w+):\s*"(https?://[^"]+)"')
_RE_METHOD_BRANCH = re.compile(r'if\s*\(\s*(?:m|method|type)\s*===\s*"(\w+)"')
_RE_DEFAULT_METHOD = re.compile(r'(?:const|let)\s+(?:m|method|type)\s*=\s*(?:params\.\w+\s*\|\||\w+\s*\|\|)\s*"(\w+)"')
_RE_FETCH = re.compile(r"fetch\(\s*`([^`]+)`")
_RE_FETCH_STR = re.compile(r'fetch\(\s*"(https?://[^"]+)"')
_RE_FETCH_VAR = re.compile(r"fetch\(\s*(\w+)\s*\)")
_RE_URL_ASSIGN = re.compile(r"(?:const|let)\s+(\w*[uU]rl\w*|\w*[eE]ndpoint\w*)\s*=\s*`([^`]+)`")
_RE_CONST_URL = re.compile(r"^\s*((?:_?[A-Z][A-Z0-9_]+)|(?:endpoints))", re.M)
_RE_JSON = re.compile(r"\.json\(\)")
_RE_RECORDS = re.compile(r"\(\s*(?:json|data)\.(\w+(?:\.\w+)*)\s*\|\|\s*\[\]\s*\)\s*(?:\.slice\(\d+,?\s*\d*\))?\s*\.map")
_RE_MAP_FIELDS = re.compile(r"\.map\(\s*(?:\((\w+)\)|(\w+))\s*=>\s*\(\{")
_RE_CONFIG_KEY = re.compile(r'\{\s*key:\s*"(\w+)"[^}]*required:\s*(true|false)')
_RE_HEADER_INJECT = re.compile(r'headers:\s*\{[^}]*["\']([\w-]+)["\']\s*:\s*(?:key|config\.\w+)')
_RE_RATE_DAILY = re.compile(r"requestsPerDay:\s*(\d+)")


_RE_POST = re.compile(r"""method:\s*['"]post['"]""", re.I)
_RE_BODY = re.compile(r"body:\s*JSON\.stringify\(\s*\{(.*?)\}\s*\)", re.S)
_RE_BODY_SIMPLE = re.compile(
    r'^(\w+):\s*(?:params\.(\w+)|([A-Za-z_]\w*)|"([^"]*)"|(true|false|\d+(?:\.\d+)?))\s*,?$'
)
_RE_BODY_OPTIONAL = re.compile(r'^(\w+):\s*params\.(\w+)\s*\|\|\s*undefined\s*,?$')
_RE_BODY_DEFAULT = re.compile(r'^(\w+):\s*params\.(\w+)\s*\|\|\s*("([^"]*)"|\d+(?:\.\d+)?)\s*,?$')
_RE_BODY_SHORTHAND = re.compile(r"^(\w+),?$")


def _extract_body(text: str, draft: "MethodDraft") -> None:
    """A POST's JSON.stringify({...}) body → [request.body] with {param} holes.
    Simple values translate: param refs, literals, shorthand (`query,` =
    `query: query`), `params.x || undefined` (optional), `params.x || lit`
    (optional with default). Anything else computed flags the method."""
    m = _RE_BODY.search(text)
    if not m:
        draft.clean = False
        draft.reasons.append("POST without a JSON.stringify object body")
        return
    draft.http_method = "POST"
    for line in m.group(1).split(","):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        opt = _RE_BODY_OPTIONAL.match(line)
        if opt:
            draft.body[opt.group(1)] = "{" + opt.group(2) + "}"
            continue  # optional: not required, no default
        dflt = _RE_BODY_DEFAULT.match(line)
        if dflt:
            draft.body[dflt.group(1)] = "{" + dflt.group(2) + "}"
            draft.defaults[dflt.group(2)] = dflt.group(4) or dflt.group(3)
            continue
        entry = _RE_BODY_SIMPLE.match(line)
        if not entry:
            short = _RE_BODY_SHORTHAND.match(line)
            if short and re.fullmatch(r"[A-Za-z_]\w*", short.group(1)):
                draft.body[short.group(1)] = "{" + short.group(1) + "}"
                draft.params_from_url.append(short.group(1))
                continue
            draft.clean = False
            draft.reasons.append(f"computed body value: {line[:60]}")
            continue
        key, param, ident, literal, const = entry.groups()
        if ident in ("true", "false"):
            draft.body[key] = ident == "true"
        elif param or ident:
            draft.body[key] = "{" + (param or ident) + "}"
            draft.params_from_url.append(param or ident)
        elif literal is not None:
            draft.body[key] = literal
        else:
            if const == "true":
                draft.body[key] = True
            elif const == "false":
                draft.body[key] = False
            elif "." in const:
                draft.body[key] = float(const)
            else:
                draft.body[key] = int(const)


@dataclass
class MethodDraft:
    """One method branch, translated or flagged."""

    method: str
    http_method: str = "GET"
    url: str = ""
    params_from_url: list = field(default_factory=list)
    body: dict = field(default_factory=dict)  # POST: JSON body template
    defaults: dict = field(default_factory=dict)  # optional params with defaults
    records_path: str = ""
    fields: dict = field(default_factory=dict)
    auth_param_seen: str = ""  # the query param the API key actually rode in on
    clean: bool = True
    reasons: list = field(default_factory=list)


@dataclass
class PluginReport:
    id: str
    name: str
    description: str
    categories: list
    auth_type: str
    website: str
    auth_param: str = "apiKey"
    auth_required: bool = True
    auth_in: str = "query"  # "query" | "header"
    auth_header: str = ""
    rate_daily: int = 0  # argus rateLimit.requestsPerDay (0 = undeclared)
    methods: list = field(default_factory=list)  # list[MethodDraft]
    reasons: list = field(default_factory=list)

    @property
    def clean_methods(self):
        return [m for m in self.methods if m.clean]


def _convert_url_holes(template: str, draft: MethodDraft, auth_param: str,
                       constants: dict) -> str:
    """${...} holes → {param}; config.<authParam> → the auth slot; this.endpoints.X
    and this._BASE-style constants → inlined from the plugin header; anything
    computed flags the method (the agent pass owns those)."""

    def _sub(match: re.Match) -> str:
        expr = match.group(1).strip()
        expr = re.sub(r"^encodeURIComponent\((.+)\)$", r"\1", expr)
        if expr.startswith("this."):
            key = expr[len("this."):]
            if key in constants:
                return constants[key]
            draft.clean = False
            draft.reasons.append(f"unresolved constant: ${{{expr}}}")
            return "__COMPUTED__"
        expr = re.sub(r"^params\.", "", expr)
        if expr in ("config." + auth_param, "key", "apiKey", "config.apiKey"):
            draft.params_from_url.append("__auth__")
            return "__AUTH__"
        if re.fullmatch(r"[A-Za-z_]\w*", expr):
            draft.params_from_url.append(expr)
            return "{" + expr + "}"
        draft.clean = False
        draft.reasons.append(f"computed URL hole: ${{{match.group(1).strip()}}}")
        return "__COMPUTED__"

    return re.sub(r"\$\{([^}]+)\}", _sub, template)


def _split_branches(body: str) -> list[tuple[str, str]]:
    """(method_name, branch_text) pairs. The pre-branch prelude becomes the
    default method when the plugin has no explicit dispatch."""
    matches = list(_RE_METHOD_BRANCH.finditer(body))
    if not matches:
        default = _RE_DEFAULT_METHOD.search(body)
        return [(default.group(1) if default else "query", body)]
    branches = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        branches.append((m.group(1), body[m.start():end]))
    return branches


def _collect_constants(text: str) -> dict:
    """URL constants declared in the plugin header: the endpoints table and
    _BASE-style members (``_BASE: "https://…"``)."""
    constants = {}
    em = re.search(r"endpoints:\s*\{([^}]*)\}", text, re.S)
    if em:
        for k, v in _RE_ENDPOINT.findall(em.group(1)):
            constants[f"endpoints.{k}"] = v
    for m in re.finditer(r'^\s{2}(_\w+):\s*"(https?://[^"]+)"', text, re.M):
        constants[m.group(1)] = m.group(2)
    return constants


def _analyze_branch(method: str, text: str, auth_param: str, constants: dict,
                    whole_text: str) -> MethodDraft:
    draft = MethodDraft(method=method)
    if "Promise.all" in text:
        draft.clean = False
        draft.reasons.append("multi-fetch (Promise.all)")
    if _RE_POST.search(text):
        _extract_body(text, draft)
    fetches = list(_RE_FETCH.findall(text))
    fetches += _RE_FETCH_STR.findall(text)
    for var in _RE_FETCH_VAR.findall(text):
        assign = re.search(
            rf"(?:const|let)\s+{re.escape(var)}\s*=\s*`([^`]+)`", text
        ) or re.search(
            rf"(?:const|let)\s+{re.escape(var)}\s*=\s*`([^`]+)`", whole_text
        ) or re.search(
            rf'(?:const|let)\s+{re.escape(var)}\s*=\s*"(https?://[^"]+)"', text
        )
        if assign:
            fetches.append(assign.group(1))
        else:
            draft.clean = False
            draft.reasons.append(f"fetch URL built in variable {var!r}")
    if not fetches:
        draft.clean = False
        draft.reasons.append("no template-literal fetch found")
        return draft
    if len(fetches) > 1:
        draft.clean = False
        draft.reasons.append(f"{len(fetches)} template fetches in one branch")
    url = _convert_url_holes(fetches[0], draft, auth_param, constants)
    if "__AUTH__" in url:
        # Capture the query param the key rode in on (api_key, apikey, api-key,
        # token — providers disagree), then strip it from the template: the
        # runner's [auth] block re-injects it (kind=query).
        seen = re.search(r"[?&]([^?&=]+)=[^&]*__AUTH__", url)
        if seen:
            draft.auth_param_seen = seen.group(1)
        url = re.sub(r"[?&][^?&]*__AUTH__[^&]*", "", url)
        url = url.replace("?&", "?").rstrip("?&")
    if "?" in url:
        base, qs = url.split("?", 1)
        draft.url = base
        for pair in qs.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                draft.fields.setdefault("__urlparams__", {})
                if not isinstance(draft.fields["__urlparams__"], dict):
                    draft.fields["__urlparams__"] = {}
                draft.fields["__urlparams__"][k] = v
    else:
        draft.url = url
    if not _RE_JSON.search(text):
        draft.clean = False
        draft.reasons.append("response not parsed as JSON")
    rec = _RE_RECORDS.search(text)
    draft.records_path = rec.group(1) if rec else ""
    fm = _RE_MAP_FIELDS.search(text)
    if fm:
        var = fm.group(1) or fm.group(2)
        for out_key, path, call in re.findall(
            rf"(\w+):\s*{var}\.([\w.?]+)(\s*\()?(?:\s*\|\|\s*(?:\"\"|''|null|false|0|\[\]))?", text
        ):
            if call:
                # row.symbol.toUpperCase() / legs.map(...) — a JS transformation,
                # not a key path. The runner cannot execute it (the projection
                # would silently be None), so the agent pass owns this one.
                draft.clean = False
                draft.reasons.append(f"method call in projection: {out_key} = {var}.{path}()")
                continue
            draft.fields[out_key] = path.replace("?.", ".")
    return draft


def analyze_plugin(path: Path) -> PluginReport:
    text = path.read_text(encoding="utf-8", errors="replace")
    rid = (_RE_ID.search(text) or [None, path.stem])[1]
    rep = PluginReport(
        id=rid,
        name=(_RE_NAME.search(text) or [None, rid])[1],
        description=(_RE_DESC.search(text) or [None, ""])[1],
        categories=[c.strip().strip('"') for c in (_RE_CATS.search(text) or [None, ""])[1].split(",") if c.strip()],
        auth_type=(_RE_AUTH.search(text) or [None, "none"])[1],
        website=(_RE_WEBSITE.search(text) or [None, ""])[1],
    )
    cfg = _RE_CONFIG_KEY.search(text)
    if cfg:
        rep.auth_param = cfg.group(1)
        rep.auth_required = cfg.group(2) == "true"
    hdr = _RE_HEADER_INJECT.search(text)
    if hdr and rep.auth_type != "none":
        rep.auth_in = "header"
        rep.auth_header = hdr.group(1)
    rate = _RE_RATE_DAILY.search(text)
    if rate:
        rep.rate_daily = int(rate.group(1))

    qpos = text.find("async query(")
    if qpos == -1:
        rep.reasons.append("no query() found")
        return rep
    constants = _collect_constants(text)
    qbody = text[qpos:]
    # The last method branch would otherwise swallow testConnection's fetch.
    tpos = qbody.find("async testConnection")
    if tpos != -1:
        qbody = qbody[:tpos]
    for method, branch in _split_branches(qbody):
        rep.methods.append(_analyze_branch(method, branch, rep.auth_param, constants, text))
    if not rep.methods:
        rep.reasons.append("no method branches found")
    return rep


def _toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _toml_scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return f'"{_toml_escape(str(v))}"'


def draft_manifest(rep: PluginReport, m: MethodDraft) -> str:
    """One clean method → one xlii manifest (TOML text)."""
    name = rep.id if len(rep.methods) == 1 else f"{rep.id}-{m.method}"
    url_params = m.fields.get("__urlparams__") or {}
    lines = [
        f'name = "{name}"',
        "version = 1",
        f'summary = "{_toml_escape(rep.description or rep.name)} (argus: {rep.id}.{m.method})"',
        f'category = "{rep.categories[0] if rep.categories else "general"}"',
    ]
    url_hole_params = [p for p in m.params_from_url if p != "__auth__"]
    qs_params = {k: v for k, v in url_params.items()}
    templated_qs = {
        k: (v if not re.fullmatch(r"\{(\w+)\}", v) else v) for k, v in qs_params.items()
    }
    required = sorted({*url_hole_params, *(v.strip("{}") for v in templated_qs.values() if v.startswith("{"))})
    if required:
        lines.append("required_params = [" + ", ".join(f'"{p}"' for p in required) + "]")
    if m.defaults:
        lines += ["", "[defaults]"]
        lines += [f"{k} = {_toml_scalar(v)}" for k, v in m.defaults.items()]
    lines += ["", "[request]", f'method = "{m.http_method}"', f'url = "{m.url}"']
    if templated_qs:
        lines += ["", "[request.params]"]
        lines += [
            f"{k if re.fullmatch(r'[A-Za-z_]\\w*', k) else chr(34) + _toml_escape(k) + chr(34)} = {_toml_scalar(v)}"
            for k, v in templated_qs.items()
        ]
    if m.body:
        lines += ["", "[request.body]"]
        lines += [f"{k} = {_toml_scalar(v)}" for k, v in m.body.items()]
    if rep.auth_type != "none":
        lines += ["", "[auth]"]
        lines.append(f'kind = "{rep.auth_in}"')
        key_env = re.sub(r"\W+", "_", rep.id).upper()
        lines.append(f'key_env = "{key_env}_API_KEY"')
        if rep.auth_in == "header":
            header = rep.auth_header or "x-api-key"
            lines.append(f'header = "{header}"')
        else:
            lines.append(f'param = "{m.auth_param_seen or rep.auth_param}"')
        lines.append(f"required = {'true' if rep.auth_required else 'false'}")
    if rep.rate_daily:
        lines += ["", "[quota]", f"daily = {rep.rate_daily}"]
    lines += ["", "[shape]", f'records = "{m.records_path}"']
    visible_fields = {k: v for k, v in m.fields.items() if not k.startswith("__")}
    if visible_fields:
        lines += ["", "[shape.fields]"]
        lines += [f'{k} = "{v}"' for k, v in visible_fields.items()]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plugins", type=Path, default=DEFAULT_PLUGINS)
    ap.add_argument("--out", type=Path, default=None, help="write draft manifests here")
    ap.add_argument("--report", type=Path, default=None, help="write the JSON report here")
    args = ap.parse_args(argv)

    reports = []
    for f in sorted(args.plugins.glob("*.js")):
        try:
            reports.append(analyze_plugin(f))
        except Exception as exc:  # a weird file degrades to a flagged report
            reports.append(PluginReport(id=f.stem, name=f.stem, description="", categories=[],
                                        auth_type="", website="", reasons=[f"analyzer crash: {exc}"]))

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
    n_drafts = 0
    table = []
    for rep in reports:
        all_clean = bool(rep.methods) and all(m.clean for m in rep.methods)
        status = "CLEAN" if all_clean and not rep.reasons else ("PARTIAL" if rep.clean_methods else "FLAGGED")
        reasons = rep.reasons + [r for m in rep.methods if not m.clean for r in m.reasons]
        table.append({"id": rep.id, "status": status, "methods": len(rep.methods),
                      "clean": len(rep.clean_methods), "auth": rep.auth_type, "reasons": reasons})
        if args.out:
            for m in rep.clean_methods:
                name = rep.id if len(rep.methods) == 1 else f"{rep.id}-{m.method}"
                (args.out / f"{name}.toml").write_text(draft_manifest(rep, m))
                n_drafts += 1

    clean_n = sum(1 for t in table if t["status"] == "CLEAN")
    partial_n = sum(1 for t in table if t["status"] == "PARTIAL")
    flagged_n = sum(1 for t in table if t["status"] == "FLAGGED")
    summary = {"plugins": len(table), "clean": clean_n, "partial": partial_n,
               "flagged": flagged_n, "drafts_written": n_drafts}
    payload = {"summary": summary, "plugins": table}
    if args.report:
        args.report.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(summary))
    for t in table:
        mark = {"CLEAN": "+", "PARTIAL": "~", "FLAGGED": "-"}[t["status"]]
        why = "; ".join(dict.fromkeys(t["reasons"]))[:100]
        print(f" {mark} {t['id']:<28} methods {t['clean']}/{t['methods']:<3} auth {t['auth']:<8} {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
