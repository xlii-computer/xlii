#!/usr/bin/env python3
"""A minimal fake ACP agent for offline AcpClient tests.

Speaks the same newline-delimited JSON-RPC 2.0 over stdio as `cursor-agent acp`,
but deterministically and with no network. On `session/prompt` it exercises every
client callback path: a tool_call lifecycle, a `session/request_permission`
round-trip, an `fs/write_text_file` round-trip, agent_message_chunks (echoing the
picked option / mode / model so tests can assert), and a final stopReason.
"""

import json
import sys


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def read_msg():
    line = sys.stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return read_msg()
    return json.loads(line)


def await_response(rid):
    while True:
        msg = read_msg()
        if msg is None:
            return None
        if msg.get("id") == rid and ("result" in msg or "error" in msg):
            return msg


def upd(sid, update):
    send({"jsonrpc": "2.0", "method": "session/update",
          "params": {"sessionId": sid, "update": update}})


def handle(msg, state):
    method = msg.get("method")
    rid = msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": 1,
            "agentCapabilities": {"loadSession": True,
                                  "promptCapabilities": {"image": True}},
            "authMethods": [{"id": "cursor_login", "name": "Cursor Login"}],
        }})
    elif method == "session/new":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "sessionId": "sess-1",
            "modes": {"currentModeId": "agent", "availableModes": [
                {"id": "agent", "name": "Agent"},
                {"id": "ask", "name": "Ask"},
                {"id": "plan", "name": "Plan"}]},
            "models": {"currentModelId": "composer-2.5[fast=true]", "availableModels": [
                {"modelId": "composer-2.5[fast=true]", "name": "composer-2.5"},
                {"modelId": "grok-build-0.1[context=200k]", "name": "grok-build-0.1"}]},
        }})
        upd("sess-1", {"sessionUpdate": "available_commands_update", "availableCommands": []})
    elif method == "session/set_mode":
        state["mode"] = msg["params"]["modeId"]
        send({"jsonrpc": "2.0", "id": rid, "result": {}})
    elif method == "session/set_model":
        state["model"] = msg["params"]["modelId"]
        send({"jsonrpc": "2.0", "id": rid, "result": {}})
    elif method == "session/prompt":
        sid = msg["params"]["sessionId"]
        upd(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1",
                  "title": "Edit File", "kind": "edit", "status": "pending", "rawInput": {}})
        send({"jsonrpc": "2.0", "id": 9001, "method": "session/request_permission", "params": {
            "sessionId": sid, "toolCall": {"toolCallId": "t1"},
            "options": [{"optionId": "allow-1", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "rej-1", "name": "Reject", "kind": "reject_once"}]}})
        presp = await_response(9001)
        picked = (((presp or {}).get("result") or {}).get("outcome") or {}).get("optionId", "?")
        send({"jsonrpc": "2.0", "id": 9002, "method": "cursor/ask_question", "params": {
            "sessionId": sid,
            "questions": [{"id": "q1", "prompt": "Pick approach?", "options": [
                {"id": "opt-a", "label": "A"}, {"id": "opt-b", "label": "B"}]}]}})
        qresp = await_response(9002)
        q_answers = (((qresp or {}).get("result") or {}).get("answers") or [])
        q_pick = (q_answers[0].get("selectedOptionIds") or ["?"])[0] if q_answers else "?"
        send({"jsonrpc": "2.0", "id": 9003, "method": "cursor/create_plan", "params": {
            "sessionId": sid, "plan": {"title": "Fake plan", "steps": ["step 1"]}}})
        plan_resp = await_response(9003)
        plan_ok = ((plan_resp or {}).get("result") or {}).get("approved", False)
        send({"jsonrpc": "2.0", "method": "cursor/update_todos",
              "params": {"sessionId": sid, "todos": [{"id": "t1", "content": "todo", "status": "pending"}]}})
        send({"jsonrpc": "2.0", "id": 9004, "method": "fs/write_text_file",
              "params": {"sessionId": sid, "path": "out.txt", "content": "written via fs"}})
        await_response(9004)
        upd(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed",
                  "content": [{"type": "diff", "path": "out.txt", "oldText": "", "newText": "x"}]})
        for chunk in ["picked=", picked, " q=", q_pick, " plan=", str(plan_ok),
                      " mode=", state["mode"], " model=", str(state["model"])]:
            upd(sid, {"sessionUpdate": "agent_message_chunk",
                      "content": {"type": "text", "text": chunk}})
        upd(sid, {"sessionUpdate": "session_info_update", "title": "Fake Turn"})
        send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid,
              "error": {"code": -32601, "message": "method not found"}})


def main():
    state = {"mode": "agent", "model": None}
    while True:
        msg = read_msg()
        if msg is None:
            break
        handle(msg, state)


if __name__ == "__main__":
    main()
