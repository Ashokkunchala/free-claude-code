"""``fcc-inspect``: read-only repository and system inspection.

Status, file reads, glob search and content grep, all confined to the
workspace root, plus running processes and listening ports, all available
as JSON for scripting. Everything here is R0 (read-only) by construction:
this command never writes.
"""

import dataclasses
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from free_claude_code.config.paths import runs_dir_path
from free_claude_code.repo import (
    RepoFileError,
    read_file,
    search_content,
    search_files,
)
from free_claude_code.repo.git import GitError, GitRepo
from free_claude_code.terminal import (
    SystemInspectError,
    collect_env_context,
    collect_error_logs,
    collect_listeners,
    collect_processes,
    collect_services,
    load_run_history,
    render_env_context,
    render_listeners,
    render_processes,
    render_run_summary,
    render_services,
)

_DEFAULT_GREP_LIMIT = 100
_DEFAULT_PROCESS_LIMIT = 50
_DEFAULT_SERVICE_LIMIT = 50
_DEFAULT_LOG_LINES = 50
_MAX_LOG_LINES = 500


def _git_section(root: Path) -> dict[str, Any]:
    try:
        status = GitRepo(root).status()
    except RepoFileError as exc:
        return {"error": str(exc)}
    except GitError:
        return {"repository": False}
    return {
        "repository": True,
        "branch": status.branch,
        "dirty": status.dirty,
        "porcelain": status.porcelain,
    }


