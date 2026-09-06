"""Deterministic plugin markdown — face/REPL ``/plugin new`` writes this.

A stub GET action so ``plugin_call`` has a manifest immediately; the author
replaces the URL and params. No $EDITOR. Secrets stay env-var names.
"""

from __future__ import annotations

from xlii.plugin import is_valid_id
from xlii.plugin_manifest import (
    EFFECT_READ_ONLY,
    TRUST_SUBSCRIPTION,
    VALID_EFFECTS,
    VALID_TRUSTS,
)

AUTH_RING = ("none", "header", "query")
OUTPUT_RING = ("schema", "raw", "interpret")
EFFECT_RING = (
    "read-only",
    "external-write",
    "local-system",
    "destructive",
)
TRUST_RING = ("subscription", "always-confirm")


def render_plugin(
    plugin_id: str,
    *,
    effect: str = EFFECT_READ_ONLY,
    trust: str = TRUST_SUBSCRIPTION,
    auth: str = "none",
    output: str = "interpret",
    name: str = "",
) -> str:
    if not is_valid_id(plugin_id):
        raise ValueError(
            f"invalid plugin id: {plugin_id!r} — letters/digits/_./- "
            "(start with letter/digit; max 64)"
        )
    eff = effect if effect in VALID_EFFECTS else EFFECT_READ_ONLY
    tru = trust if trust in VALID_TRUSTS else TRUST_SUBSCRIPTION
    auth_s = auth if auth in AUTH_RING else "none"
    out = output if output in OUTPUT_RING else "interpret"
    title = (name or plugin_id).strip() or plugin_id
    env = f"{plugin_id.upper().replace('-', '_').replace('.', '_')}_KEY"
    set_block = ""
    if auth_s == "none":
        auth_block = "auth_type: none\nauth_env_vars: []"
        header_block = ""
        auth_doc = "No key. Public GET."
    elif auth_s == "header":
        auth_block = (
            f"auth_type: header\nauth_env_vars:\n  - {env}"
        )
        header_block = (
            "    headers:\n"
            f"      Authorization: \"Bearer ${{{env}}}\"\n"
        )
        set_block = (
            f"  - id: set\n"
            f"    description: Store the API key in the vault\n"
            f"    params:\n"
            f"      {env}: {{secret: true, store: true, required: true, "
            f"description: \"API key\"}}\n"
            f"    output: raw\n"
        )
        auth_doc = (
            "Open the `set` form. The key goes to the vault — never into chat."
        )
    else:
        auth_block = (
            f"auth_type: query_param\nauth_env_vars:\n  - {env}"
        )
        header_block = ""
        set_block = (
            f"  - id: set\n"
            f"    description: Store the API key in the vault\n"
            f"    params:\n"
            f"      {env}: {{secret: true, store: true, required: true, "
            f"description: \"API key\"}}\n"
            f"    output: raw\n"
        )
        auth_doc = (
            "Open the `set` form. Then add the query param the API wants on `ping`."
        )
    return (
        f"---\n"
        f"id: {plugin_id}\n"
        f"name: {title}\n"
        f"description: (fill in — one line)\n"
        f"categories: [misc]\n"
        f"effect: {eff}\n"
        f"trust: {tru}\n"
        f"{auth_block}\n"
        f"actions:\n"
        f"{set_block}"
        f"  - id: ping\n"
        f"    description: Stub GET — replace url and params\n"
        f"    method: GET\n"
        f"    url: https://example.com/\n"
        f"{header_block}"
        f"    params: {{}}\n"
        f"    output: {out}\n"
        f"---\n"
        f"\n"
        f"# {title}\n"
        f"\n"
        f"Stub plugin. Replace `ping`'s URL and params, then:\n"
        f"\n"
        f"```\n"
        f"/plugin subscribe {plugin_id}\n"
        f"/plugin call {plugin_id}.ping\n"
        f"```\n"
        f"\n"
        f"## Auth\n"
        f"\n"
        f"{auth_doc}\n"
        f"\n"
        f"## Next\n"
        f"\n"
        f"- Real endpoint + required params on `ping` (or add more `actions:`).\n"
        f"- `output: schema` only after you know the JSON shape.\n"
        f"- Never put secrets in this file — env var names only.\n"
    )
