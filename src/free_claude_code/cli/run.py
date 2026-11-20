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
    runs_dir_path,
)
from free_claude_code.skills import merged_packs, render_plan_guidance
from free_claude_code.skills.state import (
    SkillState,
    default_packs_dir,
    default_state_path,
)
from free_claude_code.terminal import (
    FileApprovalStore,
    JsonlAuditSink,
    PlanError,
    PlanModelError,
    RunEngine,
    RunPlan,
    RunReport,
    append_run_record,
    build_plan_prompt,
    collect_env_context,
    load_run_history,
    plan_from_response,
    plan_model_from_env,
    propose_plan_with_feedback,
    propose_repair,
    render_env_context,
    render_run_summary,
)
from free_claude_code.terminal.approval import ApprovalTokenIssuer
from free_claude_code.terminal.policy import PolicyEngine, policy_config_from_env
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
        if step.rollback_status:
            line += f" (rollback {step.rollback_status})"
        if step.reason:
            line += f" - {step.reason}"
        print(line)
    if report.residual:
        print(f"  not run: {', '.join(report.residual)}")
    print(f"status: {report.status} (task {report.task_id})")


def _skills_guidance(args: argparse.Namespace) -> str | None:
    """Enabled skill-pack guidance for the plan prompt, or None when off."""

    if args.no_skills:
        return None
    packs_root = (args.packs or default_packs_dir()).resolve()
    state = SkillState((args.skills_state or default_state_path()).resolve())
    guidance = render_plan_guidance(merged_packs(packs_root), state)
    return guidance or None


