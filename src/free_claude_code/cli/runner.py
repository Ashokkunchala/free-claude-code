"""``fcc-exec``: run one command through policy enforcement and audit.

Every invocation classifies the command, records the decision, and only runs
what policy allows. R2/R3 commands need a matching approved request in the
approval store: the first run creates the request and exits with
``EXIT_APPROVAL_REQUIRED``; after ``fcc-approvals approve <id>``, re-running
with the same ``--task-id`` verifies the bound token and executes.
"""

import json
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

from free_claude_code.cli.approvals import load_or_create_secret
from free_claude_code.config.paths import (
    approval_secret_path,
    approvals_path,
    audit_log_path,
)
from free_claude_code.terminal import (
    CommandResult,
    CommandSpec,
    JsonlAuditSink,
    PolicyConfig,
    PolicyEngine,
    PolicyVerdict,
    Requirement,
    TerminalExecutor,
)
from free_claude_code.terminal.approval import ApprovalTokenIssuer
from free_claude_code.terminal.approval_store import (
    STATUS_APPROVED,
    STATUS_DENIED,
    STATUS_PENDING,
    ApprovalRecord,
    FileApprovalStore,
)

EXIT_ERROR = 1
EXIT_APPROVAL_REQUIRED = 3
EXIT_POLICY_DENIED = 4


def _print_result(result: CommandResult, *, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                {
                    "policy_id": result.policy_id,
                    "argv": list(result.argv),
                    "exit_code": result.exit_code,
                    "duration_seconds": result.duration_seconds,
                    "stdout": result.stdout_text,
                    "stderr": result.stderr_text,
                    "timed_out": result.timed_out,
                    "cancelled": result.cancelled,
                    "stdout_truncated": result.stdout_truncated,
                    "stderr_truncated": result.stderr_truncated,
                },
                indent=2,
            )
        )
        return
    if result.stdout:
        print(result.stdout_text, end="")
    if result.stderr:
        print(result.stderr_text, end="", file=sys.stderr)


def _matching_grant(
    records: list[ApprovalRecord],
    *,
    spec: CommandSpec,
    task_id: str,
    issuer: ApprovalTokenIssuer,
) -> ApprovalRecord | None:
    for record in records:
        if record.task_id != task_id or record.status != STATUS_APPROVED:
            continue
        if record.token is None:
            continue
        if record.argv != spec.argv or record.cwd != spec.cwd:
            continue
        if issuer.verify(record.token, record.spec(), record.task_id).ok:
            return record
    return None


def _pending_for(
    records: list[ApprovalRecord], *, spec: CommandSpec, task_id: str
) -> ApprovalRecord | None:
    for record in records:
        if (
            record.task_id == task_id
            and record.status == STATUS_PENDING
            and record.argv == spec.argv
            and record.cwd == spec.cwd
        ):
            return record
    return None


def _denial_for(
    records: list[ApprovalRecord], *, spec: CommandSpec, task_id: str
) -> ApprovalRecord | None:
    for record in records:
        if (
            record.task_id == task_id
            and record.status == STATUS_DENIED
            and record.argv == spec.argv
            and record.cwd == spec.cwd
        ):
            return record
    return None


def _run(
    executor: TerminalExecutor,
    spec: CommandSpec,
    verdict: PolicyVerdict,
    *,
    task_id: str,
    approved: bool,
    as_json: bool,
) -> int:
    result = executor.run(
        spec, policy=verdict.to_decision(approved=approved), task_id=task_id
    )
    _print_result(result, as_json=as_json)
    if result.timed_out:
        print("command timed out", file=sys.stderr)
        return EXIT_ERROR
    return result.exit_code if result.exit_code is not None else EXIT_ERROR


def main(argv: Sequence[str] | None = None) -> None:
    """CLI for policy-enforced command execution."""

    import argparse

    parser = argparse.ArgumentParser(
        prog="fcc-exec",
        description="Run one command through policy enforcement and audit.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Working directory and confinement root (default: cwd)",
    )
    parser.add_argument(
        "--task-id",
        default=None,
        help="Task id to resume after approval (default: new id)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Wall-clock timeout in seconds (default 300)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON result")
    parser.add_argument("--approvals-file", type=Path, default=None)
    parser.add_argument("--secret-file", type=Path, default=None)
    parser.add_argument("--audit-file", type=Path, default=None)
    parser.add_argument("command", nargs=argparse.REMAINDER)

    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print(
            "fcc-exec: no command given (use -- before the command)",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_ERROR)

    workspace = args.workspace.resolve()
    try:
        spec = CommandSpec(
            argv=tuple(command),
            cwd=str(workspace),
            timeout_seconds=args.timeout,
        )
    except ValueError as exc:
        print(f"fcc-exec: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR) from None

    engine = PolicyEngine(PolicyConfig(workspace_root=str(workspace)))
    verdict = engine.evaluate(spec)
    if verdict.requirement is Requirement.DENY:
        print(f"policy denied: {verdict.reason}", file=sys.stderr)
        raise SystemExit(EXIT_POLICY_DENIED)

    audit_path = args.audit_file if args.audit_file is not None else audit_log_path()
    executor = TerminalExecutor(sink=JsonlAuditSink(audit_path))
    task_id = args.task_id if args.task_id else uuid.uuid4().hex

    if verdict.requirement is Requirement.ALLOW:
        raise SystemExit(
            _run(
                executor,
                spec,
                verdict,
                task_id=task_id,
                approved=False,
                as_json=args.json,
            )
        )

    store = FileApprovalStore(
        args.approvals_file if args.approvals_file is not None else approvals_path()
    )
    secret_path = (
        args.secret_file if args.secret_file is not None else approval_secret_path()
    )
    issuer = ApprovalTokenIssuer(load_or_create_secret(secret_path))
    records = store.list_records(include_decided=True)

    grant = _matching_grant(records, spec=spec, task_id=task_id, issuer=issuer)
    if grant is not None:
        raise SystemExit(
            _run(
                executor,
                spec,
                verdict,
                task_id=task_id,
                approved=True,
                as_json=args.json,
            )
        )

    denial = _denial_for(records, spec=spec, task_id=task_id)
    if denial is not None:
        detail = f": {denial.decision_reason}" if denial.decision_reason else ""
        print(f"approval denied{detail}", file=sys.stderr)
        raise SystemExit(EXIT_POLICY_DENIED)

    pending = _pending_for(records, spec=spec, task_id=task_id)
    if pending is None:
        pending = store.request(
            spec,
            task_id=task_id,
            level=verdict.level.name,
            reason=verdict.reason,
            policy_id=verdict.policy_id,
        )
    print(
        f"approval required ({verdict.level.name}): {' '.join(spec.argv)}\n"
        f"  reason: {verdict.reason}\n"
        f"  approve: fcc-approvals approve {pending.approval_id}\n"
        f"  then re-run with: --task-id {task_id}",
        file=sys.stderr,
    )
    raise SystemExit(EXIT_APPROVAL_REQUIRED)


__all__ = [
    "EXIT_APPROVAL_REQUIRED",
    "EXIT_ERROR",
    "EXIT_POLICY_DENIED",
    "main",
]
