"""Secondary AI query for /consult and cross-vendor loop judges.

Implements anthropic, openai, and xai backends via urllib (no SDKs).
Config-driven, errors at boundary for missing config/env/non-2xx.
Never exposed as an agent tool.

A judge profile may BIND a gigwork provider instead of re-declaring an
endpoint: ``{"kind": "cross_vendor", "gig": "haiku"}`` resolves endpoint /
key-env / model from ``gigwork.providers`` (the one registry of foreign
brains) at call time and speaks the provider's OpenAI-compat dialect — so
every gigwork preset (Kimi, DeepSeek, Gemini, a local Ollama, …) is a legal
judge, and /consult · /verify-followups · loop judges all inherit the
binding through this one seam. A profile ``model`` still overrides.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from xlii.config import GlobalConfig
from xlii.cost import estimate_cost


@dataclass
class SecondaryResponse:
    text: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Optional[float] = None


def _resolve_profile(profile: Optional[dict[str, Any]]) -> dict[str, Any]:
    if profile and (profile.get("provider") or profile.get("gig")):
        return profile
    cfg = GlobalConfig.load()
    if profile is None:
        try:
            return cfg.consult_profile()
        except RuntimeError:
            # No consult profile configured -- fall through to the default resolution below.
            pass
    sec = getattr(cfg, "secondary_ai", {}) or {}
    if sec and sec.get("provider"):
        return sec
    judges = cfg.effective_judges()
    consult_name = (getattr(cfg, "consult", None) or {}).get("default_judge", "consult")
    if consult_name in judges:
        return judges[consult_name]
    raise RuntimeError(
        'secondary AI not configured. Add a judges profile or secondary_ai block to '
        '~/.config/xlii/config.json, e.g. '
        '"judges": {"anthropic": {"kind": "cross_vendor", "provider": "anthropic", '
        '"model": "claude-sonnet-4-6", "api_key_env": "ANTHROPIC_API_KEY"}}'
    )


def _profile_from_gig(prof: dict[str, Any], name: str) -> dict[str, Any]:
    """Resolve a gig-bound judge profile against ``gigwork.providers`` — the
    binding is by NAME, so the endpoint/key/model are read from the one
    registry at call time (edit the provider once, every job follows). The
    profile's own ``model`` (or a --model override) beats the provider's."""
    from xlii.chat_backend import GigError, gig_providers

    try:
        providers = gig_providers(GlobalConfig.load())
    except GigError as e:
        raise RuntimeError(str(e)) from e
    p = providers.get(name)
    if p is None:
        known = ", ".join(sorted(providers)) or "(none configured)"
        raise RuntimeError(
            f"judge profile binds gig {name!r}, which is not a configured "
            f"gigwork provider — configured: {known}. Add it: /gigwork add {name}"
        )
    return {
        **prof,
        "provider": "gig",
        "model": prof.get("model") or p.model,
        "api_key_env": p.api_key_env,
        "_gig_name": name,
        "_gig_base_url": p.base_url,
        "_gig_key_optional": p.key_optional,
    }


def _extract_usage(resp_json: dict, provider: str) -> tuple[int, int]:
    if provider == "anthropic":
        usage = resp_json.get("usage") or {}
        return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
    usage = resp_json.get("usage") or {}
    return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))


def query_with_profile(
    messages: list[dict] | None,
    question: str,
    *,
    scope: str | None = None,
    profile: Optional[dict[str, Any]] = None,
    system: Optional[str] = None,
    pricing: Optional[dict] = None,
) -> SecondaryResponse:
    """Send messages to a configured secondary provider. Returns text + usage."""
    _ = scope  # reserved for future history slicing
    prof = _resolve_profile(profile)
    gig_name = str(prof.get("gig") or "").strip()
    if gig_name:
        prof = _profile_from_gig(prof, gig_name)
    provider = str(prof.get("provider", "")).lower().strip()
    model = prof.get("model") or (
        "claude-sonnet-4-6" if provider == "anthropic" else "gpt-4o-mini"
    )
    api_key_env = prof.get("api_key_env")
    if not api_key_env:
        raise RuntimeError(f"api_key_env missing in judge profile for provider {provider}.")
    api_key = os.environ.get(str(api_key_env), "").strip()
    if not api_key:
        if prof.get("_gig_key_optional"):
            api_key = "unused"  # local endpoints (Ollama) ignore it; HTTP wants a string
        else:
            raise RuntimeError(f"{api_key_env} not set in environment.")

    chat_messages: list[dict] = list(messages) if messages else []
    if question and str(question).strip():
        chat_messages.append({"role": "user", "content": str(question).strip()})
    if not chat_messages:
        chat_messages = [{"role": "user", "content": "Respond."}]

    if provider == "anthropic":
        system_prompt = system
        filtered: list[dict] = []
        for m in chat_messages:
            role = m.get("role")
            content = m.get("content", "")
            if role == "system":
                system_prompt = content
            elif role in ("user", "assistant"):
                filtered.append({"role": role, "content": content})
        while filtered and filtered[0].get("role") != "user":
            filtered.pop(0)
        if not filtered:
            filtered = [{"role": "user", "content": question or "Respond."}]
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": 2048,
            "messages": filtered,
        }
        if system_prompt:
            payload["system"] = system_prompt
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    elif provider in ("openai", "xai", "gig"):
        msgs: list[dict] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(chat_messages)
        payload = {"model": model, "messages": msgs, "max_tokens": 2048}
        if provider == "xai":
            # Same region pin as the primary plane (cfg.region / XAI_REGION):
            # a consult key hits the same xAI ingress the main agent does.
            # (GlobalConfig is the module-level import — a local re-import here
            # would shadow it and UnboundLocalError the anthropic/openai paths
            # that read it later for pricing.)
            url = f"{GlobalConfig.load().api_base_url()}/chat/completions"
        elif provider == "gig":
            # The bound provider's own OpenAI-compat endpoint — any gigwork
            # preset (Kimi, DeepSeek, Gemini, a local Ollama) can judge.
            url = str(prof["_gig_base_url"]).rstrip("/") + "/chat/completions"
        else:
            url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    else:
        raise RuntimeError(
            f"Unsupported provider={provider!r}. Use anthropic, openai, or xai."
        )

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body_bytes = resp.read()
            resp_json = json.loads(body_bytes.decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        err_body = ""
        if e.fp:
            err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {provider}/{model}: {err_body[:400]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error to {provider}: {e.reason}") from e

    if provider == "anthropic":
        blocks = resp_json.get("content", []) or []
        text = "".join(
            b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        choices = resp_json.get("choices", []) or []
        msg = choices[0].get("message", {}) if choices else {}
        text = msg.get("content", "") or str(resp_json)

    in_t, out_t = _extract_usage(resp_json, provider)
    price_table = pricing if pricing is not None else GlobalConfig.load().pricing
    cost = estimate_cost(price_table, model, in_t, out_t)

    return SecondaryResponse(
        text=text.strip() if text else "(empty response from secondary model)",
        model=model,
        # A gig-bound judge reports its binding, not the internal branch name —
        # loop records and consult headers stay honest about who judged.
        provider=f"gigwork[{prof['_gig_name']}]" if provider == "gig" else provider,
        prompt_tokens=in_t,
        completion_tokens=out_t,
        cost_usd=cost,
    )


def query(
    messages: list[dict] | None,
    question: str,
    *,
    scope: str | None = None,
    profile: Optional[dict[str, Any]] = None,
) -> str:
    """Legacy /consult entry — returns plain text only."""
    return query_with_profile(messages, question, scope=scope, profile=profile).text


def query_verdict(
    brief: str,
    *,
    profile: dict[str, Any],
    mode: str = "verify",
    pricing: Optional[dict] = None,
    system_prompt: Optional[str] = None,
) -> SecondaryResponse:
    """Cold cross-vendor verdict call for the autonomous loop."""
    from xlii.repl_cmds.review import PEER_SYSTEM_PROMPT, VERIFIER_SYSTEM_PROMPT

    system = system_prompt or (PEER_SYSTEM_PROMPT if mode == "peer" else VERIFIER_SYSTEM_PROMPT)
    return query_with_profile(
        [{"role": "user", "content": brief}],
        "",
        profile=profile,
        system=system,
        pricing=pricing,
    )
