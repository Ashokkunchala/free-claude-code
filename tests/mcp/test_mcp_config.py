"""MCP server config parsing, validation and the permission gate."""

import pytest

from free_claude_code.mcp import (
    MCPState,
    evaluate_server,
    load_config,
    parse_config,
    parse_server,
)

STDIO = {
    "description": "Local Kubernetes read access",
    "transport": "stdio",
    "command": ["uvx", "mcp-k8s"],
    "env": ["KUBECONFIG"],
    "timeout_seconds": 120,
}

HTTP = {
    "description": "Hosted observability API",
    "transport": "http",
    "url": "https://mcp.example.com/sse",
}


def test_stdio_server_parses():
    server = parse_server("k8s-read", STDIO)
    assert server.name == "k8s-read"
    assert server.transport == "stdio"
    assert server.command == ("uvx", "mcp-k8s")
    assert server.env == ("KUBECONFIG",)
    assert server.timeout_seconds == 120
    assert server.max_output_bytes == 1_048_576


def test_http_server_parses_and_servers_sort():
    servers = parse_config({"servers": {"zeta": HTTP, "alpha": STDIO}})
    assert [server.name for server in servers] == ["alpha", "zeta"]


def test_localhost_http_is_allowed():
    server = parse_server("local-probe", {**HTTP, "url": "http://127.0.0.1:8765/mcp"})
    assert server.url.startswith("http://127.0.0.1")


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("Bad Name", STDIO),
        ("1leading", STDIO),
        ("ok-name", {**STDIO, "description": "  "}),
        ("ok-name", {**STDIO, "transport": "carrier-pigeon"}),
        ("ok-name", {**STDIO, "command": []}),
        ("ok-name", {**STDIO, "command": "uvx mcp-k8s"}),
        ("ok-name", {**HTTP, "url": "http://mcp.example.com/sse"}),
        ("ok-name", {**STDIO, "env": ["not a name"]}),
        ("ok-name", {**STDIO, "env": [{"name": "KUBECONFIG"}]}),
        ("ok-name", {**STDIO, "timeout_seconds": 0}),
        ("ok-name", {**STDIO, "timeout_seconds": 7200}),
        ("ok-name", {**STDIO, "max_output_bytes": -1}),
    ],
)
def test_invalid_servers_are_rejected(name, raw):
    with pytest.raises(ValueError, match="Invalid MCP server"):
        parse_server(name, raw)


def test_every_issue_is_reported_at_once():
    with pytest.raises(ValueError) as caught:
        parse_server(
            "Bad",
            {**STDIO, "description": "", "timeout_seconds": 0, "env": ["x"]},
        )
    message = str(caught.value)
    assert "kebab-case" in message
    assert "description" in message
    assert "timeout_seconds" in message
    assert "env" in message


def test_non_table_server_and_config_are_rejected():
    with pytest.raises(ValueError, match="must be a table"):
        parse_server("ok-name", "not a table")
    with pytest.raises(ValueError, match=r"\[servers\] must be a table"):
        parse_config({"servers": ["nope"]})


def test_load_config_reads_toml(tmp_path):
    config = tmp_path / "mcp.toml"
    config.write_text(
        "[servers.k8s-read]\n"
        'description = "Local Kubernetes read access"\n'
        'command = ["uvx", "mcp-k8s"]\n'
        'env = ["KUBECONFIG"]\n',
        encoding="utf-8",
    )
    (server,) = load_config(config)
    assert server.name == "k8s-read"
    assert server.transport == "stdio"


def test_state_round_trip_and_atomic_write(tmp_path):
    state = MCPState(tmp_path / "mcp.json")
    assert state.is_enabled("k8s-read")
    state.set_enabled("k8s-read", False)
    assert not state.is_enabled("k8s-read")
    reloaded = MCPState(tmp_path / "mcp.json")
    assert not reloaded.is_enabled("k8s-read")
    reloaded.set_enabled("k8s-read", True)
    assert MCPState(tmp_path / "mcp.json").is_enabled("k8s-read")


def test_enabled_server_is_allowed(tmp_path):
    state = MCPState(tmp_path / "mcp.json")
    verdict = evaluate_server(parse_server("k8s-read", STDIO), state)
    assert verdict.allowed
    assert verdict.policy_id == "allow:mcp-server"


def test_disabled_server_is_denied(tmp_path):
    state = MCPState(tmp_path / "mcp.json")
    state.set_enabled("k8s-read", False)
    verdict = evaluate_server(parse_server("k8s-read", STDIO), state)
    assert not verdict.allowed
    assert verdict.policy_id == "deny:mcp-disabled"
