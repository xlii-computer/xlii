"""Plugin action forms — closed HTML, vault writes, secrets never reach the agent.

Vocabulary on each param (and optional action-level ``store:``):

* ``form``  — collect on the face (default for required user params).
* ``secret`` — password input; stripped from agent ``plugin_call``; never
  prefilled, never logged.
* ``store`` — write the value (or a response field) through ``Vault.set``.

A missing form/secret does not seed ``/plugin call password=``. The face
opens a sandboxed srcdoc; submit posts ``xlii-plugin-call`` (not prefill).
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Optional
from urllib.parse import urlparse

from xlii.plugin_manifest import ActionSpec, ParamSpec, PluginManifest


_SECRET_JSON_KEYS = frozenset({
    "accessJwt", "refreshJwt", "access_jwt", "refresh_jwt",
    "password", "appPassword", "token", "apiKey", "api_key",
})


def redact_plugin_text(text: str) -> str:
    """Strip JWTs / passwords from a plugin body before it hits the stream."""
    raw = text or ""
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        # Tolerate a trailing non-JSON note (``invoke_action`` appends
        # "stored plugin.X" after a vault write): redact the JSON head and
        # keep the tail, rather than returning the whole body verbatim.
        stripped = raw.lstrip()
        try:
            obj, end = json.JSONDecoder().raw_decode(stripped)
        except (ValueError, TypeError):
            return raw
        return json.dumps(_redact_obj(obj), ensure_ascii=False) + stripped[end:]
    return json.dumps(_redact_obj(obj), ensure_ascii=False)


def _redact_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for key, val in obj.items():
            if key in _SECRET_JSON_KEYS or (
                isinstance(val, str) and val.startswith("eyJ") and len(val) > 40
            ):
                out[key] = "…"
            else:
                out[key] = _redact_obj(val)
        return out
    if isinstance(obj, list):
        return [_redact_obj(v) for v in obj]
    return obj


def looks_like_secret_payload(text: str) -> bool:
    blob = text or ""
    return any(k in blob for k in ("accessJwt", "refreshJwt", "eyJ")) or (
        '"password"' in blob and "identifier" in blob
    )


_LIVE_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")


def contains_live_jwt(text: str) -> bool:
    """True when a JWT-shaped VALUE is still present in *text*.

    Redaction replaces token values with ``…``, so a match here means the
    payload never went through structural redaction (a non-JSON body echoing
    a token) and the secret is live. Key names alone — ``"accessJwt": "…"``
    after redaction — do not match.
    """
    return bool(_LIVE_JWT_RE.search(text or ""))


def strip_vault_receipt_suffix(text: str) -> str:
    """Drop ``invoke_action``'s trailing ``stored plugin.X`` note for scrub checks."""
    raw = (text or "").rstrip()
    marker = "\nstored "
    if marker in raw:
        return raw.rpartition(marker)[0].rstrip()
    return raw


def stored_secret_wire_receipt(stored: list[str]) -> str:
    """Vault receipt line for a fail-closed wire body."""
    labels = [s for s in (stored or []) if s]
    if labels:
        return "stored " + ", ".join(labels)
    return "(credential-shaped payload withheld)"


def withhold_stored_secret_body(
    text: str,
    *,
    stored: list[str],
    raw_body: str = "",
) -> bool:
    """Fail closed when a vault write happened but the body wasn't scrubbed.

    Call on the *pre-redaction* wire text. JSON login bodies are left to
    :func:`redact_plugin_text`; non-JSON echoes (HTML API-key errors, etc.)
    withhold when structural redaction cannot change the payload.
    """
    if not stored:
        return False
    blob = text or ""
    if looks_like_secret_payload(blob) or looks_like_secret_payload(raw_body or ""):
        core = strip_vault_receipt_suffix(blob)
        if not core.strip():
            return False
        try:
            json.loads(core.lstrip())
        except (ValueError, TypeError):
            stripped = core.lstrip()
            try:
                json.JSONDecoder().raw_decode(stripped)
            except (ValueError, TypeError):
                return True
        return False
    core = strip_vault_receipt_suffix(blob)
    if not core.strip():
        return False
    if contains_live_jwt(core):
        return True
    return redact_plugin_text(core) == core


def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def user_params(action: ActionSpec) -> list[ParamSpec]:
    """Params the human (or agent, if not secret) may supply."""
    return [p for p in action.params.values() if p.const is None]


def form_params(action: ActionSpec) -> list[ParamSpec]:
    return [p for p in user_params(action) if p.form or p.secret]


def store_params(action: ActionSpec) -> list[ParamSpec]:
    return [p for p in user_params(action) if p.store]


