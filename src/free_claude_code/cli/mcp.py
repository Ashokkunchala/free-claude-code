"""``fcc-mcp``: list, inspect, validate, enable and disable MCP servers.

Read-only except for the enable/disable state file: listing and
validation never start or contact a server, and configuration never
carries credentials - ``env`` lists variable names to pass through.
"""

import argparse
import json
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from free_claude_code.mcp import (
    MCPClient,
    MCPError,
    MCPServer,
    MCPState,
    evaluate_server,
    probe_server,
)
from free_claude_code.mcp.config import load_config
from free_claude_code.mcp.state import default_config_path, default_state_path


def _summary(server: MCPServer, state: MCPState | None) -> dict[str, Any]:
    return {
        "name": server.name,
        "description": server.description,
        "transport": server.transport,
        "enabled": state.is_enabled(server.name) if state is not None else None,
    }


def _print_servers(servers: list[MCPServer], state: MCPState, *, as_json: bool) -> None:
    summaries = [_summary(server, state) for server in servers]
    if as_json:
        print(json.dumps({"servers": summaries}, indent=2))
        return
    if not summaries:
        print("No MCP servers configured.")
        return
    for item in summaries:
        flags = ["enabled" if item["enabled"] else "disabled", item["transport"]]
        print(f"{item['name']} [{', '.join(flags)}]")
        print(f"  {item['description']}")


def _find(servers: list[MCPServer], name: str) -> MCPServer | None:
    for server in servers:
        if server.name == name:
            return server
    return None


def _inspect(server: MCPServer, state: MCPState, *, as_json: bool) -> None:
    verdict = evaluate_server(server, state)
    if as_json:
        detail = _summary(server, state)
        detail.update(
            {
                "command": list(server.command),
                "url": server.url,
                "env": list(server.env),
                "timeout_seconds": server.timeout_seconds,
                "max_output_bytes": server.max_output_bytes,
                "allowed": verdict.allowed,
                "reason": verdict.reason,
            }
        )
        print(json.dumps(detail, indent=2))
        return
    print(f"== {server.name} ==")
    print(f"enabled: {state.is_enabled(server.name)} ({verdict.policy_id})")
    print(f"description: {server.description}")
    print(f"transport: {server.transport}")
    if server.command:
        print(f"command: {' '.join(server.command)}")
    if server.url:
        print(f"url: {server.url}")
    if server.env:
        print(f"env passthrough: {', '.join(server.env)}")
    print(f"timeout: {server.timeout_seconds}s")
    print(f"output limit: {server.max_output_bytes} bytes")


def main(argv: Sequence[str] | None = None) -> None:
    """CLI for managing configured MCP servers."""

    parser = argparse.ArgumentParser(
        prog="fcc-mcp",
        description="List, inspect, validate, enable and disable MCP servers.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="MCP server config file (default: user config mcp.toml)",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=None,
        help="Enabled/disabled state file (default: user config mcp.json)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List configured servers and their state")
    inspect_parser = subparsers.add_parser(
        "inspect", help="Show one server's configuration and verdict"
    )
    inspect_parser.add_argument("name")
    subparsers.add_parser("validate", help="Validate the config file")
    for verb in ("enable", "disable"):
        toggle = subparsers.add_parser(verb, help=f"{verb.capitalize()} one server")
        toggle.add_argument("name")
    probe_parser = subparsers.add_parser(
        "probe", help="Health-check one server (starts it, then disconnects)"
    )
    probe_parser.add_argument("name")
    tools_parser = subparsers.add_parser(
        "tools", help="List one server's advertised tools (starts it)"
    )
    tools_parser.add_argument("name")

    args = parser.parse_args(argv)
    config_path = (args.config or default_config_path()).resolve()
    state = MCPState((args.state or default_state_path()).resolve())

    try:
        if args.command == "validate":
            if not config_path.is_file():
                print(f"{config_path}: missing", file=sys.stderr)
                sys.exit(1)
            try:
                servers = load_config(config_path)
            except (ValueError, tomllib.TOMLDecodeError) as exc:
                print(f"{config_path}: INVALID")
                print(f"  {exc}")
                sys.exit(1)
            if args.json:
                print(
                    json.dumps(
                        {"servers": [_summary(s, None) for s in servers]}, indent=2
                    )
                )
            else:
                print(f"{config_path}: ok ({len(servers)} servers)")
            return
        if not config_path.is_file():
            if args.command == "list":
                _print_servers([], state, as_json=args.json)
                return
            print(f"No MCP config at {config_path}", file=sys.stderr)
            sys.exit(2)
        servers = list(load_config(config_path))
        if args.command == "list":
            _print_servers(servers, state, as_json=args.json)
            return
        server = _find(servers, args.name)
        if server is None:
            print(f"Unknown MCP server: {args.name}", file=sys.stderr)
            sys.exit(2)
        if args.command == "inspect":
            _inspect(server, state, as_json=args.json)
        elif args.command == "probe":
            report = probe_server(server, state)
            if args.json:
                print(
                    json.dumps(
                        {
                            "name": report.server,
                            "ok": report.ok,
                            "reason": report.reason,
                            "tools": list(report.tools),
                            "duration_seconds": report.duration_seconds,
                            "policy_id": report.policy_id,
                        },
                        indent=2,
                    )
                )
            else:
                status = "ok" if report.ok else "FAIL"
                print(f"{report.server}: {status} - {report.reason}")
                if report.tools:
                    print(f"  tools: {', '.join(report.tools)}")
            sys.exit(0 if report.ok else 1)
        elif args.command == "tools":
            verdict = evaluate_server(server, state)
            if not verdict.allowed:
                print(verdict.reason, file=sys.stderr)
                sys.exit(1)
            client = MCPClient(server)
            try:
                client.start()
                tools = client.list_tools()
            except MCPError as exc:
                print(f"{server.name}: {exc}", file=sys.stderr)
                sys.exit(1)
            finally:
                client.close()
            if args.json:
                print(
                    json.dumps(
                        {
                            "tools": [
                                {"name": tool.name, "description": tool.description}
                                for tool in tools
                            ]
                        },
                        indent=2,
                    )
                )
            elif not tools:
                print(f"{server.name}: no tools")
            else:
                for tool in tools:
                    print(tool.name)
                    if tool.description:
                        print(f"  {tool.description}")
        else:
            enabled = args.command == "enable"
            state.set_enabled(server.name, enabled)
            if args.json:
                print(json.dumps({"name": server.name, "enabled": enabled}, indent=2))
            else:
                print(f"{server.name} {args.command}d")
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main(sys.argv[1:])
