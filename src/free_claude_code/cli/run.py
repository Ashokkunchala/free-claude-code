"""``fcc-run``: execute a JSON plan through the run engine.

The plan file names a goal and ordered steps (argv vectors, optional
per-step cwd/timeout, optional verify commands). Every step passes the
policy engine; R2/R3 steps need a matching approved request in the
approval store, exactly like ``fcc-exec`` - the first run creates the
request and exits ``EXIT_APPROVAL_REQUIRED``, and re-running with the
same ``--task-id`` resumes once approved. ``--dry-run`` policy-checks
every step without executing anything.
"""

import argparse
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
    FileApprovalStore,
    JsonlAuditSink,
    PlanError,
    RunEngine,
    RunPlan,
    RunReport,
)
from free_claude_code.terminal.approval import ApprovalTokenIssuer
from free_claude_code.terminal.policy import PolicyConfig, PolicyEngine
from free_claude_code.terminal.spec import CommandSpec

EXIT_ERROR = 1
EXIT_BAD_PLAN = 2
EXIT_APPROVAL_REQUIRED = 3
EXIT_POLICY_DENIED = 4

_STATUS_EXIT = {
    "ok": 0,
    "failed": EXIT_ERROR,
    "verify_failed": EXIT_ERROR,
    "denied": EXIT_POLICY_DENIED,
    "approval_required": EXIT_APPROVAL_REQUIRED,
}


def _print_report(report: RunReport, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report.as_dict(), indent=2))
        return
    print(f"goal: {report.goal}" if report.goal else f"task {report.task_id}")
    for step in report.steps:
        line = f"  [{step.status}] {step.title}"
        if step.reason:
            line += f" - {step.reason}"
        print(line)
    if report.residual:
        print(f"  not run: {', '.join(report.residual)}")
    print(f"status: {report.status} (task {report.task_id})")


def main(argv: Sequence[str] | None = None) -> None:
    """CLI for plan-driven runs."""

    parser = argparse.ArgumentParser(
        prog="fcc-run",
        description="Execute a JSON plan through policy, approvals and audit.",
    )
    parser.add_argument("plan", type=Path, help="Plan JSON file")
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
        "--dry-run",
        action="store_true",
        help="Policy-check every step without executing",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument("--approvals-file", type=Path, default=None)
    parser.add_argument("--secret-file", type=Path, default=None)
    parser.add_argument("--audit-file", type=Path, default=None)

    args = parser.parse_args(argv)
    workspace = args.workspace.resolve()

    try:
        raw = json.loads(args.plan.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"fcc-run: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR) from None
    except ValueError:
        print(f"fcc-run: {args.plan}: not valid JSON", file=sys.stderr)
        raise SystemExit(EXIT_BAD_PLAN) from None
    try:
        plan = RunPlan.from_dict(raw)
    except PlanError as exc:
        print(f"fcc-run: {args.plan}: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_BAD_PLAN) from None

    if args.dry_run:
        engine = PolicyEngine(PolicyConfig(workspace_root=str(workspace)))
        rows = []
        for step in plan.steps:
            try:
                spec = CommandSpec(argv=step.argv, cwd=step.cwd or str(workspace))
            except ValueError as exc:
                rows.append((step.step_id, "INVALID", str(exc)))
                continue
            verdict = engine.evaluate(spec)
            rows.append((step.step_id, verdict.requirement.name, verdict.reason))
        if args.json:
            print(
                json.dumps(
                    {
                        "goal": plan.goal,
                        "steps": [
                            {"id": i, "requirement": r, "reason": why}
                            for i, r, why in rows
                        ],
                    },
                    indent=2,
                )
            )
        else:
            for step_id, requirement, reason in rows:
                print(f"  [{requirement}] {step_id} - {reason}")
        raise SystemExit(0)

    task_id = args.task_id if args.task_id else uuid.uuid4().hex
    audit_path = args.audit_file if args.audit_file is not None else audit_log_path()
    sink = JsonlAuditSink(audit_path)
    store = FileApprovalStore(
        args.approvals_file if args.approvals_file is not None else approvals_path()
    )
    secret_path = (
        args.secret_file if args.secret_file is not None else approval_secret_path()
    )
    issuer = ApprovalTokenIssuer(load_or_create_secret(secret_path))
    engine_runner = RunEngine(
        workspace=str(workspace), store=store, issuer=issuer, sink=sink
    )
    report = engine_runner.run(plan, task_id=task_id)
    _print_report(report, as_json=args.json)
    if report.status == "approval_required":
        print(
            f"approval required; re-run with --task-id {task_id} after approving",
            file=sys.stderr,
        )
    raise SystemExit(_STATUS_EXIT.get(report.status, EXIT_ERROR))
