from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from install_fcc import build_installer_command, validate_installer


def test_installer_command_preserves_paths_with_spaces(tmp_path: Path) -> None:
    installer = tmp_path / "folder with spaces" / "install.ps1"
    command = build_installer_command(
        "powershell.exe",
        installer,
        voice_local=True,
        torch_backend="cu130",
        rtk=True,
        dry_run=True,
    )

    assert command == [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(installer.resolve()),
        "-VoiceLocal",
        "-TorchBackend",
        "cu130",
        "-Rtk",
        "-DryRun",
    ]


def test_torch_backend_requires_local_voice(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires --voice-local"):
        build_installer_command(
            "powershell.exe",
            tmp_path / "install.ps1",
            torch_backend="cu130",
        )


def test_validate_installer_passes_path_through_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = tmp_path / "folder with spaces" / "install.ps1"
    installer.parent.mkdir()
    installer.write_text("Write-Output 'ok'\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        *,
        env: dict[str, str],
        text: bool,
        capture_output: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["env"] = env
        captured["text"] = text
        captured["capture_output"] = capture_output
        captured["check"] = check
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    validate_installer("powershell.exe", installer)

    assert captured["env"]["FCC_INSTALLER_TO_VALIDATE"] == str(installer.resolve())
    assert "$env:FCC_INSTALLER_TO_VALIDATE" in captured["command"][-1]
    assert captured["check"] is False
