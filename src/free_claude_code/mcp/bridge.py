"""Bridge MCP servers into provider-facing tool definitions.

The agent loop (and any other caller) needs two things from configured
MCP servers: tool schemas in the provider's dialect, and a dispatcher
that invokes a tool call by its exposed name. This module supplies both
without the caller touching transports, permissions or redaction:

- tools are namespaced ``<server>__<tool>`` and sanitized to the
  portable provider charset (``[A-Za-z0-9_-]``, max 64 chars), with a
  digest suffix when sanitization collides;
- every dispatch re-checks the per-server permission gate, so a server
  disabled after collection stops answering immediately;
- output is redacted inside the client before it reaches the caller;
- a server that fails collection degrades to an entry in
  :attr:`MCPBridge.collection_errors` instead of breaking the set
  (roadmap section 9: graceful degradation).
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from free_claude_code.mcp.base import BaseMCPClient, CallResult, MCPError, MCPTool
from free_claude_code.mcp.config import MCPServer
from free_claude_code.mcp.factory import client_for
from free_claude_code.mcp.permissions import evaluate_server
from free_claude_code.mcp.state import MCPState
from free_claude_code.terminal.audit import AuditSink

_PORTABLE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_INVALID_CHARS = re.compile(r"[^A-Za-z0-9_-]+")
_DIGEST_LENGTH = 16


@dataclass(frozen=True)
class BridgedTool:
    """One MCP tool under its provider-safe exposed name."""

    exposed_name: str
    server: str
    tool: MCPTool


def exposed_tool_name(server: str, tool: str, *, taken: set[str] | None = None) -> str:
    """Provider-safe name for one tool; digest suffix on collision."""

    base = _INVALID_CHARS.sub("_", f"{server}__{tool}")[:64]
    if _PORTABLE_NAME.fullmatch(base) and (taken is None or base not in taken):
        return base
    digest = hashlib.sha256(f"{server}__{tool}".encode()).hexdigest()[:_DIGEST_LENGTH]
    return f"{base[: 64 - _DIGEST_LENGTH - 1]}_{digest}"


def anthropic_tool_schema(bridged: BridgedTool) -> dict[str, Any]:
    """Anthropic Messages API tool definition for one bridged tool."""

    return {
        "name": bridged.exposed_name,
        "description": f"[{bridged.server}] {bridged.tool.description}".strip(),
        "input_schema": bridged.tool.input_schema
        or {"type": "object", "properties": {}},
    }


def openai_tool_schema(bridged: BridgedTool) -> dict[str, Any]:
    """OpenAI function-tool definition for one bridged tool."""

    return {
        "type": "function",
        "function": {
            "name": bridged.exposed_name,
            "description": f"[{bridged.server}] {bridged.tool.description}".strip(),
            "parameters": bridged.tool.input_schema
            or {"type": "object", "properties": {}},
        },
    }


class MCPBridge:
    """Collect tools from enabled servers and dispatch calls to them.

    Lifecycle: :meth:`collect` starts one client per enabled server and
    lists its tools; :meth:`dispatch` routes provider tool calls;
    :meth:`close` shuts every collected client down.
    """

    def __init__(
        self,
        servers: list[MCPServer],
        state: MCPState,
        *,
        sink: AuditSink | None = None,
    ) -> None:
        self._servers = servers
        self._state = state
        self._sink = sink
        self._clients: dict[str, BaseMCPClient] = {}
        self._tools: dict[str, BridgedTool] = {}
        self._collection_errors: dict[str, str] = {}

    @property
    def collection_errors(self) -> dict[str, str]:
        """Servers that failed collection, with the reason."""

        return dict(self._collection_errors)

    def collect(self) -> tuple[BridgedTool, ...]:
        """Discover tools from every enabled server (others degrade)."""

        taken: set[str] = set()
        for server in self._servers:
            verdict = evaluate_server(server, self._state)
            if not verdict.allowed:
                self._collection_errors[server.name] = verdict.reason
                continue
            client = client_for(server, sink=self._sink)
            try:
                client.start()
                tools = client.list_tools()
            except MCPError as exc:
                client.close()
                self._collection_errors[server.name] = str(exc)
                continue
            self._clients[server.name] = client
            for tool in tools:
                exposed = exposed_tool_name(server.name, tool.name, taken=taken)
                taken.add(exposed)
                self._tools[exposed] = BridgedTool(
                    exposed_name=exposed, server=server.name, tool=tool
                )
        return tuple(self._tools.values())

    def anthropic_tools(self) -> list[dict[str, Any]]:
        """Anthropic-dialect schemas for the collected tools."""

        return [anthropic_tool_schema(tool) for tool in self._tools.values()]

    def openai_tools(self) -> list[dict[str, Any]]:
        """OpenAI-dialect schemas for the collected tools."""

        return [openai_tool_schema(tool) for tool in self._tools.values()]

    def dispatch(self, exposed_name: str, arguments: dict[str, Any]) -> CallResult:
        """Invoke one collected tool through its server's client.

        The permission gate is re-checked on every call: disabling a
        server after collection denies further dispatches immediately.
        """

        bridged = self._tools.get(exposed_name)
        if bridged is None:
            raise MCPError(
                f"no bridged tool {exposed_name!r} (collected: {sorted(self._tools)})"
            )
        server = next(item for item in self._servers if item.name == bridged.server)
        verdict = evaluate_server(server, self._state)
        if not verdict.allowed:
            raise MCPError(verdict.reason)
        client = self._clients[bridged.server]
        return client.call_tool(bridged.tool.name, arguments)

    def close(self) -> None:
        """Shut down every collected client."""

        for client in self._clients.values():
            client.close()
        self._clients.clear()
        self._tools.clear()
