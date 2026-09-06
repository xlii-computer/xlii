"""Tool registry, JSON schemas, and project-tool loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from xlii.tool_context import ToolFn
from xlii.tool_handlers import (
    t_bash,
    t_browser,
    t_code_execute,
    t_codex_run_task,
    t_command_help,
    t_create_pdf,
    t_edit_file,
    t_generate_image,
    t_glob,
    t_grep,
    t_list_dir,
    t_map,
    t_plan_amend,
    t_plan_check,
    t_plugin_call,
    t_plugin_get,
    t_plugin_search,
    t_read_email,
    t_read_file,
    t_search_email,
    t_search_project,
    t_send_email,
    t_send_file,
    t_web_search,
    t_write_file,
    t_x_search,
    t_xai_docs,
)

# --------------------------------------------------------------------------- #
#  Tool registry + JSON schema for tool-use
# --------------------------------------------------------------------------- #


@dataclass
class AgentTool:
    """A tool that can be called by the agent (the model).

    This is the pluggable equivalent of REPLCommand, but for what the *agent*
    can invoke rather than what the human types as a slash command.
    """
    name: str
    description: str
    parameters: dict[str, Any]          # JSON Schema for the "parameters" object
    handler: ToolFn
    parallel_safe: bool = False
    source: str = "project"             # "builtin", "project", "plugin"
    category: str = "general"

    # Safety / filtering flags (default conservative for user tools)
    plan_mode_safe: bool = False        # Include in /plan mode?
    worker_safe: bool = False           # Include for dispatched workers?


def _builtin(
    name: str,
    description: str,
    parameters: dict[str, Any],
    handler: ToolFn,
    *,
    parallel: bool = False,
    plan: bool = False,
    worker: bool = False,
) -> AgentTool:
    """Construct a built-in AgentTool. The flags decide membership in the
    derived sets below — parallel -> PARALLEL_SAFE, plan -> PLAN_MODE_TOOLS,
    worker -> WORKER_REGISTRY — so each tool's identity lives in exactly one
    place instead of being restated across parallel name-keyed structures."""
    return AgentTool(
        name=name,
        description=description,
        parameters=parameters,
        handler=handler,
        parallel_safe=parallel,
        plan_mode_safe=plan,
        worker_safe=worker,
        source="builtin",
    )


# THE single source of truth for built-in tools. Every other built-in structure
# below (REGISTRY, WORKER_REGISTRY, PARALLEL_SAFE, PLAN_MODE_TOOLS, and the
# tool_schemas() the model sees) is derived from this list — add or rename a
# built-in here and nothing else needs to change. Order is the order the model
# sees in its tool list.
BUILTIN_TOOLS: list[AgentTool] = [
    _builtin(
        "read_file",
        "Read a file from the project. Returns line-numbered text. "
        "Use offset/limit for large files.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "description": "0-based first line"},
                "limit": {"type": "integer", "description": "max lines to return"},
            },
            "required": ["path"],
        },
        t_read_file,
        parallel=True, plan=True, worker=True,
    ),
    _builtin(
        "write_file",
        "Create or overwrite a file with the given content. Marks file dirty for sync.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        t_write_file,
    ),
    _builtin(
        "edit_file",
        "Replace exact substring in a file. Errors if old_string is not unique unless replace_all=true.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["path", "old_string", "new_string"],
        },
        t_edit_file,
    ),
    _builtin(
        "plan_check",
        (
            "Mark a plan checkbox done by its {#id} and attach evidence — the ONE "
            "sanctioned write into .xlii/plans/ outside plan mode (write_file/"
            "edit_file refuse that dir). With evidence the box becomes [x] and the "
            "receipt is annotated on the line; without it, [x?] (visibly "
            "unreceipted). Evidence should reference something checkable: a commit "
            "hash, test run, receipt id, or file path. It never edits plan text and "
            "never unchecks — to propose a plan change, use plan_amend; the "
            "planner (/plan) decides."
        ),
        {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "The checkbox id from a '- [ ] {#id} ...' line",
                },
                "evidence": {
                    "type": "string",
                    "description": "Receipt reference (commit hash, test output, path) — makes the check [x] instead of [x?]",
                },
                "plan": {
                    "type": "string",
                    "description": "Plan file name (default: current.md, else the newest plan)",
                },
            },
            "required": ["item_id"],
        },
        t_plan_check,
        # Mutates the planner's domain: not parallel, not for workers, and
        # deliberately NOT plan_mode_safe — the planner edits the file
        # directly and must not be nudged into checkbox-only thinking.
        parallel=False, plan=False, worker=False,
    ),
    _builtin(
        "plan_amend",
        (
            "Propose a plan change or addition when the plan turns out wrong "
            "mid-execution — appends a '- [?] {#am-N}' entry to the plan's "
            "'## Amendments' queue, which only the planner resolves (accept → "
            "folded into the plan; reject → a rejection note). Pass item_id to "
            "contest a specific '- [ ] {#id}' item; omit it to propose a new "
            "item. You cannot edit the plan yourself: plan_check marks items "
            "done, plan_amend proposes changes, the planner (/plan) decides."
        ),
        {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The proposal, one line: what you found, what the plan assumes, what you propose",
                },
                "item_id": {
                    "type": "string",
                    "description": "Optional checkbox id this amendment contests; omit for a proposed addition",
                },
                "plan": {
                    "type": "string",
                    "description": "Plan file name (default: current.md, else the newest plan)",
                },
            },
            "required": ["text"],
        },
        t_plan_amend,
        # Same posture as plan_check: the implementer's channel — never in
        # plan mode (the planner RESOLVES amendments by editing directly).
        parallel=False, plan=False, worker=False,
    ),
    _builtin(
        "list_dir",
        "List immediate children of a directory.",
        {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "default '.'"}},
        },
        t_list_dir,
        parallel=True, plan=True, worker=True,
    ),
    _builtin(
        "glob",
        "Recursive glob. Pattern uses fnmatch syntax against project-relative posix paths.",
        {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
        t_glob,
        parallel=True, plan=True, worker=True,
    ),
    _builtin(
        "grep",
        "Recursive regex search across project files. Returns 'path:line: match'.",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python regex"},
                "glob": {"type": "string", "description": "Optional fnmatch filter"},
                "case_insensitive": {"type": "boolean"},
            },
            "required": ["pattern"],
        },
        t_grep,
        parallel=True, plan=True, worker=True,
    ),
    _builtin(
        "bash",
        (
            "Run a shell command in the project root. Capture stdout/stderr/exit code. "
            "You MUST declare `intent` honestly — it's used to gate risky commands on user "
            "confirmation. Lying about intent to skip the gate is a serious bug; declare "
            "the strongest applicable category. Use `read-only` for inspection only "
            "(ls, cat, grep, find, git status/log/diff, pytest). Use `modifies-project` "
            "for in-tree edits via shell (git add/commit, sed -i on project files). Use "
            "`modifies-system` for anything outside the project root, anything needing "
            "sudo, or system config. sudo/doas/su open a real terminal for the password "
            "— never pipe a password or retry with sudo -S. Use `network` for any command that reaches the "
            "internet (curl, wget, pip install, npm install, git push/pull/fetch)."
        ),
        {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "intent": {
                    "type": "string",
                    "enum": ["read-only", "modifies-project", "modifies-system", "network"],
                    "description": "Honest declaration of what this command will do.",
                },
                "timeout": {"type": "integer", "description": "Seconds, default 60"},
            },
            "required": ["command", "intent"],
        },
        t_bash,
        worker=True,
    ),
    _builtin(
        "search_project",
        "Hybrid RAG search across the project's xAI Collection. Returns top-k matching chunks with file names.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "description": "default 10"},
                "retrieval_mode": {
                    "type": "string",
                    "enum": ["hybrid", "semantic", "keyword"],
                },
            },
            "required": ["query"],
        },
        t_search_project,
        parallel=True, plan=True, worker=True,
    ),
    _builtin(
        "map",
        (
            "Project shape at a glance: file tree + Python class/function "
            "signatures. Use to orient before searching or reading. Not a "
            "search index — use grep / search_project to find things."
        ),
        {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Optional subtree scope (e.g. 'xlii/tui'); default = whole project",
                },
                "depth": {
                    "type": "integer",
                    "description": "Optional tree depth cap (1 = top level only)",
                },
                "detail": {
                    "type": "string",
                    "enum": ["files", "symbols"],
                    "description": "'files' = tree only (huge scopes); 'symbols' (default) adds class/function signatures",
                },
            },
        },
        t_map,
        parallel=True, plan=True, worker=True,
    ),
    _builtin(
        "web_search",
        (
            "Search the live web via xAI Live Search. Use for current docs, recent "
            "library versions, breaking changes, error messages you can't resolve from "
            "project context. Returns answer text + citations. Prefer search_project "
            "for anything inside the project."
        ),
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language search query"},
                "allowed_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional whitelist of domains to search.",
                },
                "excluded_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional blacklist of domains to skip.",
                },
            },
            "required": ["query"],
        },
        t_web_search,
        parallel=True, plan=True, worker=True,
    ),
    _builtin(
        "xai_docs",
        (
            "Read the official xAI documentation (docs.x.ai) via the hosted "
            "docs MCP. This is how xlii looks up how Grok and the xAI API "
            "actually run: models, Responses, chat, Imagine, tools, limits, "
            "auth. Prefer this over web_search for anything about xAI/Grok/"
            "the API. Actions: search (keyword across docs), get (one page "
            "by slug, e.g. developers/models), list (all page slugs). Not "
            "xlii's own wiki or howto — those are the garage engine."
        ),
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "get", "list"],
                    "description": (
                        "search=keyword across docs; get=one page by slug; "
                        "list=all page slugs"
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "Required for search — keyword or phrase",
                },
                "slug": {
                    "type": "string",
                    "description": (
                        "Required for get — e.g. developers/quickstart, "
                        "developers/models"
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "For search: 1-20 (default 5)",
                },
            },
            "required": ["action"],
        },
        t_xai_docs,
        parallel=True, plan=True, worker=True,
    ),
    _builtin(
        "browser",
        (
            "Drive a local Chromium research browser (spawn / navigate / extract "
            "page text / screenshot / close). Use for reading a live page or PDF "
            "URL the agent should analyze — pair with archivebox plugin to store "
            "keep-worthy links. Actions: open, goto, extract, screenshot, status, "
            "close, dump (one-shot). One session per process."
        ),
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "open", "goto", "extract", "screenshot",
                        "status", "close", "dump",
                    ],
                    "description": "open=spawn session; goto=navigate; extract=page text; "
                                   "screenshot=PNG; status; close; dump=one-shot no session",
                },
                "url": {
                    "type": "string",
                    "description": "http(s) URL — required for goto/dump; optional for open/extract",
                },
                "headless": {
                    "type": "boolean",
                    "description": (
                        "true = hidden Chromium; false = visible window. "
                        "Omit to follow the desk (window when a display is on). "
                        "Do not hide a window already open."
                    ),
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Cap extracted text length (default 24000)",
                },
            },
            "required": ["action"],
        },
        t_browser,
        # Not plan_mode_safe: open/goto spawn a Chromium process (side effect).
        # Available on chat (CHAT_BLIND) and default code palette.
        parallel=False, plan=False, worker=False,
    ),
    _builtin(
        "x_search",
        (
            "Search posts on X (Twitter) via xAI Live Search. Use for real-time "
            "developer chatter, trending issues, official announcements from project "
            "maintainers, or community sentiment about a library. Returns answer text "
            "+ citations. Prefer web_search for stable docs and articles."
        ),
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language query about X posts"},
                "allowed_x_handles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Restrict to these handles (max 10). e.g. ['xai', 'elonmusk']",
                },
                "excluded_x_handles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Exclude these handles (max 10).",
                },
                "from_date": {"type": "string", "description": "ISO8601 start date (e.g. 2025-01-01)"},
                "to_date": {"type": "string", "description": "ISO8601 end date"},
                "enable_image_understanding": {"type": "boolean"},
                "enable_video_understanding": {"type": "boolean"},
            },
            "required": ["query"],
        },
        t_x_search,
        parallel=True, plan=True, worker=True,
    ),
    _builtin(
        "plugin_search",
        (
            "Search the project's subscribed plugins by intent. Returns top "
            "matches with id, score, effect/trust, and one-line description. "
            "If no match, returns NO_PLUGIN_MATCH — tell the user and DO NOT "
            "fabricate plugin output. Then plugin_call the action. Do not "
            "compose curl via bash. Do not ask for passwords or API keys."
        ),
        {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "description": "Natural-language description of what data/action you need (e.g. 'current weather in seattle', 'recent SEC filings for tesla').",
                },
            },
            "required": ["intent"],
        },
        t_plugin_search,
        parallel=True, plan=True, worker=True,
    ),
    _builtin(
        "plugin_get",
        (
            "Read the full markdown of a subscribed plugin. Use after "
            "plugin_search to learn action ids and params. Invoke with "
            "plugin_call — never bash/curl, never collect secrets in chat."
        ),
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Plugin id (from plugin_search results)"},
            },
            "required": ["name"],
        },
        t_plugin_get,
        parallel=True, plan=True, worker=True,
    ),
    _builtin(
        "plugin_call",
        (
            "Invoke a subscribed plugin action. Pass non-secret params you "
            "already have. If credentials or secrets are needed, a form opens "
            "on the face — stop, do not ask for the secret, do not pass "
            "passwords in params. Do not use bash to call the API."
        ),
        {
            "type": "object",
            "properties": {
                "plugin": {"type": "string", "description": "Plugin id (subscribed)"},
                "action": {"type": "string", "description": "Action id from the plugin manifest"},
                "params": {
                    "type": "object",
                    "description": "Action parameters as defined in the manifest",
                },
            },
            "required": ["plugin", "action"],
        },
        t_plugin_call,
        parallel=False, plan=False, worker=False,
    ),
    _builtin(
        "code_execute",
        (
            "Execute Python in xAI's sandbox to verify behavior, prototype logic, or "
            "do computations. NumPy/Pandas/Matplotlib/SciPy preinstalled. Pass either "
            "a description of what to compute or actual Python code. The model writes "
            "and runs the code server-side and returns the result. For project-local "
            "verification, use bash + python instead — this is for isolated snippets."
        ),
        {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Description of the computation, or Python code to run.",
                },
            },
            "required": ["task"],
        },
        t_code_execute,
        parallel=True, worker=True,
    ),
    _builtin(
        "codex_run_task",
        "Delegate a bounded coding task to OpenAI Codex CLI (cross_org harness). "
        "Use for isolated OpenAI-weight analysis or edits; prefer ask/plan for read-only.",
        {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task prompt for Codex"},
                "mode": {
                    "type": "string",
                    "enum": ["ask", "plan", "agent"],
                    "description": "ask/plan = read-only; agent may edit workspace",
                },
                "model": {"type": "string", "description": "Optional Codex model override"},
                "timeout_s": {"type": "integer", "description": "Timeout in seconds (default 600)"},
            },
            "required": ["task"],
        },
        t_codex_run_task,
        plan=True,
    ),
    _builtin(
        "generate_image",
        (
            "Generate image(s) from a text prompt (xAI Imagine — a PAID action; "
            "the user confirms unless yolo). Saves to .xlii/artifacts/ and "
            "returns the path(s) — use when the user asks for an image/diagram "
            "mock/art, not for charts you can code."
        ),
        {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What to generate"},
                "n": {"type": "integer", "description": "How many images (1-4, default 1)"},
                "model": {"type": "string", "description": "Optional image model override"},
            },
            "required": ["prompt"],
        },
        t_generate_image,
        # Paid + side-effecting: never parallel fan-out (each worker would
        # spend), never in read-only plan mode, never for workers.
        parallel=False, plan=False, worker=False,
    ),
    _builtin(
        "send_file",
        (
            "Send a local file to the person you are talking to — it lands in "
            "their chat as an inline image/audio/document right after this "
            "turn. Use for files you just made (a generate_image render, a "
            "create_pdf document) or a file they asked for. Pass the file "
            "path; tell them what you're sending, not the path."
        ),
        {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "Path of the file to deliver (as returned "
                                        "by the tool that made it)"},
            },
            "required": ["path"],
        },
        t_send_file,
        # Outward send to a human: never fan-out, never plan mode, never
        # workers. Advertised only when the caller granted an outbox.
        parallel=False, plan=False, worker=False,
    ),
    _builtin(
        "create_pdf",
        (
            "Render markdown content or a project source alias/path to a local PDF "
            "under .xlii/artifacts/ (free, local, no network). Use when the user "
            "wants a shareable document — resume, report, or exported artifact. "
            "Returns the saved path, never the PDF bytes."
        ),
        {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Markdown body to render (use this OR source, not both)",
                },
                "source": {
                    "type": "string",
                    "description": "Alias (last, verify, peer, loop, plan) or file path",
                },
                "title": {"type": "string", "description": "Optional document title hint"},
                "out": {"type": "string", "description": "Optional output filename"},
            },
        },
        t_create_pdf,
        parallel=False, plan=False, worker=False,
    ),
    _builtin(
        "read_email",
        (
            "Fetch one email message by id (account:folder:uid). Returns parsed "
            "headers and plain-text body from the local cache or IMAP. Never "
            "returns raw MIME or attachment bytes — attachments are name/size only."
        ),
        {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Message id (account:folder:uid)"},
                "message_id": {
                    "type": "string",
                    "description": "Alias for id",
                },
                "account": {
                    "type": "string",
                    "description": "Account name (optional when only one configured)",
                },
            },
            "required": ["id"],
        },
        t_read_email,
        parallel=True, plan=True, worker=True,
    ),
    _builtin(
        "search_email",
        (
            "Search the inbox for messages matching a query. Returns a bounded "
            "list of summaries (id, from, subject, date) — never raw MIME."
        ),
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search text"},
                "account": {"type": "string", "description": "Account name (optional)"},
                "unread_only": {
                    "type": "boolean",
                    "description": "Limit to unread messages",
                },
            },
            "required": ["query"],
        },
        t_search_email,
        parallel=True, plan=True, worker=True,
    ),
    _builtin(
        "send_email",
        (
            "Send an email via SMTP. Gated outward action: requires typing "
            "'send' to confirm (unless --yolo). Refuses headless and workers. "
            "Shows to/subject in the confirm prompt."
        ),
        {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient address"},
                "subject": {"type": "string", "description": "Subject line"},
                "body": {"type": "string", "description": "Plain-text body"},
                "html": {
                    "type": "string",
                    "description": "Optional HTML alternative body",
                },
                "account": {"type": "string", "description": "Account name (optional)"},
            },
            "required": ["to", "body"],
        },
        t_send_email,
        parallel=False, plan=False, worker=False,
    ),
    _builtin(
        "command_help",
        (
            "Look up ONE xlii slash command in the live registry for this build: "
            "its usage line, description, aliases, REPL surfaces, and category. "
            "Use this instead of reciting flags from memory — /howto's system "
            "prompt carries command NAMES only, so this is how you get the "
            "details. `name` takes the bare name ('plan') or '/plan'. An unknown "
            "name is refused with the nearest match; never invent a command."
        ),
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Slash command name, with or without the leading /",
                },
            },
            "required": ["name"],
        },
        t_command_help,
        # parallel: a dict lookup over the in-process registry. NOT plan/worker
        # safe by membership, only by cost — the plan and worker palettes are
        # about the code project, and a slash-command lookup is neither read
        # nor write on it; howto is the palette that wants this (HOWTO_TOOLS).
        parallel=True, plan=False, worker=False,
    ),
]


REGISTRY: dict[str, ToolFn] = {t.name: t.handler for t in BUILTIN_TOOLS}

# Workers are read-only investigators: same toolset minus mutation + no swarm.
WORKER_REGISTRY: dict[str, ToolFn] = {
    t.name: t.handler for t in BUILTIN_TOOLS if t.worker_safe
}

# Writer-workers (swarm): read palette + write_file/edit_file + bash.
_WRITER_EXTRA = frozenset({"write_file", "edit_file"})
WRITER_REGISTRY: dict[str, ToolFn] = {
    t.name: t.handler
    for t in BUILTIN_TOOLS
    if t.worker_safe or t.name in _WRITER_EXTRA
}

# Tools that are safe to execute concurrently within a single tool_calls batch.
# Read-only + idempotent. dispatch_subagent is included since worker fan-out
# is the original parallel use-case (and isn't a built-in handler here).
PARALLEL_SAFE: set[str] = {t.name for t in BUILTIN_TOOLS if t.parallel_safe} | {
    "dispatch_subagent",
    "request_deep_search",  # chat-tiers: read-only deep-search escalation
}

# --------------------------------------------------------------------------- #
#  Project / User-defined tools (the new extensible layer)
# --------------------------------------------------------------------------- #

_PROJECT_TOOLS: dict[str, AgentTool] = {}


def register_agent_tool(tool: AgentTool) -> None:
    """Register a tool that the agent can call."""
    _PROJECT_TOOLS[tool.name] = tool
    if tool.parallel_safe:
        PARALLEL_SAFE.add(tool.name)


def get_tool_fn(name: str) -> Optional[ToolFn]:
    """Resolve a tool name to its callable: builtin REGISTRY first, then
    project-loaded tools. This is THE lookup the executor must use — project
    tools are advertised in tool_schemas(), so they must also dispatch."""
    fn = REGISTRY.get(name)
    if fn is not None:
        return fn
    tool = _PROJECT_TOOLS.get(name)
    if tool is not None:
        return tool.handler
    return None


def load_project_tools(xli_dir: Path) -> None:
    """Load user-defined tools from .xlii/tools.py or .xlii/tools/ (directory, with subdirs)."""
    # Keep door tools (source="door"); only project-authored tools reload.
    for name in [n for n, t in _PROJECT_TOOLS.items() if getattr(t, "source", "project") == "project"]:
        del _PROJECT_TOOLS[name]

    # 1. Single file
    single = xli_dir / "tools.py"
    if single.exists():
        _load_tools_module(single)

    # 2. Directory tree (supports subpackages like commands)
    tools_dir = xli_dir / "tools"
    if tools_dir.is_dir():
        for pyfile in sorted(tools_dir.rglob("*.py")):
            if pyfile.name.startswith("_") or "__pycache__" in pyfile.parts:
                continue
            _load_tools_module(pyfile)


def _load_tools_module(path: Path) -> None:
    try:
        import importlib.util
        module_name = f"xlii_project_tool_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            return
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        if hasattr(mod, "get_tools"):
            for tool in mod.get_tools():
                if isinstance(tool, AgentTool):
                    register_agent_tool(tool)
    except Exception as e:
        from xlii.ui import console
        console.print(f"[yellow]Warning:[/yellow] failed to load tools from {path}: {e}")

# Tools available in PLAN MODE — strictly read-only, no shell, no fan-out.
# Bash and dispatch_subagent are excluded because both can mutate state;
# code_execute is excluded too (runs code, just server-side).
PLAN_MODE_TOOLS: set[str] = {t.name for t in BUILTIN_TOOLS if t.plan_mode_safe}


def _schema_for(tool: AgentTool) -> dict:
    """OpenAI-compatible tool schema for one AgentTool (built-in or project)."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "User-defined project tool",
            "parameters": tool.parameters or {"type": "object", "properties": {}},
        },
    }


