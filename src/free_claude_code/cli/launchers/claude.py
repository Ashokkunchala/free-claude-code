"""Installed Claude launcher: native command handling with an FCC connection."""

import os
from collections.abc import Sequence
from pathlib import Path

from free_claude_code.core.json_types import JsonValue
from free_claude_code.harnesses.claude import CLAUDE_BINARY_NAME, build_claude_proxy_env
from free_claude_code.harnesses.launch import PreparedLaunch
from free_claude_code.harnesses.resources import LaunchResources

from .runner import HarnessSpec, LaunchContext, launch_harness

STATUSLINE_ENV = "FCC_STATUSLINE"


def _scoped_settings(args: list[str], files: LaunchResources) -> str | None:
    """Build a scoped --settings file for statusline and project profile."""

    if "--settings" in args:
        return None
    payload: dict[str, JsonValue] = {}
    if os.environ.get(STATUSLINE_ENV, "1").strip().lower() not in {"0", "false", "off"}:
        payload["statusLine"] = {
            "type": "command",
            "command": "fcc-statusline",
        }
    if "--model" not in args:
        from free_claude_code.cli.profiles import model_for_directory

        model = model_for_directory(Path.cwd())
        if model:
            payload["model"] = model
    if not payload:
        return None
    return str(files.write_json("settings.json", payload))


def _configure(
    ctx: LaunchContext, args: list[str], files: LaunchResources
) -> PreparedLaunch:
    command = [ctx.binary_path]
    if (settings_path := _scoped_settings(args, files)) is not None:
        command += ["--settings", settings_path]
    command += args
    return PreparedLaunch(
        command,
        build_claude_proxy_env(
            proxy_root_url=ctx.proxy_root_url,
            auth_token=ctx.auth_token,
            base_env=ctx.base_env,
        ),
    )


SPEC = HarnessSpec(
    binary_name=CLAUDE_BINARY_NAME,
    display_name="Claude Code",
    install_hint="Install Claude Code with: npm install -g @anthropic-ai/claude-code",
    configure=_configure,
)


def launch(argv: Sequence[str] | None = None) -> None:
    launch_harness(SPEC, argv)
