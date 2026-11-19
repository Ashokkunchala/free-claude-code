"""Declarative MCP server configuration: parsing and validation.

MCP servers are explicitly configured in a ``mcp.toml`` file: one
``[servers.<name>]`` table per server. Configuration is declarative and
never carries credentials: ``env`` lists variable *names* to pass through
to the server process, never values. Local paths are allowed but never
hard-coded by free-claude-code itself. Everything a server returns is
untrusted data, so declared output limits and timeouts are part of the
validated shape, not afterthoughts (roadmap section 8).
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "mcp.toml"
TRANSPORTS = ("stdio", "http")
MAX_TIMEOUT_SECONDS = 3600
MAX_OUTPUT_BYTES = 1_048_576

_NAME = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
_HTTPS = re.compile(r"https://[^\s]+")
_LOCAL_HTTP = re.compile(r"http://(localhost|127\.0\.0\.1)(:\d+)?([/?#][^\s]*)?$")


@dataclass(frozen=True)
class MCPServer:
    """Validated declaration of one MCP server."""

    name: str
    description: str
    transport: str
    command: tuple[str, ...]
    url: str
    env: tuple[str, ...]
    timeout_seconds: int
    max_output_bytes: int


def _want(condition: bool, issues: list[str], message: str) -> None:
    if not condition:
        issues.append(message)


def parse_server(name: str, raw: object) -> MCPServer:
    """Validate one server table into an :class:`MCPServer`.

    Raises ValueError listing every issue found, so configuration errors
    are fixed in one pass instead of one error at a time.
    """

    issues: list[str] = []
    _want(
        bool(_NAME.fullmatch(name)),
        issues,
        f"server name must be kebab-case, got {name!r}",
    )
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid MCP server {name!r}: definition must be a table")
    description = raw.get("description", "")
    transport = raw.get("transport", "stdio")
    command = raw.get("command", [])
    url = raw.get("url", "")
    env = raw.get("env", [])
    timeout = raw.get("timeout_seconds", 60)
    limit = raw.get("max_output_bytes", MAX_OUTPUT_BYTES)

    _want(
        isinstance(description, str) and bool(description.strip()),
        issues,
        f"server {name!r} needs a description",
    )
    _want(
        transport in TRANSPORTS,
        issues,
        f"server {name!r} transport must be one of {sorted(TRANSPORTS)}",
    )
    _want(
        isinstance(command, list) and all(isinstance(part, str) for part in command),
        issues,
        f"server {name!r} command must be a list of strings",
    )
    if transport == "stdio":
        _want(
            isinstance(command, list) and bool(command),
            issues,
            f"server {name!r} with stdio transport needs a command",
        )
    if transport == "http":
        _want(
            isinstance(url, str)
            and bool(_HTTPS.fullmatch(url) or _LOCAL_HTTP.fullmatch(url)),
            issues,
            f"server {name!r} url must be https (plain http only on localhost)",
        )
    _want(
        isinstance(env, list)
        and all(isinstance(item, str) and _ENV_NAME.fullmatch(item) for item in env),
        issues,
        f"server {name!r} env must list variable names (values are never stored)",
    )
    _want(
        isinstance(timeout, int) and 0 < timeout <= MAX_TIMEOUT_SECONDS,
        issues,
        f"server {name!r} timeout_seconds must be 1..{MAX_TIMEOUT_SECONDS}",
    )
    _want(
        isinstance(limit, int) and 0 < limit <= MAX_OUTPUT_BYTES,
        issues,
        f"server {name!r} max_output_bytes must be 1..{MAX_OUTPUT_BYTES}",
    )
    if issues:
        raise ValueError("Invalid MCP server config: " + "; ".join(issues))
    return MCPServer(
        name=name,
        description=description.strip(),
        transport=transport,
        command=tuple(command),
        url=url if isinstance(url, str) else "",
        env=tuple(env),
        timeout_seconds=timeout,
        max_output_bytes=limit,
    )


def parse_config(data: dict) -> tuple[MCPServer, ...]:
    """Validate a whole ``mcp.toml`` document into servers sorted by name."""

    servers_raw = data.get("servers", {})
    if not isinstance(servers_raw, dict):
        raise ValueError("Invalid MCP config: [servers] must be a table")
    servers = [parse_server(name, raw) for name, raw in servers_raw.items()]
    return tuple(sorted(servers, key=lambda server: server.name))


def load_config(path: Path) -> tuple[MCPServer, ...]:
    """Parse and validate the MCP config file at ``path``."""

    return parse_config(tomllib.loads(path.read_text(encoding="utf-8")))