def _all_tools() -> list[AgentTool]:
    """Built-in tools followed by any project-loaded tools, in advertise order."""
    return [*BUILTIN_TOOLS, *_PROJECT_TOOLS.values()]


def tool_schemas() -> list[dict]:
    """OpenAI-compatible tool schemas for chat.completions — built-in tools
    followed by any project-loaded tools."""
    return [_schema_for(t) for t in _all_tools()]


# Media-out: the delivery palette exists exactly when the caller granted an
# outbox (`xlii ask --outbox` — today, the XMPP daemon). send_file rides the
# grant PAST the chat-surface allowlist because the outbox only ever delivers
# to the peer already in this conversation — it adds "hand them a file" to a
# surface that could already talk to them, no new reach. generate_image rides
# the same grant: on a headless mouth an image you can't deliver is spend with
# no product, and with a mouth the owner's text request IS the ask.
_OUTBOX_TOOLS = ("send_file", "generate_image")


def apply_outbox_gate(schemas: list[dict], outbox_dir: Any) -> list[dict]:
    """Two-way palette gate for a turn's advertised schemas.

    No outbox ⇒ strip send_file (a dead tool that could only refuse).
    Outbox ⇒ ensure the delivery palette is present, whatever surface policy
    ran before this (chat-blind included, by design — see _OUTBOX_TOOLS)."""
    if outbox_dir is None:
        return [s for s in schemas if s["function"]["name"] != "send_file"]
    have = {s["function"]["name"] for s in schemas}
    extra = [
        s for s in tool_schemas()
        if s["function"]["name"] in _OUTBOX_TOOLS
        and s["function"]["name"] not in have
    ]
    return schemas + extra


