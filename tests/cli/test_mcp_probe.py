"""fcc-mcp probe and tools subcommands against in-process fake servers."""

import json
import sys
import textwrap

import pytest

from free_claude_code.cli.mcp import main

FAKE_SERVER = textwrap.dedent(
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
            result = {"tools": [{"name": "echo", "description": "Echo tool"}]}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
        sys.stdout.flush()
    """
)

DYING_SERVER = "import sys\nsys.exit(1)\n"


@pytest.fixture
def paths(tmp_path):
    good = tmp_path / "good_server.py"
    good.write_text(FAKE_SERVER, encoding="utf-8")
    bad = tmp_path / "bad_server.py"
    bad.write_text(DYING_SERVER, encoding="utf-8")
    config = tmp_path / "mcp.toml"
    config.write_text(
        textwrap.dedent(
            f"""
            [servers.good]
            description = "Healthy fake server"
            command = [{json.dumps(sys.executable)}, {json.dumps(str(good))}]
            timeout_seconds = 5

            [servers.bad]
            description = "Broken fake server"
            command = [{json.dumps(sys.executable)}, {json.dumps(str(bad))}]
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


def test_probe_healthy_server(capsys, paths):
    assert invoke(["probe", "good"], paths) == 0
    out = capsys.readouterr().out
    assert "good: ok" in out
    assert "echo" in out


def test_probe_healthy_server_json(capsys, paths):
    assert invoke(["--json", "probe", "good"], paths) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["tools"] == ["echo"]
    assert data["duration_seconds"] > 0


def test_probe_broken_server_exits_one(capsys, paths):
    assert invoke(["probe", "bad"], paths) == 1
    assert "bad: FAIL" in capsys.readouterr().out


def test_probe_disabled_server_is_denied(capsys, paths):
    invoke(["disable", "good"], paths)
    assert invoke(["probe", "good"], paths) == 1
    out = capsys.readouterr().out
    assert "disabled" in out


def test_tools_lists_advertised_tools(capsys, paths):
    assert invoke(["tools", "good"], paths) == 0
    out = capsys.readouterr().out
    assert "echo" in out
    assert "Echo tool" in out


def test_tools_json(capsys, paths):
    main([*paths, "--json", "tools", "good"])
    data = json.loads(capsys.readouterr().out)
    assert data["tools"] == [{"name": "echo", "description": "Echo tool"}]


def test_tools_disabled_server_exits_one(capsys, paths):
    invoke(["disable", "good"], paths)
    assert invoke(["tools", "good"], paths) == 1
    assert "disabled" in capsys.readouterr().err
