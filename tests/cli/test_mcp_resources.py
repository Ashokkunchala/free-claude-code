"""fcc-mcp resources/read/prompts/prompt subcommands against a fake server."""

import json
import sys
import textwrap

import pytest

from free_claude_code.cli.mcp import main
from tests.mcp.test_client import make_server  # noqa: F401  (fixture naming parity)
from tests.mcp.test_resources import RP_SERVER


@pytest.fixture
def paths(tmp_path):
    script = tmp_path / "rp_server.py"
    script.write_text(RP_SERVER, encoding="utf-8")
    config = tmp_path / "mcp.toml"
    config.write_text(
        textwrap.dedent(
            f"""
            [servers.rp]
            description = "Fake resources server"
            command = [{json.dumps(sys.executable)}, {json.dumps(str(script))}]
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


def test_resources_lists_advertised(capsys, paths):
    assert invoke([*paths, "resources", "rp"]) in (0, None)
    out = capsys.readouterr().out
    assert "mem://status" in out and "status" in out


def test_read_prints_redacted_text(capsys, paths):
    assert invoke([*paths, "read", "rp", "mem://leak"]) in (0, None)
    out = capsys.readouterr().out
    assert "sk-abcdefghij" not in out
    assert "<redacted>" in out


def test_prompts_lists_advertised(capsys, paths):
    assert invoke([*paths, "prompts", "rp"]) in (0, None)
    assert "summarize" in capsys.readouterr().out


def test_prompt_renders_json(capsys, paths):
    assert invoke(
        [*paths, "--json", "prompt", "rp", "summarize", "--args", '{"topic": "ci"}']
    ) in (0, None)
    data = json.loads(capsys.readouterr().out)
    assert data == {"text": "summarize ci", "description": "Summarize a topic"}


def test_prompt_unknown_exits_one(capsys, paths):
    assert invoke([*paths, "prompt", "rp", "nope"]) == 1
    assert "no prompt" in capsys.readouterr().err


def test_resources_disabled_server_exits_one(capsys, paths):
    invoke([*paths, "disable", "rp"])
    assert invoke([*paths, "resources", "rp"]) == 1
    assert "disabled" in capsys.readouterr().err