# IMAP/SMTP tools exist only when an account is configured. Advertising them
# empty burns chat iterations: the model retries a guaranteed fail until
# max_chat_tool_iterations. Same idea as plugin_search with no subscriptions.
_EMAIL_TOOLS = frozenset({"read_email", "search_email", "send_email"})


def apply_email_account_gate(schemas: list[dict], cfg: Any) -> list[dict]:
    """Strip mail tools when no account can answer them. Do not inject."""
    accounts = getattr(cfg, "email_accounts", None) or {}
    if accounts:
        return schemas
    return [s for s in schemas if s["function"]["name"] not in _EMAIL_TOOLS]


def apply_xai_docs_gate(schemas: list[dict], cfg: Any) -> list[dict]:
    """Strip ``xai_docs`` when the DNA read is switched off. Default on."""
    if getattr(cfg, "xai_docs", True) is not False:
        return schemas
    return [s for s in schemas if s["function"]["name"] != "xai_docs"]


# Subagent roles (cursor-workflows.md A3). A role narrows the worker's tool
# palette: `explore` drops the shell so search-heavy investigation pays no bash
# surface and adds no shell noise; `bash` is a minimal read+shell worker for
# isolated command runs; `general` is the full read-only worker palette.
WORKER_ROLES = {"explore", "bash", "general", "lab"}
# Both restricted roles use ALLOWLISTS (not denylists) so a worker_safe project
# tool that shells out can't slip through `explore` — the role is a real boundary.
_EXPLORE_TOOLS = {"search_project", "read_file", "list_dir", "glob", "grep",
                  "plugin_search", "plugin_get", "map", "xai_docs"}
