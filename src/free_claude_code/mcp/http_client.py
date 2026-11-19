"""MCP streamable-HTTP client: the HTTP transport sibling of MCPClient.

The client POSTs newline-independent JSON-RPC 2.0 messages to the
server's configured URL and accepts either a plain ``application/json``
response or a ``text/event-stream`` (SSE) body, per the MCP streamable
HTTP transport. Everything the server returns is untrusted data:
responses are validated for shape before use, bodies are capped at the
server's configured output limit, and every request carries the
server's configured timeout.

Configuration never carries credentials, so this client speaks to
unauthenticated or locally-proxied servers only; servers that need
authorization headers are rejected at config validation time (there is
no header field to leak).
"""

import json
import socket
import urllib.error
import urllib.request
from typing import Any

from free_claude_code.mcp.base import (
    CLIENT_NAME,
    PROTOCOL_VERSION,
    BaseMCPClient,
    HandshakeError,
    MCPError,
    OutputLimitError,
    RequestTimeoutError,
)
from free_claude_code.mcp.config import MCPServer
from free_claude_code.terminal.audit import AuditSink

_SSE_MEDIA = "text/event-stream"


class HTTPMCPClient(BaseMCPClient):
    """Client for one streamable-HTTP MCP server.

    Lifecycle: :meth:`start` runs the initialize handshake and records
    the session id when the server assigns one; :meth:`list_tools`,
    :meth:`call_tool` and :meth:`ping` issue POSTs; :meth:`close`
    best-effort terminates the session. Not thread-safe: one caller at
    a time (the scheduler serializes agent work).
    """

    def __init__(self, server: MCPServer, *, sink: AuditSink | None = None) -> None:
        if server.transport != "http":
            raise MCPError(
                f"HTTPMCPClient supports http servers, got {server.transport}"
            )
        super().__init__(server, sink=sink)
        self._session_id: str | None = None
        self._started = False

    def start(self) -> None:
        """Complete the initialize handshake over HTTP."""

        if self._started:
            raise MCPError("client already started")
        self._audit("planned")
        self._audit("started")
        message, session = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._fresh_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": CLIENT_NAME, "version": "0"},
                },
            }
        )
        self._session_id = session
        result = message.get("result") if isinstance(message, dict) else None
        if not isinstance(result, dict) or "protocolVersion" not in result:
            raise HandshakeError(
                f"server {self._server.name!r} returned an invalid initialize result"
            )
        self._started = True
        self._notify("notifications/initialized")

    def _fresh_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": f"application/json, {_SSE_MEDIA}",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None]:
        """POST one JSON-RPC message; return (response message, session id).

        Notifications come back as 202 with no message. Raises MCPError
        on transport failures, RequestTimeoutError on timeout and
        OutputLimitError when the body exceeds the configured cap.
        """

        request = urllib.request.Request(
            self._server.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            response = urllib.request.urlopen(
                request, timeout=self._server.timeout_seconds
            )
        except urllib.error.HTTPError as exc:
            raise MCPError(
                f"server {self._server.name!r} answered HTTP {exc.code}"
            ) from exc
        except TimeoutError as exc:
            raise RequestTimeoutError(
                f"server {self._server.name!r} did not answer within "
                f"{self._server.timeout_seconds}s"
            ) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise RequestTimeoutError(
                    f"server {self._server.name!r} did not answer within "
                    f"{self._server.timeout_seconds}s"
                ) from exc
            raise MCPError(
                f"could not reach MCP server {self._server.name!r}: {reason}"
            ) from exc
        with response:
            session = response.headers.get("Mcp-Session-Id")
            if response.status == 202:
                return None, session
            media = response.headers.get_content_type()
            body = response.read(self._server.max_output_bytes + 1)
        if len(body) > self._server.max_output_bytes:
            raise OutputLimitError(
                f"server {self._server.name!r} exceeded its output cap"
            )
        if not body.strip():
            return None, session  # notification acknowledged with an empty 200
        if media == _SSE_MEDIA:
            message = self._read_sse(body, payload.get("id"))
        else:
            message = self._parse_json(body)
        return message, session

    def _parse_json(self, body: bytes) -> dict[str, Any]:
        try:
            message = json.loads(body.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise MCPError(
                f"server {self._server.name!r} returned an invalid response"
            ) from exc
        if not isinstance(message, dict):
            raise MCPError(f"server {self._server.name!r} returned an invalid response")
        return message

    def _read_sse(self, body: bytes, request_id: Any) -> dict[str, Any]:
        """Pick our request's response out of an SSE event stream."""

        for line in body.decode("utf-8", errors="replace").splitlines():
            if not line.startswith("data:"):
                continue
            try:
                message = json.loads(line[len("data:") :].strip())
            except ValueError:
                continue
            if isinstance(message, dict) and message.get("id") == request_id:
                return message
        raise MCPError(
            f"server {self._server.name!r} streamed no response to the request"
        )

    def _request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        message, session = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._fresh_id(),
                "method": method,
                "params": params or {},
            }
        )
        if session is not None:
            self._session_id = session
        if message is None:
            raise MCPError(
                f"server {self._server.name!r} returned no response to {method}"
            )
        if "error" in message:
            self._audit("failed")
            error = message["error"]
            detail = (
                error.get("message", "unknown error")
                if isinstance(error, dict)
                else "unknown error"
            )
            raise MCPError(f"server {self._server.name!r} error: {detail}")
        return message.get("result")

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._post({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def close(self) -> None:
        """Best-effort session termination; HTTP is otherwise stateless."""

        if self._session_id is None:
            return
        request = urllib.request.Request(
            self._server.url, headers=self._headers(), method="DELETE"
        )
        try:
            with urllib.request.urlopen(request, timeout=5):
                pass
        except Exception:
            pass  # session teardown is best-effort by design
        self._session_id = None
        self._audit("finished")
