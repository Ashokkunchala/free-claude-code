"""``fcc-audit``: inspect the terminal command audit log.

The executor writes one JSON line per command-lifecycle event (planned,
started, finished, timed_out, cancelled). This command lists recent events
and shows the full history of one task. Events never contain command output,
so the log stays shareable.
"""

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from free_claude_code.config.paths import audit_log_path

_DEFAULT_LIMIT = 50


def read_events(path: Path) -> list[dict[str, Any]]:
    """Parse the JSONL audit log, skipping corrupt lines."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _render(event: dict[str, Any]) -> str:
    argv = event.get("argv")
    command = " ".join(str(part) for part in argv) if isinstance(argv, list) else "?"
    detail = ""
    if event.get("exit_code") is not None:
        detail = f" exit={event['exit_code']}"
    if event.get("duration_seconds") is not None:
        detail += f" {float(event['duration_seconds']):.2f}s"
    return f"{event.get('task_id', '?')} {event.get('kind', '?')}: {command}{detail}"


def main(argv: Sequence[str] | None = None) -> None:
    """CLI for reading the audit log."""

    import argparse

    parser = argparse.ArgumentParser(
        prog="fcc-audit",
        description="Inspect the terminal command audit log.",
    )
    parser.add_argument(
        "--audit-file",
        type=Path,
        default=None,
        help="Override the audit log path (testing and automation).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List recent audit events")
    list_parser.add_argument(
        "--limit",
        type=int,
        default=_DEFAULT_LIMIT,
        help=f"Maximum events to print (default {_DEFAULT_LIMIT})",
    )
    list_parser.add_argument("--json", action="store_true", help="Emit JSON")

    show_parser = subparsers.add_parser("show", help="Show one task's events")
    show_parser.add_argument("task_id")
    show_parser.add_argument("--json", action="store_true", help="Emit JSON")

    args = parser.parse_args(argv)
    path = args.audit_file if args.audit_file is not None else audit_log_path()
    events = read_events(path)

    if args.command == "list":
        selected = events[-args.limit :] if args.limit > 0 else events
        if args.json:
            print(json.dumps(selected, indent=2))
        elif selected:
            for event in selected:
                print(_render(event))
        else:
            print(f"no audit events in {path}")
    elif args.command == "show":
        selected = [event for event in events if event.get("task_id") == args.task_id]
        if not selected:
            print(f"no audit events for task {args.task_id}", file=sys.stderr)
            raise SystemExit(1)
        if args.json:
            print(json.dumps(selected, indent=2))
        else:
            for event in selected:
                print(_render(event))


__all__ = ["main", "read_events"]
