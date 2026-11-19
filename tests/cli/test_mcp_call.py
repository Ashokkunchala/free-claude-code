"""fcc-mcp call subcommand against an in-process fake server."""

import json
import sys
import textwrap

import pytest

from free_claude_code.cli.mcp import main

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
            result = {"tools": [{"name": "echo", "description": "Echo", "inputSchema": SCHEMA}]}
        elif method == "tools/call":
            text = "echo:" + msg["params"]["arguments"].get("text", "")
            result = {"content": [{"type": "text", "text": text}]}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
        sys.stdout.flush()
    """
)


@pytest.fixture
def paths(tmp_path):
    script = tmp_path / "call_server.py"
    script.write_text(CALL_SERVER, encoding="utf-8")
    config = tmp_path / "mcp.toml"
    config.write_text(
        textwrap.dedent(
            f"""
            [servers.echoer]
            description = "Echo fake server"
            command = [{json.dumps(sys.executable)}, {json.dumps(str(script))}]
            timeout_seconds = 5
            """
        ),
        encoding="utf-8",
    )
    return ["--config", str(config), "--state", str(tmp_path / "mcp.json")]


def invoke(args, paths):
    try:
        main([*paths, *args])
    except SystemExit as exc:
        return exc.code
    return 0


def test_call_prints_redacted_text(capsys, paths):
    assert invoke(["call", "echoer", "echo", "--args", '{"text": "hello"}'], paths) == 0
    assert "echo:hello" in capsys.readouterr().out


def test_call_json_output(capsys, paths):
    assert (
        invoke(["--json", "call", "echoer", "echo", "--args", '{"text": "hi"}'], paths)
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data == {"text": "echo:hi", "is_error": False}


def test_call_invalid_args_exits_one(capsys, paths):
    assert invoke(["call", "echoer", "echo", "--args", "{}"], paths) == 1
    assert "invalid arguments" in capsys.readouterr().err


def test_call_malformed_args_json_exits_two(capsys, paths):
    assert invoke(["call", "echoer", "echo", "--args", "not json"], paths) == 2
    assert "--args must be a JSON object" in capsys.readouterr().err


def test_call_disabled_server_exits_one(capsys, paths):
    invoke(["disable", "echoer"], paths)
    assert invoke(["call", "echoer", "echo"], paths) == 1
    assert "disabled" in capsys.readouterr().err
