"""Minimal MCP stdio client: handshake, tool discovery, graceful probes.

The client speaks newline-delimited JSON-RPC 2.0 over the server's stdio
(the MCP stdio transport). Server stdout lines that are not JSON are
tolerated as noise (non-compliant servers log to stdout); stderr is
captured for diagnostics. Everything the server returns is untrusted
data: responses are validated for shape before use, output is capped,
and every request carries the server's configured timeout.

Child environment is least-privilege by construction: only the variable
names declared in the server config are passed through, never values
from this process's environment otherwise.
"""

import contextlib
import json
import os
import queue
import subprocess
import threading
import time
from typing import Any

from free_claude_code.mcp.base import (
    CLIENT_NAME,
    PROTOCOL_VERSION,
    BaseMCPClient,
    HandshakeError,
    MCPError,
    OutputLimitError,
    RequestTimeoutError,
    ServerExitedError,
    ServerStartError,
)
from free_claude_code.mcp.config import MCPServer
from free_claude_code.terminal import processes
from free_claude_code.terminal.audit import AuditSink

__all__ = [
    "CLIENT_NAME",
    "PROTOCOL_VERSION",
    "BaseMCPClient",
    "HandshakeError",
    "MCPClient",
    "MCPError",
    "OutputLimitError",
    "RequestTimeoutError",
    "ServerExitedError",
    "ServerStartError",
]


class MCPClient(BaseMCPClient):
    """Stateful client for one stdio MCP server.

    Lifecycle: :meth:`start` spawns the server and runs the initialize
    handshake; :meth:`list_tools` / :meth:`ping` issue requests;
    :meth:`close` terminates the child. Not thread-safe: one caller at a
    time (the scheduler serializes agent work).
    """

    def __init__(self, server: MCPServer, *, sink: AuditSink | None = None) -> None:
        if server.transport != "stdio":
            raise MCPError(f"MCPClient supports stdio servers, got {server.transport}")
        super().__init__(server, sink=sink)
        self._process: subprocess.Popen[bytes] | None = None
        self._replies: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._read_bytes = 0

    def start(self) -> None:
        """Spawn the server and complete the initialize handshake."""

        if self._process is not None:
            raise MCPError("client already started")
        env = {
            name: os.environ[name] for name in self._server.env if name in os.environ
        }
        self._audit("planned")
        try:
            self._process = processes.spawn(
                self._server.command,
                env=env,
                inherit_stdio=False,
                isolated=True,
                track=True,
                pipe_stdin=True,
            )
        except OSError as exc:
            self._audit("failed")
            raise ServerStartError(
                f"could not start MCP server {self._server.name!r}: {exc}"
            ) from exc
        if self._process.stdout is None or self._process.stdin is None:
            self._audit("failed")
            raise ServerStartError("server stdio pipes were not created")
        self._reader = threading.Thread(
            target=self._read_loop, name=f"mcp-reader-{self._server.name}", daemon=True
        )
        self._reader.start()
        self._audit("started")
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": "0"},
            },
        )
        if not isinstance(result, dict) or "protocolVersion" not in result:
            self.close()
            raise HandshakeError(
                f"server {self._server.name!r} returned an invalid initialize result"
            )
        self._notify("notifications/initialized")

    def _read_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            for raw in self._process.stdout:
                self._read_bytes += len(raw)
                if self._read_bytes > self._server.max_output_bytes:
                    self._replies.put(
                        OutputLimitError(
                            f"server {self._server.name!r} exceeded its output cap"
                        )
                    )
                    return
                try:
                    message = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue  # non-compliant stdout noise; tolerate
                if isinstance(message, dict) and "id" in message:
                    self._replies.put(message)
        except OSError, ValueError:
            pass
        self._replies.put(ServerExitedError(f"server {self._server.name!r} exited"))

    def _send(self, message: dict[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        line = json.dumps(message) + "\n"
        try:
            self._process.stdin.write(line.encode("utf-8"))
            self._process.stdin.flush()
        except (OSError, BrokenPipeError) as exc:
            raise ServerExitedError(
                f"server {self._server.name!r} is not accepting input: {exc}"
            ) from exc

    def _request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._next_id += 1
        request_id = self._next_id
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        deadline = time.monotonic() + self._server.timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._audit("failed")
                raise RequestTimeoutError(
                    f"server {self._server.name!r} did not answer {method} "
                    f"within {self._server.timeout_seconds}s"
                )
            try:
                item = self._replies.get(timeout=remaining)
            except queue.Empty:
                continue
            if isinstance(item, BaseException):
                self._audit("failed")
                raise item
            if item.get("id") != request_id:
                continue  # a reply to an earlier (already timed-out) request
            if "error" in item:
                self._audit("failed")
                error = item["error"]
                message = (
                    error.get("message", "unknown error")
                    if isinstance(error, dict)
                    else "unknown error"
                )
                raise MCPError(f"server {self._server.name!r} error: {message}")
            return item.get("result")

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def close(self) -> None:
        """Close stdin and terminate the server process tree."""

        process, self._process = self._process, None
        if process is None:
            return
        with contextlib.suppress(OSError):
            if process.stdin is not None:
                process.stdin.close()
        with contextlib.suppress(OSError):
            if process.stdout is not None:
                process.stdout.close()
        with contextlib.suppress(OSError):
            if process.stderr is not None:
                process.stderr.close()
        processes.kill_tree(process)
        # Reap in every path: kill_tree can return early when the child
        # already exited, and the Windows taskkill path never waits.
        with contextlib.suppress(Exception):
            process.wait(timeout=5)
        processes.untrack(process)
        self._audit("finished")
