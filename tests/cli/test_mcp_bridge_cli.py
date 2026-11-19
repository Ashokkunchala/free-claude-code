"""fcc-mcp export-tools / invoke over the bridge, against a fake server."""

import json
import sys
import textwrap

import pytest

from free_claude_code.cli.mcp import main
from tests.mcp.test_bridge import BRIDGE_SERVER


@pytest.fixture
def paths(tmp_path):
    script = tmp_path / "bridge_server.py"
    script.write_text(BRIDGE_SERVER, encoding="utf-8")
    dead = tmp_path / "dead.py"
    dead.write_text("import sys; sys.exit(1)", encoding="utf-8")
    config = tmp_path / "mcp.toml"
    config.write_text(
        textwrap.dedent(
            f"""
            [servers.live]
            description = "Fake bridge server"
            command = [{json.dumps(sys.executable)}, {json.dumps(str(script))}]
            timeout_seconds = 5

            [servers.dead]
            description = "Exits immediately"
            command = [{json.dumps(sys.executable)}, {json.dumps(str(dead))}]
            timeout_seconds = 5
            """
        ),
        encoding="utf-8",
    )
    return ["--config", str(config), "--state", str(tmp_path / "mcp.json")]


def invoke(args):
    try:
        main(args)
    except SystemExit as exc:
        return exc.code
    return 0


def test_export_tools_anthropic_envelope(capsys, paths):
    assert invoke([*paths, "export-tools"]) in (0, None)
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == "anthropic"
    names = [tool["name"] for tool in data["tools"]]
    assert "live__echo" in names and "live__get_pods" in names
    echo = next(tool for tool in data["tools"] if tool["name"] == "live__echo")
    assert echo["input_schema"]["properties"]["text"]["type"] == "string"
    assert set(data["errors"]) == {"dead"}


def test_export_tools_openai_envelope(capsys, paths):
    assert invoke([*paths, "export-tools", "--format", "openai"]) in (0, None)
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == "openai"
    fn = next(t for t in data["tools"] if t["function"]["name"] == "live__echo")
    assert fn["type"] == "function"
    assert fn["function"]["parameters"]["type"] == "object"


def test_invoke_routes_by_exposed_name(capsys, paths):
    assert invoke([*paths, "invoke", "live__echo", "--args", '{"text": "hi"}']) in (
        0,
        None,
    )
    assert capsys.readouterr().out.strip() == "echo:hi"


def test_invoke_unknown_exits_one(capsys, paths):
    assert invoke([*paths, "invoke", "live__nope"]) == 1
    assert "no bridged tool" in capsys.readouterr().err


def test_invoke_disabled_server_is_denied(capsys, paths):
    assert invoke([*paths, "disable", "live"]) in (0, None)
    capsys.readouterr()
    assert invoke([*paths, "invoke", "live__echo", "--args", '{"text": "hi"}']) == 1
    err = capsys.readouterr().err
    assert "no bridged tool" in err or "disabled" in err


def test_export_tools_skips_disabled(capsys, paths):
    assert invoke([*paths, "disable", "live"]) in (0, None)
    capsys.readouterr()
    assert invoke([*paths, "export-tools"]) in (0, None)
    data = json.loads(capsys.readouterr().out)
    assert data["tools"] == []
    assert set(data["errors"]) == {"live", "dead"}
