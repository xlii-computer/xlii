"""Chat backends — which API brain drives a worker pass (gigwork G0/G1).

The worker loop's tools, gating, and read-only contract are xlii's; the *brain*
behind ``chat.completions.create`` is swappable. Home is the xAI plane
(``Clients.chat``, today's path, untouched when no backend is passed). A **gig**
is a named OpenAI-compatible endpoint (Kimi, DeepSeek, OpenRouter, Ollama, …)
hired for one bounded pass — never promoted to orchestrator.

Capabilities are enforced at schema-filter time, not prompt theater: a backend
without the xAI server plane never even sees ``search_project`` / ``web_search``
/ ``x_search`` in its tool list (proposals/gigwork.md §4).

Provider registry (G1) lives in ``config.json``::

    "gigwork": {
      "providers": {
        "kimi": {"kind": "openai_compat",
                 "base_url": "https://api.moonshot.cn/v1",
                 "api_key_env": "KIMI_API_KEY",
                 "model": "moonshot-v1-128k"}
      },
      "defaults": {
        "allow": ["kimi"],
        "investigate_gig": "kimi"   # or investigate_gaggle, not both
      }
    }

Keys come ONLY from the environment (``api_key_env``) — an inline ``api_key``
in config is refused outright, so an exported config can never carry a secret.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

# Capability atoms. CAP_XAI_SERVER covers everything that rides the home xAI
# plane regardless of which chat brain runs: Collections RAG + server tools.
CAP_CHAT = "chat"
CAP_TOOLS = "tools"
CAP_XAI_SERVER = "xai_server"

HOME_CAPABILITIES = frozenset({CAP_CHAT, CAP_TOOLS, CAP_XAI_SERVER})
GIG_CAPABILITIES = frozenset({CAP_CHAT, CAP_TOOLS})

# Tool names stripped from a pass whose backend lacks CAP_XAI_SERVER.
# ``xai_docs`` is NOT in this set — it is an outbound HTTP read of the
# hosted docs MCP, not an xAI Responses server tool. A gig brain can
# still look up how Grok/the API runs.
XAI_SERVER_TOOL_NAMES = frozenset({"search_project", "web_search", "x_search"})

_VALID_KINDS = frozenset({"anthropic_native", "openai_compat"})


class ChatBackend:
    """One chat brain: ``create(**kwargs)`` plus identity + capability flags.

    ``model`` is the backend's fixed model ("" = caller resolves per role, the
    home behavior). Subclasses implement :meth:`create` with the OpenAI
    chat-completions call signature (messages / tools / temperature / …).
    """

    label: str = "?"
    model: str = ""
    capabilities: frozenset = GIG_CAPABILITIES

    def create(self, **kwargs: Any) -> Any:  # pragma: no cover - interface
        raise NotImplementedError

    def allows_tool(self, name: str) -> bool:
        """Schema-filter hook: xAI-plane tools only on xAI-plane backends."""
        if name in XAI_SERVER_TOOL_NAMES:
            return CAP_XAI_SERVER in self.capabilities
        return True


class HomeBackend(ChatBackend):
    """Today's path as a backend: wraps ``Clients.chat`` (used by jams/tests;
    a ``WorkerAgent`` with no backend still calls the clients directly)."""

    capabilities = HOME_CAPABILITIES

    def __init__(self, clients: Any) -> None:
        self._clients = clients
        self.label = getattr(clients, "label", "primary")
        self.model = ""  # per-role resolution stays with the caller

    def create(self, **kwargs: Any) -> Any:
        return self._clients.chat.chat.completions.create(**kwargs)


class OpenAICompatBackend(ChatBackend):
    """A gig: any OpenAI-compatible chat endpoint, keyed from the environment."""

    capabilities = GIG_CAPABILITIES

    def __init__(self, *, label: str, model: str, api_key: str, base_url: str,
                 temperature: Optional[float] = None,
                 cache_marks: bool = False) -> None:
        from openai import OpenAI

        self.label = label
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.cache_marks = cache_marks
        self._chat = OpenAI(api_key=api_key, base_url=base_url)

    def create(self, **kwargs: Any) -> Any:
        # xAI prompt-cache headers and stream_options are home-plane plumbing —
        # never send them to a foreign endpoint (Kimi etc. reject them).
        kwargs.pop("extra_headers", None)
        kwargs.pop("stream_options", None)
        # Foreign brains have their own parameter rules (Kimi K2 allows ONLY
        # temperature=1; others reject sampling knobs the home plane accepts).
        # The caller's temperature is ALWAYS dropped: a provider that pins one
        # sends that, otherwise the endpoint's own default applies.
        kwargs.pop("temperature", None)
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.cache_marks:
            msgs = kwargs.get("messages")
            if isinstance(msgs, list):
                kwargs["messages"] = _with_cache_marks(msgs)
        return self._chat.chat.completions.create(**kwargs)


def _markable(msg: Any) -> bool:
    """A message whose content can carry a cache_control part: a dict with a
    non-empty string content, or a parts list holding at least one text part.
    Assistant tool-call turns (content None / SDK objects) are never touched."""
    if not isinstance(msg, dict):
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return bool(content)
    if isinstance(content, list):
        return any(isinstance(p, dict) and "text" in p for p in content)
    return False


def _marked(msg: "dict[str, Any]") -> "dict[str, Any]":
    """A copy of ``msg`` with ``cache_control: ephemeral`` on its last text
    part (string content converts to parts form). Pure — the worker loop
    reuses its messages list across iterations, so nothing is mutated."""
    out = dict(msg)
    content = out["content"]
    if isinstance(content, str):
        out["content"] = [{"type": "text", "text": content,
                           "cache_control": {"type": "ephemeral"}}]
        return out
    parts = list(content)
    for i in range(len(parts) - 1, -1, -1):
        if isinstance(parts[i], dict) and "text" in parts[i]:
            parts[i] = {**parts[i], "cache_control": {"type": "ephemeral"}}
            break
    out["content"] = parts
    return out


def _with_cache_marks(messages: "list[Any]") -> "list[Any]":
    """Copy of ``messages`` with anthropic-style cache breakpoints on the last
    system message and the last user message.

    Anthropic (and aggregators that front it, e.g. OpenRouter) cache the prefix
    up to a marked block — automatic server-side caching (DeepSeek, OpenAI,
    Gemini) needs none of this. Marking system covers the tools+system harness
    prefix (the bulk of a worker pass's tokens); marking the last user message
    extends the cached prefix over the task, so a multi-iteration tool loop
    re-reads it instead of re-buying it. Roles stay system/user only — tool
    and assistant messages keep their wire shape."""
    sys_i = user_i = -1
    for i, m in enumerate(messages):
        if not _markable(m):
            continue
        role = m.get("role")
        if role == "system":
            sys_i = i
        elif role == "user":
            user_i = i
    out = list(messages)
    for i in {sys_i, user_i} - {-1}:
        out[i] = _marked(out[i])
    return out


# --------------------------------------------------------------------------- #
#  Preset catalog — known OpenAI-compatible endpoints, one `/gigwork add` away
# --------------------------------------------------------------------------- #
#
# Conventions only: base_url + the conventional key env + a sane default model.
# The model lands in YOUR config where you can edit it (`--model` overrides at
# add time); slugs drift faster than endpoints, so treat them as suggestions.
# Nothing here is contacted until you hire — presets are inert strings.

GIG_PRESETS: dict[str, dict[str, Any]] = {
    "kimi": {
        "base_url": "https://api.kimi.com/coding/v1",
        "api_key_env": "KIMI_API_KEY",
        "model": "k3",
        "note": "Kimi For Coding (coding subscription — NOT the Moonshot chat platform)",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-chat",
        "note": "DeepSeek V3 chat (deepseek-reasoner for R1)",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1/",
        "api_key_env": "ANTHROPIC_API_KEY",
        "model": "claude-sonnet-5",
        "note": "Claude via Anthropic's OpenAI-compat layer (same key as /consult)",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
        "model": "gemini-2.5-flash",
        "note": "Google Gemini via its OpenAI-compat endpoint",
    },
    "huggingface": {
        "base_url": "https://router.huggingface.co/v1",
        "api_key_env": "HF_TOKEN",
        "model": "meta-llama/Llama-3.3-70B-Instruct",
        "note": "HF Inference Providers router — any hosted slug, one token",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "model": "openrouter/auto",
        "note": "aggregator — one key, many models",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "model": "llama-3.3-70b-versatile",
        "note": "very fast open-weights inference",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "api_key_env": "TOGETHER_API_KEY",
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "note": "open-weights catalog",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "api_key_env": "MISTRAL_API_KEY",
        "model": "mistral-large-latest",
        "note": "Mistral platform",
    },
    "fireworks": {
        "base_url": "https://api.fireworks.ai/inference/v1",
        "api_key_env": "FIREWORKS_API_KEY",
        "model": "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "note": "open-weights serving",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "model": "gpt-4o-mini",
        "note": "OpenAI platform",
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key_env": "OLLAMA_API_KEY",
        "model": "llama3.2",
        "note": "local Ollama — no key needed; model = whatever you've pulled",
        "key_optional": True,
    },
}


# --------------------------------------------------------------------------- #
#  Provider registry (G1) — parse config, mint backends, friendly errors
# --------------------------------------------------------------------------- #


class GigError(RuntimeError):
    """Configuration/lookup problem a user can act on (message is the fix)."""


# Hosts whose "auto" cache setting means "send cache_control marks".
_ANTHROPIC_CACHE_HOSTS = frozenset({"api.anthropic.com"})


@dataclass(frozen=True)
class GigProvider:
    name: str
    kind: str
    base_url: str
    api_key_env: str
    model: str
    # Local endpoints (Ollama) authenticate with nothing; the OpenAI client
    # still wants a string, so resolve sends a placeholder when the env is
    # absent instead of refusing.
    key_optional: bool = False
    # Foreign brains have their own parameter rules (Kimi K2 allows ONLY
    # temperature=1). None = omit the parameter and let the endpoint use its
    # own default; a number pins it for this provider.
    temperature: Optional[float] = None
    # Prompt-cache marks ("auto" | "on" | "off"; config: "auto" | true | false).
    # Anthropic-style endpoints cache nothing without explicit cache_control
    # breakpoints; auto turns them on for api.anthropic.com only — providers
    # that cache server-side (DeepSeek, OpenAI, Gemini) need no marks, and
    # "on" opts in an aggregator/proxy that honors them (e.g. OpenRouter).
    cache: str = "auto"

    @property
    def key_set(self) -> bool:
        return self.key_optional or bool(os.environ.get(self.api_key_env, "").strip())

    @property
    def host(self) -> str:
        return self.base_url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]

    @property
    def is_local(self) -> bool:
        return self.host in {"127.0.0.1", "localhost", "::1"}

    @property
    def cache_effective(self) -> bool:
        if self.cache == "on":
            return True
        if self.cache == "off":
            return False
        if self.kind == "anthropic_native":
            return True
        return self.host in _ANTHROPIC_CACHE_HOSTS


def gig_providers(cfg: Any) -> dict[str, GigProvider]:
    """Validated ``gigwork.providers`` map from config. Bad entries raise
    :class:`GigError` naming the entry and the fix — never silently dropped
    (a typo'd provider that vanishes would read as "gigwork is broken")."""
    block = getattr(cfg, "gigwork", None) or {}
    raw = block.get("providers") if isinstance(block, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, GigProvider] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            raise GigError(f"gigwork.providers.{name}: expected an object")
        if "api_key" in entry:
            raise GigError(
                f"gigwork.providers.{name}: inline api_key is refused — "
                "put the key in the environment and reference it via api_key_env"
            )
        kind = str(entry.get("kind", "openai_compat"))
        if kind not in _VALID_KINDS:
            raise GigError(
                f"gigwork.providers.{name}: unknown kind {kind!r} "
                f"(supported: {', '.join(sorted(_VALID_KINDS))})"
            )
        base_url = str(entry.get("base_url", "")).strip()
        api_key_env = str(entry.get("api_key_env", "")).strip()
        model = str(entry.get("model", "")).strip()
        missing = [k for k, v in
                   (("base_url", base_url), ("api_key_env", api_key_env), ("model", model))
                   if not v]
        if missing:
            raise GigError(
                f"gigwork.providers.{name}: missing {', '.join(missing)}"
            )
        temperature = entry.get("temperature")
        if temperature is not None:
            try:
                temperature = float(temperature)
            except (TypeError, ValueError):
                raise GigError(
                    f"gigwork.providers.{name}: temperature must be a number; "
                    f"got {temperature!r}"
                ) from None
        cache = entry.get("cache", "auto")
        if cache is True:
            cache = "on"
        elif cache is False:
            cache = "off"
        elif cache != "auto":
            raise GigError(
                f'gigwork.providers.{name}: cache must be true, false, or '
                f'"auto"; got {cache!r}'
            )
        out[str(name)] = GigProvider(
            name=str(name), kind=kind, base_url=base_url,
            api_key_env=api_key_env, model=model,
            key_optional=bool(entry.get("key_optional", False)),
            temperature=temperature,
            cache=cache,
        )
    return out


def _consult_gig_name(cfg: Any) -> str:
    """Name of the gigwork provider currently bound as the /consult judge, or ''
    when consult is unset, inline, or not gig-bound. Best-effort: a config with
    no consult profile (e.g. a bare SimpleNamespace in tests) yields ''."""
    fn = getattr(cfg, "consult_profile", None)
    if not callable(fn):
        return ""
    try:
        prof = fn() or {}
    except Exception:
        return ""
    return str(prof.get("gig") or "").strip()


def gigwork_status_suffixes(cfg: Any) -> list[str]:
    """Extra /status lines for configured gigwork providers (gig/<name>):
    model, endpoint, and whether the referenced env key is present. Mirrors
    ``harness_status_suffixes`` — the two blocks sit side by side in /status.
    Every gigwork model qualifies as a judge, so the one currently bound to
    /consult is marked here (``· consult judge``) rather than in a row of its
    own — see ``consult_status_line``."""
    lines: list[str] = []
    try:
        providers = gig_providers(cfg)
    except GigError:
        return ["  gigwork:  [yellow]config error in gigwork.providers (see /gigwork ls)[/yellow]"]
    allow = set(gig_allowlist(cfg))
    consult_name = _consult_gig_name(cfg)
    for p in sorted(providers.values(), key=lambda p: p.name):
        if p.key_optional:
            key = "[green]no key needed[/green]"
        elif p.key_set:
            key = "[green]key set[/green]"
        else:
            key = f"[red]${p.api_key_env} unset[/red]"
        judge = " · [cyan]consult judge[/cyan]" if p.name == consult_name else ""
        agent = " · agent-allowed" if p.name in allow else ""
        lines.append(f"  gig/{p.name}:  {p.model} · {p.base_url} · {key}{judge}{agent}")
    return lines


def gig_allowlist(cfg: Any) -> list[str]:
    """Providers the ORCHESTRATOR may hire via dispatch (G2). The slash command
    is the human and may hire any configured provider; the model only gets the
    explicit allow list (empty = the agent can hire nothing)."""
    block = getattr(cfg, "gigwork", None) or {}
    defaults = block.get("defaults") if isinstance(block, dict) else None
    allow = defaults.get("allow") if isinstance(defaults, dict) else None
    if not isinstance(allow, list):
        return []
    return [str(a) for a in allow if isinstance(a, str) and a.strip()]


def gigwork_investigate_defaults(cfg: Any) -> tuple[str, str]:
    """``(investigate_gig, investigate_gaggle)`` from ``gigwork.defaults``.

    Empty strings mean unset. Both set is a config error — callers fail closed
    rather than picking one (G4).
    """
    block = getattr(cfg, "gigwork", None) or {}
    defaults = block.get("defaults") if isinstance(block, dict) else None
    if not isinstance(defaults, dict):
        return "", ""

    def _one(key: str) -> str:
        raw = defaults.get(key)
        return raw.strip() if isinstance(raw, str) else ""

    return _one("investigate_gig"), _one("investigate_gaggle")


def resolve_gig_backend(cfg: Any, name: str) -> ChatBackend:
    """Mint the backend for provider ``name``, or raise a :class:`GigError`
    whose message IS the fix (unknown name → list what exists; env unset →
    name the variable). Never falls back to the home plane pretending to be
    the gig."""
    providers = gig_providers(cfg)
    provider = providers.get(name)
    if provider is None:
        known = ", ".join(sorted(providers)) or "(none configured)"
        raise GigError(
            f"unknown gig provider {name!r} — configured: {known}. "
            "Add it under gigwork.providers in ~/.config/xlii/config.json"
        )
    api_key = os.environ.get(provider.api_key_env, "").strip()
    if not api_key:
        if not provider.key_optional:
            raise GigError(
                f"gig provider {name!r}: environment variable "
                f"{provider.api_key_env} is not set"
            )
        api_key = "unused"  # local endpoints (Ollama) ignore it; client wants a string
    if provider.kind == "anthropic_native":
        from xlii.anthropic_native import AnthropicNativeBackend

        return AnthropicNativeBackend(
            label=provider.name,
            model=provider.model,
            api_key=api_key,
            base_url=provider.base_url,
            temperature=provider.temperature,
            cache_marks=provider.cache_effective,
        )
    return OpenAICompatBackend(
        label=provider.name,
        model=provider.model,
        api_key=api_key,
        base_url=provider.base_url,
        temperature=provider.temperature,
        cache_marks=provider.cache_effective,
    )


def add_provider_to_config(
    cfg: Any,
    name: str,
    *,
    preset: "str | None" = None,
    base_url: str = "",
    api_key_env: str = "",
    model: str = "",
) -> GigProvider:
    """Write one provider block into ``cfg.gigwork`` (caller persists via
    ``cfg.save()``). From a preset, explicit fields override its suggestions;
    custom entries must supply all three. Never touches keys — the env var is
    a NAME here, the secret stays in the environment."""
    entry: dict[str, Any] = {}
    if preset is not None:
        p = GIG_PRESETS.get(preset)
        if p is None:
            known = ", ".join(sorted(GIG_PRESETS))
            raise GigError(f"unknown preset {preset!r} — presets: {known}")
        entry = {k: p[k] for k in ("base_url", "api_key_env", "model") if k in p}
        if p.get("key_optional"):
            entry["key_optional"] = True
    if base_url:
        entry["base_url"] = base_url
    if api_key_env:
        entry["api_key_env"] = api_key_env
    if model:
        entry["model"] = model
    entry.setdefault("kind", "openai_compat")

    block = dict(getattr(cfg, "gigwork", None) or {})
    providers = dict(block.get("providers") or {})
    providers[name] = entry
    block["providers"] = providers
    block.setdefault("defaults", {"allow": []})
    cfg.gigwork = block
    # Validate the whole map through the one parser so a bad add is refused
    # with the same message a bad hand-edit would get.
    return gig_providers(cfg)[name]


def remove_provider_from_config(cfg: Any, name: str) -> bool:
    """Drop one provider block (caller persists). True if it existed."""
    block = dict(getattr(cfg, "gigwork", None) or {})
    providers = dict(block.get("providers") or {})
    existed = name in providers
    providers.pop(name, None)
    block["providers"] = providers
    allow = (block.get("defaults") or {}).get("allow")
    if isinstance(allow, list) and name in allow:
        block["defaults"] = {**block["defaults"], "allow": [a for a in allow if a != name]}
    cfg.gigwork = block
    return existed


def set_provider_allowed(cfg: Any, name: str, allowed: bool) -> "list[str]":
    """Toggle ``name`` on ``gigwork.defaults.allow`` — what the ORCHESTRATOR
    may hire via ``dispatch_subagent(gig=)`` (caller persists). The provider
    must be configured: an allow entry for a ghost would surface as a broken
    dispatch later instead of a typo now. Returns the new allow list."""
    providers = gig_providers(cfg)
    if name not in providers:
        known = ", ".join(sorted(providers)) or "(none configured)"
        raise GigError(f"unknown gig provider {name!r} — configured: {known}")
    block = dict(getattr(cfg, "gigwork", None) or {})
    defaults = dict(block.get("defaults") or {})
    raw = defaults.get("allow")
    allow = [a for a in raw if isinstance(a, str) and a.strip()] if isinstance(raw, list) else []
    if allowed and name not in allow:
        allow.append(name)
    elif not allowed:
        allow = [a for a in allow if a != name]
    defaults["allow"] = allow
    block["defaults"] = defaults
    cfg.gigwork = block
    return allow