_BASH_ROLE_TOOLS = {"read_file", "bash"}  # the `bash` role's whole palette


def worker_tool_schemas(*, writer: bool = False, role: str = "general") -> list[dict]:
    """Schemas for workers — read-only by default; writer=True adds write tools.

    `role` (A3) further narrows a read-only worker's palette: `explore` is the
    built-in read/search tools only (NO shell, NO project tools), `bash` is
    read_file + bash only, `general` is the full read-only set. Roles do not apply
    to writer workers (the swarm path), which always get the full writer palette."""
    base = [
        t for t in _all_tools()
        if t.worker_safe or (writer and t.name in _WRITER_EXTRA)
    ]
    if not writer and role == "explore":
        base = [t for t in base if t.name in _EXPLORE_TOOLS]
    elif not writer and role == "bash":
        base = [t for t in base if t.name in _BASH_ROLE_TOOLS]
    return [_schema_for(t) for t in base]


def plan_mode_schemas() -> list[dict]:
    """Schemas available in plan mode — read-only investigation only (every tool
    whose plan_mode_safe flag is set)."""
    return [_schema_for(t) for t in _all_tools() if t.plan_mode_safe]


def _tool_named(name: str) -> AgentTool:
    for t in _all_tools():
        if t.name == name:
            return t
    raise KeyError(f"no such tool: {name}")


