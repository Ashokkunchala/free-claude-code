#!/usr/bin/env python3
"""Windows bootstrapper for the official Free Claude Code installer.

This script deliberately delegates package installation to scripts/install.ps1.
The PowerShell installer provisions the project's pinned Python runtime with uv;
the Python version used to launch this bootstrapper does not need to match it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


def find_repository(requested: Path | None = None) -> Path:
    """Find a checkout containing both project metadata and the official installer."""
    candidates: list[Path] = []
    if requested is not None:
        candidates.append(requested.expanduser())
    candidates.extend((Path.cwd(), Path(__file__).resolve().parent))

    visited: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        for directory in (resolved, *resolved.parents):
            if directory in visited:
                continue
            visited.add(directory)
            if (
                (directory / "pyproject.toml").is_file()
                and (directory / "scripts" / "install.ps1").is_file()
            ):
                return directory

    raise FileNotFoundError(
        "Could not find a Free Claude Code checkout. Clone "
        "https://github.com/Ashokkunchala/free-claude-code and run this "
        "script from that folder, or pass --repo <checkout-path>."
    )


def find_powershell() -> str:
    """Prefer Windows PowerShell for compatibility with the project's installer."""
    for name in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
        executable = shutil.which(name)
        if executable:
            return executable
    raise FileNotFoundError(
        "PowerShell was not found. Windows PowerShell 5.1 is included with "
        "supported Windows versions; otherwise install PowerShell 7."
    )


def validate_installer(powershell: str, installer: Path) -> None:
    """Parse the installer using an environment variable (safe for paths with spaces)."""
    env = os.environ.copy()
    env["FCC_INSTALLER_TO_VALIDATE"] = str(installer.resolve())
    command = (
        "$tokens = $null; $parseErrors = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "$env:FCC_INSTALLER_TO_VALIDATE, [ref]$tokens, [ref]$parseErrors) "
        "| Out-Null; "
        "if ($parseErrors.Count -gt 0) { "
        "$parseErrors | ForEach-Object { Write-Error $_ }; exit 1 }"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        details = (result.stdout + "\n" + result.stderr).strip()
        raise RuntimeError(
            "PowerShell could not parse scripts/install.ps1."
            + (f"\n{details}" if details else "")
        )


def build_installer_command(
    powershell: str,
    installer: Path,
    *,
    voice_local: bool = False,
    torch_backend: str | None = None,
    rtk: bool = False,
    dry_run: bool = False,
) -> list[str]:
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(installer.resolve()),
    ]
    if voice_local:
        command.append("-VoiceLocal")
    if torch_backend:
        if not voice_local:
            raise ValueError("--torch-backend requires --voice-local")
        command.extend(["-TorchBackend", torch_backend])
    if rtk:
        command.append("-Rtk")
    if dry_run:
        command.append("-DryRun")
    return command


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Automatically bootstrap Free Claude Code on Windows."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        help="Path to an existing Free Claude Code repository checkout.",
    )
    parser.add_argument(
        "--voice-local",
        action="store_true",
        help="Install optional local Whisper voice dependencies.",
    )
    parser.add_argument(
        "--torch-backend",
        help="PyTorch backend for local voice, for example cu130.",
    )
    parser.add_argument(
        "--rtk",
        action="store_true",
        help="Install and configure RTK.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the official installer's dry-run mode.",
    )
    args = parser.parse_args(argv)

    try:
        if args.torch_backend and not args.voice_local:
            parser.error("--torch-backend requires --voice-local")

        repo = find_repository(args.repo)
        installer = repo / "scripts" / "install.ps1"
        powershell = find_powershell()

        print("=" * 68)
        print(" FREE CLAUDE CODE - WINDOWS AUTO INSTALLER")
        print("=" * 68)
        print(f"Bootstrap Python : {sys.version.split()[0]}")
        print(f"Repository       : {repo}")
        print(f"PowerShell       : {powershell}")
        print(f"Installer        : {installer}")
        print(
            "\nThe installer uses uv to provision the Python version pinned "
            "by this repository. Your existing Python installation is not "
            "uninstalled or downgraded."
        )

        print("\n[1/2] Validating the official PowerShell installer...")
        validate_installer(powershell, installer)
        print("PowerShell syntax validation passed.")

        command = build_installer_command(
            powershell,
            installer,
            voice_local=args.voice_local,
            torch_backend=args.torch_backend,
            rtk=args.rtk,
            dry_run=args.dry_run,
        )

        print("\n[2/2] Starting the official installer...")
        # Inherit the console so interactive coding-agent selection works.
        result = subprocess.run(command, cwd=repo, check=False)
        if result.returncode:
            raise RuntimeError(
                f"Official installer exited with code {result.returncode}."
            )

        if args.dry_run:
            print("\nDry run completed; no installation changes were made.")
        else:
            print("\nFree Claude Code installer completed.")
            print("Open a NEW PowerShell window, then run:")
            print("  fcc-server --version")
            print("  fcc-doctor")
            print("  fcc-server")
        return 0

    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        print(f"\nINSTALLATION FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
