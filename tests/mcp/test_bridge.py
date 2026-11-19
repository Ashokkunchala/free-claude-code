"""MCP bridge: provider-facing schemas and dispatch."""

import textwrap
from pathlib import Path

import pytest

from free_claude_code.mcp import MCPError, MCPState
from free_claude_code.mcp.bridge import (
    MCPBridge,
    exposed_tool_name,
)
from tests.mcp.test_client import make_server

BRIDGE_SERVER = textwrap.dedent(
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
            result = {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                        },
                    },
                    {"name": "leak", "description": "Leaks a token"},
                    {"name": "get pods", "description": "List pods"},
                    {"name": "get_pods", "description": "List pods again"},
                ]
            }
        elif method == "tools/call":
            name = msg["params"]["name"]
            if name == "leak":
                text = "token sk-abcdefghij1234567890ABCDEF"
            else:
                text = "echo:" + msg["params"]["arguments"].get("text", "")
            result = {"content": [{"type": "text", "text": text}]}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
        sys.stdout.flush()
    """
)

DEAD_SERVER = "import sys; sys.exit(1)"


def make_bridge(tmp_path: Path, *, disable: bool = False) -> tuple[MCPBridge, MCPState]:
    live = make_server(tmp_path, BRIDGE_SERVER, name="live")
    dead_script = tmp_path / "dead.py"
    dead_script.write_text(DEAD_SERVER, encoding="utf-8")
    dead = make_server(tmp_path, BRIDGE_SERVER, name="dead")
    import sys

    dead = type(live)(
        name="dead",
        description="Dead server",
        transport="stdio",
        command=(sys.executable, str(dead_script)),
        url="",
        env=(),
        timeout_seconds=5,
        max_output_bytes=65536,
    )
    state = MCPState(tmp_path / "mcp.json")
    if disable:
        state.set_enabled("live", False)
    return MCPBridge([live, dead], state), state


def test_exposed_name_sanitizes_and_dedupes() -> None:
    assert exposed_tool_name("k8s", "get pods") == "k8s__get_pods"
    taken = {"k8s__get_pods"}
    second = exposed_tool_name("k8s", "get_pods", taken=taken)
    assert second != "k8s__get_pods"
    assert len(second) <= 64
    first = exposed_tool_name("k8s", "get pods", taken=set())
    assert second != first


def test_collect_skips_dead_and_disabled(tmp_path: Path) -> None:
    bridge, _state = make_bridge(tmp_path)
    try:
        tools = bridge.collect()
        names = [tool.exposed_name for tool in tools]
        assert "live__echo" in names
        assert "live__get_pods" in names  # sanitized from "get pods"
        assert any(
            name.startswith("live__get_pods_") and name != "live__get_pods"
            for name in names
        )
        assert set(bridge.collection_errors) == {"dead"}
    finally:
        bridge.close()


def test_schemas_match_provider_dialects(tmp_path: Path) -> None:
    bridge, _state = make_bridge(tmp_path)
    try:
        bridge.collect()
        anthropic = bridge.anthropic_tools()
        openai = bridge.openai_tools()
        assert len(anthropic) == len(openai) == 4
        echo = next(t for t in anthropic if t["name"] == "live__echo")
        assert echo["description"].startswith("[live]")
        assert echo["input_schema"]["properties"]["text"]["type"] == "string"
        fn = next(t for t in openai if t["function"]["name"] == "live__echo")
        assert fn["type"] == "function"
        assert fn["function"]["parameters"]["type"] == "object"
        # tools without a schema get an empty-object schema for providers
        leak = next(t for t in anthropic if t["name"] == "live__leak")
        assert leak["input_schema"] == {"type": "object", "properties": {}}
        for schema in anthropic:
            import re

            assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", schema["name"])
    finally:
        bridge.close()


def test_dispatch_routes_and_redacts(tmp_path: Path) -> None:
    bridge, _state = make_bridge(tmp_path)
    try:
        bridge.collect()
        result = bridge.dispatch("live__echo", {"text": "hi"})
        assert result.text == "echo:hi"
        leaked = bridge.dispatch("live__leak", {})
        assert "sk-abcdefghij" not in leaked.text
        assert "<redacted>" in leaked.text
    finally:
        bridge.close()


def test_dispatch_unknown_tool(tmp_path: Path) -> None:
    bridge, _state = make_bridge(tmp_path)
    try:
        bridge.collect()
        with pytest.raises(MCPError, match="no bridged tool"):
            bridge.dispatch("live__nope", {})
    finally:
        bridge.close()


def test_dispatch_disabled_server_is_denied(tmp_path: Path) -> None:
    bridge, _state = make_bridge(tmp_path)
    try:
        bridge.collect()
        _state.set_enabled("live", False)
        with pytest.raises(MCPError, match="disabled"):
            bridge.dispatch("live__echo", {"text": "hi"})
    finally:
        bridge.close()


def test_disabled_server_degrades_at_collect(tmp_path: Path) -> None:
    bridge, _state = make_bridge(tmp_path, disable=True)
    try:
        tools = bridge.collect()
        assert tools == ()
        assert set(bridge.collection_errors) == {"live", "dead"}
    finally:
        bridge.close()
