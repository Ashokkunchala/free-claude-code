"""MCP adapters: declarative server configuration, validation and trust."""

from free_claude_code.mcp.config import (
    CONFIG_NAME,
    TRANSPORTS,
    MCPServer,
    load_config,
    parse_config,
    parse_server,
)
from free_claude_code.mcp.permissions import ServerVerdict, evaluate_server
from free_claude_code.mcp.state import MCPState, default_config_path, default_state_path

__all__ = [
    "CONFIG_NAME",
    "TRANSPORTS",
    "MCPServer",
    "MCPState",
    "ServerVerdict",
    "default_config_path",
    "default_state_path",
    "evaluate_server",
    "load_config",
    "parse_config",
    "parse_server",
]
