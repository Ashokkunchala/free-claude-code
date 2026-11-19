"""Transport selection and health probes for MCP servers."""

import time

from free_claude_code.mcp.base import (
    BaseMCPClient,
    MCPError,
    ServerProbe,
)
from free_claude_code.mcp.client import MCPClient
from free_claude_code.mcp.config import MCPServer
from free_claude_code.mcp.http_client import HTTPMCPClient
from free_claude_code.mcp.permissions import evaluate_server
from free_claude_code.mcp.state import MCPState
from free_claude_code.terminal.audit import AuditSink


def client_for(server: MCPServer, *, sink: AuditSink | None = None) -> BaseMCPClient:
    """Return the client implementation matching the server's transport."""

    if server.transport == "http":
        return HTTPMCPClient(server, sink=sink)
    return MCPClient(server, sink=sink)


def probe_server(
    server: MCPServer, state: MCPState, *, sink: AuditSink | None = None
) -> ServerProbe:
    """Check one server's health: permission, handshake, tool listing.

    Never raises; the probe report is the degradation path when a server
    is disabled, unreachable or misbehaving (roadmap section 9).
    """

    started = time.monotonic()
    verdict = evaluate_server(server, state)
    if not verdict.allowed:
        return ServerProbe(
            server=server.name,
            ok=False,
            reason=verdict.reason,
            policy_id=verdict.policy_id,
        )
    client = client_for(server, sink=sink)
    try:
        client.start()
        tools = client.list_tools()
    except MCPError as exc:
        return ServerProbe(
            server=server.name,
            ok=False,
            reason=str(exc),
            duration_seconds=time.monotonic() - started,
            policy_id=verdict.policy_id,
        )
    finally:
        client.close()
    return ServerProbe(
        server=server.name,
        ok=True,
        reason=f"server {server.name!r} answered with {len(tools)} tools",
        tools=tuple(tool.name for tool in tools),
        duration_seconds=time.monotonic() - started,
        policy_id=verdict.policy_id,
    )
