---
sources: file://xlii/chat_backend.py, file://xlii/anthropic_native.py, file://docs/help/topics/modes.md#L44-153, file://docs/help/topics/workflows.md#L97-121, file://docs/GUIDE.md#L680-730, file://xlii/chat_tiers.py#L1-90, file://xlii/repl_cmds/code.py#L700-790, file://xlii/config.py#L534-544, file://proposals/done/overnight-slam.md#L140-160
verified: false
---
# gigwork-and-foreign-brains

One registry of foreign brains: a swappable *chat backend* under the worker loop, a provider catalog in config, and a set of surfaces (`/gigwork`, `/gig`, `/gaggle`, `/jam`, `/consult --set-to`, `/plan --with`) that all hire from that one registry. The worker loop's tools, gating, and read-only contract stay xlii's — only the model behind `chat.completions.create` is hired out. A gig is hired for one bounded pass; it is never promoted to orchestrator.

## The ChatBackend seam

`xlii/chat_backend.py` defines `ChatBackend`: `create(**kwargs)` in the OpenAI chat-completions signature, plus a label, a model, and a capability set. Three implementations:

- **`HomeBackend`** wraps `Clients.chat` — today's xAI plane, `HOME_CAPABILITIES`. A `WorkerAgent` with no backend calls the clients directly; passing `HomeBackend` is the same path as an object.
- **`OpenAICompatBackend`** — a gig: any OpenAI-compatible endpoint (Kimi, DeepSeek, OpenRouter, Ollama, …), keyed from the environment.
- **`AnthropicNativeBackend`** (`xlii/anthropic_native.py`) — speaks Anthropic's native `/v1/messages` (see below).

Capability is enforced at **schema-filter** time, not prompt theater. `CAP_XAI_SERVER` covers the home-plane server tools (`search_project`, `web_search`, `x_search`); `allows_tool` strips those names from any backend lacking the atom, so a gig never even sees them in its tool list. Home gets `{chat, tools, xai_server}`; a gig gets `{chat, tools}`.

## Providers, presets, allowlist

The provider registry lives in `config.json` under `gigwork.providers`. `gig_providers(cfg)` validates the whole map and raises `GigError` (whose message *is* the fix) naming the bad entry — never silently dropping it. `kind` is `openai_compat` (default) or `anthropic_native`.

**Keys are env-only.** An inline `api_key` in config is refused outright (`GigError`); `api_key_env` names the variable, the secret stays in the environment, so an exported config can never carry a key. Local endpoints (Ollama) set `key_optional`.

`GIG_PRESETS` is a catalog of known endpoints — kimi, deepseek, anthropic, gemini, huggingface, openrouter, groq, together, mistral, fireworks, openai, ollama — each a `base_url` + conventional key env + a suggested model. Presets are inert strings; nothing is contacted until you hire. `/gigwork add <preset>` writes a provider block (explicit `--model` etc. override the suggestion).

Two hiring scopes:
- The **slash command is the human** and may hire any *configured* provider (`/gigwork <provider> <task>`, `--kit bash` for a read+shell palette).
- `gigwork.defaults.allow` is the **orchestrator's** permit — the only names the main agent may pass as `dispatch_subagent(gig=…)`. Empty list = the agent can hire nothing. Toggle with `/gigwork allow`/`deny` (see [[trust-and-gates]]).

