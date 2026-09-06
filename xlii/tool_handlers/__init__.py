"""Built-in agent tool handlers (t_* implementations)."""

from __future__ import annotations

from ._common import (
    _cap_output,
    _files_mount,
    _iter_remote_files,
    _join_mount,
    _mark_dirty,
    _resolve_in_project,
    _spill_threshold,
    _truncate,
)
from .delegate import t_codex_run_task
from .files import (
    _iter_searchable_files,
    _notify_if_plan,
    _resolve_write_target,
    _wiki_search_block,
    t_edit_file,
    t_glob,
    t_grep,
    t_list_dir,
    t_read_file,
    t_search_project,
    t_write_file,
)
from .introspect import t_command_help
from .mail import t_read_email, t_search_email, t_send_email
from .media import (
    confirm_paid_action,
    t_create_pdf,
    t_generate_image,
    t_map,
    t_send_file,
)
from .plan import t_plan_amend, t_plan_check
from .plugins import (
    _emit_plugin_user_output,
    _open_plugin_form,
    _strip_renderer_sections,
    t_plugin_call,
    t_plugin_get,
    t_plugin_search,
)
from .shell import (
    _check_intent_and_gate,
    _env_with_plugin_secrets,
    _redact_secret_values,
    t_bash,
)
from .web import (
    XAI_DOCS_MCP_URL,
    _XAI_DOCS_ACTIONS,
    _server_tool,
    t_browser,
    t_code_execute,
    t_web_search,
    t_x_search,
    t_xai_docs,
)

__all__ = [
    "XAI_DOCS_MCP_URL",
    "_XAI_DOCS_ACTIONS",
    "_cap_output",
    "_check_intent_and_gate",
    "_emit_plugin_user_output",
    "_env_with_plugin_secrets",
    "_files_mount",
    "_iter_remote_files",
    "_iter_searchable_files",
    "_join_mount",
    "_mark_dirty",
    "_notify_if_plan",
    "_open_plugin_form",
    "_redact_secret_values",
    "_resolve_in_project",
    "_resolve_write_target",
    "_server_tool",
    "_spill_threshold",
    "_strip_renderer_sections",
    "_truncate",
    "_wiki_search_block",
    "confirm_paid_action",
    "t_bash",
    "t_browser",
    "t_code_execute",
    "t_codex_run_task",
    "t_command_help",
    "t_create_pdf",
    "t_edit_file",
    "t_generate_image",
    "t_glob",
    "t_grep",
    "t_list_dir",
    "t_map",
    "t_plan_amend",
    "t_plan_check",
    "t_plugin_call",
    "t_plugin_get",
    "t_plugin_search",
    "t_read_email",
    "t_read_file",
    "t_search_email",
    "t_search_project",
    "t_send_email",
    "t_send_file",
    "t_web_search",
    "t_write_file",
    "t_x_search",
    "t_xai_docs",
]

