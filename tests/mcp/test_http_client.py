"""MCP streamable-HTTP client against an in-process fake server."""

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from free_claude_code.mcp import (
    HTTPMCPClient,
    MCPClient,
    MCPError,
    client_for,
)
from free_claude_code.mcp.client import (
    HandshakeError,
    OutputLimitError,
    RequestTimeoutError,
)
from free_claude_code.mcp.config import MCPServer

REQUESTS: list[dict] = []


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _reply(self, payload: dict, *, session: bool = True, sse: bool = False) -> None:
        body = json.dumps(payload)
        if sse:
            raw = f"data: {body}\n\n".encode()
            media = "text/event-stream"
        else:
            raw = body.encode()
            media = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", media)
        if session:
            self.send_header("Mcp-Session-Id", "sess-1")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        REQUESTS.append(dict(self.headers))
        msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method = msg.get("method")
        if "id" not in msg:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        reply = {"jsonrpc": "2.0", "id": msg["id"]}
        if method == "initialize" and self.path == "/bad":
            reply["result"] = {"nope": True}
            self._reply(reply)
        elif method == "initialize":
            reply["result"] = {"protocolVersion": "2025-03-26", "capabilities": {}}
            self._reply(reply)
        elif method == "tools/list":
            reply["result"] = {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                    },
                    {"name": "oops", "description": "Always errors"},
                    {"name": "leak", "description": "Leaks a token"},
                    {"name": "slow", "description": "Slow"},
                ]
            }
            self._reply(reply, sse=True)
        elif method == "tools/call":
            name = msg["params"]["name"]
            if name == "echo":
                reply["result"] = {
                    "content": [
                        {
                            "type": "text",
                            "text": "echo:"
                            + msg["params"]["arguments"].get("text", ""),
                        }
                    ]
                }
                self._reply(reply, sse=True)
            elif name == "oops":
                reply["result"] = {
                    "content": [{"type": "text", "text": "it broke"}],
                    "isError": True,
                }
                self._reply(reply)
            elif name == "leak":
                reply["result"] = {
                    "content": [
                        {
                            "type": "text",
                            "text": "token is sk-abcdefghij1234567890ABCDEF",
                        }
                    ]
                }
                self._reply(reply)
            elif name == "slow":
                time.sleep(3)
                reply["result"] = {"content": [{"type": "text", "text": "late"}]}
                self._reply(reply)
            else:
                reply["error"] = {"code": -32601, "message": "no such tool"}
                self._reply(reply)
        else:
            reply["result"] = {}
            self._reply(reply)

    def do_DELETE(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def make_http_server(
    url: str, *, timeout: int = 5, cap: int = 4096, name: str = "web"
) -> MCPServer:
    return MCPServer(
        name=name,
        description="Fake HTTP server",
        transport="http",
        command=(),
        url=url,
        env=(),
        timeout_seconds=timeout,
        max_output_bytes=cap,
    )


@contextmanager
def running_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/mcp"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _clear_requests() -> Iterator[None]:
    REQUESTS.clear()
    yield


def test_factory_selects_http_client() -> None:
    server = make_http_server("http://127.0.0.1:1/mcp")
    assert isinstance(client_for(server), HTTPMCPClient)


def test_factory_keeps_stdio_client() -> None:
    server = MCPServer(
        name="local",
        description="stdio",
        transport="stdio",
        command=("python", "server.py"),
        url="",
        env=(),
        timeout_seconds=5,
        max_output_bytes=4096,
    )
    assert isinstance(client_for(server), MCPClient)


def test_http_probe_list_and_call() -> None:
    with running_server() as url:
        client = HTTPMCPClient(make_http_server(url))
        client.start()
        try:
            tools = client.list_tools()
            assert [tool.name for tool in tools] == ["echo", "oops", "leak", "slow"]
            assert tools[0].input_schema["required"] == ["text"]
            result = client.call_tool("echo", {"text": "hi"})
            assert result.text == "echo:hi"
            assert not result.is_error
        finally:
            client.close()


def test_http_session_id_is_forwarded() -> None:
    with running_server() as url:
        client = HTTPMCPClient(make_http_server(url))
        client.start()
        try:
            client.list_tools()
        finally:
            client.close()
    post_init = [h for h in REQUESTS[1:] if h.get("Mcp-Session-Id")]
    assert post_init, "expected Mcp-Session-Id on requests after initialize"


def test_http_handshake_error() -> None:
    with running_server() as url:
        client = HTTPMCPClient(make_http_server(url.replace("/mcp", "/bad")))
        with pytest.raises(HandshakeError):
            client.start()


def test_http_schema_violation_blocks_call() -> None:
    with running_server() as url:
        client = HTTPMCPClient(make_http_server(url))
        client.start()
        try:
            with pytest.raises(MCPError, match="invalid arguments"):
                client.call_tool("echo", {})
        finally:
            client.close()


def test_http_is_error_surfaces_as_flag() -> None:
    with running_server() as url:
        client = HTTPMCPClient(make_http_server(url))
        client.start()
        try:
            result = client.call_tool("oops", {})
            assert result.is_error
            assert "it broke" in result.text
        finally:
            client.close()


def test_http_output_is_redacted() -> None:
    with running_server() as url:
        client = HTTPMCPClient(make_http_server(url))
        client.start()
        try:
            result = client.call_tool("leak", {})
            assert "sk-abcdefghij" not in result.text
            assert "<redacted>" in result.text
        finally:
            client.close()


def test_http_timeout_raises() -> None:
    with running_server() as url:
        client = HTTPMCPClient(make_http_server(url, timeout=1))
        client.start()
        try:
            with pytest.raises(RequestTimeoutError):
                client.call_tool("slow", {})
        finally:
            client.close()


def test_http_unreachable_server() -> None:
    with running_server() as url:
        client = HTTPMCPClient(make_http_server(url))
    with pytest.raises(MCPError, match="could not reach"):
        client.start()


def test_http_output_cap() -> None:
    with running_server() as url:
        client = HTTPMCPClient(make_http_server(url, cap=64))
        with pytest.raises(OutputLimitError):
            client.start()