def plan_write_schemas() -> list[dict]:
    """Plan mode (plan-write-domain P0): read-only investigation plus
    `write_file` / `edit_file` — planning IS writing the plan. Both writers are
    path-gated in tool_handlers (`write_path_refusal`) to `<xli_dir>/plans/`;
    the repo stays read-only until /execute. No bash / dispatch_subagent."""
    return plan_mode_schemas() + [
        _schema_for(_tool_named("write_file")),
        _schema_for(_tool_named("edit_file")),
    ]


def debug_instrument_schemas() -> list[dict]:
    """Debug Instrument phase (cursor-workflows.md B0): read-only investigation
    plus `edit_file`, which is marker-gated in `t_edit_file` — every added line
    must carry DEBUG_MARKER. No write_file / bash (instrumentation is marked log
    lines inserted into existing files, not new files or program runs)."""
    return plan_mode_schemas() + [_schema_for(_tool_named("edit_file"))]


def debug_reproduce_schemas() -> list[dict]:
    """Debug Reproduce phase: read-only investigation plus `bash` to run the repro
    and capture the instrumented output. File writes stay locked."""
    return plan_mode_schemas() + [_schema_for(_tool_named("bash"))]


def ops_mode_schemas() -> list[dict]:
    """Ops mode (terminal-native-toolkit Phase 8): read-only investigation plus
    `bash` for OS diagnostics — no file writes or subagent dispatch."""
    return plan_mode_schemas() + [_schema_for(_tool_named("bash"))]


