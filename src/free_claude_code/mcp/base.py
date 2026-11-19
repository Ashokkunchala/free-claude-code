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
class MCPResource:
    """One resource advertised by an MCP server."""

    uri: str
    name: str
    description: str
    mime_type: str


@dataclass(frozen=True)
class MCPPrompt:
    """One prompt advertised by an MCP server."""

    name: str
    description: str
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromptResult:
    """Outcome of one prompts/get: redacted text plus any description."""

    text: str
    description: str


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

    def list_resources(self) -> tuple[MCPResource, ...]:
        """Return the server's advertised resources (validated shape)."""

        result = self._request("resources/list")
        raw = result.get("resources") if isinstance(result, dict) else None
        if not isinstance(raw, list):
            raise MCPError(
                f"server {self._server.name!r} returned invalid resources/list"
            )
        resources: list[MCPResource] = []
        for entry in raw:
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("uri"), str)
                and isinstance(entry.get("name"), str)
            ):
                description = entry.get("description")
                mime = entry.get("mimeType")
                resources.append(
                    MCPResource(
                        uri=entry["uri"],
                        name=entry["name"],
                        description=description if isinstance(description, str) else "",
                        mime_type=mime if isinstance(mime, str) else "",
                    )
                )
        return tuple(resources)

    def read_resource(self, uri: str) -> str:
        """Read one resource; returns redacted joined text content.

        Blob (binary) contents are skipped: they cannot be redacted as
        text and never reach the audit log.
        """

        self._audit("planned")
        result = self._request("resources/read", {"uri": uri})
        raw = result.get("contents") if isinstance(result, dict) else None
        if not isinstance(raw, list):
            raise MCPError(
                f"server {self._server.name!r} returned invalid resources/read"
            )
        parts: list[str] = []
        for entry in raw:
            if isinstance(entry, dict):
                text = entry.get("text")
                if isinstance(text, str):
                    parts.append(text)
        self._audit("finished")
        return default_redactor().redact_text("\n".join(parts))

    def list_prompts(self) -> tuple[MCPPrompt, ...]:
        """Return the server's advertised prompts (validated shape)."""

        result = self._request("prompts/list")
        raw = result.get("prompts") if isinstance(result, dict) else None
        if not isinstance(raw, list):
            raise MCPError(
                f"server {self._server.name!r} returned invalid prompts/list"
            )
        prompts: list[MCPPrompt] = []
        for entry in raw:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                description = entry.get("description")
                args_raw = entry.get("arguments")
                arguments: tuple[str, ...] = ()
                if isinstance(args_raw, list):
                    arguments = tuple(
                        arg["name"]
                        for arg in args_raw
                        if isinstance(arg, dict) and isinstance(arg.get("name"), str)
                    )
                prompts.append(
                    MCPPrompt(
                        name=entry["name"],
                        description=description if isinstance(description, str) else "",
                        arguments=arguments,
                    )
                )
        return tuple(prompts)

    def get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> PromptResult:
        """Render one advertised prompt; returns redacted joined text."""

        args = arguments or {}
        if not isinstance(args, dict):
            raise MCPError("prompt arguments must be a JSON object")
        prompts = {prompt.name for prompt in self.list_prompts()}
        if name not in prompts:
            raise MCPError(
                f"server {self._server.name!r} has no prompt {name!r} "
                f"(advertised: {sorted(prompts)})"
            )
        self._audit("planned")
        result = self._request("prompts/get", {"name": name, "arguments": args})
        raw = result.get("messages") if isinstance(result, dict) else None
        if not isinstance(raw, list):
            raise MCPError(f"server {self._server.name!r} returned invalid prompts/get")
        parts: list[str] = []
        for message in raw:
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, dict) and content.get("type") == "text":
                    text = content.get("text")
                    if isinstance(text, str):
                        parts.append(text)
        description = result.get("description") if isinstance(result, dict) else None
        self._audit("finished")
        return PromptResult(
            text=default_redactor().redact_text("\n".join(parts)),
            description=description if isinstance(description, str) else "",
        )

    def ping(self) -> None:
        """Liveness check; raises on failure."""

        self._request("ping")
