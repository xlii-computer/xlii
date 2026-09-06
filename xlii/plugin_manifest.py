"""Structured plugin manifests — action specs parsed from frontmatter.

A plugin manifest is the machine-executable layer of a plugin: typed actions
with validated params that plugin_call can invoke directly. A file without
an ``actions:`` block is not a plugin.

The frontmatter is parsed with PyYAML (safe_load) to handle the nested
``actions:`` structure. The simpler parse_frontmatter in plugin.py continues
to work for flat fields; this module handles the structured bits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

import yaml


# --------------------------------------------------------------------------- #
#  Effect / trust — replaces the flat "risk" field
# --------------------------------------------------------------------------- #

EFFECT_READ_ONLY = "read-only"
EFFECT_EXTERNAL_WRITE = "external-write"
EFFECT_LOCAL_SYSTEM = "local-system"
EFFECT_DESTRUCTIVE = "destructive"
VALID_EFFECTS = {EFFECT_READ_ONLY, EFFECT_EXTERNAL_WRITE, EFFECT_LOCAL_SYSTEM, EFFECT_DESTRUCTIVE}

TRUST_SUBSCRIPTION = "subscription"
TRUST_ALWAYS_CONFIRM = "always-confirm"
VALID_TRUSTS = {TRUST_SUBSCRIPTION, TRUST_ALWAYS_CONFIRM}

# High-risk classification (Vector E). Which plugins need *elevation* before a
# role/persona may auto-attach or invoke them on activation. A role may freely
# *list* a high-risk plugin (the point of shareable roles), but materializing it
# is gated — see role.gate_loadout_plugins. Conservative + fail-closed: anything
# we can't confidently call read-only/subscription is treated as high-risk.
HIGH_RISK_EFFECTS = {EFFECT_LOCAL_SYSTEM, EFFECT_DESTRUCTIVE}


def effect_trust_is_high_risk(effect: str, trust: str) -> bool:
    """True if a plugin with this (effect, trust) needs elevation to auto-attach.
    High-risk = it touches the local system / is destructive, or it always
    demands per-call confirmation. An unknown effect fails closed → high-risk."""
    if effect not in VALID_EFFECTS:
        return True
    if effect in HIGH_RISK_EFFECTS:
        return True
    return trust == TRUST_ALWAYS_CONFIRM


# --------------------------------------------------------------------------- #
#  Param spec
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ParamSpec:
    """One parameter in an action.

    ``form`` — collect via closed HTML (default for required user params).
    ``secret`` — password field; never accepted from the agent.
    ``store`` — write the supplied value to the vault (``True`` / env name /
    ``other-plugin.ENV``).
    """
    name: str
    description: str = ""
    required: bool = False
    default: Optional[str] = None
    const: Optional[str] = None      # value injected automatically (not user-supplied)
    enum: Optional[list[str]] = None  # allowed values
    form: bool = False
    secret: bool = False
    store: Optional[str] = None
    input: str = ""  # "" | text | textarea

    def validate(self, value: Any) -> str | None:
        """Return an error message if ``value`` is invalid, else None."""
        if self.const is not None:
            return None  # const params are never user-supplied
        if self.enum is not None and str(value) not in self.enum:
            return f"param {self.name!r}: value {value!r} not in {self.enum}"
        return None


# --------------------------------------------------------------------------- #
#  Output mode — what happens to an action's response (the-fold Vector B, rung 2)
# --------------------------------------------------------------------------- #
#
# The independent axis the old design fused into "everything routes through the
# model". An action DECLARES how its output is handled; the caller (direct
# /plugin call, a pipe step, or the agent tool) honours it identically:
#
#   raw       — body prints straight to the user; the model receives at most a
#               one-line receipt. A response that never enters model context
#               can't prompt-inject the model (today every response is a surface).
#   schema    — body reshaped by output_transforms (optional) then rendered
#               deterministically via output_renderer (table / list / template).
#               No model synthesis. (output_schema is documentation of the raw→
#               extracted mapping, not a runtime validation step — the transforms
#               + renderer args are what actually run.)
#   interpret — today's behaviour: the body returns to the model for synthesis.
#
# Default is interpret. Per-ACTION (not per-plugin): one plugin can have a
# raw `current` action and an interpret `explain` action.
OUTPUT_RAW = "raw"
OUTPUT_SCHEMA = "schema"
OUTPUT_INTERPRET = "interpret"
VALID_OUTPUTS = {OUTPUT_RAW, OUTPUT_SCHEMA, OUTPUT_INTERPRET}


def normalize_output(value: Any) -> str:
    """Coerce a declared ``output:`` value to a valid mode, defaulting to
    ``interpret``. Fail-safe (not fail-closed): an unknown/missing mode means
    today's behaviour, never a silent capability change."""
    v = str(value).strip().lower() if value is not None else ""
    return v if v in VALID_OUTPUTS else OUTPUT_INTERPRET