def dispatch_subagent_schema() -> dict:
    """Schema for the dispatch_subagent tool — only available to the main agent."""
    return {
        "type": "function",
        "function": {
            "name": "dispatch_subagent",
            "description": (
                "Dispatch a read-only worker agent on a focused investigation task. "
                "Workers run in parallel when multiple are dispatched in the same batch. "
                "They cannot dispatch further workers. Workers see "
                "ONLY the brief — write a tight, self-contained task description. Use "
                "`context` to pass relevant snippets. Returns the worker's final summary. "
                "On a pointer desk (Files mount set) workers inherit that mount: "
                "list_dir/read_file inspect the remote app, not the empty local stub. "
                "Do not bash-ls the stub to decide the codebase is missing.\n"
                "Pick a `role` for the right tool palette:\n"
                "- explore (DEFAULT): search_project/read_file/list_dir/glob/grep, NO shell — "
                "use for code-reading and investigation (most dispatches).\n"
                "- bash: read_file + bash only — use to run a command in isolation so its "
                "shell output doesn't fill your context.\n"
                "- general: the full read-only palette including bash.\n"
                "- lab: write_file/edit_file/bash on the current desk; "
                "refused on Home — switch into a folder first. "
                "Use this to create or edit files the rider asked for.\n"
                "If the task needs to run commands, you MUST pass role 'bash', 'general', "
                "or 'lab' — the default 'explore' role has no shell."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "The investigation task. Be specific about what you want back.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional snippets, file excerpts, or background the worker needs.",
                    },
                    "role": {
                        "type": "string",
                        "enum": ["explore", "bash", "general", "lab"],
                        "description": "Tool palette for the worker (default explore — no shell).",
                    },
                    "gig": {
                        "type": "string",
                        "description": (
                            "Optional: hire a configured non-xAI provider as this "
                            "worker's brain (gigwork). Only names in the user's "
                            "gigwork.defaults.allow list are permitted; use for "
                            "second opinions / cheap exploration when the user "
                            "asks for an outside perspective."
                        ),
                    },
                    "gaggle": {
                        "type": "string",
                        "description": (
                            "Optional: run a named gaggle (stock: second-opinion) "
                            "instead of one worker — home explore + one allowlisted "
                            "gig, then synth_conflicts. Do not pass gig= together "
                            "with gaggle=. Members are read-only (write: false)."
                        ),
                    },
                    "where": {
                        "type": "string",
                        "description": (
                            "Where to run: omit or 'this body' = local worker. "
                            "A pool name (the jobs room) publishes an ad instead. "
                            "Remote posts always confirm and name the pool and budget."
                        ),
                    },
                },
                "required": ["task"],
            },
        },
    }
