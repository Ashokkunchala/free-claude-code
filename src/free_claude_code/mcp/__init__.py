"""MCP adapters: declarative server configuration, validation and trust."""

from free_claude_code.mcp.base import (
    CallResult,
    HandshakeError,
    MCPError,
    MCPPrompt,
    MCPResource,
    MCPTool,
    OutputLimitError,
    PromptResult,
    RequestTimeoutError,
    ServerExitedError,
    ServerProbe,
    ServerStartError,
)
from free_claude_code.mcp.client import MCPClient
from free_claude_code.mcp.config import (
    CONFIG_NAME,
    TRANSPORTS,
    MCPServer,
    load_config,
    parse_config,
    parse_server,
)
from free_claude_code.mcp.factory import client_for, probe_server
from free_claude_code.mcp.http_client import HTTPMCPClient
from free_claude_code.mcp.permissions import ServerVerdict, evaluate_server
from free_claude_code.mcp.state import MCPState, default_config_path, default_state_path

__all__ = [
    "CONFIG_NAME",
    "TRANSPORTS",
    "CallResult",
    "HTTPMCPClient",
    "HandshakeError",
    "MCPClient",
    "MCPError",
    "MCPPrompt",
    "MCPResource",
    "MCPServer",
    "MCPState",
    "MCPTool",
    "OutputLimitError",
    "PromptResult",
    "RequestTimeoutError",
    "ServerExitedError",
    "ServerProbe",
    "ServerStartError",
    "ServerVerdict",
    "client_for",
    "default_config_path",
    "default_state_path",
    "evaluate_server",
    "load_config",
    "parse_config",
    "parse_server",
    "probe_server",
]
