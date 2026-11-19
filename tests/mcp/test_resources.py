"""MCP resources and prompts over both transports."""

import json
import textwrap
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from free_claude_code.mcp import MCPError
from free_claude_code.mcp.client import MCPClient
from free_claude_code.mcp.config import MCPServer
from free_claude_code.mcp.http_client import HTTPMCPClient
from tests.mcp.test_client import make_server

RP_SERVER = textwrap.dedent(
    """
    import json
    import sys

    RESOURCES = {
        "resources": [
            {
                "uri": "mem://status",
                "name": "status",
                "description": "Server status",
                "mimeType": "text/plain",
            }
        ]
    }
    PROMPTS = {
        "prompts": [
            {
                "name": "summarize",
                "description": "Summarize a topic",
                "arguments": [{"name": "topic", "required": True}],
            }
        ]
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
        elif method == "resources/list":
            result = RESOURCES
        elif method == "resources/read":
            uri = msg["params"]["uri"]
            if uri == "mem://status":
                result = {
                    "contents": [
                        {"uri": uri, "mimeType": "text/plain", "text": "all green"},
                        {"uri": uri, "blob": "aGV4"},
                    ]
                }
            elif uri == "mem://leak":
                result = {
                    "contents": [
                        {"uri": uri, "text": "key sk-abcdefghij1234567890ABCDEF"}
                    ]
                }
            else:
                result = {"contents": []}
        elif method == "prompts/list":
            result = PROMPTS
        elif method == "prompts/get":
            topic = msg["params"].get("arguments", {}).get("topic", "nothing")
            result = {
                "description": "Summarize a topic",
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": "summarize " + topic},
                    }
                ],
            }
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
        sys.stdout.flush()
    """
)


class _RPHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "id" not in msg:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        method = msg.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {}}
        elif method == "resources/list":
            result = {
                "resources": [
                    {
                        "uri": "mem://status",
                        "name": "status",
                        "description": "Server status",
                        "mimeType": "text/plain",
                    }
                ]
            }
        elif method == "resources/read":
            uri = msg["params"]["uri"]
            if uri == "mem://status":
                result = {
                    "contents": [
                        {"uri": uri, "mimeType": "text/plain", "text": "all green"},
                        {"uri": uri, "blob": "aGV4"},
                    ]
                }
            elif uri == "mem://leak":
                result = {
                    "contents": [
                        {"uri": uri, "text": "key sk-abcdefghij1234567890ABCDEF"}
                    ]
                }
            else:
                result = {"contents": []}
        elif method == "prompts/list":
            result = {
                "prompts": [
                    {
                        "name": "summarize",
                        "description": "Summarize a topic",
                        "arguments": [{"name": "topic", "required": True}],
                    }
                ]
            }
        elif method == "prompts/get":
            topic = msg["params"].get("arguments", {}).get("topic", "nothing")
            result = {
                "description": "Summarize a topic",
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": "summarize " + topic},
                    }
                ],
            }
        else:
            result = {}
        raw = json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@contextmanager
def running_rp_http() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/mcp"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def rp_client(kind: str, tmp_path: Path) -> Iterator:
    if kind == "stdio":
        client = MCPClient(make_server(tmp_path, RP_SERVER, name="rp"))
    else:
        server = None
        with running_rp_http() as url:
            server = MCPServer(
                name="rp-http",
                description="Fake HTTP resources server",
                transport="http",
                command=(),
                url=url,
                env=(),
                timeout_seconds=5,
                max_output_bytes=65536,
            )
            client = HTTPMCPClient(server)
            client.start()
            try:
                yield client
            finally:
                client.close()
            return
    client.start()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(params=["stdio", "http"])
def client(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator:
    with rp_client(request.param, tmp_path) as found:
        yield found


def test_list_resources(client) -> None:
    resources = client.list_resources()
    assert [(r.uri, r.name, r.mime_type) for r in resources] == [
        ("mem://status", "status", "text/plain")
    ]
    assert resources[0].description == "Server status"


def test_read_resource_joins_text_and_skips_blobs(client) -> None:
    assert client.read_resource("mem://status") == "all green"


def test_read_resource_redacts_tokens(client) -> None:
    text = client.read_resource("mem://leak")
    assert "sk-abcdefghij" not in text
    assert "<redacted>" in text


def test_list_prompts(client) -> None:
    prompts = client.list_prompts()
    assert [(p.name, p.arguments) for p in prompts] == [("summarize", ("topic",))]


def test_get_prompt(client) -> None:
    result = client.get_prompt("summarize", {"topic": "deploys"})
    assert result.text == "summarize deploys"
    assert result.description == "Summarize a topic"


def test_get_prompt_unknown_lists_advertised(client) -> None:
    with pytest.raises(MCPError, match="no prompt 'nope'"):
        client.get_prompt("nope", {})
