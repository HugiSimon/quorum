"""Gemini adapter: recovering command output, which the ACP stream does not send.

Neither command output nor the content of files read comes back with the end of the tool.
The only place they exist is the local telemetry log, and they land there **with the next
model request** — one or two seconds later, in one block.

The log is not JSONL despite its extension: it is a run of indented JSON objects glued
together. So we decode it as a stream, object by object.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Callable, Iterator

EVENT = "gemini_cli.api_request"
PGID_PATTERN = re.compile(r"(?m)^Process Group PGID: \d+$\n?")
DECODER = json.JSONDecoder()

# Output arrives with the next model request; if there is none, it will never come.
# Measured at +5.5 s on a `wc -l` — the announced "1 to 2 s" is optimistic, hence the margin.
GRACE = 8.0


def objects(text: str) -> tuple[list[dict], int]:
    """Decodes every complete JSON object in a text, and returns where we stopped."""
    found: list[dict] = []
    i = 0
    while i < len(text):
        while i < len(text) and text[i] in " \r\n\t":
            i += 1
        if i >= len(text):
            break
        try:
            obj, j = DECODER.raw_decode(text, i)
        except ValueError:
            break  # object still incomplete: we will pick it up on the next pass
        found.append(obj)
        i = j
    return found, i


def outputs_of(obj: dict) -> Iterator[tuple[str, str, str]]:
    """Yields the (tool name, call id, output) carried by one record."""
    attributes = obj.get("attributes") or {}
    if attributes.get("event.name") != EVENT:
        return
    try:
        content = json.loads(attributes.get("request_text") or "")
    except ValueError:
        return
    for response in _responses(content):
        output = (response.get("response") or {}).get("output")
        if response.get("id") and output is not None:
            yield response.get("name", ""), response["id"], clean(str(output))


def _responses(node) -> Iterator[dict]:
    if isinstance(node, dict):
        if isinstance(node.get("functionResponse"), dict):
            yield node["functionResponse"]
        for value in node.values():
            yield from _responses(value)
    elif isinstance(node, list):
        for value in node:
            yield from _responses(value)


def clean(output: str) -> str:
    """Strips the wrapping the agent adds around a command output.

    The spaces after `Output:` belong to the output (`wc -l` aligns on them): we only
    remove the label itself.
    """
    text = output
    start = text.find("<untrusted_context>")
    if start != -1:
        end = text.find("</untrusted_context>", start)
        text = text[start + len("<untrusted_context>") : end if end != -1 else None]
    text = PGID_PATTERN.sub("", text).strip("\n")
    if text.startswith("Output:"):
        text = text[len("Output:") :]
    return text.strip("\n")


class GeminiOutputs:
    """Follows a bot's log and reports each output as soon as it shows up."""

    def __init__(self, path: Path, on_output: Callable[[str, str], None]) -> None:
        self.path = path
        self.on_output = on_output
        self._position = 0
        self._rest = ""
        self._task: asyncio.Task | None = None
        self._seen: set[str] = set()

    def start(self) -> None:
        self._task = asyncio.create_task(self._follow())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _follow(self, interval: float = 0.5) -> None:
        while True:
            self.harvest()
            await asyncio.sleep(interval)

    def harvest(self) -> None:
        """Reads what was appended to the log and reports the outputs not yet known."""
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8", errors="replace") as file:
            file.seek(self._position)
            self._rest += file.read()
            self._position = file.tell()
        found, consumed = objects(self._rest)
        self._rest = self._rest[consumed:]
        for obj in found:
            for _, call_id, output in outputs_of(obj):
                if call_id not in self._seen:
                    self._seen.add(call_id)
                    self.on_output(call_id, output)


def short_id(tool_call_id: str) -> str:
    """The ACP `toolCallId` is `<name>__<id>`: the `<id>` is what links to the log."""
    return tool_call_id.rsplit("__", 1)[-1]
