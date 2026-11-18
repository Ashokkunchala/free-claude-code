"""Per-project default model profiles persisted in the user config directory.

A profile remembers which ``provider/model`` a client should request when it is
launched from a given directory. Launchers read the profile for the working
directory and pass it through Claude Code's scoped ``--settings`` file, so the
choice never leaks into the user's global configuration or into other projects.
"""

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from free_claude_code.config.paths import profiles_path

_VERSION = 1


def model_for_directory(directory: str | Path) -> str:
    """Return the profiled model for a directory, or an empty string."""

    key = _key_for(directory)
    document = load()
    entry = document.get("projects", {}).get(key)
    if not isinstance(entry, Mapping):
        return ""
    model = entry.get("model")
    return model.strip() if isinstance(model, str) else ""


def set_model(model: str, directory: str | Path) -> None:
    """Persist a model profile for a directory."""

    normalized = model.strip()
    if not normalized:
        raise ValueError("Model profile cannot be empty.")
    document = load()
    projects = document.setdefault("projects", {})
    projects[_key_for(directory)] = {"model": normalized}
    _write(document)


def clear(directory: str | Path) -> bool:
    """Drop the profile for a directory; report whether one existed."""

    document = load()
    projects = document.get("projects", {})
    if _key_for(directory) not in projects:
        return False
    del projects[_key_for(directory)]
    _write(document)
    return True


def list_profiles() -> dict[str, str]:
    """Return every stored profile keyed by directory."""

    document = load()
    projects = document.get("projects", {})
    if not isinstance(projects, Mapping):
        return {}
    result: dict[str, str] = {}
    for key, entry in projects.items():
        if isinstance(entry, Mapping):
            model = entry.get("model")
            if isinstance(model, str):
                result[str(key)] = model
    return result


def load() -> dict[str, Any]:
    """Return the persisted profiles document, or a fresh one."""

    path = profiles_path()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return _empty()
    if not isinstance(document, dict) or document.get("version") != _VERSION:
        return _empty()
    return document


def _empty() -> dict[str, Any]:
    return {"version": _VERSION, "projects": {}}


def _key_for(directory: str | Path) -> str:
    return os.path.normcase(str(Path(directory).resolve()))


def _write(document: dict[str, Any]) -> None:
    path = profiles_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=".profiles-", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(document, output, ensure_ascii=True, indent=2)
                output.write("\n")
            os.replace(temp_name, path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise ValueError(f"Could not write profiles to {path}: {exc}") from None


def main(argv: list[str] | None = None) -> None:
    """CLI for managing per-project model profiles."""

    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="fcc-profile",
        description="Manage per-project model profiles used by FCC launchers.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    set_parser = subparsers.add_parser("set", help="Set the model for a directory")
    set_parser.add_argument(
        "model", help="Provider/model reference, e.g. nvidia_nim/foo"
    )
    set_parser.add_argument(
        "directory", nargs="?", default=".", help="Project directory"
    )

    show_parser = subparsers.add_parser("show", help="Show the model for a directory")
    show_parser.add_argument(
        "directory", nargs="?", default=".", help="Project directory"
    )

    clear_parser = subparsers.add_parser(
        "clear", help="Clear the profile for a directory"
    )
    clear_parser.add_argument(
        "directory", nargs="?", default=".", help="Project directory"
    )

    subparsers.add_parser("list", help="List every stored profile")

    args = parser.parse_args(argv)

    try:
        if args.command == "set":
            set_model(args.model, args.directory)
            print(f"{args.directory}: {args.model}")
        elif args.command == "show":
            model = model_for_directory(args.directory)
            if model:
                print(model)
            else:
                print(f"{args.directory}: (no profile)", file=sys.stderr)
                raise SystemExit(1)
        elif args.command == "clear":
            if clear(args.directory):
                print(f"{args.directory}: cleared")
            else:
                print(f"{args.directory}: (no profile)", file=sys.stderr)
                raise SystemExit(1)
        else:
            for directory, model in sorted(list_profiles().items()):
                print(f"{directory}: {model}")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None


__all__ = [
    "clear",
    "list_profiles",
    "load",
    "main",
    "model_for_directory",
    "set_model",
]