def main(argv: Sequence[str] | None = None) -> None:
    """CLI for read-only repository inspection."""

    import argparse

    parser = argparse.ArgumentParser(
        prog="fcc-inspect",
        description="Read-only repository inspection (status, files, grep).",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Workspace root (default: cwd)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Git state and workspace summary")

    subparsers.add_parser("env", help="Platform, shell, git and workspace listing")

    processes_parser = subparsers.add_parser(
        "processes", help="Running processes (pid, memory, command)"
    )
    processes_parser.add_argument(
        "--limit",
        type=int,
        default=_DEFAULT_PROCESS_LIMIT,
        help=f"Maximum processes shown (default {_DEFAULT_PROCESS_LIMIT})",
    )

    subparsers.add_parser("ports", help="Listening TCP/UDP sockets")

    services_parser = subparsers.add_parser(
        "services", help="System services (failures first)"
    )
    services_parser.add_argument(
        "--limit",
        type=int,
        default=_DEFAULT_SERVICE_LIMIT,
        help=f"Maximum services shown (default {_DEFAULT_SERVICE_LIMIT})",
    )

    logs_parser = subparsers.add_parser(
        "logs", help="Newest system error/warning log lines"
    )
    logs_parser.add_argument(
        "--lines",
        type=int,
        default=_DEFAULT_LOG_LINES,
        help=f"Log lines to fetch (default {_DEFAULT_LOG_LINES}, max {_MAX_LOG_LINES})",
    )

    runs_parser = subparsers.add_parser("runs", help="Recorded run reports")
    runs_parser.add_argument(
        "task_id",
        nargs="?",
        default=None,
        help="Show one task's full history (default: list all recorded tasks)",
    )
    runs_parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="Run report directory (default: config runs dir)",
    )

    read_parser = subparsers.add_parser("read", help="Read one file")
    read_parser.add_argument("path")
    read_parser.add_argument(
        "--max-bytes",
        type=int,
        default=None,
        help="Override the read size cap",
    )

    files_parser = subparsers.add_parser("files", help="Find files by glob")
    files_parser.add_argument("glob")

    grep_parser = subparsers.add_parser("grep", help="Regex-search file contents")
    grep_parser.add_argument("pattern")
    grep_parser.add_argument("--glob", default=None, help="Limit to matching files")
    grep_parser.add_argument(
        "--limit",
        type=int,
        default=_DEFAULT_GREP_LIMIT,
        help=f"Maximum matches (default {_DEFAULT_GREP_LIMIT})",
    )

    args = parser.parse_args(argv)
    root = args.workspace.resolve()

    if args.command == "env":
        context = collect_env_context(str(root))
        if args.json:
            print(
                json.dumps(
                    {
                        "os": context.os_name,
                        "arch": context.arch,
                        "python": context.python_version,
                        "shell": context.shell,
                        "git_branch": context.git_branch,
                        "git_dirty": list(context.git_dirty),
                        "top_level": list(context.top_level),
                    },
                    indent=2,
                )
            )
        else:
            print(render_env_context(context))
        return

    if args.command == "processes":
        try:
            processes = collect_processes()
        except SystemInspectError as exc:
            print(f"fcc-inspect: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        processes.sort(key=lambda p: (p.rss_bytes is None, -(p.rss_bytes or 0)))
        shown = processes[: args.limit]
        if args.json:
            print(
                json.dumps(
                    {
                        "processes": [dataclasses.asdict(p) for p in shown],
                        "total": len(processes),
                    },
                    indent=2,
                )
            )
        else:
            print(render_processes(shown, total=len(processes)))
        return

    if args.command == "services":
        try:
            services = collect_services()
        except SystemInspectError as exc:
            print(f"fcc-inspect: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        shown_services = services[: args.limit]
        if args.json:
            print(
                json.dumps(
                    {
                        "services": [
                            dataclasses.asdict(service) for service in shown_services
                        ],
                        "total": len(services),
                    },
                    indent=2,
                )
            )
        else:
            print(render_services(shown_services, total=len(services)))
        return

    if args.command == "logs":
        lines_wanted = min(args.lines, _MAX_LOG_LINES)
        try:
            log_lines = collect_error_logs(lines=lines_wanted)
        except SystemInspectError as exc:
            print(f"fcc-inspect: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        if args.json:
            print(json.dumps({"lines": log_lines}, indent=2))
        else:
            for line in log_lines:
                print(line)
        return

    if args.command == "ports":
        try:
            listeners = collect_listeners()
        except SystemInspectError as exc:
            print(f"fcc-inspect: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        listeners.sort(key=lambda listener: (listener.protocol, listener.port))
        if args.json:
            print(
                json.dumps(
                    [dataclasses.asdict(listener) for listener in listeners],
                    indent=2,
                )
            )
        else:
            print(render_listeners(listeners))
        return

    if args.command == "runs":
        runs_dir = args.runs_dir if args.runs_dir is not None else runs_dir_path()
        if args.task_id is not None:
            history = load_run_history(runs_dir, args.task_id)
            if history is None:
                print(
                    f"fcc-inspect: no recorded run for task {args.task_id}",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            if args.json:
                print(json.dumps(history, indent=2, sort_keys=True))
            else:
                print(render_run_summary(history))
            return
        records = []
        if runs_dir.is_dir():
            for path in sorted(runs_dir.glob("*.json")):
                history = load_run_history(runs_dir, path.stem)
                if history is None:
                    continue
                attempts = history["attempts"]
                records.append(
                    {
                        "task_id": history.get("task_id", path.stem),
                        "goal": history.get("goal", ""),
                        "attempts": len(attempts),
                        "last_status": attempts[-1].get("status", "?")
                        if attempts
                        else "?",
                    }
                )
        if args.json:
            print(json.dumps(records, indent=2))
        else:
            if not records:
                print("no recorded runs")
            for record in records:
                print(
                    f"{record['task_id']}: {record['last_status']} "
                    f"({record['attempts']} attempt(s)) - {record['goal']}"
                )
        return

    try:
        if args.command == "status":
            section = _git_section(root)
            if args.json:
                print(json.dumps({"root": str(root), "git": section}, indent=2))
            else:
                print(f"root: {root}")
                if section.get("repository"):
                    dirty = "dirty" if section["dirty"] else "clean"
                    print(f"git: {section['branch']} ({dirty})")
                    if section["porcelain"]:
                        print(section["porcelain"], end="")
                else:
                    print("git: not a repository")
        elif args.command == "read":
            kwargs: dict[str, int] = (
                {"max_bytes": args.max_bytes} if args.max_bytes is not None else {}
            )
            result = read_file(root, args.path, **kwargs)
            if args.json:
                print(
                    json.dumps(
                        {
                            "path": result.path,
                            "size_bytes": result.size_bytes,
                            "truncated": result.truncated,
                            "content": result.content,
                        },
                        indent=2,
                    )
                )
            else:
                print(result.content, end="")
                if result.truncated:
                    print(
                        f"\n[fcc-inspect: truncated at {len(result.content)} of "
                        f"{result.size_bytes} bytes]",
                        file=sys.stderr,
                    )
        elif args.command == "files":
            found = search_files(root, args.glob)
            if args.json:
                print(json.dumps(found, indent=2))
            else:
                for rel in found:
                    print(rel)
        elif args.command == "grep":
            result = search_content(
                root, args.pattern, glob=args.glob, max_results=args.limit
            )
            if args.json:
                print(
                    json.dumps(
                        {
                            "matches": [
                                {
                                    "path": match.path,
                                    "line_number": match.line_number,
                                    "line": match.line,
                                }
                                for match in result.matches
                            ],
                            "truncated": result.truncated,
                            "files_searched": result.files_searched,
                            "files_skipped": result.files_skipped,
                        },
                        indent=2,
                    )
                )
            else:
                for match in result.matches:
                    print(f"{match.path}:{match.line_number}: {match.line}")
                if result.truncated:
                    print(
                        f"[fcc-inspect: stopped at {args.limit} matches]",
                        file=sys.stderr,
                    )
    except RepoFileError as exc:
        print(f"fcc-inspect: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except OSError as exc:
        print(f"fcc-inspect: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


__all__ = ["main"]
