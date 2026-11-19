"""Per-server permission gate for MCP adapters.

Every MCP server is individually permissioned (roadmap section 8): a
server is usable only while it is enabled in the state file, and a
server that fails validation is never contacted. The gate returns a
verdict instead of raising so callers can surface the reason.
"""

from dataclasses import dataclass

from free_claude_code.mcp.config import MCPServer
from free_claude_code.mcp.state import MCPState


@dataclass(frozen=True)
class ServerVerdict:
    """Whether one MCP server may be contacted, and why."""

    allowed: bool
    reason: str
    policy_id: str


def evaluate_server(server: MCPServer, state: MCPState) -> ServerVerdict:
    """Decide whether ``server`` may be contacted right now."""

    if not state.is_enabled(server.name):
        return ServerVerdict(
            allowed=False,
            reason=f"MCP server {server.name!r} is disabled",
            policy_id="deny:mcp-disabled",
        )
    return ServerVerdict(
        allowed=True,
        reason=f"MCP server {server.name!r} is enabled",
        policy_id="allow:mcp-server",
    )
