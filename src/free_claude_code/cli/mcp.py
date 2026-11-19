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
    MCPBridge,
    MCPError,
    MCPServer,
    MCPState,
    client_for,
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


def _bridge_command(
    args: argparse.Namespace, servers: list[MCPServer], state: MCPState
) -> None:
    """``export-tools``/``invoke``: the bridge over every enabled server."""

    bridge = MCPBridge(servers, state)
    try:
        bridge.collect()
        if args.command == "export-tools":
            tools = (
                bridge.anthropic_tools()
                if args.format == "anthropic"
                else bridge.openai_tools()
            )
            print(
                json.dumps(
                    {
                        "format": args.format,
                        "tools": tools,
                        "errors": bridge.collection_errors,
                    },
                    indent=2,
                )
            )
            return
        try:
            call_args = json.loads(args.args)
        except ValueError:
            print("--args must be a JSON object", file=sys.stderr)
            sys.exit(2)
        if not isinstance(call_args, dict):
            print("--args must be a JSON object", file=sys.stderr)
            sys.exit(2)
        try:
            result = bridge.dispatch(args.exposed, call_args)
        except MCPError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
        if result.text:
            print(result.text)
        sys.exit(1 if result.is_error else 0)
    finally:
        bridge.close()


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
    call_parser = subparsers.add_parser(
        "call", help="Invoke one tool on a server (arguments as a JSON object)"
    )
    call_parser.add_argument("name")
    call_parser.add_argument("tool")
    call_parser.add_argument(
        "--args", default="{}", help="Tool arguments as a JSON object"
    )
    resources_parser = subparsers.add_parser(
        "resources", help="List one server's advertised resources (starts it)"
    )
    resources_parser.add_argument("name")
    read_parser = subparsers.add_parser(
        "read", help="Read one resource from a server (redacted text)"
    )
    read_parser.add_argument("name")
    read_parser.add_argument("uri")
    prompts_parser = subparsers.add_parser(
        "prompts", help="List one server's advertised prompts (starts it)"
    )
    prompts_parser.add_argument("name")
    prompt_parser = subparsers.add_parser(
        "prompt", help="Render one prompt on a server (arguments as a JSON object)"
    )
    prompt_parser.add_argument("name")
    prompt_parser.add_argument("prompt")
    prompt_parser.add_argument(
        "--args", default="{}", help="Prompt arguments as a JSON object"
    )
    export_parser = subparsers.add_parser(
        "export-tools",
        help="Export enabled servers' tools as provider tool definitions (JSON)",
    )
    export_parser.add_argument(
        "--format",
        choices=("anthropic", "openai"),
        default="anthropic",
        help="Provider schema dialect (default: anthropic)",
    )
    invoke_parser = subparsers.add_parser(
        "invoke", help="Invoke one tool by its exposed <server>__<tool> name"
    )
    invoke_parser.add_argument("exposed")
    invoke_parser.add_argument(
        "--args", default="{}", help="Tool arguments as a JSON object"
    )

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
        if args.command in ("export-tools", "invoke"):
            _bridge_command(args, servers, state)
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
        elif args.command == "call":
            verdict = evaluate_server(server, state)
            if not verdict.allowed:
                print(verdict.reason, file=sys.stderr)
                sys.exit(1)
            try:
                call_args = json.loads(args.args)
            except ValueError:
                print("--args must be a JSON object", file=sys.stderr)
                sys.exit(2)
            if not isinstance(call_args, dict):
                print("--args must be a JSON object", file=sys.stderr)
                sys.exit(2)
            client = client_for(server)
            try:
                client.start()
                result = client.call_tool(args.tool, call_args)
            except MCPError as exc:
                print(f"{server.name}: {exc}", file=sys.stderr)
                sys.exit(1)
            finally:
                client.close()
            if args.json:
                print(
                    json.dumps(
                        {"text": result.text, "is_error": result.is_error}, indent=2
                    )
                )
            elif result.text:
                print(result.text)
            sys.exit(1 if result.is_error else 0)
        elif args.command == "resources":
            verdict = evaluate_server(server, state)
            if not verdict.allowed:
                print(verdict.reason, file=sys.stderr)
                sys.exit(1)
            client = client_for(server)
            try:
                client.start()
                resources = client.list_resources()
            except MCPError as exc:
                print(f"{server.name}: {exc}", file=sys.stderr)
                sys.exit(1)
            finally:
                client.close()
            if args.json:
                print(
                    json.dumps(
                        {
                            "resources": [
                                {
                                    "uri": resource.uri,
                                    "name": resource.name,
                                    "description": resource.description,
                                    "mime_type": resource.mime_type,
                                }
                                for resource in resources
                            ]
                        },
                        indent=2,
                    )
                )
            elif not resources:
                print(f"{server.name}: no resources")
            else:
                for resource in resources:
                    print(f"{resource.uri}  {resource.name}")
        elif args.command == "read":
            verdict = evaluate_server(server, state)
            if not verdict.allowed:
                print(verdict.reason, file=sys.stderr)
                sys.exit(1)
            client = client_for(server)
            try:
                client.start()
                text = client.read_resource(args.uri)
            except MCPError as exc:
                print(f"{server.name}: {exc}", file=sys.stderr)
                sys.exit(1)
            finally:
                client.close()
            if args.json:
                print(json.dumps({"uri": args.uri, "text": text}, indent=2))
            elif text:
                print(text)
        elif args.command == "prompts":
            verdict = evaluate_server(server, state)
            if not verdict.allowed:
                print(verdict.reason, file=sys.stderr)
                sys.exit(1)
            client = client_for(server)
            try:
                client.start()
                prompts = client.list_prompts()
            except MCPError as exc:
                print(f"{server.name}: {exc}", file=sys.stderr)
                sys.exit(1)
            finally:
                client.close()
            if args.json:
                print(
                    json.dumps(
                        {
                            "prompts": [
                                {
                                    "name": prompt.name,
                                    "description": prompt.description,
                                    "arguments": list(prompt.arguments),
                                }
                                for prompt in prompts
                            ]
                        },
                        indent=2,
                    )
                )
            elif not prompts:
                print(f"{server.name}: no prompts")
            else:
                for prompt in prompts:
                    print(prompt.name)
        elif args.command == "prompt":
            verdict = evaluate_server(server, state)
            if not verdict.allowed:
                print(verdict.reason, file=sys.stderr)
                sys.exit(1)
            try:
                prompt_args = json.loads(args.args)
            except ValueError:
                print("--args must be a JSON object", file=sys.stderr)
                sys.exit(2)
            if not isinstance(prompt_args, dict):
                print("--args must be a JSON object", file=sys.stderr)
                sys.exit(2)
            client = client_for(server)
            try:
                client.start()
                result = client.get_prompt(args.prompt, prompt_args)
            except MCPError as exc:
                print(f"{server.name}: {exc}", file=sys.stderr)
                sys.exit(1)
            finally:
                client.close()
            if args.json:
                print(
                    json.dumps(
                        {"text": result.text, "description": result.description},
                        indent=2,
                    )
                )
            elif result.text:
                print(result.text)
        elif args.command == "tools":
            verdict = evaluate_server(server, state)
            if not verdict.allowed:
                print(verdict.reason, file=sys.stderr)
                sys.exit(1)
            client = client_for(server)
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