def strip_agent_secrets(action: ActionSpec, params: dict[str, Any]) -> dict[str, Any]:
    """Drop secret values an agent tried to pass. The form is the only door."""
    if not params:
        return {}
    secret = {p.name for p in action.params.values() if p.secret}
    if not secret:
        return dict(params)
    return {k: v for k, v in params.items() if k not in secret}


def missing_form_names(action: ActionSpec, params: dict[str, Any]) -> list[str]:
    """Required form/secret names that are absent or blank."""
    have = params or {}
    out: list[str] = []
    for spec in form_params(action):
        if not spec.required or spec.default is not None:
            continue
        if spec.name not in have:
            out.append(spec.name)
            continue
        val = have[spec.name]
        if val is None or (isinstance(val, str) and not val.strip()):
            out.append(spec.name)
    return out


def action_needs_form(action: ActionSpec, params: dict[str, Any]) -> bool:
    return bool(missing_form_names(action, params))


def parse_store_target(plugin_id: str, spec: ParamSpec) -> tuple[str, str]:
    """``store: true`` → this plugin / param name; ``store: ENV`` or ``other.ENV``."""
    raw = (spec.store or spec.name).strip()
    if "." in raw:
        pid, _, env = raw.partition(".")
        if pid and env:
            return pid, env
    return plugin_id, raw or spec.name


def first_store_action(manifest: PluginManifest | None) -> ActionSpec | None:
    if manifest is None:
        return None
    for a in manifest.actions:
        if store_params(a):
            return a
    return None


def extract_store_value(body: Any, source: str) -> Optional[str]:
    """Pull a vault value from a parsed JSON body.

    ``source`` is a dotted path, or the closed token ``pds_host`` (Bluesky
    ``didDoc.service[]`` → AtprotoPersonalDataServer endpoint, scheme stripped).
    """
    src = (source or "").strip()
    if not src:
        return None
    if src == "pds_host":
        return _pds_host(body)
    got = _dotted(body, src)
    if got is None:
        return None
    if isinstance(got, (dict, list)):
        return None
    text = str(got).strip()
    return text or None


