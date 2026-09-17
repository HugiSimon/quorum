"""A scripted ACP agent, for the recordings only.

`tests/fake_agent.py` is driven by keywords in the prompt — perfect for a test, useless in
front of a camera, since the keyword would have to be typed on screen. This one ignores
what it is told and plays the beats written for it in `scenarios/<room>.json`, turn after
turn, with the pacing the real agent has: a thought before the tools, a command whose output
lands a second after it finished, a permission that stops everything until it is answered.

    python agent.py <bot name> <scenario file>
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

NAME = sys.argv[1] if len(sys.argv) > 1 else "demo"
SCENARIO = Path(sys.argv[2]) if len(sys.argv) > 2 else Path()
SESSION = f"sess-{NAME}"
LOG = os.environ.get("GEMINI_TELEMETRY_OUTFILE")

TURNS: list[list[dict]] = json.loads(SCENARIO.read_text(encoding="utf-8")).get(NAME, [])
_lock = threading.Lock()
_counter = 0


def send(message: dict) -> None:
    with _lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def update(content: dict) -> None:
    send({"jsonrpc": "2.0", "method": "session/update",
          "params": {"sessionId": SESSION, "update": content}})


def call_id(tool: str) -> str:
    """The agent's own shape: `<tool>__call_<n>`, which is the key the log is joined on."""
    global _counter
    _counter += 1
    return f"{tool}__call_{_counter}"


def log_output(tool: str, raw_id: str, output: str) -> None:
    """Writes an output where the real agent writes it: the log, never the stream."""
    if not LOG:
        return
    record = {"attributes": {
        "event.name": "gemini_cli.api_request",
        "request_text": json.dumps([{"role": "user", "parts": [{"functionResponse": {
            "name": tool, "id": raw_id,
            "response": {"output": f"<untrusted_context>\nOutput:{output}\n</untrusted_context>"},
        }}]}]),
    }}
    with open(LOG, "a", encoding="utf-8") as file:
        file.write(json.dumps(record, indent=2) + "\n")


def later(delay: float, work) -> None:
    threading.Timer(delay, work).start()


class Agent:
    def __init__(self) -> None:
        self.turn: int | None = None   # id of the running session/prompt
        self.played = 0                # how many turns of the scenario are spent
        self.pending: list[dict] = []  # beats waiting on a permission answer

    # — the beats —————————————————————————————————————————————————————————————

    def play(self, beats: list[dict]) -> None:
        """Runs beats until one of them asks for permission, or until the turn is over."""
        for index, beat in enumerate(beats):
            if self.turn is None:
                return
            time.sleep(beat.get("pause", 0.35))
            if "think" in beat:
                update({"sessionUpdate": "agent_thought_chunk",
                        "content": {"type": "text", "text": beat["think"]}})
            elif "tool" in beat:
                self.tool(beat)
            elif "say" in beat:
                update({"sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": beat["say"]}})
            elif "ask" in beat:
                self.pending = beats[index + 1:]
                self.ask(beat)
                return
        self.finish()

    def tool(self, beat: dict) -> None:
        tool = "run_shell_command" if beat.get("kind") == "execute" else "read_file"
        identifier = call_id(tool)
        locations = [{"path": os.path.expanduser(beat["path"])}] if beat.get("path") else []
        common = {"toolCallId": identifier, "title": beat["tool"],
                  "kind": beat.get("kind", "read"), "locations": locations}
        update({"sessionUpdate": "tool_call", "status": "in_progress", **common})
        time.sleep(beat.get("runs", 0.5))
        update({"sessionUpdate": "tool_call_update", "status": "completed", **common})
        if beat.get("output"):
            # The delay is the point: the stream never carries the output, the log does, and
            # it only gets there on the agent's next round trip.
            later(beat.get("late", 1.4),
                  lambda: log_output(tool, identifier.split("__", 1)[1], beat["output"]))

    def ask(self, beat: dict) -> None:
        send({"jsonrpc": "2.0", "id": 0, "method": "session/request_permission", "params": {
            "sessionId": SESSION,
            "toolCall": {"toolCallId": call_id("run_shell_command"), "title": beat["ask"],
                         "rawInput": {"command": beat.get("raw", beat["ask"])}},
            "options": [
                {"optionId": "proceed_always", "name": "Allow for this session",
                 "kind": "allow_always"},
                {"optionId": "proceed_once", "name": "Allow", "kind": "allow_once"},
                {"optionId": "cancel", "name": "Reject", "kind": "reject_once"},
            ],
        }})

    def answered(self, outcome: dict) -> None:
        """A permission came back: the branch the scenario wrote for it takes over."""
        if self.turn is None:
            return
        chosen = (outcome or {}).get("optionId", "cancel")
        branch = "rejected" if chosen == "cancel" else "allowed"
        beats, self.pending = self.pending, []
        follow = next((b[branch] for b in beats if branch in b), [])
        threading.Thread(target=self.play, args=(list(follow),), daemon=True).start()

    def finish(self) -> None:
        if self.turn is not None:
            send({"jsonrpc": "2.0", "id": self.turn, "result": {"stopReason": "end_turn"}})
            self.turn = None

    # — the protocol ——————————————————————————————————————————————————————————

    def prompt(self, mid: int) -> None:
        self.turn = mid
        beats = TURNS[self.played] if self.played < len(TURNS) else [{"say": "Nothing to add."}]
        self.played += 1
        threading.Thread(target=self.play, args=(list(beats),), daemon=True).start()

    def handle(self, message: dict) -> None:
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
                "models": {"availableModels": [{"modelId": "gemini-3.1-pro-preview",
                                                "name": "gemini-3.1-pro-preview"}]},
            }})
        elif method == "session/load":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "session/prompt":
            self.prompt(mid)
        elif method == "session/cancel":
            if self.turn is not None:
                send({"jsonrpc": "2.0", "id": self.turn, "result": {"stopReason": "cancelled"}})
                self.turn = None
        elif mid is not None and method is None:
            self.answered((message.get("result") or {}).get("outcome") or {})


def main() -> None:
    agent = Agent()
    for line in sys.stdin:
        line = line.strip()
        if line:
            agent.handle(json.loads(line))


if __name__ == "__main__":
    main()
