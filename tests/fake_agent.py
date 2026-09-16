"""Fake ACP agent: just enough protocol to exercise the client, without network or model.

It deliberately sends its permission request with **id 0**: that is the trap the client
must get past.
"""

import json
import os
import sys

NAME = sys.argv[1] if len(sys.argv) > 1 else "fake"
RELAY = sys.argv[2] if len(sys.argv) > 2 else ""
SESSION = f"sess-{NAME}"


def reply(prompt: str) -> tuple[str, bool]:
    """What the fake agent answers, and whether it asks for permission first.

    Keywords in the prompt drive the scenario: PERM asks for a permission, RELAY calls the
    other bot out, REPEAT always answers the same thing.
    """
    if "[refusal from the user]" in prompt:
        return "Understood. I suggest `uv pip install -r requirements.txt` instead.", False
    if "REPEAT" in prompt:
        return "I always say the same thing", False
    if RELAY and ("RELAY" in prompt or "do you confirm" in prompt):
        return f"@{RELAY} do you confirm?", False
    if "WRITE:" in prompt:
        return "write requested", False
    if "PERM" in prompt:
        return f"done, {NAME}.", True
    return f"done, {NAME}.", False


LOG = os.environ.get("GEMINI_TELEMETRY_OUTFILE")


def log_output(tool_name: str, call_id: str, output: str) -> None:
    """Writes a tool's output like the real agent: in the log, not in the stream."""
    if not LOG:
        return
    record = {
        "attributes": {
            "event.name": "gemini_cli.api_request",
            "request_text": json.dumps([{
                "role": "user",
                "parts": [{"functionResponse": {
                    "name": tool_name, "id": call_id,
                    "response": {"output": f"<untrusted_context>\nOutput:{output}\n"
                                           f"Process Group PGID: 1\n</untrusted_context>"},
                }}],
            }]),
        },
        "_body": "API request to fake-1.",
    }
    with open(LOG, "a", encoding="utf-8") as file:
        file.write(json.dumps(record, indent=2) + "\n")


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def update(content: dict) -> None:
    send({"jsonrpc": "2.0", "method": "session/update",
          "params": {"sessionId": SESSION, "update": content}})


def main() -> None:
    turn = None  # id of the running session/prompt
    planned_answer = f"done, {NAME}."
    while True:
        line = sys.stdin.readline()
        if not line:
            return
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        mid, method = message.get("id"), message.get("method")

        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": 1,
                "agentCapabilities": {"loadSession": True},
                "authMethods": [],
            }})
        elif method == "session/new":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "sessionId": SESSION,
                "modes": {"currentModeId": "default", "availableModes": [{"id": "default"}]},
                "models": {"availableModels": [{"modelId": "fake-1", "name": "Fake 1"}]},
            }})
        elif method == "session/load":
            asked = (message.get("params") or {}).get("sessionId")
            if asked == SESSION:
                send({"jsonrpc": "2.0", "id": mid, "result": {}})
            else:
                send({"jsonrpc": "2.0", "id": mid, "error": {
                    "code": -32602, "message": f"unknown session: {asked}"}})
        elif method == "session/prompt":
            turn = mid
            received = " ".join(
                block.get("text", "") for block in (message.get("params") or {}).get("prompt", [])
            )
            text, asks = reply(received)
            if "WRITE:" in received:
                # The agent asks the CLIENT to write: that is the boundary the app guards.
                target = received.split("WRITE:", 1)[1].split()[0]
                send({"jsonrpc": "2.0", "id": 7, "method": "fs/write_text_file",
                      "params": {"sessionId": SESSION, "path": target,
                                 "content": "written by the agent\n"}})
                continue
            if not asks:
                update({"sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": text}})
                send({"jsonrpc": "2.0", "id": turn, "result": {"stopReason": "end_turn"}})
                turn = None
                continue
            planned_answer = text
            update({"sessionUpdate": "agent_thought_chunk", "content": {
                "type": "text",
                "text": "**Reading The Sources**\nI am looking at the notes first.\n"
                        "**Executing Shell Commands**\nI am now proceeding with the execution.",
            }})
            update({"sessionUpdate": "tool_call", "toolCallId": "run_shell_command__call_0",
                    "title": "rm -rf .venv && uv sync", "kind": "execute", "status": "pending",
                    "locations": []})
            update({"sessionUpdate": "tool_call", "toolCallId": "read_file__call_7",
                    "title": "reads notes.md", "kind": "read",
                    "locations": [{"path": "/tmp/notes.md"}]})
            update({"sessionUpdate": "tool_call_update", "toolCallId": "read_file__call_7",
                    "status": "completed", "kind": "read"})
            send({"jsonrpc": "2.0", "id": 0, "method": "session/request_permission", "params": {
                "sessionId": SESSION,
                "toolCall": {"toolCallId": "run_shell_command__call_0",
                             "title": "rm -rf .venv && uv sync"},
                # Order deliberately taken from the real agent: the widest permission comes
                # first.
                "options": [
                    {"optionId": "proceed_always", "name": "Allow for this session",
                     "kind": "allow_always"},
                    {"optionId": "proceed_once", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "cancel", "name": "Reject", "kind": "reject_once"},
                ],
            }})
        elif method == "session/cancel":
            if turn is not None:
                send({"jsonrpc": "2.0", "id": turn, "result": {"stopReason": "cancelled"}})
                turn = None
        elif mid == 7 and method is None:
            refused = "error" in message
            update({"sessionUpdate": "agent_message_chunk", "content": {
                "type": "text",
                "text": "write refused" if refused else "write done"}})
            if turn is not None:
                send({"jsonrpc": "2.0", "id": turn, "result": {"stopReason": "end_turn"}})
                turn = None
        elif mid is not None and method is None:
            outcome = (message.get("result") or {}).get("outcome")
            if turn is None:
                # Answer that arrived after the cancellation: we echo it so the test sees it.
                update({"sessionUpdate": "_permission_answer", "outcome": outcome})
            else:
                update({"sessionUpdate": "tool_call_update", "kind": "execute",
                        "toolCallId": "run_shell_command__call_0", "status": "completed"})
                log_output("run_shell_command", "call_0", "        2 data.txt")
                update({"sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": planned_answer}})
                send({"jsonrpc": "2.0", "id": turn,
                      "result": {"stopReason": "end_turn", "_meta": {"outcome": outcome}}})
                turn = None


if __name__ == "__main__":
    main()