# --------------------------------------------------------------------------- #
#  Action spec
# --------------------------------------------------------------------------- #

VALID_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "exec"}


@dataclass(frozen=True)
class ActionSpec:
    """One invocable action in a plugin manifest."""
    id: str
    description: str
    method: str                                # HTTP verb or "exec"
    url: str = ""                              # URL template for HTTP actions
    command: str = ""                          # command template for exec actions
    params: dict[str, ParamSpec] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    response_shape: str = ""                   # brief hint for the model
    post_call_instruction: str = ""            # appended to tool result so the
                                                # agent sees rendering directives
                                                # at response time, not just at
                                                # search time
    # output_schema DOCUMENTS the raw→extracted field mapping (it is the spec the
    # renderer args / output_transforms are written against). It is NOT a runtime
    # validation step — nothing validates a body against it; the model-extraction
    # ("forced schema") path it was originally written for is not wired. Kept as
    # authoring documentation. See feedback_force_schema_dont_prompt_engineer.md.
    output_schema: Optional[dict] = None
    output_renderer: str = ""                  # name of renderer dispatch (e.g. "paste_command")
    output_renderer_args: dict = field(default_factory=dict)
    # Render-prep transforms (the-fold Vector B follow-up): an ordered list of
    # {op: name, …} that reshape the RAW body into the renderer's expected shape
    # before output_renderer runs — so a stock plugin's upstream body can go
    # deterministic. See xlii/plugin_transforms.py for the closed op vocabulary.
    output_transforms: list = field(default_factory=list)
    # Body parse format for schema rendering: "json" (default) or "rss" (the one
    # non-JSON stock case, google-news — parsed to {items:[…]} before transforms).
    body_format: str = "json"
    # Output MODE (rung 2): raw | schema | interpret. Default interpret.
    output: str = OUTPUT_INTERPRET
    # Response → vault: {ENV_VAR: source} where source is a dotted path or
    # the closed token ``pds_host``. ``store_plugin`` names the vault slot
    # (default: this plugin).
    store_plugin: str = ""
    store_map: dict = field(default_factory=dict)
    # Closed compose flow: steps[] then a final POST. See plugin_call._run_compose.
    compose: dict = field(default_factory=dict)

    @property
    def is_exec(self) -> bool:
        return self.method == "exec"

    @property
    def is_store_only(self) -> bool:
        """No HTTP — the action only writes ``store:`` params to the vault."""
        if self.url or self.command or self.is_exec:
            return False
        return any(p.store for p in self.params.values())

    @property
    def is_raw(self) -> bool:
        return self.output == OUTPUT_RAW

    @property
    def is_schema(self) -> bool:
        return self.output == OUTPUT_SCHEMA

    @property
    def is_deterministic(self) -> bool:
        """raw + schema both resolve without the model in the loop — the fast-path
        rungs. Only ``interpret`` needs a model turn."""
        return self.output in (OUTPUT_RAW, OUTPUT_SCHEMA)

    @property
    def allowed_hosts(self) -> set[str]:
        """Hosts extracted from the url template — used for runtime pinning.

        Returns empty set (= skip host-pin) when the URL template uses
        env-var substitution like `${BSKY_PDS_HOST}`. The user explicitly
        controls those hosts via the vault, so host-pinning isn't a
        meaningful defense — the user is the host-picker, not an attacker.
        Static URLs still get pinned against their template host.
        """
        if self.is_exec or not self.url:
            return set()
        # Skip pinning if the URL has env-var placeholders. Resolution happens
        # at call time and the resolved host depends on the user's vault.
        if "${" in self.url:
            return set()
        try:
            return {urlparse(self.url).hostname}
        except Exception:
            return set()

    def resolve_params(self, user_params: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Merge user params with defaults/consts. Returns (merged, errors).

        Preserves dict/list values for POST JSON bodies that expect nested
        objects (e.g. Bluesky chat sendMessage requires `message: {text: ...}`).
        Other types get string-coerced so query-string and path-param paths
        keep working unchanged.
        """
        merged: dict[str, Any] = {}
        errors: list[str] = []

        for name, spec in self.params.items():
            if spec.const is not None:
                merged[name] = spec.const
                continue
            if name in user_params:
                err = spec.validate(user_params[name])
                if err:
                    errors.append(err)
                else:
                    val = user_params[name]
                    # Preserve nested types so JSON bodies serialize them as
                    # objects/arrays. Everything else stringifies as before.
                    merged[name] = val if isinstance(val, (dict, list)) else str(val)
            elif spec.default is not None:
                merged[name] = spec.default
            elif spec.required:
                errors.append(f"missing required param: {name!r}")

        # Warn about unknown params (typos).
        unknown = set(user_params) - set(self.params)
        for u in sorted(unknown):
            errors.append(f"unknown param: {u!r}")

        return merged, errors


# --------------------------------------------------------------------------- #
#  Plugin manifest
# --------------------------------------------------------------------------- #

@dataclass
class PluginManifest:
    """Structured manifest parsed from a plugin's frontmatter."""
    plugin_id: str
    effect: str = EFFECT_READ_ONLY
    trust: str = TRUST_SUBSCRIPTION
    actions: list[ActionSpec] = field(default_factory=list)

    def get_action(self, action_id: str) -> ActionSpec | None:
        for a in self.actions:
            if a.id == action_id:
                return a
        return None

    @property
    def action_ids(self) -> list[str]:
        return [a.id for a in self.actions]

    @property
    def is_high_risk(self) -> bool:
        """Does auto-attaching/invoking this plugin require elevation? (Vector E)"""
        return effect_trust_is_high_risk(self.effect, self.trust)


# --------------------------------------------------------------------------- #
#  Parsing
# --------------------------------------------------------------------------- #

def _input_token(raw: Any) -> str:
    v = str(raw or "").strip().lower()
    return v if v in ("text", "textarea") else ""


def _store_token(name: str, raw: Any) -> Optional[str]:
    if raw is True:
        return name
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _parse_param(name: str, raw: Any) -> ParamSpec:
    """Parse a single param spec from YAML."""
    if isinstance(raw, dict):
        required = bool(raw.get("required", False))
        secret = bool(raw.get("secret", False))
        const = str(raw["const"]) if "const" in raw else None
        if "form" in raw:
            form = bool(raw.get("form"))
        else:
            form = bool(secret or (required and const is None))
        return ParamSpec(
            name=name,
            description=str(raw.get("description", "")),
            required=required,
            default=str(raw["default"]) if "default" in raw else None,
            const=const,
            enum=[str(v) for v in raw["enum"]] if "enum" in raw else None,
            form=form and const is None,
            secret=secret,
            store=_store_token(name, raw.get("store")),
            input=_input_token(raw.get("input")),
        )
    # Shorthand: `param_name: true` means required, `param_name: "value"` means const
    if raw is True:
        return ParamSpec(name=name, required=True, form=True)
    if isinstance(raw, (str, int, float)):
        return ParamSpec(name=name, const=str(raw))
    return ParamSpec(name=name)


def _parse_action(raw: dict) -> ActionSpec | None:
    """Parse one action dict from YAML. Returns None if malformed."""
    aid = raw.get("id")
    if not aid or not isinstance(aid, str):
        return None
    raw_method = str(raw.get("method", "GET"))
    # HTTP verbs canonicalize to uppercase ("get" → "GET"); the special
    # "exec" sentinel keeps lowercase. Without this branch a manifest
    # written with `method: exec` becomes "EXEC" and fails validation.
    method = "exec" if raw_method.lower() == "exec" else raw_method.upper()
    if method not in VALID_METHODS:
        return None

    params: dict[str, ParamSpec] = {}
    raw_params = raw.get("params") or {}
    if isinstance(raw_params, dict):
        for pname, pval in raw_params.items():
            params[pname] = _parse_param(pname, pval)

    headers: dict[str, str] = {}
    raw_headers = raw.get("headers") or {}
    if isinstance(raw_headers, dict):
        headers = {str(k): str(v) for k, v in raw_headers.items()}

    raw_output_schema = raw.get("output_schema")
    output_schema = raw_output_schema if isinstance(raw_output_schema, dict) else None
    raw_renderer_args = raw.get("output_renderer_args") or {}
    output_renderer_args = raw_renderer_args if isinstance(raw_renderer_args, dict) else {}
    raw_transforms = raw.get("output_transforms") or []
    output_transforms = [t for t in raw_transforms if isinstance(t, dict)] if isinstance(raw_transforms, list) else []
    body_format = str(raw.get("body_format", "json")).strip().lower() or "json"

    store_plugin = ""
    store_map: dict[str, str] = {}
    raw_store = raw.get("store")
    if isinstance(raw_store, dict):
        store_plugin = str(raw_store.get("plugin") or "").strip()
        raw_map = raw_store.get("map") or {}
        if isinstance(raw_map, dict):
            store_map = {str(k): str(v) for k, v in raw_map.items() if k and v}

    return ActionSpec(
        id=aid,
        description=str(raw.get("description", "")),
        method=method,
        url=str(raw.get("url", "")),
        command=str(raw.get("command", "")),
        params=params,
        headers=headers,
        response_shape=str(raw.get("response_shape", "")),
        post_call_instruction=str(raw.get("post_call_instruction", "")),
        output_schema=output_schema,
        output_renderer=str(raw.get("output_renderer", "")),
        output_renderer_args=output_renderer_args,
        output_transforms=output_transforms,
        body_format=body_format,
        output=normalize_output(raw.get("output")),
        store_plugin=store_plugin,
        store_map=store_map,
        compose=raw.get("compose") if isinstance(raw.get("compose"), dict) else {},
    )


def _resolve_effect_trust(meta: dict) -> tuple[str, str]:
    """Extract effect/trust. Missing or invalid badges fail closed."""
    effect = meta.get("effect", "")
    trust = meta.get("trust", "")
    if effect in VALID_EFFECTS and trust in VALID_TRUSTS:
        return effect, trust
    return EFFECT_DESTRUCTIVE, TRUST_ALWAYS_CONFIRM


def parse_manifest(raw_text: str) -> PluginManifest | None:
    """Parse a full plugin markdown file and extract the structured manifest.

    Returns None if the plugin has no ``actions:`` block.
    """
    lines = raw_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None

    fm_text = "\n".join(lines[1:end])
    try:
        meta = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return None

    if not isinstance(meta, dict):
        return None

    raw_actions = meta.get("actions")
    if not raw_actions or not isinstance(raw_actions, list):
        return None  # no actions → not a plugin

    actions = []
    for raw in raw_actions:
        if isinstance(raw, dict):
            spec = _parse_action(raw)
            if spec is not None:
                actions.append(spec)

    if not actions:
        return None

    plugin_id = str(meta.get("id", ""))
    effect, trust = _resolve_effect_trust(meta)

    return PluginManifest(
        plugin_id=plugin_id,
        effect=effect,
        trust=trust,
        actions=actions,
    )
