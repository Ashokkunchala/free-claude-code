"""MCP tools/call: schema validation, redaction and error handling."""

import textwrap
from pathlib import Path

import pytest

from free_claude_code.mcp import MCPError
from free_claude_code.mcp.client import MCPClient
from tests.mcp.test_client import make_server

CALL_SERVER = textwrap.dedent(
    """
    import json
    import sys

    SCHEMA = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if "id" not in msg:
            continue
        method = msg.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {}}
        elif method == "tools/list":
            result = {
                "tools": [
                    {"name": "echo", "description": "Echo", "inputSchema": SCHEMA},
                    {"name": "oops", "description": "Always errors"},
                ]
            }
        elif method == "tools/call":
            name = msg["params"]["name"]
            if name == "oops":
                result = {
                    "content": [{"type": "text", "text": "it broke"}],
                    "isError": True,
                }
            else:
                text = "echo:" + msg["params"]["arguments"].get("text", "")
                result = {"content": [{"type": "text", "text": text}]}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
        sys.stdout.flush()
    """
)

LEAKY_SERVER = textwrap.dedent(
    """
    import json
    import sys

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if "id" not in msg:
            continue
        method = msg.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {}}
        elif method == "tools/list":
            result = {"tools": [{"name": "leak", "description": "Leaks a token"}]}
        elif method == "tools/call":
            result = {
                "content": [
                    {"type": "text", "text": "token is sk-abcdefghij1234567890ABCDEF"}
                ]
            }
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
        sys.stdout.flush()
    """
)


def test_call_returns_text(tmp_path: Path) -> None:
    client = MCPClient(make_server(tmp_path, CALL_SERVER, name="call"))
    client.start()
    try:
        result = client.call_tool("echo", {"text": "hi"})
        assert result.text == "echo:hi"
        assert not result.is_error
    finally:
        client.close()


def test_call_validates_arguments_against_schema(tmp_path: Path) -> None:
    client = MCPClient(make_server(tmp_path, CALL_SERVER, name="call2"))
    client.start()
    try:
        with pytest.raises(MCPError, match="invalid arguments"):
            client.call_tool("echo", {})
        with pytest.raises(MCPError, match="invalid arguments"):
            client.call_tool("echo", {"text": 42})
    finally:
        client.close()


def test_call_unknown_tool_lists_advertised(tmp_path: Path) -> None:
    client = MCPClient(make_server(tmp_path, CALL_SERVER, name="call3"))
    client.start()
    try:
        with pytest.raises(MCPError, match="no tool 'nope'"):
            client.call_tool("nope", {})
    finally:
        client.close()


def test_call_server_error_flag(tmp_path: Path) -> None:
    client = MCPClient(make_server(tmp_path, CALL_SERVER, name="call4"))
    client.start()
    try:
        result = client.call_tool("oops", {})
        assert result.is_error
        assert "it broke" in result.text
    finally:
        client.close()


def test_call_output_is_redacted(tmp_path: Path) -> None:
    client = MCPClient(make_server(tmp_path, LEAKY_SERVER, name="leaky"))
    client.start()
    try:
        result = client.call_tool("leak", {})
        assert "sk-abcdefghij" not in result.text
        assert "<redacted>" in result.text
    finally:
        client.close()
