"""fcc-mcp probe/tools/call against an in-process HTTP MCP server."""

import json
import textwrap

import pytest

from free_claude_code.cli.mcp import main
from tests.mcp.test_http_client import running_server


@pytest.fixture
def paths(tmp_path):
    config = tmp_path / "mcp.toml"
    config_path = str(config)
    state_path = str(tmp_path / "mcp.json")

    def write(url: str) -> list[str]:
        config.write_text(
            textwrap.dedent(
                f"""
                [servers.web]
                description = "Fake HTTP server"
                transport = "http"
                url = {json.dumps(url)}
                timeout_seconds = 5
                """
            ),
            encoding="utf-8",
        )
        return ["--config", config_path, "--state", state_path]

    return write


def invoke(args):
    try:
        main(args)
    except SystemExit as exc:
        return exc.code
    return 0


def test_probe_http_server(capsys, paths):
    with running_server() as url:
        base = paths(url)
        assert invoke([*base, "probe", "web"]) == 0
    assert "4 tools" in capsys.readouterr().out


def test_tools_http_server(capsys, paths):
    with running_server() as url:
        base = paths(url)
        assert invoke([*base, "tools", "web"]) == 0
    out = capsys.readouterr().out
    assert "echo" in out and "leak" in out


def test_call_http_server_json(capsys, paths):
    with running_server() as url:
        base = paths(url)
        assert (
            invoke([*base, "--json", "call", "web", "echo", "--args", '{"text": "hi"}'])
            == 0
        )
    data = json.loads(capsys.readouterr().out)
    assert data == {"text": "echo:hi", "is_error": False}


def test_call_http_server_unreachable(capsys, paths):
    with running_server() as url:
        base = paths(url)
    assert (
        invoke(
            [*base, "call", "web", "echo", "--args", "{}"],
        )
        == 1
    )
    assert "could not reach" in capsys.readouterr().err
