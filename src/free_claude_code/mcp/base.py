"""Shared MCP client surface: errors, dataclasses and the base client.

Transport implementations (stdio in :mod:`.client`, streamable HTTP in
:mod:`.http_client`) subclass :class:`BaseMCPClient`; tool discovery and
invocation - with validation, redaction and audit events - live on the
base class so every transport behaves identically. Everything a server
returns is untrusted data: responses are validated for shape, output is
capped and redacted, and every request carries the server's timeout.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from free_claude_code.mcp.config import MCPServer
from free_claude_code.terminal.audit import AuditSink, NullAuditSink, make_event
from free_claude_code.terminal.redaction import default_redactor

PROTOCOL_VERSION = "2025-03-26"
CLIENT_NAME = "free-claude-code"


class MCPError(Exception):
    """Base class for MCP client failures."""


class ServerStartError(MCPError):
    """The server process could not be spawned."""


class ServerExitedError(MCPError):
    """The server process died mid-conversation."""


class HandshakeError(MCPError):
    """The initialize handshake failed or returned an invalid result."""


class RequestTimeoutError(MCPError):
    """The server did not answer a request within timeout_seconds."""


class OutputLimitError(MCPError):
    """The server produced more output than its configured cap."""


@dataclass(frozen=True)
class MCPTool:
    """One tool advertised by an MCP server.

    ``input_schema`` is the tool's advertised JSON Schema for its arguments
    (empty dict when the server does not declare one); calls are validated
    against it before any request leaves the client.
    """

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CallResult:
    """Outcome of one tools/call: redacted text content plus the error flag."""

    text: str
    is_error: bool


@dataclass(frozen=True)
class ServerProbe:
    """Health report for one configured server. Never raises out of probe."""

    server: str
    ok: bool
    reason: str
    tools: tuple[str, ...] = ()
    duration_seconds: float = 0.0
    policy_id: str | None = None


class BaseMCPClient:
    """Shared request surface for MCP clients, transport-independent.

    Subclasses implement the transport itself (:meth:`start`,
    :meth:`close`, ``_request`` and ``_notify``); tool discovery and
    invocation - with their validation, redaction and audit events -
    live here so every transport behaves identically.
    """

    def __init__(self, server: MCPServer, *, sink: AuditSink | None = None) -> None:
        self._server = server
        self._sink = sink if sink is not None else NullAuditSink()
        self._task_id = f"mcp:{server.name}:{uuid.uuid4().hex[:8]}"
        self._next_id = 0

    def _audit(self, kind: str) -> None:
        self._sink.record(make_event(kind, self._task_id, self._server.command, None))

    def start(self) -> None:
        """Start the transport and complete the initialize handshake."""
        raise NotImplementedError

    def close(self) -> None:
        """Release the transport's resources."""
        raise NotImplementedError

    def _request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        raise NotImplementedError

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        raise NotImplementedError

    def list_tools(self) -> tuple[MCPTool, ...]:
        """Return the server's advertised tools (validated shape)."""

        result = self._request("tools/list")
        tools_raw = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools_raw, list):
            raise MCPError(f"server {self._server.name!r} returned invalid tools/list")
        tools: list[MCPTool] = []
        for entry in tools_raw:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                description = entry.get("description")
                schema = entry.get("inputSchema")
                tools.append(
                    MCPTool(
                        name=entry["name"],
                        description=description if isinstance(description, str) else "",
                        input_schema=schema if isinstance(schema, dict) else {},
                    )
                )
        return tuple(tools)

    def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> CallResult:
        """Invoke one advertised tool.

        Arguments are validated against the tool's advertised inputSchema
        before the request is sent; text content comes back redacted through
        the default redactor. Raises MCPError on unknown tools, invalid
        arguments, transport failures and server-side call errors.
        """

        args = arguments or {}
        if not isinstance(args, dict):
            raise MCPError("tool arguments must be a JSON object")
        tools = {tool.name: tool for tool in self.list_tools()}
        tool = tools.get(name)
        if tool is None:
            raise MCPError(
                f"server {self._server.name!r} has no tool {name!r} "
                f"(advertised: {sorted(tools)})"
            )
        if tool.input_schema:
            try:
                jsonschema.validate(args, tool.input_schema)
            except jsonschema.ValidationError as exc:
                raise MCPError(
                    f"invalid arguments for {name!r}: {exc.message}"
                ) from exc
        self._audit("planned")
        try:
            result = self._request("tools/call", {"name": name, "arguments": args})
        except MCPError:
            raise
        content = result.get("content") if isinstance(result, dict) else None
        if not isinstance(content, list):
            raise MCPError(f"server {self._server.name!r} returned invalid tools/call")
        parts: list[str] = []
        for entry in content:
            if isinstance(entry, dict) and entry.get("type") == "text":
                text = entry.get("text")
                if isinstance(text, str):
                    parts.append(text)
        is_error = bool(result.get("isError")) if isinstance(result, dict) else False
        self._audit("failed" if is_error else "finished")
        redacted = default_redactor().redact_text("\n".join(parts))
        return CallResult(text=redacted, is_error=is_error)

    def ping(self) -> None:
        """Liveness check; raises on failure."""

        self._request("ping")
