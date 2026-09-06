"""Plugin search / get / call tool handlers."""

from __future__ import annotations

from typing import Any, Optional

from xlii.tool_context import (
    ToolContext,
    ToolResult,
)

from ._common import _cap_output


def t_plugin_search(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    from xlii.plugin import (
        NO_PLUGIN_MATCH_MARKER,
        Plugin,
        search_plugins,
    )
    intent = (args.get("intent") or "").strip()
    if not intent:
        return ToolResult("plugin_search: 'intent' is required", is_error=True)
    if not ctx.subscribed_plugins:
        return ToolResult(
            f"{NO_PLUGIN_MATCH_MARKER} for intent={intent!r}\n"
            "No plugins are subscribed for this project. The user can install/subscribe "
            "plugins with `xlii plugin --new` and `/plugin subscribe <id>`. "
            "Tell the user no plugin is available — do NOT fabricate plugin output."
        )
    available = [Plugin(id=pid) for pid in ctx.subscribed_plugins]
    available = [p for p in available if p.exists()]
    matches = search_plugins(intent, available, limit=5)
    if not matches:
        cats = sorted({c for p in available for c in p.categories()})
        cat_line = (
            "Categories of subscribed plugins: " + ", ".join(cats)
            if cats else
            "No plugin categories declared on subscribed plugins."
        )
        return ToolResult(
            f"{NO_PLUGIN_MATCH_MARKER} for intent={intent!r}\n"
            f"{cat_line}\n"
            "Suggest: install/subscribe a plugin that fits, or fall back to "
            "web_search/bash. Do NOT fabricate plugin output."
        )
    out = ["Top matches (call plugin_get for full content of any candidate):"]
    for p, score in matches:
        cats = ", ".join(p.categories()) or "—"
        effect, trust = p.effect_trust()
        out.append(
            f"- [{p.id}] (score={score:.1f}, {effect} · {trust}, categories={cats})\n"
            f"    {p.name()}: {p.description() or '(no description)'}"
        )
    return ToolResult("\n".join(out))


def t_plugin_get(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    from xlii.plugin import Plugin
    name = (args.get("name") or "").strip()
    if not name:
        return ToolResult("plugin_get: 'name' is required", is_error=True)
    if name not in ctx.subscribed_plugins:
        return ToolResult(
            f"plugin {name!r} is not subscribed for this project. "
            f"Subscribed: {ctx.subscribed_plugins or '(none)'}. "
            f"Use plugin_search first or ask the user to /plugin subscribe {name}.",
            is_error=True,
        )
    p = Plugin(id=name)
    if not p.exists():
        return ToolResult(
            f"plugin {name!r} is subscribed but the file is missing on disk "
            "(orphan subscription). Tell the user — do NOT fabricate.",
            is_error=True,
        )
    try:
        text = p.read_raw()
    except OSError as e:
        return ToolResult(f"read failed: {e}", is_error=True)
    return ToolResult(_cap_output(ctx, _strip_renderer_sections(text)))


def _emit_plugin_user_output(ctx: ToolContext, user_text: str) -> bool:
    """Print an action's raw/schema output to the user's console — UNTRUSTED API
    text goes through ``Text()`` so it is never interpreted as console markup.
    Returns False when there is no console (a worker), so the caller can note that
    the body wasn't surfaced."""
    console = getattr(ctx, "console", None)
    if console is None:
        return False
    try:
        from rich.text import Text
        console.print(Text(user_text.rstrip("\n")))
    except Exception:
        return False
    return True


def _open_plugin_form(ctx: ToolContext, plugin_id: str, action, seed: dict) -> ToolResult:
    from xlii.plugin_form import form_spec

    spec = form_spec(plugin_id, action, seed=seed)
    hook = getattr(ctx, "open_plugin_form", None)
    if callable(hook):
        hook(spec)
        return ToolResult(
            f"[{plugin_id}.{action.id}] form opened. The user fills it. "
            "Do not ask for passwords or API keys in chat. Wait."
        )
    return ToolResult(
        f"[{plugin_id}.{action.id}] needs the face form "
        "(secrets never go through you). Do not collect them in chat.",
        is_error=True,
    )


def t_plugin_call(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Invoke a structured plugin action. Secrets never arrive here — missing
    form/secret params (or missing vault auth) open the face form instead."""
    from xlii.plugin import Plugin
    from xlii.plugin_call import invoke_action
    from xlii.plugin_form import (
        action_needs_form,
        auth_form_target,
        strip_agent_secrets,
    )
    from xlii.plugin_manifest import OUTPUT_INTERPRET

    plugin_id = (args.get("plugin") or args.get("name") or "").strip()
    action_id = (args.get("action") or "").strip()
    params = args.get("params") or {}
    if not isinstance(params, dict):
        return ToolResult("plugin_call: 'params' must be an object", is_error=True)
    if not plugin_id or not action_id:
        return ToolResult("plugin_call: 'plugin' and 'action' are required", is_error=True)
    if plugin_id not in ctx.subscribed_plugins:
        return ToolResult(
            f"plugin {plugin_id!r} is not subscribed. Subscribed: {ctx.subscribed_plugins or '(none)'}",
            is_error=True,
        )
    p = Plugin(id=plugin_id)
    if not p.exists():
        return ToolResult(f"plugin {plugin_id!r} file missing on disk", is_error=True)
    try:
        raw = p.read_raw()
    except OSError as e:
        return ToolResult(f"read failed: {e}", is_error=True)
    manifest = p.manifest()
    if manifest is None:
        return ToolResult(
            f"plugin {plugin_id!r} has no actions — not a plugin",
            is_error=True,
        )
    action = manifest.get_action(action_id)
    if action is None:
        return ToolResult(
            f"unknown action {action_id!r} for plugin {plugin_id!r}; "
            f"available: {', '.join(manifest.action_ids)}",
            is_error=True,
        )
    params = strip_agent_secrets(action, params)
    setup = auth_form_target(p)
    if setup is not None and setup != (plugin_id, action_id):
        other_id, other_aid = setup
        if other_id not in ctx.subscribed_plugins:
            return ToolResult(
                f"vault missing auth for {plugin_id}. "
                f"Subscribe {other_id} and run {other_id}.{other_aid} — "
                "a form opens. Do not ask for the password.",
                is_error=True,
            )
        other = Plugin(id=other_id)
        om = other.manifest() if other.exists() else None
        oa = om.get_action(other_aid) if om is not None else None
        if oa is not None:
            return _open_plugin_form(ctx, other_id, oa, {})
    if action_needs_form(action, params):
        return _open_plugin_form(ctx, plugin_id, action, params)
    env = None
    try:
        from xlii.vault import env_for_plugins
        overrides = env_for_plugins([p])
        if overrides:
            env = {**__import__("os").environ, **overrides}
    except Exception:
        # env stays None, so the plugin runs with the ambient environment.
        pass
    try:
        result = invoke_action(plugin_id, raw, action_id, params, env=env)
    except ValueError as e:
        return ToolResult(f"plugin_call failed: {e}", is_error=True)

    # A hard failure (no body at all) is an error the model must react to;
    # an HTTP error WITH a body is surfaced, matching the prior behaviour.
    is_error = bool(result.error and not result.body)

    if result.mode == OUTPUT_INTERPRET:
        return ToolResult(_cap_output(ctx, result.model_text), is_error=is_error)

    # raw / schema — the deterministic modes: body to the user, receipt to the model.
    shown = _emit_plugin_user_output(ctx, result.user_text)
    receipt = result.receipt if shown else (
        result.receipt + " [note: no user console attached — body not displayed]"
    )
    return ToolResult(receipt, is_error=is_error)


def _strip_renderer_sections(text: str) -> str:
    """Drop output_schema / output_renderer blocks from plugin markdown.

    Those sections drive xlii's own response rendering — to the model they
    are unusable YAML noise that crowds out the parts it needs (auth, actions,
    params, response_shape)."""
    out: list[str] = []
    skip_indent: Optional[int] = None
    for line in text.splitlines():
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if skip_indent is not None:
            if stripped and indent <= skip_indent:
                skip_indent = None  # block ended; fall through to normal handling
            else:
                continue
        if stripped.startswith(("output_schema:", "output_renderer")):
            skip_indent = indent
            continue
        out.append(line)
    return "\n".join(out)
