"""fcc-mcp CLI: list, inspect, validate, enable and disable."""

import json

import pytest

from free_claude_code.cli.mcp import main

CONFIG = """
[servers.k8s-read]
description = "Local Kubernetes read access"
command = ["uvx", "mcp-k8s"]
env = ["KUBECONFIG"]

[servers.metrics]
description = "Hosted observability API"
transport = "http"
url = "https://mcp.example.com/sse"
timeout_seconds = 30
"""


@pytest.fixture
def paths(tmp_path):
    config = tmp_path / "mcp.toml"
    config.write_text(CONFIG, encoding="utf-8")
    return {"--config": config, "--state": tmp_path / "mcp.json"}


def run(args, paths, *extra):
    main([*[f"{key}={value}" for key, value in paths.items()], *extra, *args])


def test_list_text_and_json(capsys, paths):
    run(["list"], paths)
    out = capsys.readouterr().out
    assert "k8s-read [enabled, stdio]" in out
    assert "metrics [enabled, http]" in out
    run(["list"], paths, "--json")
    data = json.loads(capsys.readouterr().out)
    assert [s["name"] for s in data["servers"]] == ["k8s-read", "metrics"]


def test_list_without_config_is_empty(capsys, tmp_path):
    main(["--config", str(tmp_path / "none.toml"), "list"])
    assert "No MCP servers configured." in capsys.readouterr().out


def test_inspect_shows_configuration_and_verdict(capsys, paths):
    run(["inspect", "k8s-read"], paths)
    out = capsys.readouterr().out
    assert "command: uvx mcp-k8s" in out
    assert "env passthrough: KUBECONFIG" in out
    assert "allow:mcp-server" in out


def test_inspect_json(capsys, paths):
    run(["inspect", "metrics"], paths, "--json")
    data = json.loads(capsys.readouterr().out)
    assert data["transport"] == "http"
    assert data["url"] == "https://mcp.example.com/sse"
    assert data["allowed"] is True


def test_validate_ok_and_json(capsys, paths):
    run(["validate"], paths)
    assert "ok (2 servers)" in capsys.readouterr().out
    run(["validate"], paths, "--json")
    data = json.loads(capsys.readouterr().out)
    assert len(data["servers"]) == 2


def test_validate_reports_issues(capsys, tmp_path):
    config = tmp_path / "mcp.toml"
    config.write_text('[servers.bad]\ndescription = ""\n', encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        main(["--config", str(config), "validate"])
    assert caught.value.code == 1
    assert "INVALID" in capsys.readouterr().out


def test_validate_missing_config(capsys, tmp_path):
    with pytest.raises(SystemExit) as caught:
        main(["--config", str(tmp_path / "none.toml"), "validate"])
    assert caught.value.code == 1


def test_enable_disable_persist(capsys, paths):
    run(["disable", "k8s-read"], paths)
    assert "k8s-read disabled" in capsys.readouterr().out
    run(["inspect", "k8s-read"], paths, "--json")
    data = json.loads(capsys.readouterr().out)
    assert data["enabled"] is False
    assert data["allowed"] is False
    run(["enable", "k8s-read"], paths)
    assert "k8s-read enabled" in capsys.readouterr().out


def test_unknown_server_exits_2(capsys, paths):
    with pytest.raises(SystemExit) as caught:
        run(["inspect", "ghost"], paths)
    assert caught.value.code == 2
    assert "Unknown MCP server: ghost" in capsys.readouterr().err


def test_missing_config_exits_2(capsys, tmp_path):
    with pytest.raises(SystemExit) as caught:
        main(["--config", str(tmp_path / "none.toml"), "inspect", "x"])
    assert caught.value.code == 2
