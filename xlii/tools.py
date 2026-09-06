"""Agent tool implementations — re-export façade.

Each tool is a function that takes (project_root, args_dict, ctx) and returns
ToolResult(content, dirty_paths). Dirty paths are queued for end-of-turn sync.

Implementation lives in tool_context / tool_gating / tool_handlers /
tool_schemas (godzilla refactor Track B1).
"""

from __future__ import annotations

import contextlib
import threading
from typing import Callable

# Injectable confirmation prompt — tests monkeypatch this name on xlii.tools.
_confirm: Callable[[str], str] = input

# Overriding ``_confirm`` is process-global, and bodies (the WS head, the jobs
# runner) run one turn per thread. Serialize the swap so two overlapping turns
# can't clobber each other's saved value (leaving ``_confirm`` stuck on the
# real ``input()`` — which wedges on a headless body's stdin — or on auto-deny
# forever).
_CONFIRM_SWAP_LOCK = threading.Lock()


@contextlib.contextmanager
def confirm_override(fn: Callable[[str], str]):
    """Hold the injectable ``_confirm`` hook at ``fn`` for one turn.

    The public form of the lock-guarded swap the WS server built privately
    (godzilla-mothra B5, audit cheap-win #10): every headless body needs to
    replace the default ``input()`` confirm for the duration of a turn without
    racing concurrent turns. Guarded by :data:`_CONFIRM_SWAP_LOCK` so the
    save/restore is atomic across threads — turns serialize on the confirm
    hook rather than corrupt it.
    """
    global _confirm
    with _CONFIRM_SWAP_LOCK:
        saved = _confirm
        _confirm = fn
        try:
            yield
        finally:
            _confirm = saved


def auto_deny(_prompt: str) -> str:
    """Non-interactive confirm — always declines, prints nothing.

    For bodies with no approval channel: a gated tool intent is refused cleanly
    here instead of routing through the default ``_confirm`` (``input()``),
    which would print the y/N prompt to the body's stdout and block on a stdin
    nobody is servicing, wedging the turn. Returning ``""`` reads to every gate
    as a clean denial (a non-"y"/"send" answer), so the intent comes back to
    the model as a refusal ToolResult. Bodies that pre-approve gated intents
    (e.g. a trusted-loopback ``--yolo``) simply don't install this.
    """
    return ""

from xlii.tool_context import (  # noqa: E402, F401  — public re-export façade
    GATED_INTENTS,
    INTENT_MODIFIES_PROJECT,
    INTENT_MODIFIES_SYSTEM,
    INTENT_NETWORK,
    INTENT_READ_ONLY,
    MAX_OUTPUT_BYTES,
    VALID_INTENTS,
    ToolContext,
    ToolFn,
    ToolResult,
)
from xlii.tool_gating import (  # noqa: E402, F401
    _test_path_locked,
    write_path_refusal,
)
from xlii.tool_handlers import (  # noqa: E402, F401
    _cap_output,
    _check_intent_and_gate,
    _server_tool,
    t_bash,
    t_code_execute,
    t_create_pdf,
    t_edit_file,
    t_glob,
    t_grep,
    t_list_dir,
    t_plan_amend,
    t_plan_check,
    t_plugin_call,
    t_plugin_get,
    t_plugin_search,
    t_read_file,
    t_search_project,
    t_web_search,
    t_write_file,
    t_x_search,
    t_xai_docs,
)
from xlii.tool_schemas import (  # noqa: E402, F401
    AgentTool,
    BUILTIN_TOOLS,
    PARALLEL_SAFE,
    PLAN_MODE_TOOLS,
    REGISTRY,
    WORKER_REGISTRY,
    WORKER_ROLES,
    WRITER_REGISTRY,
    _PROJECT_TOOLS,
    debug_instrument_schemas,
    debug_reproduce_schemas,
    dispatch_subagent_schema,
    get_tool_fn,
    load_project_tools,
    ops_mode_schemas,
    plan_mode_schemas,
    plan_write_schemas,
    register_agent_tool,
    tool_schemas,
    worker_tool_schemas,
)
