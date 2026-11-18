"""``fcc-approvals``: review, approve and deny pending privileged actions.

The agent loop records a pending request when policy requires approval. This
command lets the operator list pending requests, inspect one, approve it
(minting the bound token into the store) or deny it. ``--yes`` style bypasses
do not exist here by design: every grant is an explicit per-action decision.
"""

import json
import os
import secrets
import sys
from collections.abc import Sequence
from pathlib import Path

from free_claude_code.config.paths import approval_secret_path, approvals_path
from free_claude_code.terminal.approval import ApprovalTokenIssuer
from free_claude_code.terminal.approval_store import (
    STATUS_PENDING,
    ApprovalRecord,
    FileApprovalStore,
)

_SECRET_BYTES = 32


def load_or_create_secret(path: Path | None = None) -> bytes:
    """Read the signing secret, generating it privately on first use."""

    secret_path = path if path is not None else approval_secret_path()
    try:
        data = secret_path.read_bytes()
        if data:
            return data
    except OSError:
        pass
    data = secrets.token_bytes(_SECRET_BYTES)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)
    return data


def _store(path: Path | None = None) -> FileApprovalStore:
    return FileApprovalStore(path if path is not None else approvals_path())


def _render(record: ApprovalRecord) -> str:
    command = " ".join(record.argv)
    cwd = f" (cwd: {record.cwd})" if record.cwd else ""
    return (
        f"{record.approval_id} [{record.status}] {record.level} "
        f"{command}{cwd}\n    {record.reason}"
    )


def main(argv: Sequence[str] | None = None) -> None:
    """CLI for managing approval requests."""

    import argparse

    parser = argparse.ArgumentParser(
        prog="fcc-approvals",
        description="Review and decide pending privileged-action approvals.",
    )
    parser.add_argument(
        "--approvals-file",
        type=Path,
        default=None,
        help="Override the approval store path (testing and automation).",
    )
    parser.add_argument(
        "--secret-file",
        type=Path,
        default=None,
        help="Override the approval signing secret path.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List pending requests")
    list_parser.add_argument(
        "--all", action="store_true", help="Include decided requests"
    )
    list_parser.add_argument("--json", action="store_true", help="Emit JSON")

    show_parser = subparsers.add_parser("show", help="Show one request")
    show_parser.add_argument("approval_id")
    show_parser.add_argument("--json", action="store_true", help="Emit JSON")

    approve_parser = subparsers.add_parser("approve", help="Approve a request")
    approve_parser.add_argument("approval_id")
    approve_parser.add_argument(
        "--ttl",
        type=float,
        default=300.0,
        help="Seconds the granted token stays valid (default 300)",
    )

    deny_parser = subparsers.add_parser("deny", help="Deny a request")
    deny_parser.add_argument("approval_id")
    deny_parser.add_argument("--reason", default=None, help="Why it was denied")

    args = parser.parse_args(argv)
    store = _store(args.approvals_file)

    try:
        if args.command == "list":
            records = store.list_records(include_decided=args.all)
            if args.json:
                print(json.dumps([record.as_dict() for record in records], indent=2))
            elif records:
                for record in records:
                    print(_render(record))
            else:
                print("no pending approvals")
        elif args.command == "show":
            record = store.get(args.approval_id)
            if args.json:
                print(json.dumps(record.as_dict(), indent=2))
            else:
                print(_render(record))
        elif args.command == "approve":
            record = store.get(args.approval_id)
            if record.status != STATUS_PENDING:
                print(
                    f"approval {args.approval_id} is {record.status}",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            issuer = ApprovalTokenIssuer(load_or_create_secret(args.secret_file))
            token = issuer.mint(
                record.spec(),
                record.task_id,
                ttl_seconds=args.ttl,
                note=record.policy_id,
            )
            decided = store.decide(args.approval_id, approve=True, token=token)
            print(
                f"approved {decided.approval_id}: {' '.join(decided.argv)} "
                f"(token valid {args.ttl:g}s)"
            )
        elif args.command == "deny":
            decided = store.decide(
                args.approval_id, approve=False, decision_reason=args.reason
            )
            print(f"denied {decided.approval_id}: {' '.join(decided.argv)}")
    except KeyError as exc:
        print(str(exc).strip("'"), file=sys.stderr)
        raise SystemExit(1) from None
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None


__all__ = ["load_or_create_secret", "main"]
