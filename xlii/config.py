"""Global + per-project configuration."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from xlii.atomicio import write_text_atomic


def global_config_dir() -> Path:
    """Config dir, honoring the XLII_CONFIG_DIR env override (test seam —
    set it before importing xlii and nothing touches ~/.config/xlii)."""
    override = os.environ.get("XLII_CONFIG_DIR")
    if override:
        return Path(override)
    return Path.home() / ".config" / "xlii"


GLOBAL_CONFIG_DIR = global_config_dir()
GLOBAL_CONFIG_FILE = GLOBAL_CONFIG_DIR / "config.json"

PROJECT_DIR_NAME = ".xlii"
PROJECT_CONFIG_FILE = "project.json"
MANIFEST_FILE = "manifest.json"

# Project Shadow (the journal, Theme B) keeps its raw entries in a dedicated xAI
# Collection named with THIS prefix — distinct from the main project Collection
# ("xlii/<name>", see sync.init_project) so the two never collide and removal
# (A3 `xlii project rm`) can recognize + sweep orphan journal Collections.
# JRN-1 creates them and records the id on project.json (journal_collection_id).
JOURNAL_COLLECTION_PREFIX = "xlii-journal/"

# Reserved key label (in cfg.keys[]) for a key used EXCLUSIVELY by the journal —
# provisioned via `xlii journal key`. The pool keeps it out of the orchestrator +
# worker rotation (pool.primary/acquire) and hands it only to the journal, so the
# journal's LLM spend (rolling summaries + /askjo) is isolated for cost auditing.
JOURNAL_KEY_LABEL = "journal"

# grok-build-0.1: xAI's fast coding model (replaces the grok-code-fast line).
# Cheaper than the grok-4.x flagships ($1/$2 per M in/out) and tuned for code,
# so it's the default for both the orchestrator and the workers — and for the
# `help` role (/howto self-guide turns), which should stay cheap.
DEFAULT_MODEL = "grok-build-0.1"
DEFAULT_WORKER_MODEL = "grok-build-0.1"
DEFAULT_HELP_MODEL = "grok-build-0.1"

GLOBAL_API_HOST = "api.x.ai"


def normalize_api_region(region: Optional[str]) -> Optional[str]:
    """Return a safe xAI region label, or raise for values that cannot be a host label."""
    r = (region or "").strip().strip(".").lower()
    if not r or r in {"global", "off", "none"}:
        return None
    if (
        len(r) > 63
        or r.startswith("-")
        or r.endswith("-")
        or not all(ch.isascii() and (ch.isalnum() or ch == "-") for ch in r)
    ):
        raise ValueError(
            "region must be a single ASCII DNS label "
            "(letters, digits, dashes; no leading/trailing dash)"
        )
    return r


def xai_api_host(region: Optional[str] = None) -> str:
    """The bare xAI API host for *region* — ``us-west-2`` → ``us-west-2.api.x.ai``;
    ``None``/empty → the global edge. Unknown-but-DNS-shaped regions are xAI's
    to judge (DNS/TLS fails loudly on a typo); host-invalid values fall back to
    the global edge so config/env mistakes cannot brick process startup."""
    try:
        r = normalize_api_region(region)
    except ValueError:
        r = None
    return f"{r}.{GLOBAL_API_HOST}" if r else GLOBAL_API_HOST
# grok-4.3: general conversational / persona chat. Separate from `help` so
# /howto does not borrow the flagship (or the chat slot) by default.
DEFAULT_CHAT_MODEL = "grok-4.3"
DEFAULT_RETRIEVAL_MODE = "hybrid"

# Temperature defaults — orchestrator runs warmer (creative planning + tool
# strategy), workers run colder (precise, repeatable execution).
DEFAULT_ORCH_TEMP = 0.7
DEFAULT_WORKER_TEMP = 0.3
DEFAULT_CHAT_TEMP = 0.7


@dataclass
class KeyPair:
    api_key: str
    management_api_key: Optional[str] = None
    label: Optional[str] = None


# Namespace under which chat-key secrets live in the encrypted vault. The vault's
# layout is {namespace: {ref: secret}}; `keys[]` entries reference a secret here
# via `vault_ref`. Keep this stable — changing it strands migrated keys.
VAULT_CHATKEYS_NS = "xlii:chat-keys"


# Single template emitted when the user has no config yet.
# IMPORTANT: management_api_key is intentionally NOT in this file. It must be set
# via the XAI_MANAGEMENT_API_KEY environment variable. Keeping it out of any
# on-disk config makes it harder to leak through git, backups, or screen-shares.
# All chat keys in `keys[]` are bootstrappable from the management key and are
# easily revocable via `xlii bootstrap --revoke`.
CONFIG_TEMPLATE = {
    "_comment": (
        "REQUIRED: export XAI_MANAGEMENT_API_KEY=... in your shell before running xlii. "
        "It is the only privileged credential and should never live in a file. "
        "Run `xlii setup` to provision chat keys; they will be saved here. "
        "`pricing` is optional — fill in per-model rates from your xAI dashboard."
    ),
    "orchestrator_model": DEFAULT_MODEL,
    "worker_model": DEFAULT_WORKER_MODEL,
    "chat_model": DEFAULT_CHAT_MODEL,
    "help_model": DEFAULT_HELP_MODEL,
    "model": DEFAULT_MODEL,
    "orchestrator_temperature": DEFAULT_ORCH_TEMP,
    "worker_temperature": DEFAULT_WORKER_TEMP,
    "chat_temperature": DEFAULT_CHAT_TEMP,
    "retrieval_mode": DEFAULT_RETRIEVAL_MODE,
    "claim_gates": "warn",
    "team_id": "",
    "region": None,
    "keys": [],
    "max_tool_iterations": 20,
    "max_chat_tool_iterations": 8,
    "max_worker_iterations": 10,
    "editor": "",
    "image_editor": "",
    "browser": "",
    "tui_terminal": "",
    "tui_terminal_cwd": "project",
    "tui_terminal_cwd_path": "",
    "fallback_persona": "",
    "max_parallel_workers": 8,
    "model_profiles": {},
    "worker_models": {},
    "loop_defaults": {
        "merge_judge": "merge",
    },
    # Judge profiles: cross-vendor verdicts for /consult and loop judges. The
    # simple shape is a BINDING to the gigwork registry — {"kind":
    # "cross_vendor", "gig": "<provider>"} resolves endpoint/key/model from
    # gigwork.providers at call time (written by /consult --set-to <name>).
    # Inline provider/model/api_key_env profiles (below) remain for
    # hand-rolled setups.
    "judges": {
        "merge": {
            "kind": "cross_vendor",
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "api_key_env": "ANTHROPIC_API_KEY",
            "tier": "cross_org",
            "description": "merge resolution vetting (writer swarm W3)",
        }
    },
    # Reference only — ignored by xlii (the leading "_" keeps it out of `judges`).
    # /consult and /loop are NOT locked to anthropic: copy one of these into
    # `judges` above, point `consult.default_judge` at its name, then export the
    # `api_key_env` you chose. Supported providers: anthropic, openai, xai.
    "_judge_examples": {
        "anthropic": {"kind": "cross_vendor", "provider": "anthropic",
                      "model": "claude-sonnet-4-6", "api_key_env": "ANTHROPIC_API_KEY"},
        "openai": {"kind": "cross_vendor", "provider": "openai",
                   "model": "gpt-4o-mini", "api_key_env": "OPENAI_API_KEY"},
        "xai": {"kind": "cross_vendor", "provider": "xai",
                "model": "grok-4", "api_key_env": "XAI_CONSULT_KEY"},
    },
    "max_file_bytes": 1_000_000,
    "pricing": {
        # Fill in per-model rates from your xAI dashboard (USD per million tokens).
        # "grok-build-0.1": {"input_per_million": 1.0, "output_per_million": 2.0},
    },
    # Public browser gate (serve-public). base_url is REQUIRED for --public.
    "serve": {
        "public": {
            "base_url": "",
            "code_ttl_s": 300,
            "session_ttl_s": 28800,
            "idle_timeout_s": 1800,
            "max_sessions": 3,
            "closed_door": False,
            "redirect_off_url": "",
            "face_default": True,
        },
    },
    # Gigwork: named non-xAI worker brains (proposals/gigwork.md). Easiest via
    # `/gigwork add <preset>` (kimi · deepseek · anthropic · gemini ·
    # huggingface · openrouter · groq · together · mistral · fireworks ·
    # openai · ollama) — or by hand as below. Keys come ONLY from the
    # environment via api_key_env — inline api_key is refused. "defaults.allow"
    # is what the ORCHESTRATOR may hire; /gigwork (the human) can hire any
    # configured provider.
    "gigwork": {
        "providers": {
            # "kimi": {"kind": "openai_compat",
            #          "base_url": "https://api.moonshot.cn/v1",
            #          "api_key_env": "KIMI_API_KEY",
            #          "model": "moonshot-v1-128k"},
            # Optional per-provider keys: "temperature" pins a sampling value
            # (omitted = the endpoint's default), and "cache" (true|false|"auto")
            # controls anthropic-style cache_control marks — "auto" sends them
            # for api.anthropic.com only (server-side cachers need nothing).
        },
        # What the ORCHESTRATOR may hire via dispatch — toggled by
        # /gigwork allow|deny <name>; empty = the agent hires nothing.
        # Optional G4 pin for heavy investigate hops: a provider name
        # (`investigate_gig`) or a gaggle name (`investigate_gaggle`) —
        # not both. Call-site gig=/gaggle= on request_deep_search still
        # wins. Omitted = home worker.
        # "investigate_gig": "kimi",
        # "investigate_gaggle": "second-opinion",
        "defaults": {"allow": []},
        # Named multi-brain presets (/gaggle, synonym /jam). Stock
        # second-opinion/debate/scout ship in code; entries here add or
        # override by name (easiest via /gaggle add <name>
        # <backend[:kit][@model]>…). write is locked false. A member
        # "model" overrides the provider's model for that member only.
        # "jams": {"my-crew": {"members": [{"backend": "xai", "kit": "explore"},
        #                                     {"backend": "kimi", "kit": "explore",
        #                                      "model": "kimi-k2-turbo"}],
        #             "merge": "synth_conflicts", "max_parallel": 2,
        #             "write": false}},
    },
}


# Defaults for [serve.public] — mirrored in CONFIG_TEMPLATE above.
DEFAULT_SERVE_PUBLIC_CODE_TTL_S = 300
DEFAULT_SERVE_PUBLIC_SESSION_TTL_S = 28800
DEFAULT_SERVE_PUBLIC_IDLE_TIMEOUT_S = 1800
DEFAULT_SERVE_PUBLIC_MAX_SESSIONS = 3
DEFAULT_SERVE_PUBLIC_FACE_DEFAULT = True

MISSING_SERVE_PUBLIC_BASE_URL = (
    "serve --public requires serve.public.base_url in config.json "
    '(e.g. "https://your.domain"). Set it under the serve.public block — '
    "the entry page is operator-served content on your own domain."
)

MISSING_SERVE_PUBLIC_REDIRECT_OFF_URL = (
    "serve.public.closed_door requires serve.public.redirect_off_url in "
    'config.json (e.g. "https://example.org") — the 302 target for a GET / '
    "carrying neither a live grant nor a live ?code=."
)


def _serve_public_int(pub: dict[str, Any], key: str, default: int) -> int:
    """Coerce a serve.public numeric key; non-positive / missing → default."""
    raw = pub.get(key, default)
    if isinstance(raw, bool):   # bool is an int subclass; true → 1 is a silent trap
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


def _serve_public_int_nonneg(
    pub: dict[str, Any], key: str, default: int,
) -> int:
    """Like ``_serve_public_int`` but ``0`` is valid (e.g. face_linger_s off)."""
    if key not in pub:
        return default
    raw = pub.get(key)
    if isinstance(raw, bool):
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    return val if val >= 0 else default


@dataclass
class GlobalConfig:
    """All credentials and tunables live here. One file, one source of truth.

    Keys are listed in `keys[]`; the first entry is the primary (used for
    sync + the main agent). Workers round-robin through the rest. A top-level
    `management_api_key` is read from the XAI_MANAGEMENT_API_KEY environment
    variable. It is intentionally NOT loaded from disk — that one credential
    is privileged (creates other keys, manages collections) and should never
    live in a config file. Chat keys in `keys[]` can be auto-provisioned via
    `xlii setup` and are revocable via `xlii bootstrap --revoke`.
    """
    keys: list = field(default_factory=list)            # list[dict] | list[str]
    management_api_key: Optional[str] = None             # default for all entries
    team_id: Optional[str] = None                        # for bootstrap key creation
    model: str = DEFAULT_MODEL                           # legacy fallback
    orchestrator_model: Optional[str] = None             # main agent model
    worker_model: Optional[str] = None                   # subagent model
    chat_model: Optional[str] = None                     # persona / conversational chat model
    help_model: Optional[str] = None                     # /howto (and help-surface) model
    orchestrator_temperature: float = DEFAULT_ORCH_TEMP
    worker_temperature: float = DEFAULT_WORKER_TEMP
    chat_temperature: float = DEFAULT_CHAT_TEMP
    retrieval_mode: str = DEFAULT_RETRIEVAL_MODE
    # Turn-receipts / claim gates (turn-receipts-claim-gates P2):
    #   warn (default) — P0 edit-claim warning + receipt line on verify/publish
    #   strict         — receipt line on ANY unsubstantiated gate (edit too)
    #   off            — no claim warnings; receipts still record quietly
    claim_gates: str = "warn"
    max_file_bytes: int = 1_000_000
    # Tool output larger than this spills to .xlii/scratch/tool-output/ (full text
    # preserved on disk; the tool returns a head+tail preview + path). The agent
    # reads slices with read_file offset/limit — "dynamic context" (cursor-workflows
    # A2). Set 0 to disable spill (fall back to lossy inline truncation).
    tool_output_spill_threshold: int = 8192
    max_tool_iterations: int = 20
    # Tighter tool-loop cap for conversational turns (persona chat, the /mojo &
    # phone mouth): a chat reply should answer fast, not spiral through the full
    # coding budget of tool round-trips. Coding turns keep max_tool_iterations.
    max_chat_tool_iterations: int = 8
    max_worker_iterations: int = 10
    max_parallel_workers: int = 8
    pricing: dict = field(default_factory=dict)          # model -> {input_per_million, output_per_million}
    models_detected_at: Optional[str] = None             # ISO ts; set by `xlii setup` auto-detection
    # xAI API region — pins the chat/media/collections plane to a regional edge
    # ("us-west-2" → us-west-2.api.x.ai). None/"" = the global edge (api.x.ai).
    # Regional edges are independent ingress points, so pinning one rides out a
    # flapping global edge (2026-07-16 outage) and keeps traffic in-region. The
    # XAI_REGION env var overrides per-shell; the management API host is a
    # separate plane and never regionalized here.
    region: Optional[str] = None
    # Optional cross-vendor "second opinion" provider for /consult. Deliberately
    # NOT the primary (xAI) vendor — the whole point is an independent model.
    # Shape: {"provider": "anthropic"|"openai"|"xai", "model": str, "api_key_env": str}.
    secondary_ai: dict = field(default_factory=dict)
    # Autonomous loop (L0+): defaults and named judge profiles.
    loop_defaults: dict = field(default_factory=dict)
    judges: dict = field(default_factory=dict)
    # /consult default judge profile name (falls back to legacy secondary_ai).
    consult: dict = field(default_factory=dict)
    # Auto-discover skills installed by other Agent-Skills tools (grok-build's
    # ~/.grok/skills, Claude Code's ~/.claude/skills + plugin skills). Imported
    # skills sit below xlii's own in precedence. Set false to disable.
    import_foreign_skills: bool = True
    # TUI doorway hotkey modifier (commander mode). Alt-<letter> opens/toggles a content type in
    # Pane 2 (S skills · D docs · M marks · I images · F files); some terminals grab Alt for their
    # own menus, so the modifier is configurable — e.g. "alt", "ctrl+alt", "ctrl+shift+alt". A future
    # Options-menu panel flips this live (App.set_hotkey_modifier persists it here).
    tui_hotkey_modifier: str = "alt"
    # TUI app theme — the Textual color palette (e.g. "textual-dark", "nord", "gruvbox"). Empty means
    # leave Textual's default. Options → Theme… (the Themes panel) sets this live; App._apply_theme
    # validates against the running app's available themes and persists it here.
    tui_theme: str = ""
    # Face web skin (dark | light | slate | mojo | pack:<name>). Empty = client
    # default. Options → skins on the face persists here — localStorage does
    # not survive a Tauri relaunch. Pack ids are namespaced so they never
    # shadow the four compiled skins.
    face_skin: str = ""
    # Options → F-keys strip (Face + TUI). True = visible. Survives relaunch.
    face_fkeys: bool = True
    # Face Options → Bold type. False = browser weight; True = terminal-bold.
    face_bold: bool = False
    # Sticky chat reasoning tier across sessions (auto|fast|expert|heavy|off).
    # Copied onto SessionState at boot; ``/tier`` and the config pane write it back.
    chat_tier: str = "auto"
    # Preferred external editor for /edit, wiki, docs, browse (command line, e.g. "code -w").
    # Empty → use $EDITOR, then $VISUAL, then vi (see xlii/editor.py).
    editor: str = ""
    # Preferred image editor (gimp, krita, …). Empty → OS opener (xdg-open).
    image_editor: str = ""
    # Preferred desktop browser for OS-fallback URL opens (your Firefox, etc.).
    # Empty → webbrowser / xdg-open. This is NOT the research browser — a
    # controllable AI window is always Chromium we launch (CDP). See
    # xlii/agent_browser.py.
    browser: str = ""
    # Name of the mobile journal (mojo). Empty = mojo. Set by setup or ``/name``.
    # This is a spelling of the one default persona — not a picker of others.
    fallback_persona: str = ""
    # Transcript paper polarity (Track C): ``dark`` or ``light``. Independent of ``tui_theme``
    # (trim/chrome). Options → Config… canvas row cycles this live; Screen + ``#log`` paper CSS
    # follows. Themes list is filtered to matching polarity for contrast.
    tui_canvas: str = "dark"
    # Preferred terminal emulator for Tools → New terminal. A command string;
    # an optional "{cwd}" token is substituted with the dest path (e.g. "kitty",
    # "kitty -d {cwd}", "wezterm start --cwd {cwd}"). Empty → auto-detect
    # (x-terminal-emulator, gnome-terminal, …), which can pick the wrong one
    # on some systems (e.g. zutty via the Debian alternative).
    tui_terminal: str = ""
    # Where New terminal lands: project (live desk) | home (~) | root (/) | custom.
    tui_terminal_cwd: str = "project"
    # Folder used when tui_terminal_cwd is custom. Empty custom falls back to project.
    tui_terminal_cwd_path: str = ""
    # Preferred side for the docked panel beside the transcript (left|right).
    # Options → Config… and ``/panel --set`` persist here; restored on TUI/face.
    tui_panel_side: str = "right"
    # Default side-dock width as a percent of the workspace (face). 0 = auto
    # (~280px). Cycle 25/33/50/60/70 so "50%" is actually half the window.
    panel_width_pct: int = 0
    # Home hub: open a row in this panel (back works) or the other panel
    # (hub stays, tape may move). ``this`` | ``other``.
    hub_open: str = "this"
    # DNA read: official xAI docs MCP (docs.x.ai) as a first-class agent
    # tool. Default on — this is how the house brain looks up how Grok
    # and the API actually run. Set false to hide ``xai_docs`` (offline,
    # or when docs.x.ai is unreachable). Not a marketplace of MCP servers.
    xai_docs: bool = True
    # Remote-filesystem connections for the ftp:// / sftp:// provider (see
    # xlii/remotefs.py). Non-secret fields only — name -> {host, port, user,
    # protocol (ftp|ftps|sftp), vault_ref?, key_path?, insecure?}; the password /
    # SSH passphrase lives encrypted in the vault under the xlii:ftp namespace,
    # keyed by vault_ref (the exact split chat keys use).
    ftp_connections: dict = field(default_factory=dict)
    # Fabric node roster (the mojo's node→center pull, xlii/fabric.py + `xlii
    # fabric`). name -> {remote (an ftp_connections name that reaches the node),
    # chat_state? (remote path to the node's ~/.xlii/chat, default '.xlii/chat'),
    # persona? (default persona to pull, default 'mojo')}. The node name is the
    # provenance tag on pulled turns and should match the node daemon's
    # [daemon] node_name. No secrets here — the sftp secret lives in the vault
    # under the referenced ftp_connections entry.
    fabric_nodes: dict = field(default_factory=dict)
    # Seconds between throne auto-pulls (chat open / mojo turn). 0 = off.
    # Manual ``xlii fabric pull`` always runs and refreshes the stamp.
    fabric_pull_interval_s: int = 900
    # Email accounts (IMAP/SMTP descriptors). Secrets live in the vault under
    # vault_ns (e.g. email:personal); config holds host/port/user only.
    email_accounts: dict = field(default_factory=dict)
    # Named model profiles (build / reason / economy / vision + user extensions).
    # Built-ins always exist; entries here override or add profiles.
    model_profiles: dict = field(default_factory=dict)
    # Per subagent role model overrides (explore / bash / general / verify / …).
    worker_models: dict = field(default_factory=dict)
    # Public serve gate (serve-public P0): nested serve.public block.
    # Shape: {"public": {"base_url": str, "code_ttl_s": int, "session_ttl_s": int,
    #                   "idle_timeout_s": int, "max_sessions": int,
    #                   "closed_door": bool, "redirect_off_url": str,
    #                   "face_default": bool}}.
    # ``base_url`` is REQUIRED when ``xlii serve --public`` is used;
    # ``redirect_off_url`` is REQUIRED when ``closed_door`` is on.
    # ``face_default`` (default on) lands a granted GET / on /face/.
    serve: dict = field(default_factory=dict)

    # Gigwork provider registry: {"providers": {name: {kind, base_url,
    # api_key_env, model}}, "defaults": {"allow": [name, ...]}}. Parsed and
    # validated by xlii.chat_backend (gig_providers / gig_allowlist) — this
    # field only makes the block a known key so load/save round-trips it.
    gigwork: dict = field(default_factory=dict)
    # Farm jobs (named advisory ads a node may pick up). Shape:
    # {"offers": ["explore"], "gig": "kimi", "node": "kimi-laptop",
    #  "projects": {"iXaac-lab": "/path"}, "board": "/optional/jobs/dir",
    #  "muc": "jobs@conference.home.xlii-remote.com"}.
    # Empty offers = this box never claims (still observes the MUC if set).
    # Overlay (Tailscale) is optional. MUC is not OMEMO — members-only room.
    jobs: dict = field(default_factory=dict)
    # House XMPP (Prosody). Shape:
    # {"domain": "home.xlii-remote.com", "admin_remote": "xliiec2",
    #  "accounts": {"me": {"jid": "me@…", "role": "me"}}}.
    # Passwords live in the vault (xlii:xmpp), never here. The rider mints
    # JIDs with `xlii jid add`; in-band registration stays off.
    xmpp: dict = field(default_factory=dict)

    def effective_judges(self) -> dict[str, Any]:
        """Named judge profiles, with legacy secondary_ai migrated to consult."""
        out: dict[str, Any] = dict(self.judges or {})
        sec = self.secondary_ai or {}
        consult_name = (self.consult or {}).get("default_judge", "consult")
        if sec.get("provider") and consult_name not in out:
            out[consult_name] = {
                "kind": "cross_vendor",
                "mode": "verify",
                "provider": sec.get("provider"),
                "model": sec.get("model", ""),
                "api_key_env": sec.get("api_key_env", ""),
                "tier": "cross_org",
                "description": "legacy secondary_ai (/consult)",
            }
        return out

    def consult_profile(self) -> dict[str, Any]:
        """Resolve the judge profile used by /consult."""
        judges = self.effective_judges()
        name = (self.consult or {}).get("default_judge", "consult")
        if name in judges:
            return judges[name]
        raise RuntimeError(
            'secondary AI not configured. Add a judges profile or secondary_ai block to '
            '~/.config/xlii/config.json'
        )

    def orchestrator(self) -> str:
        """Model used by the main agent. Falls back to `model`."""
        return self.orchestrator_model or self.model

    def worker(self) -> str:
        """Model used by dispatched workers. Falls back to orchestrator/model."""
        return self.worker_model or self.orchestrator_model or self.model

    def chat(self) -> str:
        """Model for persona / conversational chat. Defaults to a general
        chat-class model (not the code build-model). /howto uses :meth:`help`
        instead so help stays on the cheap slot."""
        return self.chat_model or DEFAULT_CHAT_MODEL

    def help(self) -> str:
        """Model for help surfaces (/howto). Defaults to the cheap build model
        so self-guide turns do not burn the flagship. Falls back to
        DEFAULT_HELP_MODEL when unset (older configs)."""
        return self.help_model or DEFAULT_HELP_MODEL

    def orchestrator_temp(self) -> float:
        """Orchestrator sampling temperature. Use `is not None` so a deliberate
        0.0 (deterministic mode) is preserved instead of being coerced."""
        return (
            self.orchestrator_temperature
            if self.orchestrator_temperature is not None
            else DEFAULT_ORCH_TEMP
        )

    def worker_temp(self) -> float:
        return (
            self.worker_temperature
            if self.worker_temperature is not None
            else DEFAULT_WORKER_TEMP
        )

    def chat_temp(self) -> float:
        return (
            self.chat_temperature
            if self.chat_temperature is not None
            else DEFAULT_CHAT_TEMP
        )

    def get_model_for_role(self, role: str = "orchestrator") -> str:
        """Single dispatcher used by Agent and WorkerAgent so the call sites
        are uniform and any future role (e.g. 'planner') only needs one
        new branch here."""
        if role == "worker":
            return self.worker()
        if role == "orchestrator":
            return self.orchestrator()
        if role == "chat":
            return self.chat()
        if role == "help":
            return self.help()
        raise ValueError(f"unknown model role: {role!r}")

    def get_model_for_worker_role(self, worker_role: str = "general") -> str:
        """Model for a dispatched subagent. ``worker_models`` map wins, else worker."""
        wm = self.worker_models or {}
        if isinstance(wm, dict):
            mapped = wm.get(worker_role) or wm.get(str(worker_role))
            if isinstance(mapped, str) and mapped.strip():
                return mapped.strip()
        return self.worker()

    def _serve_public(self) -> dict[str, Any]:
        """The nested ``serve.public`` table (empty dict when absent/malformed)."""
        serve = self.serve if isinstance(self.serve, dict) else {}
        pub = serve.get("public")
        return pub if isinstance(pub, dict) else {}

    @property
    def serve_public_base_url(self) -> Optional[str]:
        """Operator domain for the public entry page, or None if unset."""
        raw = self._serve_public().get("base_url")
        if not isinstance(raw, str):
            return None
        url = raw.strip()
        return url or None

    @property
    def serve_public_code_ttl_s(self) -> int:
        return _serve_public_int(
            self._serve_public(), "code_ttl_s", DEFAULT_SERVE_PUBLIC_CODE_TTL_S,
        )

    @property
    def serve_public_session_ttl_s(self) -> int:
        return _serve_public_int(
            self._serve_public(), "session_ttl_s", DEFAULT_SERVE_PUBLIC_SESSION_TTL_S,
        )

    @property
    def serve_public_idle_timeout_s(self) -> int:
        return _serve_public_int(
            self._serve_public(), "idle_timeout_s", DEFAULT_SERVE_PUBLIC_IDLE_TIMEOUT_S,
        )

    @property
    def serve_public_max_sessions(self) -> int:
        return _serve_public_int(
            self._serve_public(), "max_sessions", DEFAULT_SERVE_PUBLIC_MAX_SESSIONS,
        )

    @property
    def serve_public_closed_door(self) -> bool:
        """Closed-door: GET / without a grant or live ``?code=`` 302s away.

        Strict bool — only JSON ``true`` counts (the inverse of the is-int
        trap ``_serve_public_int`` guards); ints/strings read as off.
        """
        raw = self._serve_public().get("closed_door")
        return isinstance(raw, bool) and raw

    @property
    def serve_public_face_default(self) -> bool:
        """Granted GET / lands on /face/ (the chat-first face).

        Default on. Set ``false`` to keep the xterm.js TUI as the landing.
        When the key is present, only a real JSON bool counts (``"yes"`` /
        ``1`` read as off) — same strict-bool doctrine as ``closed_door``.
        """
        pub = self._serve_public()
        if "face_default" not in pub:
            return DEFAULT_SERVE_PUBLIC_FACE_DEFAULT
        raw = pub.get("face_default")
        return isinstance(raw, bool) and raw

    @property
    def serve_public_redirect_off_url(self) -> Optional[str]:
        """Where closed-door sends strangers, or None if unset."""
        raw = self._serve_public().get("redirect_off_url")
        if not isinstance(raw, str):
            return None
        url = raw.strip()
        return url or None

    @property
    def serve_public_face_linger_s(self) -> int:
        """Seconds a face backend lingers at zero views before reap.

        Default matches ``idle_timeout_s``. ``0`` = reap immediately (legacy).
        """
        pub = self._serve_public()
        if "face_linger_s" not in pub:
            return self.serve_public_idle_timeout_s
        return _serve_public_int_nonneg(pub, "face_linger_s", self.serve_public_idle_timeout_s)

    @property
    def serve_public_face_attach_existing(self) -> bool:
        """Attach a new grant to a live desk face instead of 502 on lock clash."""
        raw = self._serve_public().get("face_attach_existing")
        return isinstance(raw, bool) and raw

    def require_serve_public_base_url(self) -> str:
        """Return ``base_url`` or raise ``ValueError`` with the friendly hint.

        Callers of ``--public`` (V2 serve wiring, V3 admin mint) use this so the
        missing-config failure stays one message, not a stack trace.
        """
        url = self.serve_public_base_url
        if not url:
            raise ValueError(MISSING_SERVE_PUBLIC_BASE_URL)
        return url

    @classmethod
    def load(cls) -> "GlobalConfig":
        cfg = cls()
        if GLOBAL_CONFIG_FILE.exists():
            data = json.loads(GLOBAL_CONFIG_FILE.read_text())
            for k, v in data.items():
                if hasattr(cfg, k) and v is not None:
                    setattr(cfg, k, v)
            # Surface unknown top-level keys instead of silently dropping them: a
            # typo (e.g. 'secondary_api' for 'secondary_ai') or a misplaced value
            # otherwise vanishes with no feedback. '_'-prefixed keys are a comment
            # convention (e.g. '_comment') and are intentionally allowed.
            unknown = [k for k in data if not k.startswith("_") and not hasattr(cfg, k)]
            if unknown:
                import warnings
                hint = " (did you mean 'secondary_ai'?)" if "secondary_api" in unknown else ""
                warnings.warn(
                    f"config.json: ignoring unrecognized key(s): {', '.join(sorted(unknown))}{hint}. "
                    "Check for typos — run `xlii doctor`.",
                    stacklevel=2,
                )
        # Management key: env always wins. We deliberately ignore it from the
        # file (security: never persist privileged creds on disk). If a legacy
        # config still has one, callers can detect that with `mgmt_key_in_file()`.
        env_mgmt = os.environ.get("XAI_MANAGEMENT_API_KEY", "").strip()
        cfg.management_api_key = env_mgmt or None
        if cfg.secondary_ai and not cfg.judges:
            import warnings
            warnings.warn(
                "config.json: 'secondary_ai' is deprecated — add a 'judges' profile "
                "and optional consult.default_judge (see /describe consult). "
                "Run `xlii doctor --migrate-legacy` for path cleanup.",
                DeprecationWarning,
                stacklevel=2,
            )
        return cfg

    def api_region(self) -> Optional[str]:
        """The active xAI region: ``XAI_REGION`` env (per-shell override) beats
        the persisted ``region`` field. ``None`` = the global edge."""
        raw_env = os.environ.get("XAI_REGION")
        env_override = bool(raw_env and raw_env.strip())
        raw = raw_env if env_override else self.region
        try:
            return normalize_api_region(raw)
        except ValueError as exc:
            import warnings

            source = "XAI_REGION" if env_override else "config region"
            warnings.warn(
                f"ignoring invalid {source}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
            return None

    def api_host(self) -> str:
        """Bare API host for the chat/media/collections plane (region-aware)."""
        return xai_api_host(self.api_region())

    def api_base_url(self) -> str:
        """OpenAI-compatible base URL for the active region."""
        return f"https://{self.api_host()}/v1"

    @staticmethod
    def mgmt_key_in_file() -> bool:
        """True if the on-disk config still contains a non-empty management_api_key.
        Used to surface a legacy/security warning."""
        if not GLOBAL_CONFIG_FILE.exists():
            return False
        try:
            data = json.loads(GLOBAL_CONFIG_FILE.read_text())
        except Exception:
            return False
        v = data.get("management_api_key")
        return bool(v and isinstance(v, str) and v.strip())

    def save(self) -> None:
        data = asdict(self)
        # The management key is documented as env-only (XAI_MANAGEMENT_API_KEY)
        # — enforce that invariant: never persist it, even if set in memory.
        data.pop("management_api_key", None)
        write_text_atomic(GLOBAL_CONFIG_FILE, json.dumps(data, indent=2), mode=0o600)

    @classmethod
    def write_template(cls) -> Path:
        if not GLOBAL_CONFIG_FILE.exists():
            write_text_atomic(GLOBAL_CONFIG_FILE, json.dumps(CONFIG_TEMPLATE, indent=2), mode=0o600)
        else:
            GLOBAL_CONFIG_FILE.chmod(0o600)  # idempotent perms-fix for an existing file
        return GLOBAL_CONFIG_FILE

    def key_pairs(self) -> list[KeyPair]:
        """Resolve `keys[]` into KeyPair list, primary first.

        An entry carries the secret one of two ways:
          - plaintext: a bare string, or a dict with `api_key` (legacy);
          - vault-backed: a dict with `vault_ref`, whose secret lives in the
            encrypted vault under the `VAULT_CHATKEYS_NS` namespace.
        Refs are resolved lazily — the vault is unlocked at most once per call,
        and only if some entry actually needs it.
        """
        out: list[KeyPair] = []
        _ns_cache: dict[str, str] | None = None

        def _vault_secret(ref: str) -> str:
            nonlocal _ns_cache
            if _ns_cache is None:
                from xlii.vault import Vault, VaultError
                try:
                    _ns_cache = Vault.unlock(create_if_missing=False).get(VAULT_CHATKEYS_NS)
                except VaultError as e:
                    raise RuntimeError(
                        "a chat key is vault-backed but the vault could not be opened: "
                        f"{str(e).rstrip('.')}."
                    ) from e
            secret = _ns_cache.get(ref, "")
            if not secret:
                raise RuntimeError(
                    f"vault_ref {ref!r} not found in vault namespace "
                    f"{VAULT_CHATKEYS_NS!r} — restore the key or re-run `xlii keys migrate`."
                )
            return secret

        for i, entry in enumerate(self.keys):
            label = None
            if isinstance(entry, str):
                api_key, mgmt = entry, None
            elif isinstance(entry, dict):
                mgmt = entry.get("management_api_key")
                label = entry.get("label")
                ref = entry.get("vault_ref")
                api_key = _vault_secret(str(ref)) if ref else (entry.get("api_key") or "")
            else:
                continue
            if not api_key:
                continue
            out.append(
                KeyPair(
                    api_key=api_key,
                    management_api_key=mgmt or self.management_api_key,
                    label=label or ("primary" if i == 0 else f"k{i}"),
                )
            )
        return out

    def plaintext_key_count(self) -> int:
        """How many `keys[]` entries still hold a literal secret on disk (i.e.
        are not vault-backed). Used by `xlii doctor` to nudge toward migration."""
        return sum(1 for entry in self.keys if is_plaintext_key_entry(entry))


def is_plaintext_key_entry(entry) -> bool:
    """True when a keys[] entry carries a literal secret on disk: a bare string,
    or a dict with `api_key` and no `vault_ref`. Single predicate shared by
    GlobalConfig.plaintext_key_count and migrate_chat_keys."""
    if isinstance(entry, str):
        return bool(entry)
    return isinstance(entry, dict) and bool(entry.get("api_key")) and not entry.get("vault_ref")


@dataclass
class PendingKeyMigration:
    """One plaintext keys[] entry awaiting vault migration."""
    index: int
    label: str
    bare: bool  # bare-string entry vs dict with api_key


@dataclass
class KeyMigrationReport:
    """Outcome of migrate_chat_keys — pure data for the face to render."""
    config_missing: bool = False
    pending: list[PendingKeyMigration] = field(default_factory=list)
    migrated: int = 0
    backup_path: Optional[Path] = None
    vault_backend: Optional[str] = None


def migrate_chat_keys(*, dry_run: bool = False, backup: bool = True) -> KeyMigrationReport:
    """Move plaintext chat-key secrets out of config.json into the encrypted
    vault, replacing each with a `vault_ref` (was cmds/provision/migrate.py —
    kernel-homed so any body can run the migration headless).

    Local-only — needs neither the management key nor the network. Idempotent:
    entries already vault-backed are left alone. Detection/ref-uniquing/backup/
    atomic rewrite all live here; VaultError propagates for the face to render
    (config.json is untouched in that case — the vault is opened first).
    """
    import shutil
    from datetime import datetime

    if not GLOBAL_CONFIG_FILE.exists():
        return KeyMigrationReport(config_missing=True)

    raw = json.loads(GLOBAL_CONFIG_FILE.read_text())
    keys = raw.get("keys") or []
    dict_pending = [(i, e) for i, e in enumerate(keys)
                    if isinstance(e, dict) and is_plaintext_key_entry(e)]
    str_pending = [(i, e) for i, e in enumerate(keys)
                   if isinstance(e, str) and is_plaintext_key_entry(e)]
    pending = (
        [PendingKeyMigration(i, e.get("label", f"key{i}"), False) for i, e in dict_pending]
        + [PendingKeyMigration(i, f"key{i}", True) for i, _ in str_pending]
    )
    if not pending:
        return KeyMigrationReport()
    if dry_run:
        return KeyMigrationReport(pending=pending)

    from xlii.vault import Vault  # local: vault imports this module (cycle)

    # Open the vault first; if this fails we have not touched config.json.
    vault = Vault.unlock()  # provisions a master key on first use

    backup_path = None
    if backup:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = GLOBAL_CONFIG_FILE.with_name(GLOBAL_CONFIG_FILE.name + f".bak-{ts}")
        shutil.copy2(GLOBAL_CONFIG_FILE, backup_path)

    used: set[str] = set()

    def _ref_for(label: str) -> str:
        base = label or "key"
        ref, n = base, 1
        while ref in used or vault.has(VAULT_CHATKEYS_NS, ref):
            n += 1
            ref = f"{base}#{n}"
        used.add(ref)
        return ref

    new_keys = list(keys)
    migrated = 0
    for i, e in dict_pending:
        ref = _ref_for(e.get("label") or f"key{i}")
        vault.set(VAULT_CHATKEYS_NS, ref, e["api_key"])
        new_keys[i] = {**{k: v for k, v in e.items() if k != "api_key"}, "vault_ref": ref}
        migrated += 1
    for i, e in str_pending:
        ref = _ref_for(f"key{i}")
        vault.set(VAULT_CHATKEYS_NS, ref, e)
        new_keys[i] = {"label": f"key{i}", "vault_ref": ref}
        migrated += 1

    raw["keys"] = new_keys
    write_text_atomic(GLOBAL_CONFIG_FILE, json.dumps(raw, indent=2), mode=0o600)

    return KeyMigrationReport(
        pending=pending,
        migrated=migrated,
        backup_path=backup_path,
        vault_backend=vault.backend,
    )


PROJECT_KIND_CODE = "code"
PROJECT_KIND_COLLECTION = "collection"
PROJECT_KINDS = (PROJECT_KIND_CODE, PROJECT_KIND_COLLECTION)


def normalize_project_kind(raw: Optional[str]) -> Optional[str]:
    """``code`` | ``collection``, or None when unstamped."""
    if raw is None:
        return None
    k = str(raw).strip().lower()
    if k in ("collection", "collect", "research", "notes", "pile"):
        return PROJECT_KIND_COLLECTION
    if k in ("code", "lab", "repo"):
        return PROJECT_KIND_CODE
    return None


def project_kind(project: Any) -> str:
    """Effective folder kind. Unstamped named scratch (not home) = collection.

    Missing kind on a normal tree is ``code`` — migration, not inference of a pile.
    Home (``scratch/home``) is not a kind; callers treat it as the unbound desk.
    """
    stored = normalize_project_kind(getattr(project, "kind", None))
    if stored is not None:
        return stored
    name = (getattr(project, "name", "") or "").strip()
    if name.startswith("scratch/") and name != "scratch/home":
        return PROJECT_KIND_COLLECTION
    return PROJECT_KIND_CODE


def _optional_str(raw: Any) -> Optional[str]:
    text = (raw or "").strip() if isinstance(raw, str) else ""
    return text or None


def project_landing_posture(project: Any) -> str:
    """Talk vs lab when you open this folder. Home is not decided here."""
    if project_kind(project) == PROJECT_KIND_COLLECTION:
        return "chat"
    name = (getattr(project, "name", "") or "").strip()
    # Persona islands live at chat/<name> — talk, not a lab.
    if name.startswith("chat/"):
        return "chat"
    return "code"


def project_landing_pack(project: Any) -> str:
    return "chat" if project_landing_posture(project) == "chat" else "code"


@dataclass
class ProjectConfig:
    project_root: Path
    name: str
    collection_id: str
    created_at: str
    extra_ignores: list[str] = field(default_factory=list)
    conversation_id: Optional[str] = None  # stable per-project UUID; used for xAI prompt-cache key
    # Local-only mode: no remote Collection is provisioned and sync is a no-op.
    # Tools available are everything except search_project (no RAG index to query).
    # Use for ad-hoc / file-management workflows in directories you don't want to
    # upload (private docs, mixed-content folders, ephemeral scratch).
    local_only: bool = False
    # RP4: persona this project is bound to. When set, a `code` session inherits
    # the persona's conversational memory (turns_dir) + loadout, while code RAG
    # (search_project) still follows this project's own Collection. None = unbound
    # (project-local memory). Set via `xlii init --id <persona>`.
    bound_persona: Optional[str] = None
    # Default role equipped at code-session boot (roles.md). When set, the named
    # role's loadout + identity are equipped automatically on start, exactly as
    # `/role <name>` would (and `/role off` still drops it for the session). None
    # = no default (the out-of-box posture — a body opts in per project). Set via
    # `/role default <name>`. Parity knob: every body has it; only ones that opt
    # in start with a role.
    default_role: Optional[str] = None
    # Project Shadow (Theme B / JRN-1): the id of this project's dedicated journal
    # Collection (named JOURNAL_COLLECTION_PREFIX + <name>). Recorded here so
    # removal (`xlii project rm`, A3) tears it down alongside the main Collection.
    # None until the journal first archives raw entries. Set by the journal.
    journal_collection_id: Optional[str] = None
    # Folder kind: ``code`` (repo / lab) or ``collection`` (real-folder pile:
    # links, notes, raw materials). None = unstamped; :func:`project_kind`
    # supplies the effective value. Home (scratch/home) is not a kind.
    kind: Optional[str] = None
    # Optional VFS address Files mounts instead of project_root (sftp://…).
    # project_root stays a local Path — journal/turns/.xlii never leave this box.
    # Empty/None = Files is the local tree. See xlii.desk_files.
    files_root: Optional[str] = None
    # Optional git remote for later throne sync. A node updates a throne copy
    # through this repo, not by writing the throne tree over VFS.
    repo: Optional[str] = None
    # Runtime-only: redirect every .xlii/ read/write to another directory without
    # touching `project_root`. Set by `xlii code` preview mode so an ephemeral
    # session writes its history/turns/session.json to a throwaway temp dir
    # instead of creating a .xlii/ in the target folder. NOT persisted by
    # save()/load() — purely in-memory and never written to disk.
    state_dir_override: Optional[Path] = None

    @property
    def xli_dir(self) -> Path:
        """The project state dir (``.xlii/``). A pre-rename ``.xli/`` dir is moved
        to ``.xlii/`` on load (see ProjectConfig.load), so there is no fallback."""
        if self.state_dir_override is not None:
            return self.state_dir_override
        return self.project_root / PROJECT_DIR_NAME

    @property
    def config_path(self) -> Path:
        return self.xli_dir / PROJECT_CONFIG_FILE

    @property
    def manifest_path(self) -> Path:
        return self.xli_dir / MANIFEST_FILE

    @classmethod
    def load(cls, project_root: Path) -> Optional["ProjectConfig"]:
        import uuid

        # One-time auto-migration: silently move a pre-rename ``.xli/`` state dir
        # (and ``.xliignore``) to the canonical ``.xlii/`` so every reader below
        # can assume the modern layout — no legacy fallbacks anywhere else.
        try:
            from xlii.legacy_migrate import migrate_ignore_file, migrate_project_state

            migrate_project_state(project_root)
            migrate_ignore_file(project_root)
        except (ImportError, OSError, ValueError):
            # A project that can't be migrated reads as if it had no legacy state, which is the safe
            # interpretation.
            pass

        path = project_root / PROJECT_DIR_NAME / PROJECT_CONFIG_FILE
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(data, dict) or "name" not in data or "collection_id" not in data:
            return None
        recorded_root = data.get("root")
        actual_root = str(project_root.resolve())
        if recorded_root and recorded_root != actual_root:
            import sys
            print(
                f"xlii: project state was created at {recorded_root!r} but is now at "
                f"{actual_root!r}.\nRefusing to bind this checkout to the recorded "
                f"Collection (it may belong to another clone).\nIf you moved the "
                f"project, delete the \"root\" line in {path} to re-bind; for a fresh "
                f"clone, remove the state dir and run 'xlii init'.",
                file=sys.stderr,
            )
            return None
        cfg = cls(
            project_root=project_root,
            name=data["name"],
            collection_id=data["collection_id"],
            created_at=data["created_at"],
            extra_ignores=data.get("extra_ignores", []),
            conversation_id=data.get("conversation_id"),
            local_only=data.get("local_only", False),
            bound_persona=data.get("bound_persona"),
            default_role=data.get("default_role"),
            journal_collection_id=data.get("journal_collection_id"),
            kind=normalize_project_kind(data.get("kind")),
            files_root=_optional_str(data.get("files_root")),
            repo=_optional_str(data.get("repo")),
        )
        # Backfill: legacy projects (created before these fields existed) get a
        # stable UUID / root path persisted on first load. From then on they stay.
        if not cfg.conversation_id or not recorded_root:
            if not cfg.conversation_id:
                cfg.conversation_id = uuid.uuid4().hex
            cfg.save()
        return cfg

    def save(self) -> None:
        write_text_atomic(
            self.config_path,
            json.dumps(
                {
                    "name": self.name,
                    "root": str(self.project_root.resolve()),
                    "collection_id": self.collection_id,
                    "created_at": self.created_at,
                    "extra_ignores": self.extra_ignores,
                    "conversation_id": self.conversation_id,
                    "local_only": self.local_only,
                    "bound_persona": self.bound_persona,
                    "default_role": self.default_role,
                    "journal_collection_id": self.journal_collection_id,
                    "kind": project_kind(self),
                    "files_root": self.files_root or "",
                    "repo": self.repo or "",
                },
                indent=2,
            ),
            mode=0o644,
        )