Per-provider knobs: `temperature` pins a value for endpoints with their own rules (the caller's temperature is always dropped); `cache` (`true`|`false`|`"auto"`) drives prompt-cache marks. Anthropic-style endpoints cache nothing without `cache_control` breakpoints, so `auto` marks the system prefix + last-user message for `api.anthropic.com` only; DeepSeek/OpenAI/Gemini cache server-side and need none; `true` opts in an aggregator that honors marks (OpenRouter fronting Claude). `/status` gains a `gig/<name>` line per provider (model, endpoint, key state) — see [[status-and-chrome]].

## Gaggles

`/gaggle` (synonym `/jam`; nested `/gigwork gaggle`) composes named multi-brain presets over the same seam — *who* + *cap* + *merge policy* + *budget*, not a graph editor. `write: false` is locked. Stock crews: `second-opinion` (home xAI explore + your default gig, then a synthesis listing agreements, conflicts, verdict), `debate` (same, gig first), `scout` (two gig passes, plain digest, no synthesis). Members are `backend[:kit][@model]` tokens, so one crew mixes providers, tool palettes, and per-member models. Members are read-only workers; a failed member is reported and the rest proceed. The orchestrator may pass `dispatch_subagent(gaggle="second-opinion")` when every foreign member is on `gigwork.defaults.allow`. A heavy investigate hop accepts `gaggle=` / `gig=` on `request_deep_search`, and falls through to `gigwork.defaults.investigate_gaggle` or `investigate_gig` when the call site is unset (not both). Hire is allowlist-gated with no silent home fallback.

## anthropic_native

The compat-layer experiment reported **ZERO cached tokens** for `cache_control` marks — that is why `anthropic_native` exists (PR #297). `AnthropicNativeBackend` translates the worker loop's OpenAI-shaped request to `/v1/messages`: system → top-level `system`, `tool` role → `tool_result` blocks, assistant `tool_calls` → `tool_use` blocks, tools schema → Anthropic tools, `cache_control` (ephemeral) on tools, system, and last-user. It adapts the response *back* to OpenAI shape (`msg.content`, `msg.tool_calls[i].function`, and `cache_read_input_tokens` → `prompt_tokens_details.cached_tokens`) so `WorkerAgent` and `/plan --with` need zero edits. Same `ANTHROPIC_API_KEY`, same `api.anthropic.com` base; `cache_effective` is always on for this kind.

**Scope is workers + `/plan --with` only (D18).** Judges stay on the compat path — one-shot calls where caching is pointless. Streaming is deferred: a `stream` request raises `GigError` ("anthropic_native does not stream yet — plan turns need a compat provider"); the documented workaround is to use an `openai_compat` provider for `/plan --with` on Claude until a native stream adapter ships (see modes.md).

## Judges bind to gigwork

`/consult --set-to <provider>` perma-hires a `gigwork.providers` entry as the default consult judge (PR #293) — a **binding by name** (`judges.consult = {"gig": "<name>"}`), so the endpoint/key/model stay defined once in the registry and loop judges accept the same `gig:` key. One registry says *who* exists; jobs say *which one* they use. Inline `judges` profiles remain for hand-rolled setups. `/consult` itself is a cross-vendor one-shot that never enters history (see [[command-surface]]).

## /plan --with

`/plan --with <provider>` hires a configured gig as **this plan session's planner**: plan-mode turns route through that endpoint (its model, its key), with the xAI server tools stripped by capability — it investigates with grep, glob, and file reads. The hire is session-scoped; `/execute` and `/cancel` end it, and execution always runs on the home plane. A gig-planned plan is indistinguishable downstream. See [[plan-surface]].

## Adjacent (home-plane, not foreign brains)

Two knobs sit near this surface but do **not** hire a foreign brain:

- **Chat tiers** (`xlii/chat_tiers.py`): `/tier fast|expert|heavy|auto` and the `>>tier` one-shot sigil overlay the *chat* model role's reasoning depth by mapping to home-plane model profiles (`economy`/`reason`) and a turn strategy — they never change the tool palette or the provider.
- **Region** (`cfg.region` / `XAI_REGION`, `config.py`): selects the active xAI region for the home plane; the env var overrides per-shell. A foreign gig carries its own `base_url`, so region does not apply to it.

## Related

[[command-surface]] (the `/gigwork`·`/gaggle`·`/jam`·`/consult` command family), [[trust-and-gates]] (the allow/deny permit and capability atoms), [[plan-surface]] (`/plan --with`), [[status-and-chrome]] (the `gig/<name>` status lines and Gigwork panel).
