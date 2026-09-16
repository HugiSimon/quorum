"""ACP client: JSON-RPC 2.0 over stdio, both ways.

Three message shapes and a single sorting rule: an `id` **and** a `method` is a request
from the agent, an `id` alone is the answer to one of ours, neither is a notification. The
agent's ids start at 0: we always test `is not None`, never the truthiness of the id.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any, Awaitable, Callable

# A history replay (session/load) arrives on a single line: asyncio's 64 KiB limit is far
# below what is needed.
LINE_LIMIT = 4 * 1024 * 1024


class AcpError(Exception):
    """Error carried by the `error` field of a response, or a transport failure."""


class AcpClient:
    """One agent process, its read loop and its in-flight requests.

    `on_notification(message)` is called **inside** the read loop: it must return
    immediately. `on_permission(params)` is a coroutine that may wait for a human decision
    as long as needed — it runs in its own task.
    """

    def __init__(
        self,
        on_notification: Callable[[dict], None],
        on_permission: Callable[[dict], Awaitable[dict]],
        on_write: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        self.on_notification = on_notification
        self.on_permission = on_permission
        # The agent asks the client to write: that is a trust boundary, not a transport
        # detail. The application can step in there.
        self.on_write = on_write
        self.capabilities: dict[str, Any] = {}
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr = None
        self._reader: asyncio.Task | None = None
        self._last_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._tasks: dict[Any, asyncio.Task] = {}
        self._permissions: set[Any] = set()

    # ── life cycle ──────────────────────────────────────────────────────────────────

    async def start(
        self,
        command: str,
        args: list[str],
        env: dict[str, str],
        cwd: Path,
        stderr_log: Path,
    ) -> None:
        """Launches the agent process, stderr diverted to a file (the terminal is taken)."""
        stderr_log.parent.mkdir(parents=True, exist_ok=True)
        self._stderr = stderr_log.open("ab", buffering=0)
        self._proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self._stderr,
            limit=LINE_LIMIT,
            # `gemini --acp` is only a launcher: it opens a second node process that
            # outlives it. A separate group lets us close both in one gesture.
            start_new_session=True,
        )
        self._reader = asyncio.create_task(self._read_loop())

    async def close(self, grace: float = 4.0) -> None:
        """Ends the process without leaving an orphan — but leaves it a clean exit.

        Closing stdin is the protocol's only "goodbye": it is what lets the agent save its
        session to disk, and therefore lets `session/load` find it on the next launch. An
        immediate SIGTERM loses it. The signal only comes after.
        """
        if self._proc is not None and self._proc.returncode is None:
            if self._proc.stdin is not None and not self._proc.stdin.is_closing():
                self._proc.stdin.close()
            try:
                await asyncio.wait_for(self._proc.wait(), grace)
            except asyncio.TimeoutError:
                self._signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(self._proc.wait(), 3)
                except asyncio.TimeoutError:
                    self._signal(signal.SIGKILL)
        if self._reader is not None:
            self._reader.cancel()
        if self._stderr is not None:
            self._stderr.close()

    def _signal(self, sig: int) -> None:
        """Aims at the process group, not just the launcher, so no child is left behind."""
        assert self._proc is not None
        try:
            os.killpg(os.getpgid(self._proc.pid), sig)
        except (ProcessLookupError, PermissionError):
            self._proc.send_signal(sig)

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    # ── the four protocol calls ─────────────────────────────────────────────────────

    async def initialize(self) -> dict:
        """Announces what the client can do; the answer gives the agent's capabilities."""
        self.capabilities = await self.request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}},
            },
        )
        return self.capabilities

    async def new_session(self, cwd: Path, mcp_servers: list[dict] | None = None) -> dict:
        """Opens a session. The answer carries sessionId, the modes and the model list."""
        return await self.request(
            "session/new", {"cwd": str(cwd), "mcpServers": mcp_servers or []}
        )

    async def load_session(
        self, session_id: str, cwd: Path, mcp_servers: list[dict] | None = None
    ) -> dict:
        """Resumes a session across processes: the agent replays everything as notifications."""
        return await self.request(
            "session/load",
            {"sessionId": session_id, "cwd": str(cwd), "mcpServers": mcp_servers or []},
        )

    async def prompt(self, session_id: str, text: str) -> dict:
        """Blocks until the end of the turn and returns {stopReason, _meta.quota}."""
        return await self.request(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
        )

    def cancel(self, session_id: str) -> None:
        """Cancels the turn, then resolves in-flight permission requests by hand.

        The agent does not resolve them on its own: without that `cancelled` answer the
        turn stays open forever. The two gestures never come apart.
        """
        self.notify("session/cancel", {"sessionId": session_id})
        for rid in list(self._permissions):
            self._permissions.discard(rid)
            task = self._tasks.get(rid)
            if task is not None:
                task.cancel()
            self._send({"jsonrpc": "2.0", "id": rid, "result": {"outcome": {"outcome": "cancelled"}}})

    # ── transport ───────────────────────────────────────────────────────────────────

    async def request(self, method: str, params: dict) -> Any:
        """Sends a request and waits for its answer."""
        self._last_id += 1
        rid = self._last_id
        pending = asyncio.get_running_loop().create_future()
        self._pending[rid] = pending
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        return await pending

    def notify(self, method: str, params: dict) -> None:
        """Sends a notification: no id, no answer expected."""
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _send(self, message: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise AcpError("agent not started")
        self._proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._dispatch(message)
        self._fail_pending(AcpError("the agent process stopped"))

    def _dispatch(self, message: dict) -> None:
        rid = message.get("id")
        if rid is not None and "method" in message:
            self._tasks[rid] = asyncio.create_task(
                self._serve(rid, message["method"], message.get("params") or {})
            )
        elif rid is not None:
            pending = self._pending.pop(rid, None)
            if pending is None or pending.done():
                return
            if "error" in message:
                pending.set_exception(AcpError(json.dumps(message["error"], ensure_ascii=False)))
            else:
                pending.set_result(message.get("result"))
        else:
            self.on_notification(message)

    async def _serve(self, rid: Any, method: str, params: dict) -> None:
        """Handles a request from the agent, in its own task.

        The read loop must never wait here: a permission request may stay pending while a
        human makes up their mind, and the agent keeps emitting during that time.
        """
        try:
            if method == "session/request_permission":
                self._permissions.add(rid)
                result = {"outcome": await self.on_permission(params)}
            elif method == "fs/read_text_file":
                result = {"content": _read_file(params)}
            elif method == "fs/write_text_file":
                if self.on_write is not None:
                    await self.on_write(params)
                else:
                    write_file(params)
                result = None
            else:
                raise AcpError(f"unknown method: {method}")
        except asyncio.CancelledError:
            return  # cancel() already answered "cancelled" to this request
        except Exception as error:
            self._send(
                {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": str(error)}}
            )
        else:
            self._send({"jsonrpc": "2.0", "id": rid, "result": result})
        finally:
            self._permissions.discard(rid)
            self._tasks.pop(rid, None)

    def _fail_pending(self, error: Exception) -> None:
        for pending in self._pending.values():
            if not pending.done():
                pending.set_exception(error)
        self._pending.clear()


def write_file(params: dict) -> None:
    """Writes what the agent asks for. The checks happen before we get here."""
    path = Path(params["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(params.get("content", ""), encoding="utf-8")


def _read_file(params: dict) -> str:
    """Reads a file for the agent, honouring the `line`/`limit` window if it asks for one."""
    text = Path(params["path"]).read_text(encoding="utf-8")
    start, limit = params.get("line"), params.get("limit")
    if start is None and limit is None:
        return text
    lines = text.splitlines(keepends=True)[max((start or 1) - 1, 0) :]
    if limit is not None:
        lines = lines[:limit]
    return "".join(lines)
