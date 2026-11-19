"""``fcc-config``: show, validate and explain effective configuration.

Every value is annotated with its live source (process environment, the
managed env file, or the built-in default) and secret fields are masked in
all output formats, so the report answers "what is configured and why"
without ever printing a credential.
"""

import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from free_claude_code.config.env_files import (
    FCC_CONFIG_SCHEMA_ENV,
    dotenv_values_from_file,
)
from free_claude_code.config.env_migrations import CONFIG_SCHEMA_VERSION
from free_claude_code.config.loader import ConfigSource, compose_settings_snapshot
from free_claude_code.config.paths import managed_env_path
from free_claude_code.config.settings import Settings

_MASKED = "<redacted>"
_SECRET_MARKERS = ("key", "token", "secret", "password")


def _secret_fields() -> set[str]:
    return {
        name
        for name in Settings.model_fields
        if any(marker in name for marker in _SECRET_MARKERS)
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


def _field_report(
    snapshot_settings: Settings,
    sources: Mapping[str, ConfigSource],
) -> dict[str, dict[str, Any]]:
    secrets = _secret_fields()
    fields: dict[str, dict[str, Any]] = {}
    for name in Settings.model_fields:
        value = getattr(snapshot_settings, name)
        is_set = value is not None and value != "" and value != () and value != {}
        fields[name] = {
            "value": _MASKED if name in secrets and is_set else _jsonable(value),
            "source": str(sources.get(name, ConfigSource.DEFAULT)),
            "is_secret": name in secrets,
        }
    return fields


def _load_managed(path: Path) -> dict[str, str]:
    managed = dotenv_values_from_file(path)
    schema = managed.get(FCC_CONFIG_SCHEMA_ENV)
    if schema != CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"Managed config {path} uses unsupported schema {schema!r}; "
            f"this FCC version supports {CONFIG_SCHEMA_VERSION}."
        )
    return managed


def _snapshot(path: Path, env: Mapping[str, str]) -> Any:
    managed = _load_managed(path)
    return compose_settings_snapshot(managed, dict(env))


def _cmd_show(path: Path, as_json: bool) -> None:
    snapshot = _snapshot(path, os.environ)
    fields = _field_report(snapshot.settings, snapshot.sources)
    if as_json:
        print(
            json.dumps(
                {"config_file": str(path), "fields": fields},
                indent=2,
            )
        )
        return
    print(f"config file: {path}")
    for name, info in fields.items():
        value = info["value"]
        rendered = _MASKED if value == _MASKED else json.dumps(value)
        print(f"{name} = {rendered}  [{info['source']}]")


def _cmd_validate(path: Path) -> None:
    try:
        managed = _load_managed(path)
        # Validate the file's own values first: a valid process override
        # must not mask an invalid managed value from this report.
        compose_settings_snapshot(managed, {})
        snapshot = compose_settings_snapshot(managed, dict(os.environ))
    except (OSError, ValueError) as exc:
        print(f"invalid: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    count = sum(
        1 for source in snapshot.sources.values() if source != ConfigSource.DEFAULT
    )
    print(f"valid: {path} ({count} configured values, rest defaults)")


def _cmd_precedence(path: Path) -> None:
    snapshot = _snapshot(path, os.environ)
    print("precedence, highest first:")
    print("  1. process   - environment variables of the current process")
    print(f"  2. managed   - managed env file: {path}")
    print("  3. default   - built-in defaults")
    print()
    for name in Settings.model_fields:
        print(f"{name}: {snapshot.sources.get(name, ConfigSource.DEFAULT)}")


def main(argv: Sequence[str] | None = None) -> None:
    """CLI for inspecting effective configuration."""

    import argparse

    parser = argparse.ArgumentParser(
        prog="fcc-config",
        description="Show, validate and explain effective configuration.",
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=None,
        help="Override the managed env file path (testing and automation).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    show_parser = subparsers.add_parser(
        "show", help="Show effective values with their sources"
    )
    show_parser.add_argument("--json", action="store_true", help="Emit JSON")

    subparsers.add_parser("validate", help="Validate the managed config file")

    subparsers.add_parser(
        "precedence", help="Explain source precedence and per-field sources"
    )

    args = parser.parse_args(argv)
    path = args.config_file if args.config_file is not None else managed_env_path()

    if args.command == "show":
        _cmd_show(path, args.json)
    elif args.command == "validate":
        _cmd_validate(path)
    elif args.command == "precedence":
        _cmd_precedence(path)


__all__ = ["main"]
