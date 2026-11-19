"""``fcc-skills``: list, inspect, validate, enable and disable skill packs.

Read-only except for the enable/disable state file: validating and
listing never execute pack code, and packs declaring executable code
stay quarantined unless they live under a trusted root.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from free_claude_code.skills import SkillPack, discover_packs, load_pack
from free_claude_code.skills.state import (
    SkillState,
    default_packs_dir,
    default_state_path,
)


def _summary(pack: SkillPack, state: SkillState | None) -> dict[str, Any]:
    manifest = pack.manifest
    return {
        "name": pack.name,
        "version": manifest.version if manifest else None,
        "purpose": manifest.purpose if manifest else None,
        "enabled": state.is_enabled(pack.name) if state is not None else None,
        "trusted": pack.trusted,
        "usable": pack.usable,
        "issues": list(pack.issues),
    }


def _print_packs(packs: list[SkillPack], state: SkillState, *, as_json: bool) -> None:
    summaries = [_summary(pack, state) for pack in packs]
    if as_json:
        print(json.dumps({"packs": summaries}, indent=2))
        return
    if not summaries:
        print("No skill packs found.")
        return
    for item in summaries:
        flags = []
        flags.append("enabled" if item["enabled"] else "disabled")
        if not item["usable"]:
            flags.append("quarantined")
        version = item["version"] or "?"
        print(f"{item['name']} {version} [{', '.join(flags)}]")
        if item["purpose"]:
            print(f"  {item['purpose']}")
        for issue in item["issues"]:
            print(f"  issue: {issue}")


def _find(packs: list[SkillPack], name: str) -> SkillPack | None:
    for pack in packs:
        if pack.name == name:
            return pack
    return None


def _inspect(pack: SkillPack, state: SkillState, *, as_json: bool) -> None:
    manifest = pack.manifest
    if as_json:
        detail = _summary(pack, state)
        detail["path"] = str(pack.path)
        if manifest is not None:
            detail.update(
                {
                    "platforms": list(manifest.platforms),
                    "required_tools": list(manifest.required_tools),
                    "max_risk": manifest.max_risk.name.lower(),
                    "approval_from": manifest.approval_from.name.lower(),
                    "timeout_seconds": manifest.timeout_seconds,
                    "rollback": manifest.rollback,
                    "verification": list(manifest.verification),
                    "executable": manifest.executable,
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "timeout_seconds": tool.timeout_seconds,
                            "max_output_bytes": tool.max_output_bytes,
                        }
                        for tool in manifest.tools
                    ],
                }
            )
        print(json.dumps(detail, indent=2))
        return
    print(f"== {pack.name} ==")
    print(f"path: {pack.path}")
    print(f"enabled: {state.is_enabled(pack.name)}")
    if manifest is not None:
        print(f"version: {manifest.version}")
        print(f"purpose: {manifest.purpose}")
        print(f"platforms: {', '.join(manifest.platforms)}")
        if manifest.required_tools:
            print(f"requires: {', '.join(manifest.required_tools)}")
        print(f"max risk: {manifest.max_risk.name.lower()}")
        print(f"approval from: {manifest.approval_from.name.lower()}")
        print(f"timeout: {manifest.timeout_seconds}s")
        if manifest.rollback:
            print(f"rollback: {manifest.rollback}")
        for step in manifest.verification:
            print(f"verify: {step}")
        print(f"executable code: {'yes' if manifest.executable else 'no'}")
        for tool in manifest.tools:
            print(f"tool: {tool.name} ({tool.timeout_seconds}s) - {tool.description}")
    for issue in pack.issues:
        print(f"issue: {issue}")
    if pack.instructions:
        print("--- instructions ---")
        print(pack.instructions.rstrip())


def _validate(paths: list[Path], trusted: tuple[Path, ...], *, as_json: bool) -> int:
    reports = []
    for path in paths:
        pack = load_pack(path, trusted)
        reports.append(_summary(pack, None))
    if as_json:
        print(json.dumps({"packs": reports}, indent=2))
    else:
        for report in reports:
            if report["issues"]:
                print(f"{report['name']}: INVALID")
                for issue in report["issues"]:
                    print(f"  {issue}")
            else:
                print(f"{report['name']}: ok")
    return 1 if any(report["issues"] for report in reports) else 0


def main(argv: Sequence[str] | None = None) -> None:
    """CLI for managing declarative skill packs."""

    parser = argparse.ArgumentParser(
        prog="fcc-skills",
        description="List, inspect, validate, enable and disable skill packs.",
    )
    parser.add_argument(
        "--packs",
        type=Path,
        default=None,
        help="Directory scanned for skill packs (default: user config skill-packs)",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=None,
        help="Enabled/disabled state file (default: user config skills.json)",
    )
    parser.add_argument(
        "--trusted-root",
        action="append",
        default=[],
        type=Path,
        help="Root under which executable packs may load (repeatable; "
        "default: the packs directory itself)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List discovered packs and their state")
    inspect_parser = subparsers.add_parser(
        "inspect", help="Show one pack's manifest, state and instructions"
    )
    inspect_parser.add_argument("name")
    validate_parser = subparsers.add_parser(
        "validate", help="Validate one pack directory, or every pack under --packs"
    )
    validate_parser.add_argument("path", nargs="?", type=Path, default=None)
    for verb in ("enable", "disable"):
        toggle = subparsers.add_parser(verb, help=f"{verb.capitalize()} one pack")
        toggle.add_argument("name")

    args = parser.parse_args(argv)
    packs_root = (args.packs or default_packs_dir()).resolve()
    trusted = (
        tuple(root.resolve() for root in args.trusted_root)
        if args.trusted_root
        else (packs_root,)
    )
    state = SkillState((args.state or default_state_path()).resolve())

    try:
        if args.command == "list":
            _print_packs(discover_packs(packs_root, trusted), state, as_json=args.json)
        elif args.command == "validate":
            if args.path is not None:
                paths = [args.path.resolve()]
            else:
                paths = (
                    [child for child in sorted(packs_root.iterdir()) if child.is_dir()]
                    if packs_root.is_dir()
                    else []
                )
            sys.exit(_validate(paths, trusted, as_json=args.json))
        else:
            packs = discover_packs(packs_root, trusted)
            pack = _find(packs, args.name)
            if pack is None:
                print(f"Unknown skill pack: {args.name}", file=sys.stderr)
                sys.exit(2)
            if args.command == "inspect":
                _inspect(pack, state, as_json=args.json)
            else:
                enabled = args.command == "enable"
                state.set_enabled(pack.name, enabled)
                if args.json:
                    print(json.dumps({"name": pack.name, "enabled": enabled}, indent=2))
                else:
                    print(f"{pack.name} {args.command}d")
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main(sys.argv[1:])
