"""Tests for the statusline/profile scoped ``--settings`` injection in fcc-claude."""

import json
from pathlib import Path

import pytest

from free_claude_code.cli import profiles
from tests.cli.conftest import LaunchCapture
from tests.cli.test_launcher_workflow import launch


def _settings_from_command(command: list[str]) -> dict:
    assert "--settings" in command
    index = command.index("--settings")
    path = Path(command[index + 1])
    return json.loads(path.read_text(encoding="utf-8"))


def test_statusline_is_injected_by_default(
    launch_capture: LaunchCapture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FCC_STATUSLINE", "1")
    captured: list[dict] = []
    launch_capture.on_start = lambda command, env: captured.append(
        _settings_from_command(command)
    )

    launch("claude", ["fix tests"])

    assert captured == [
        {"statusLine": {"type": "command", "command": "fcc-statusline"}}
    ]


def test_settings_pair_is_inserted_before_user_args(
    launch_capture: LaunchCapture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FCC_STATUSLINE", "1")

    launch("claude", ["--permission-mode", "auto", "fix tests"])

    command = launch_capture.commands[0]
    assert command[0] == "claude"
    assert command[1] == "--settings"
    assert command[3:] == ["--permission-mode", "auto", "fix tests"]


def test_statusline_opt_out_produces_no_settings(
    launch_capture: LaunchCapture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FCC_STATUSLINE", "0")

    launch("claude", ["fix tests"])

    assert launch_capture.commands == [["claude", "fix tests"]]


def test_project_profile_is_injected_as_model(
    launch_capture: LaunchCapture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FCC_STATUSLINE", "0")
    monkeypatch.setattr(
        profiles, "model_for_directory", lambda _directory: "nvidia_nim/fast"
    )
    captured: list[dict] = []
    launch_capture.on_start = lambda command, env: captured.append(
        _settings_from_command(command)
    )

    launch("claude", [])

    assert captured == [{"model": "nvidia_nim/fast"}]


def test_explicit_model_flag_skips_profile(
    launch_capture: LaunchCapture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FCC_STATUSLINE", "0")
    monkeypatch.setattr(
        profiles, "model_for_directory", lambda _directory: "nvidia_nim/fast"
    )

    launch("claude", ["--model", "sonnet"])

    assert launch_capture.commands == [["claude", "--model", "sonnet"]]


def test_user_settings_flag_is_respected(
    launch_capture: LaunchCapture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FCC_STATUSLINE", "1")
    monkeypatch.setattr(
        profiles, "model_for_directory", lambda _directory: "nvidia_nim/fast"
    )

    launch("claude", ["--settings", "./mine.json"])

    command = launch_capture.commands[0]
    assert command == ["claude", "--settings", "./mine.json"]


def test_statusline_and_profile_combine(
    launch_capture: LaunchCapture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FCC_STATUSLINE", "1")
    monkeypatch.setattr(
        profiles, "model_for_directory", lambda _directory: "nvidia_nim/fast"
    )
    captured: list[dict] = []
    launch_capture.on_start = lambda command, env: captured.append(
        _settings_from_command(command)
    )

    launch("claude", [])

    assert captured == [
        {
            "statusLine": {"type": "command", "command": "fcc-statusline"},
            "model": "nvidia_nim/fast",
        }
    ]
