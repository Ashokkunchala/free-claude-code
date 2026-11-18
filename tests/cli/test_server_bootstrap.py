"""Tests for best-effort fcc-server auto-start used by client launchers."""

import pytest

from free_claude_code.cli import server_bootstrap


def test_autostart_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(server_bootstrap.AUTOSTART_ENV, raising=False)

    assert server_bootstrap.autostart_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "OFF", " off "])
def test_autostart_opt_out_values(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(server_bootstrap.AUTOSTART_ENV, value)

    assert server_bootstrap.autostart_enabled() is False


def test_ensure_returns_none_when_already_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "free_claude_code.cli.launchers.common.preflight_proxy", lambda _url: None
    )
    spawned: list[bool] = []
    monkeypatch.setattr(
        server_bootstrap, "_spawn_detached_server", lambda: spawned.append(True) or True
    )

    result = server_bootstrap.ensure_server_running("http://127.0.0.1:8182")

    assert result is None
    assert spawned == []


def test_ensure_skips_spawn_when_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An HTTP error means something owns the port; a second server cannot help."""

    monkeypatch.setattr(
        "free_claude_code.cli.launchers.common.preflight_proxy",
        lambda _url: "returned HTTP 404",
    )
    spawned: list[bool] = []
    monkeypatch.setattr(
        server_bootstrap, "_spawn_detached_server", lambda: spawned.append(True) or True
    )

    result = server_bootstrap.ensure_server_running("http://127.0.0.1:8182")

    assert result == "returned HTTP 404"
    assert spawned == []


def test_ensure_skips_spawn_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(server_bootstrap.AUTOSTART_ENV, "0")
    monkeypatch.setattr(
        "free_claude_code.cli.launchers.common.preflight_proxy",
        lambda _url: "connection refused",
    )
    spawned: list[bool] = []
    monkeypatch.setattr(
        server_bootstrap, "_spawn_detached_server", lambda: spawned.append(True) or True
    )

    result = server_bootstrap.ensure_server_running("http://127.0.0.1:8182")

    assert result == "connection refused"
    assert spawned == []


def test_ensure_spawns_and_waits_until_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"preflight": 0, "sleep": 0}

    def preflight(_url: str) -> str | None:
        calls["preflight"] += 1
        if calls["preflight"] == 1:
            return "connection refused"
        if calls["preflight"] < 4:
            return "connection refused"
        return None

    monkeypatch.setattr(
        "free_claude_code.cli.launchers.common.preflight_proxy", preflight
    )
    monkeypatch.setattr(server_bootstrap, "_spawn_detached_server", lambda: True)
    monkeypatch.setattr(
        server_bootstrap.time,
        "sleep",
        lambda _s: calls.__setitem__("sleep", calls["sleep"] + 1),
    )
    announced: list[str] = []

    result = server_bootstrap.ensure_server_running(
        "http://127.0.0.1:8182", announce=announced.append
    )

    assert result is None
    assert calls["preflight"] >= 4
    assert any("starting fcc-server" in message for message in announced)
    assert any("proxy is ready" in message for message in announced)


def test_ensure_returns_error_when_spawn_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "free_claude_code.cli.launchers.common.preflight_proxy",
        lambda _url: "connection refused",
    )
    monkeypatch.setattr(server_bootstrap, "_spawn_detached_server", lambda: False)

    result = server_bootstrap.ensure_server_running("http://127.0.0.1:8182")

    assert result == "connection refused"


def test_ensure_returns_error_when_server_never_becomes_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "free_claude_code.cli.launchers.common.preflight_proxy",
        lambda _url: "connection refused",
    )
    monkeypatch.setattr(server_bootstrap, "_spawn_detached_server", lambda: True)
    monkeypatch.setattr(server_bootstrap, "AUTOSTART_TIMEOUT_SECONDS", 0.0)

    result = server_bootstrap.ensure_server_running("http://127.0.0.1:8182")

    assert result == "connection refused"


def test_spawn_reports_false_when_no_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_bootstrap.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        server_bootstrap,
        "_server_command",
        lambda: None,
    )

    assert server_bootstrap._spawn_detached_server() is False


def test_server_command_prefers_installed_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server_bootstrap.shutil, "which", lambda name: "/usr/bin/fcc-server"
    )

    assert server_bootstrap._server_command() == ["/usr/bin/fcc-server"]


def test_server_command_falls_back_to_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_bootstrap.shutil, "which", lambda _name: None)

    command = server_bootstrap._server_command()

    assert command is not None
    assert command[1] == "-c"
    assert "serve" in command[2]


def test_ensure_surfaces_preflight_error_after_failed_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto-start must not swallow the reason the proxy is unreachable."""

    monkeypatch.setattr(
        "free_claude_code.cli.launchers.common.preflight_proxy",
        lambda _url: "connection refused",
    )
    monkeypatch.setattr(server_bootstrap, "_spawn_detached_server", lambda: True)
    monkeypatch.setattr(server_bootstrap, "AUTOSTART_TIMEOUT_SECONDS", 0.0)

    assert (
        server_bootstrap.ensure_server_running("http://127.0.0.1:8182")
        == "connection refused"
    )
