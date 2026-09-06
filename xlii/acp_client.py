"""Agent Client Protocol (ACP) client — drive Cursor's Composer agent.

xlii speaks ACP as the **client** (the editor role): it spawns ``cursor-agent acp``
as a JSON-RPC-over-stdio subprocess and drives a full agent session. Composer owns
the read/propose/diff/apply loop; xlii streams its updates and answers its
permission / filesystem callbacks. This is the *inverse* of xlii's MCP context
server (``xlii/mcp/context_server.py``), where xlii is the server an external
agent pulls from. The two compose: drive Composer over ACP while pointing it at
xlii's MCP server for DeepContext.

Transport: newline-delimited JSON-RPC 2.0. Client → stdin, agent → stdout, logs →
stderr. Stdlib only.

Empirically verified against cursor-agent 2026.06 (protocol v1):
  initialize → session/new → session/prompt → session/update* → {stopReason}
  agent → client callbacks: session/request_permission, fs/read_text_file,
  fs/write_text_file. (session/cancel is NOT implemented by this build — close()
  cancels by terminating the subprocess.)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

ACP_PROTOCOL_VERSION = 1
DEFAULT_REQUEST_TIMEOUT = 600.0

# Permission policies for session/request_permission callbacks.
ALLOW = "allow"   # auto-select an allow-once option (default; let Composer work)
REJECT = "reject"  # auto-select a reject option

EventCallback = Callable[[str, dict[str, Any]], None]
PermissionCallback = Callable[[dict[str, Any]], Optional[str]]
AskQuestionCallback = Callable[[dict[str, Any]], Optional[dict[str, Any]]]
CreatePlanCallback = Callable[[dict[str, Any]], Optional[dict[str, Any]]]


class AcpError(RuntimeError):
    """ACP transport or protocol error."""


def resolve_cursor_cli() -> str | None:
    """Locate the Cursor agent CLI.

    Prefers the unambiguous ``cursor-agent`` name over ``agent`` (which collides
    with the Grok CLI on PATH). Honours ``XLII_CURSOR_AGENT_BIN`` for an explicit
    path. Returns ``None`` when no usable binary is found.
    """
    override = os.environ.get("XLII_CURSOR_AGENT_BIN")
    if override:
        return override if os.path.exists(override) else None
    return shutil.which("cursor-agent")


XLII_MCP_SERVER_NAME = "xlii-deep-contexts"


def xlii_mcp_server_spec(
    *, command: str = "xlii", args: list[str] | None = None,
) -> dict[str, Any]:
    """`.cursor/mcp.json` entry value for xlii's DeepContext stdio server.

    Note: ``command`` must resolve to an interpreter/entrypoint whose
    environment has xlii's ``[mcp]`` extra installed (the same ``xlii`` that
    serves Grok Build via ``xlii mcp deep-contexts``).
    """
    return {"command": command, "args": list(args) if args is not None else ["mcp", "deep-contexts"]}


def ensure_cursor_mcp_config(
    project_root: str | Path,
    *,
    name: str = XLII_MCP_SERVER_NAME,
    spec: dict[str, Any] | None = None,
) -> str:
    """Merge xlii's DeepContext MCP server into ``<project_root>/.cursor/mcp.json``.

    This is how Composer actually attaches MCP servers: empirically, the
    cursor-agent ACP build only loads MCP servers declared in project/user
    ``.cursor/mcp.json`` (then approved) — stdio servers passed via
    ``session/new``'s ``mcpServers`` are accepted by the schema but never
    connected (only http/sse are in the agent's mcpCapabilities). Existing
    servers are preserved. Returns "present" | "added" | "updated".
    """
    spec = spec or xlii_mcp_server_spec()
    cdir = Path(project_root) / ".cursor"
    cfg_path = cdir / "mcp.json"
    data: dict[str, Any] = {}
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(errors="replace"))
        except (json.JSONDecodeError, OSError):
            data = {}
    if not isinstance(data, dict):
        data = {}
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = data["mcpServers"] = {}
    if servers.get(name) == spec:
        return "present"
    existed = name in servers
    servers[name] = spec
    cdir.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(data, indent=2) + "\n")
    return "updated" if existed else "added"


def enable_cursor_mcp(
    name: str = XLII_MCP_SERVER_NAME, *, cwd: str | Path, cli: str | None = None,
) -> bool:
    """Approve an MCP server for headless/ACP use (``cursor-agent mcp enable``).

    Approval is required: a configured-but-unapproved server shows as
    "not loaded (needs approval)" and its tools never reach the model.
    """
    cli = cli or resolve_cursor_cli()
    if not cli:
        return False
    try:
        proc = subprocess.run(
            [cli, "mcp", "enable", name],
            cwd=str(cwd), capture_output=True, text=True, timeout=30,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@dataclass
class ToolCall:
    id: str
    title: str = ""
    kind: str = ""
    status: str = ""
    paths: list[str] = field(default_factory=list)


@dataclass
class AcpResult:
    """Outcome of a single ``session/prompt`` turn."""

    stop_reason: str = ""
    text: str = ""  # concatenated agent_message_chunk text
    title: str = ""
    tool_calls: dict[str, ToolCall] = field(default_factory=dict)
    files_touched: list[str] = field(default_factory=list)
    raw_updates: list[dict[str, Any]] = field(default_factory=list)


def _pick_option(options: list[dict[str, Any]], prefer: tuple[str, ...]) -> str | None:
    """Pick an option id, preferring kinds/names matching `prefer` substrings."""
    for key in prefer:
        for o in options:
            hay = (str(o.get("kind", "")) + str(o.get("optionId", "")) + str(o.get("name", ""))).lower()
            if key in hay:
                return o.get("optionId")
    return options[0].get("optionId") if options else None


class AcpClient:
    """A live ACP session against ``cursor-agent acp``.

    Typical use::

        with AcpClient(cwd=repo, mode="agent", on_event=render) as client:
            client.new_session()
            result = client.prompt("refactor X")
            print(result.text, result.files_touched)
    """

    def __init__(
        self,
        *,
        cwd: str | Path,
        argv: list[str] | None = None,
        mode: str = "agent",
        model: str | None = None,
        permission: str = ALLOW,
        on_event: EventCallback | None = None,
        on_permission: PermissionCallback | None = None,
        on_ask_question: AskQuestionCallback | None = None,
        on_create_plan: CreatePlanCallback | None = None,
        fs_root: str | Path | None = None,
        env: dict[str, str] | None = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        login_hint: str | None = None,
    ) -> None:
        self.cwd = str(cwd)
        if argv is None:
            cli = resolve_cursor_cli()
            if not cli:
                raise AcpError(
                    "cursor-agent CLI not found "
                    "(install: curl https://cursor.com/install | bash)"
                )
            argv = [cli, "acp"]
        self.argv = list(argv)
        self.mode = mode
        self.model = model
        self.permission = permission
        self.on_event = on_event
        self.on_permission = on_permission
        self.on_ask_question = on_ask_question
        self.on_create_plan = on_create_plan
        self.login_hint = login_hint or "cursor-agent login"
        self.fs_root = Path(fs_root) if fs_root else Path(self.cwd)
        self.env = env
        self.request_timeout = request_timeout

        self.session_id: str | None = None
        self.agent_capabilities: dict[str, Any] = {}
        self.auth_methods: list[dict[str, Any]] = []
        self.available_modes: list[dict[str, Any]] = []
        self.current_mode_id: str | None = None
        self.available_models: list[dict[str, Any]] = []
        self.current_model_id: str | None = None
        self.notes: list[str] = []  # best-effort warnings (e.g. model pin failed)
        self.files_touched: list[str] = []

        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._id = 0
        self._pending: dict[int, threading.Event] = {}
        self._responses: dict[int, dict[str, Any]] = {}
        self._result: AcpResult | None = None
        self._stderr_lines: list[str] = []
        self._closed = False

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> "AcpClient":
        self._proc = subprocess.Popen(
            self.argv,
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=self.env,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()
        self._initialize()
        return self

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            # Closing an already-dead child's stdin -- the wait/kill below is what actually reaps it.
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                # The wait timed out and the kill failed too, so the child is either already gone or unkillable;
                # close() is done either way.
                pass

    def __enter__(self) -> "AcpClient":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_lines[-20:])

    # --- protocol steps -----------------------------------------------------

    def _initialize(self) -> dict[str, Any]:
        result = self._request(
            "initialize",
            {
                "protocolVersion": ACP_PROTOCOL_VERSION,
                "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}},
            },
            timeout=30,
        )
        self.agent_capabilities = result.get("agentCapabilities", {}) or {}
        self.auth_methods = result.get("authMethods", []) or []
        return result

    def new_session(self, mcp_servers: list[dict[str, Any]] | None = None) -> str:
        try:
            result = self._request(
                "session/new",
                {"cwd": self.cwd, "mcpServers": mcp_servers or []},
                timeout=60,
            )
        except AcpError as e:
            raise AcpError(
                f"{e} — is the harness logged in? run `{self.login_hint}`"
            ) from e
        self.session_id = result.get("sessionId")
        if not self.session_id:
            raise AcpError("session/new returned no sessionId")

        modes = result.get("modes") or {}
        self.available_modes = modes.get("availableModes", []) or []
        self.current_mode_id = modes.get("currentModeId")
        models = result.get("models") or {}
        self.available_models = models.get("availableModels", []) or []
        self.current_model_id = models.get("currentModelId")

        if self.mode and self.mode != self.current_mode_id:
            # ACP servers advertise heterogeneous mode sets (community harnesses
            # e.g. kimi don't share the built-ins' ask/agent ids). Best-effort —
            # mirror _maybe_set_model: an unadvertised mode is a note, not a
            # fatal error; the session's current mode stands.
            advertised = {m.get("id") or m.get("modeId") for m in self.available_modes}
            if self.mode not in advertised:
                self.notes.append(
                    f"mode {self.mode!r} not advertised by this ACP server "
                    f"(has: {sorted(a for a in advertised if a) or 'none'}); "
                    f"keeping session default {self.current_mode_id!r}"
                )
            else:
                self.set_mode(self.mode)
        if self.model:
            self._maybe_set_model(self.model)
        return self.session_id

    def set_mode(self, mode_id: str) -> None:
        self._request(
            "session/set_mode",
            {"sessionId": self.session_id, "modeId": mode_id},
            timeout=30,
        )
        self.current_mode_id = mode_id
        self.mode = mode_id

    def _resolve_model_id(self, want: str) -> str | None:
        """Map a friendly model name (e.g. 'composer-2.5') to a concrete modelId."""
        for m in self.available_models:
            if m.get("name") == want or m.get("modelId") == want:
                return m.get("modelId")
        for m in self.available_models:
            mid = str(m.get("modelId", ""))
            if mid.split("[", 1)[0] == want:  # match base id, ignore [bracket params]
                return mid
        return None

    def _maybe_set_model(self, want: str) -> None:
        """Best-effort model pin. Default session model is already Composer 2.5,
        so a failure here is a note, not a fatal error."""
        model_id = self._resolve_model_id(want)
        if not model_id:
            self.notes.append(f"model {want!r} not in availableModels; using session default")
            return
        if model_id == self.current_model_id:
            return
        try:
            self._request(
                "session/set_model",
                {"sessionId": self.session_id, "modelId": model_id},
                timeout=30,
            )
            self.current_model_id = model_id
        except AcpError as e:
            self.notes.append(f"could not pin model {want!r} ({e}); using session default")

    def prompt(self, text: str, timeout: float | None = None) -> AcpResult:
        if not self.session_id:
            raise AcpError("no active session; call new_session() first")
        self._result = AcpResult()
        try:
            result = self._request(
                "session/prompt",
                {"sessionId": self.session_id, "prompt": [{"type": "text", "text": text}]},
                timeout=timeout if timeout is not None else self.request_timeout,
            )
        finally:
            res = self._result
            self._result = None
        res.stop_reason = result.get("stopReason", "")
        return res

    # --- JSON-RPC plumbing --------------------------------------------------

    def _next_id(self) -> int:
        with self._write_lock:
            self._id += 1
            return self._id

    def _write(self, obj: dict[str, Any]) -> None:
        data = json.dumps(obj) + "\n"
        with self._write_lock:
            proc = self._proc
            if proc is None or proc.stdin is None:
                raise AcpError("ACP process not running")
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (BrokenPipeError, ValueError, OSError) as e:
                raise AcpError(f"ACP write failed: {e}") from e

    def _request(self, method: str, params: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        rid = self._next_id()
        ev = threading.Event()
        with self._state_lock:
            self._pending[rid] = ev
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        got = ev.wait(timeout if timeout is not None else self.request_timeout)
        with self._state_lock:
            self._pending.pop(rid, None)
            msg = self._responses.pop(rid, None)
        if not got or msg is None:
            tail = self.stderr_tail
            raise AcpError(
                f"timeout/no response for {method}" + (f"\n{tail}" if tail else "")
            )
        if "error" in msg:
            err = msg["error"] or {}
            raise AcpError(f"{method} failed: {err.get('message')} (code {err.get('code')})")
        return msg.get("result", {}) or {}

    def _read_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict):
                    self._dispatch(msg)
        except (ValueError, OSError):
            # Reading a closed or detached pipe just ends the dispatch loop -- normal on shutdown.
            pass
        finally:
            with self._state_lock:
                for ev in self._pending.values():
                    ev.set()

    def _read_stderr(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        try:
            for line in proc.stderr:
                self._stderr_lines.append(line.rstrip())
                if len(self._stderr_lines) > 200:
                    del self._stderr_lines[:-200]
        except (ValueError, OSError):
            # Reading a closed or detached pipe just ends the stderr tail -- normal on shutdown.
            pass

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if "method" in msg and "id" in msg:  # agent → client request
            self._handle_incoming_request(msg)
        elif "method" in msg:  # notification
            self._handle_notification(msg)
        elif "id" in msg:  # response to one of our requests
            rid = msg["id"]
            with self._state_lock:
                self._responses[rid] = msg
                ev = self._pending.get(rid)
            if ev:
                ev.set()

    # --- agent → client callbacks -------------------------------------------

    def _handle_incoming_request(self, msg: dict[str, Any]) -> None:
        method = msg.get("method")
        rid = msg["id"]
        params = msg.get("params") or {}
        try:
            if method == "session/request_permission":
                result = self._handle_permission(params)
            elif method == "fs/read_text_file":
                result = self._handle_fs_read(params)
            elif method == "fs/write_text_file":
                result = self._handle_fs_write(params)
            elif method == "cursor/ask_question":
                result = self._handle_ask_question(params)
            elif method == "cursor/create_plan":
                result = self._handle_create_plan(params)
            else:
                self._write(
                    {"jsonrpc": "2.0", "id": rid,
                     "error": {"code": -32601, "message": f"method not handled: {method}"}}
                )
                return
        except Exception as e:  # never let a callback kill the reader
            self._write({"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": str(e)}})
            return
        self._write({"jsonrpc": "2.0", "id": rid, "result": result})

    def _handle_permission(self, params: dict[str, Any]) -> dict[str, Any]:
        options = params.get("options") or []
        if self.on_permission is not None:
            chosen = self.on_permission(params)
            if chosen:
                return {"outcome": {"outcome": "selected", "optionId": chosen}}
            return {"outcome": {"outcome": "cancelled"}}
        if self.mode in ("ask", "plan") or self.permission == REJECT:
            prefer = ("reject_once", "reject")
        else:
            prefer = ("allow_once", "allow")
        pick = _pick_option(options, prefer)
        if pick:
            return {"outcome": {"outcome": "selected", "optionId": pick}}
        return {"outcome": {"outcome": "cancelled"}}

    def _safe_path(self, path: str, *, for_write: bool) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self.fs_root / p
        p = p.resolve()
        if for_write:
            root = self.fs_root.resolve()
            if root != p and root not in p.parents:
                raise AcpError(f"refusing write outside workspace: {p}")
        return p

    def _handle_fs_read(self, params: dict[str, Any]) -> dict[str, Any]:
        p = self._safe_path(params.get("path", ""), for_write=False)
        text = p.read_text(errors="replace")
        line = params.get("line")
        limit = params.get("limit")
        if line is not None or limit is not None:
            lines = text.splitlines(keepends=True)
            start = max(0, (line or 1) - 1)
            end = start + limit if limit else len(lines)
            text = "".join(lines[start:end])
        return {"content": text}

    def _handle_fs_write(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.mode in ("ask", "plan"):
            raise AcpError(f"refusing write in read-only {self.mode} mode")
        if self.permission == REJECT:
            raise AcpError("refusing write with reject permission")
        p = self._safe_path(params.get("path", ""), for_write=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(params.get("content", ""))
        self._record_file(str(p))
        return {}

    def _handle_ask_question(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.on_ask_question is not None:
            custom = self.on_ask_question(params)
            if custom is not None:
                return custom
        questions = params.get("questions") or []
        if self.permission == REJECT:
            return {"answers": []}
        answers: list[dict[str, Any]] = []
        for q in questions:
            if not isinstance(q, dict):
                continue
            opts = q.get("options") or []
            if not opts:
                continue
            first = opts[0]
            opt_id = first.get("id") if isinstance(first, dict) else None
            if opt_id:
                answers.append({"questionId": q.get("id"), "selectedOptionIds": [opt_id]})
        return {"answers": answers}

    def _handle_create_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.on_create_plan is not None:
            custom = self.on_create_plan(params)
            if custom is not None:
                return custom
        # Plan mode exists precisely to let the agent form a plan, so plan (and
        # agent) mode auto-approve unless --reject is set. Pure read-only ask mode
        # has no planning step, so it still rejects. Creating a plan never writes
        # (workspace writes are gated separately in _handle_fs_write).
        if self.mode == "ask" or self.permission == REJECT:
            return {"approved": False}
        return {"approved": True}

    # --- streaming updates --------------------------------------------------

    def _handle_notification(self, msg: dict[str, Any]) -> None:
        method = msg.get("method") or ""
        if method.startswith("cursor/"):
            if self.on_event is not None:
                try:
                    self.on_event(method, msg.get("params") or {})
                except Exception:
                    pass  # event callback failures must not interrupt the ACP reader
            return
        if method != "session/update":
            return
        update = (msg.get("params") or {}).get("update") or {}
        kind = update.get("sessionUpdate", "")
        res = self._result
        if res is not None:
            res.raw_updates.append(update)
            if kind == "agent_message_chunk":
                content = update.get("content") or {}
                if isinstance(content, dict) and content.get("type") == "text":
                    res.text += content.get("text", "")
            elif kind == "tool_call":
                tc = ToolCall(
                    id=update.get("toolCallId", ""),
                    title=update.get("title", ""),
                    kind=update.get("kind", ""),
                    status=update.get("status", ""),
                )
                res.tool_calls[tc.id] = tc
            elif kind == "tool_call_update":
                tcid = update.get("toolCallId", "")
                tc = res.tool_calls.get(tcid)
                if tc is not None:
                    if update.get("status"):
                        tc.status = update["status"]
                    for c in update.get("content") or []:
                        pth = c.get("path") if isinstance(c, dict) else None
                        if pth:
                            if pth not in tc.paths:
                                tc.paths.append(pth)
                            self._record_file(pth)
            elif kind == "session_info_update":
                if update.get("title"):
                    res.title = update["title"]
        if self.on_event is not None:
            try:
                self.on_event(kind, update)
            except Exception:
                pass  # event callback failures must not interrupt the ACP reader

    def _record_file(self, path: str) -> None:
        with self._state_lock:
            if path not in self.files_touched:
                self.files_touched.append(path)
            res = self._result
            if res is not None and path not in res.files_touched:
                res.files_touched.append(path)


def run_turn(
    task: str,
    *,
    cwd: str | Path,
    mode: str = "agent",
    model: str | None = None,
    permission: str = ALLOW,
    on_event: EventCallback | None = None,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
    argv: list[str] | None = None,
) -> AcpResult:
    """One-shot: open a session, run a single prompt, return the result."""
    with AcpClient(
        cwd=cwd,
        argv=argv,
        mode=mode,
        model=model,
        permission=permission,
        on_event=on_event,
        request_timeout=timeout,
    ) as client:
        client.new_session()
        return client.prompt(task, timeout=timeout)