def _dotted(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
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


def _strip_host(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        return raw.rstrip("/")
    parsed = urlparse(raw)
    host = parsed.netloc or parsed.path
    return host.rstrip("/")


def _pds_host(body: Any) -> Optional[str]:
    if not isinstance(body, dict):
        return None
    direct = body.get("pds_host")
    if direct:
        return _strip_host(str(direct)) or None
    doc = body.get("didDoc") or body.get("did_doc") or {}
    services = doc.get("service") if isinstance(doc, dict) else None
    if not isinstance(services, list):
        return None
    for svc in services:
        if not isinstance(svc, dict):
            continue
        if svc.get("type") != "AtprotoPersonalDataServer":
            continue
        host = _strip_host(str(svc.get("serviceEndpoint") or ""))
        if host:
            return host
    return None


def apply_param_stores(plugin_id: str, action: ActionSpec, params: dict[str, Any]) -> list[str]:
    """``Vault.set`` for ``store:`` params. Returns ``plugin.env`` labels written."""
    written: list[str] = []
    if not store_params(action):
        return written
    from xlii.vault import Vault

    vault = Vault.unlock(create_if_missing=True)
    for spec in store_params(action):
        val = params.get(spec.name)
        if val is None or (isinstance(val, str) and not str(val).strip()):
            continue
        target, env = parse_store_target(plugin_id, spec)
        vault.set(target, env, str(val))
        written.append(f"{target}.{env}")
    return written


def apply_response_stores(plugin_id: str, action: ActionSpec, body: Any) -> list[str]:
    """``Vault.set`` from the action's ``store.map`` over a parsed (or raw) body."""
    mapping = getattr(action, "store_map", None) or {}
    if not mapping:
        return []
    parsed = body
    if isinstance(body, str):
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            return []
    target = (getattr(action, "store_plugin", None) or plugin_id).strip() or plugin_id
    from xlii.vault import Vault

    vault = Vault.unlock(create_if_missing=True)
    written: list[str] = []
    for env, source in mapping.items():
        env_s = str(env).strip()
        if not env_s:
            continue
        val = extract_store_value(parsed, str(source))
        if not val:
            continue
        vault.set(target, env_s, val)
        written.append(f"{target}.{env_s}")
    return written


def apply_stores(
    plugin_id: str,
    action: ActionSpec,
    params: dict[str, Any],
    body: Any = None,
) -> list[str]:
    written = apply_param_stores(plugin_id, action, params)
    if body is not None and (getattr(action, "store_map", None) or {}):
        written.extend(apply_response_stores(plugin_id, action, body))
    return written


def form_spec(
    plugin_id: str,
    action: ActionSpec,
    *,
    seed: Optional[dict[str, Any]] = None,
    name: str = "",
) -> dict[str, Any]:
    """JSON-safe spec the face paints as closed HTML (and tests snapshot)."""
    seed = seed or {}
    fields: list[dict[str, Any]] = []
    for spec in form_params(action):
        if spec.secret:
            kind = "secret"
        elif spec.enum:
            kind = "select"
        elif (getattr(spec, "input", "") or "").lower() == "textarea":
            kind = "textarea"
        else:
            kind = "text"
        seeded = ""
        if not spec.secret and spec.name in seed and seed[spec.name] is not None:
            seeded = str(seed[spec.name])
        elif not spec.secret and spec.default is not None:
            seeded = str(spec.default)
        fields.append({
            "name": spec.name,
            "kind": kind,
            "description": spec.description or "",
            "required": bool(spec.required and spec.default is None),
            "enum": list(spec.enum) if spec.enum else [],
            "value": seeded,
        })
    title = name or f"{plugin_id}.{action.id}"
    lead = (action.description or "").strip() or "Fill and send. Secrets stay off the agent."
    spec = {
        "plugin": plugin_id,
        "action": action.id,
        "title": title,
        "lead": lead,
        "fields": fields,
    }
    spec["html"] = render_form_html(spec)
    return spec


_FORM_CSS = """
:root { color-scheme: dark light; }
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body {
  font: 14px/1.45 system-ui, sans-serif;
  color: CanvasText;
  background: transparent;
  padding: 16px 18px 20px;
}
h1 { font-size: 15px; font-weight: 600; margin: 0 0 4px; }
.lead { margin: 0 0 14px; font-size: 12px; opacity: .7; }
form { display: flex; flex-direction: column; gap: 12px; max-width: 36rem; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; opacity: .85; }
input, select, textarea {
  font: 14px/1.3 inherit; color: inherit;
  background: color-mix(in srgb, CanvasText 6%, transparent);
  border: 1px solid color-mix(in srgb, CanvasText 22%, transparent);
  border-radius: 6px; padding: 8px 10px;
}
textarea { min-height: 8rem; resize: vertical; }
input:focus, select:focus, textarea:focus { outline: 1px solid color-mix(in srgb, CanvasText 45%, transparent); }
.go { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
button {
  font: 13px inherit; color: inherit; cursor: pointer;
  background: color-mix(in srgb, CanvasText 10%, transparent);
  border: 1px solid color-mix(in srgb, CanvasText 28%, transparent);
  border-radius: 6px; padding: 8px 12px;
}
button:hover { background: color-mix(in srgb, CanvasText 16%, transparent); }
.note { font-size: 12px; opacity: .65; margin: 0; }
"""


def render_form_html(spec: dict[str, Any]) -> str:
    """Closed srcdoc. Submit posts ``xlii-plugin-call`` — never ``xlii-prefill``."""
    title = _esc(spec.get("title") or f"{spec.get('plugin')}.{spec.get('action')}")
    lead = _esc(spec.get("lead") or "")
    controls: list[str] = []
    for field in spec.get("fields") or []:
        name = _esc(field.get("name"))
        if not name:
            continue
        lab = _esc(field.get("description") or field.get("name"))
        req = " required" if field.get("required") else ""
        kind = field.get("kind") or "text"
        if kind == "select":
            opts = []
            cur = str(field.get("value") or "")
            for opt in field.get("enum") or []:
                sel = " selected" if str(opt) == cur else ""
                opts.append(f'<option value="{_esc(opt)}"{sel}>{_esc(opt)}</option>')
            control = f'<select name="{name}"{req}>{"".join(opts)}</select>'
        elif kind == "secret":
            control = (
                f'<input type="password" name="{name}" autocomplete="off" spellcheck="false"{req}>'
            )
        elif kind == "textarea":
            val = _esc(field.get("value"))
            control = (
                f'<textarea name="{name}" rows="6" autocomplete="off"{req}>'
                f"{val}</textarea>"
            )
        else:
            val = _esc(field.get("value"))
            extra = f' value="{val}"' if val else ""
            control = (
                f'<input type="text" name="{name}" autocomplete="off" '
                f'spellcheck="false"{extra}{req}>'
            )
        controls.append(f"<label>{lab}{control}</label>")
    fields_html = "\n".join(controls)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{_FORM_CSS}</style></head><body>
<h1>{title}</h1>
<p class="lead">{lead}</p>
<form id="f">
{fields_html}
<p class="note" id="st">Secrets stay off the agent. Run sends the action — this box will say if it worked.</p>
<div class="go"><button type="button" id="go">Run</button></div>
</form>
<script>
const PLUGIN = {json.dumps(spec.get("plugin") or "")};
const ACTION = {json.dumps(spec.get("action") or "")};
const st = document.getElementById("st");
const go = document.getElementById("go");
function send() {{
  const fd = new FormData(document.getElementById("f"));
  const params = {{}};
  for (const [k, v] of fd.entries()) params[k] = String(v);
  go.disabled = true;
  go.textContent = "Sending…";
  st.textContent = "Sending — leave this open.";
  parent.postMessage({{type:"xlii-plugin-call", plugin:PLUGIN, action:ACTION, params:params}}, "*");
}}
function dumpDraft() {{
  const fd = new FormData(document.getElementById("f"));
  const params = {{}};
  for (const [k, v] of fd.entries()) {{
    const el = document.getElementById("f").elements[k];
    if (el && el.type === "password") continue;
    params[k] = String(v);
  }}
  parent.postMessage({{type:"xlii-plugin-draft", plugin:PLUGIN, action:ACTION, params:params}}, "*");
}}
function applyDraft(params) {{
  if (!params) return;
  const f = document.getElementById("f");
  for (const [k, v] of Object.entries(params)) {{
    const el = f.elements[k];
    if (el && el.type !== "password") el.value = String(v);
  }}
}}
document.getElementById("go").addEventListener("click", send);
document.getElementById("f").addEventListener("submit", (e) => {{ e.preventDefault(); send(); }});
document.getElementById("f").addEventListener("input", dumpDraft);
window.addEventListener("message", (e) => {{
  if (e.source !== window.parent) return;
  const d = e.data;
  if (!d) return;
  if (d.type === "xlii-plugin-draft-restore") {{ applyDraft(d.params); return; }}
  if (d.type !== "xlii-plugin-result") return;
  go.disabled = false;
  go.textContent = "Run";
  st.textContent = d.text || (d.ok ? "done" : "failed");
  if (d.ok) {{
    for (const inp of document.querySelectorAll('input[type=password]')) inp.value = "";
  }}
}});
</script>
</body></html>"""


def missing_vault_vars(plugin: Any) -> list[str]:
    """Declared ``auth_env_vars`` that are empty in the vault."""
    try:
        needed = list(plugin.auth_env_vars() or [])
    except Exception:
        return []
    needed = [n for n in needed if n]
    if not needed:
        return []
    from xlii.vault import Vault, VaultError

    try:
        vault = Vault.unlock(create_if_missing=False)
    except VaultError:
        return needed
    have = vault.get(plugin.id)
    return [n for n in needed if not str(have.get(n) or "").strip()]


def fill_form_tty(action: ActionSpec, params: dict[str, Any]) -> dict[str, Any]:
    """Prompt for missing form/secret fields. Empty / interrupt = leave hole."""
    import getpass

    out = dict(params or {})
    for spec in form_params(action):
        if spec.name in out and str(out[spec.name] or "").strip():
            continue
        if not spec.required and spec.default is not None:
            continue
        label = spec.description or spec.name
        try:
            if spec.secret:
                val = getpass.getpass(f"{spec.name}: ").strip()
            else:
                val = input(f"{spec.name} ({label}): ").strip()
        except (EOFError, KeyboardInterrupt):
            return out
        if val:
            out[spec.name] = val
    return out


def auth_form_target(plugin: Any) -> tuple[str, str] | None:
    """If vault auth is missing, the plugin.action whose form should open.

    Prefers a ``store:`` action on this plugin (``set``). Else ``auth_setup:``
    names another plugin whose first form/secret action is the door
    (``bluesky_chat`` → ``bluesky_login.login``).
    """
    if not missing_vault_vars(plugin):
        return None
    manifest = plugin.manifest()
    store_act = first_store_action(manifest)
    if store_act is not None:
        return plugin.id, store_act.id
    setup = ""
    try:
        setup = str((plugin.metadata() or {}).get("auth_setup") or "").strip()
    except Exception:
        setup = ""
    if not setup:
        return None
    setup_id, _, setup_action = setup.partition(".")
    from xlii.plugin import Plugin

    other = Plugin(id=setup_id)
    if not other.exists():
        return None
    om = other.manifest()
    if om is None:
        return None
    if setup_action:
        act = om.get_action(setup_action)
        if act is not None:
            return other.id, act.id
    for a in om.actions:
        if form_params(a):
            return other.id, a.id
    return None