def main(argv: Sequence[str] | None = None) -> None:
    """CLI for plan-driven runs."""

    parser = argparse.ArgumentParser(
        prog="fcc-run",
        description="Execute a JSON plan through policy, approvals and audit.",
    )
    parser.add_argument("plan", type=Path, nargs="?", help="Plan JSON file")
    parser.add_argument(
        "--task",
        metavar="TASK",
        default=None,
        help="Generate a plan for TASK with the FCC_PLAN_* model and run it",
    )
    parser.add_argument(
        "--no-context",
        action="store_true",
        help="Do not attach observed workspace context to the plan prompt",
    )
    parser.add_argument(
        "--no-skills",
        action="store_true",
        help="Do not attach enabled skill-pack guidance to the plan prompt",
    )
    parser.add_argument(
        "--packs",
        type=Path,
        default=None,
        help="Skill-pack directory (default: the user packs directory)",
    )
    parser.add_argument(
        "--skills-state",
        type=Path,
        default=None,
        help="Skill enablement state file (default: the user state file)",
    )
    parser.add_argument(
        "--model-attempts",
        type=int,
        default=2,
        help="Plan-generation attempts before giving up (default: 2)",
    )
    parser.add_argument(
        "--repair-attempts",
        type=int,
        default=0,
        help="With --task: model-driven repair rounds after a failed run (default: 0)",
    )
    parser.add_argument(
        "--print-prompt",
        metavar="TASK",
        default=None,
        help="Print the plan-generation prompt for TASK and exit",
    )
    parser.add_argument(
        "--from-response",
        metavar="TASK",
        default=None,
        help="Validate a model response (--response FILE) into plan JSON",
    )
    parser.add_argument(
        "--response",
        type=Path,
        default=None,
        help="File with the model's raw response (with --from-response)",
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
        "--dry-run",
        action="store_true",
        help="Policy-check every step without executing",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument("--approvals-file", type=Path, default=None)
    parser.add_argument("--secret-file", type=Path, default=None)
    parser.add_argument("--audit-file", type=Path, default=None)
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="Directory for persisted run reports (default: config runs dir)",
    )
    parser.add_argument(
        "--history",
        metavar="TASK_ID",
        default=None,
        help="Print the recorded run history for a task id and exit",
    )

    args = parser.parse_args(argv)

    if args.history is not None:
        runs_dir = args.runs_dir if args.runs_dir is not None else runs_dir_path()
        history = load_run_history(runs_dir, args.history)
        if history is None:
            print(f"fcc-run: no recorded run for task {args.history}", file=sys.stderr)
            raise SystemExit(EXIT_ERROR)
        if args.json:
            print(json.dumps(history, indent=2, sort_keys=True))
        else:
            print(render_run_summary(history))
        raise SystemExit(0)
    workspace = args.workspace.resolve()

    if args.print_prompt is not None:
        print(build_plan_prompt(args.print_prompt, workspace=str(workspace)))
        raise SystemExit(0)
    if args.from_response is not None:
        if args.response is None:
            print("fcc-run: --from-response needs --response FILE", file=sys.stderr)
            raise SystemExit(EXIT_ERROR)
        try:
            raw_response = args.response.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"fcc-run: {exc}", file=sys.stderr)
            raise SystemExit(EXIT_ERROR) from None
        try:
            proposed = plan_from_response(raw_response)
        except PlanError as exc:
            print(f"fcc-run: invalid model response: {exc}", file=sys.stderr)
            raise SystemExit(EXIT_BAD_PLAN) from None
        print(
            json.dumps(
                {
                    "goal": proposed.goal,
                    "steps": [
                        {
                            "id": step.step_id,
                            "title": step.title,
                            "argv": list(step.argv),
                            **({"cwd": step.cwd} if step.cwd is not None else {}),
                            **(
                                {"timeout_seconds": step.timeout_seconds}
                                if step.timeout_seconds is not None
                                else {}
                            ),
                            **(
                                {
                                    "verify": {
                                        "argv": list(step.verify.argv),
                                        "expect_exit": step.verify.expect_exit,
                                    }
                                }
                                if step.verify is not None
                                else {}
                            ),
                        }
                        for step in proposed.steps
                    ],
                },
                indent=2,
            )
        )
        raise SystemExit(0)
    if args.task is not None:
        if args.plan is not None:
            print("fcc-run: pass a plan file or --task, not both", file=sys.stderr)
            raise SystemExit(EXIT_ERROR)
        try:
            model = plan_model_from_env()
        except PlanModelError as exc:
            print(f"fcc-run: {exc}", file=sys.stderr)
            raise SystemExit(EXIT_ERROR) from None
        context = None
        if not args.no_context:
            context = render_env_context(collect_env_context(str(workspace)))
        skills = _skills_guidance(args)
        try:
            plan = propose_plan_with_feedback(
                args.task,
                model,
                workspace=str(workspace),
                context=context,
                skills=skills,
                attempts=args.model_attempts,
            )
        except PlanModelError as exc:
            print(f"fcc-run: {exc}", file=sys.stderr)
            raise SystemExit(EXIT_ERROR) from None
        except PlanError as exc:
            print(
                f"fcc-run: model did not produce a valid plan: {exc}", file=sys.stderr
            )
            raise SystemExit(EXIT_BAD_PLAN) from None
    else:
        if args.plan is None:
            print("fcc-run: no plan file given", file=sys.stderr)
            raise SystemExit(EXIT_ERROR)
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
        engine = PolicyEngine(policy_config_from_env(workspace_root=str(workspace)))
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
    runs_dir = args.runs_dir if args.runs_dir is not None else runs_dir_path()
    report = engine_runner.run(plan, task_id=task_id)
    record_path = append_run_record(runs_dir, report)
    repairs_left = args.repair_attempts
    while report.status in ("failed", "verify_failed") and repairs_left > 0:
        if args.task is None:
            break
        repairs_left -= 1
        print(
            f"run {report.status}; asking the model for a repair plan "
            f"({repairs_left} left)",
            file=sys.stderr,
        )
        try:
            plan = propose_repair(
                args.task,
                report,
                model,
                workspace=str(workspace),
                context=context,
                skills=skills,
                attempts=args.model_attempts,
            )
        except PlanModelError as exc:
            print(f"fcc-run: {exc}", file=sys.stderr)
            raise SystemExit(EXIT_ERROR) from None
        except PlanError as exc:
            print(
                f"fcc-run: model did not produce a valid repair: {exc}", file=sys.stderr
            )
            raise SystemExit(EXIT_BAD_PLAN) from None
        report = engine_runner.run(plan, task_id=task_id)
        record_path = append_run_record(runs_dir, report)
    _print_report(report, as_json=args.json)
    print(f"run recorded: {record_path}", file=sys.stderr)
    if report.status == "approval_required":
        print(
            f"approval required; re-run with --task-id {task_id} after approving",
            file=sys.stderr,
        )
    raise SystemExit(_STATUS_EXIT.get(report.status, EXIT_ERROR))
