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
from free_claude_code.terminal.redaction import Redactor, default_redactor

_DEFAULT_LIMIT = 50
_EXPORT_FORMATS = ("json", "jsonl", "text")


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


def _redact_value(value: Any, redactor: Redactor) -> Any:
    """Redact every string in an audit event, recursively."""

    if isinstance(value, str):
        return redactor.redact_text(value)
    if isinstance(value, list):
        return [_redact_value(item, redactor) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item, redactor) for key, item in value.items()}
    return value


def export_events(
    events: list[dict[str, Any]],
    export_format: str,
    redactor: Redactor | None = None,
) -> str:
    """Render redacted events as a shareable report in the given format."""

    active = redactor if redactor is not None else default_redactor()
    redacted = [_redact_value(event, active) for event in events]
    if export_format == "json":
        return json.dumps(redacted, indent=2) + "\n"
    if export_format == "jsonl":
        return "".join(json.dumps(event) + "\n" for event in redacted)
    if export_format == "text":
        return "".join(_render(event) + "\n" for event in redacted)
    raise ValueError(f"unknown export format: {export_format}")


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

    export_parser = subparsers.add_parser(
        "export", help="Export a redacted, shareable audit report"
    )
    export_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the report to this file instead of stdout",
    )
    export_parser.add_argument(
        "--format",
        choices=_EXPORT_FORMATS,
        default="json",
        help="Report format (default json)",
    )
    export_parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only export the most recent N events (default all)",
    )
    export_parser.add_argument(
        "--task-id",
        default=None,
        help="Only export this task's events",
    )

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
    elif args.command == "export":
        selected = events
        if args.task_id is not None:
            selected = [
                event for event in selected if event.get("task_id") == args.task_id
            ]
        if args.limit > 0:
            selected = selected[-args.limit :]
        report = export_events(selected, args.format)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report, encoding="utf-8")
        else:
            print(report, end="")


__all__ = ["export_events", "main", "read_events"]
