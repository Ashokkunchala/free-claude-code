"""MCP stdio client tests against in-process fake servers."""

import sys
import textwrap
from pathlib import Path

import pytest

from free_claude_code.mcp import (
    MCPError,
    MCPServer,
    MCPState,
    RequestTimeoutError,
    ServerExitedError,
    probe_server,
)
from free_claude_code.mcp.client import MCPClient

FAKE_SERVER = textwrap.dedent(
    """
    import json
    import sys

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "id" not in msg:
            continue
        method = msg.get("method")
        if method == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "0.1"},
            }
            resp = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        elif method == "tools/list":
            resp = {
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": {
                    "tools": [
                        {"name": "echo", "description": "Echo tool"},
                        {"name": "fail", "description": "Fail tool"},
                    ]
                },
            }
        elif method == "ping":
            resp = {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
        else:
            resp = {
                "jsonrpc": "2.0",
                "id": msg["id"],
                "error": {"code": -32601, "message": "no such method"},
            }
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
    """
)

NOISY_SERVER = textwrap.dedent(
    """
    import json
    import sys

    print("boot log line that is not json")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if "id" not in msg:
            continue
        if msg.get("method") == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {}}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
        sys.stdout.flush()
    """
)

SILENT_SERVER = "import sys, time\nfor _ in sys.stdin:\n    time.sleep(30)\n"

DYING_SERVER = "import sys\nsys.exit(1)\n"


def make_server(tmp_path: Path, script: str, name: str = "fake") -> MCPServer:
    script_path = tmp_path / f"{name}_server.py"
    script_path.write_text(textwrap.dedent(script), encoding="utf-8")
    return MCPServer(
        name=name,
        description="fake server",
        transport="stdio",
        command=(sys.executable, str(script_path)),
        url="",
        env=(),
        timeout_seconds=2,
        max_output_bytes=1_048_576,
    )


def state(tmp_path: Path) -> MCPState:
    return MCPState(tmp_path / "mcp-state.json")


def test_handshake_list_tools_and_ping(tmp_path: Path) -> None:
    client = MCPClient(make_server(tmp_path, FAKE_SERVER))
    client.start()
    try:
        tools = client.list_tools()
        assert [tool.name for tool in tools] == ["echo", "fail"]
        assert tools[0].description == "Echo tool"
        client.ping()
    finally:
        client.close()


def test_unknown_method_raises_mcp_error(tmp_path: Path) -> None:
    client = MCPClient(make_server(tmp_path, FAKE_SERVER))
    client.start()
    try:
        with pytest.raises(MCPError, match="no such method"):
            client._request("tools/call", {"name": "echo"})
    finally:
        client.close()


def test_non_json_stdout_noise_is_tolerated(tmp_path: Path) -> None:
    client = MCPClient(make_server(tmp_path, NOISY_SERVER, name="noisy"))
    client.start()
    try:
        client.ping()
    finally:
        client.close()


def test_silent_server_times_out(tmp_path: Path) -> None:
    client = MCPClient(make_server(tmp_path, SILENT_SERVER, name="silent"))
    with pytest.raises(RequestTimeoutError):
        client.start()
    client.close()


def test_dying_server_raises_start_or_exited(tmp_path: Path) -> None:
    client = MCPClient(make_server(tmp_path, DYING_SERVER, name="dying"))
    with pytest.raises((ServerExitedError, RequestTimeoutError, MCPError)):
        client.start()
    client.close()


def test_probe_healthy_server(tmp_path: Path) -> None:
    report = probe_server(make_server(tmp_path, FAKE_SERVER), state(tmp_path))
    assert report.ok
    assert report.tools == ("echo", "fail")
    assert report.duration_seconds > 0


def test_probe_disabled_server_is_denied(tmp_path: Path) -> None:
    server = make_server(tmp_path, FAKE_SERVER)
    st = state(tmp_path)
    st.set_enabled(server.name, False)
    report = probe_server(server, st)
    assert not report.ok
    assert "disabled" in report.reason
    assert report.policy_id == "deny:mcp-disabled"


def test_probe_broken_server_degrades(tmp_path: Path) -> None:
    report = probe_server(
        make_server(tmp_path, DYING_SERVER, name="dying"), state(tmp_path)
    )
    assert not report.ok
    assert report.reason
