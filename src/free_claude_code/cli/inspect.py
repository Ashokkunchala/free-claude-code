"""``fcc-inspect``: read-only repository inspection.

Status, file reads, glob search and content grep, all confined to the
workspace root and all available as JSON for scripting. Everything here is
R0 (read-only) by construction: this command never writes.
"""

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from free_claude_code.repo import (
    RepoFileError,
    read_file,
    search_content,
    search_files,
)
from free_claude_code.repo.git import GitError, GitRepo

_DEFAULT_GREP_LIMIT = 100


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
