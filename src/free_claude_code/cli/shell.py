"""``fcc-shell``: interactive terminal agent session (roadmap section 3).

One session is bound to one workspace. The observed environment context
is collected once at start-up and attached to every plan prompt in the
session (persistent project context); the run engine, approval store and
audit sink are likewise created once, so every turn shares the same
policy, approvals and audit trail as ``fcc-run``. Approvals are still
granted with ``fcc-approvals`` between turns - the session never
bypasses policy, and ``--yes`` does not exist here by design.

Each line read from stdin is one task: the FCC_PLAN_* model proposes a
plan, the run engine executes it, and the outcome is printed before the
next prompt. Session commands start with ``:`` (``:help``, ``:context``,
``:history TASK_ID``, ``:quit``); plain ``exit``/``quit`` or EOF also end
the session. Piped stdin is fully supported, which keeps the session
scriptable. The prompt and progress chatter go to stderr so ``--json``
keeps stdout machine-readable.

Exit code: zero when every task in the session succeeded; otherwise the
exit code of the first failed turn (same mapping as ``fcc-run``).
"""

import argparse
import json
import sys
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

from free_claude_code.cli.approvals import load_or_create_secret
from free_claude_code.cli.run import (
    _STATUS_EXIT,
    EXIT_BAD_PLAN,
    EXIT_ERROR,
)
from free_claude_code.config.paths import (
    approval_secret_path,
    approvals_path,
    audit_log_path,
    runs_dir_path,
)
from free_claude_code.terminal import (
    FileApprovalStore,
    JsonlAuditSink,
    PlanError,
    PlanModelError,
    RunEngine,
    RunReport,
    append_run_record,
    collect_env_context,
    load_run_history,
    plan_model_from_env,
    propose_plan_with_feedback,
    propose_repair,
    render_env_context,
    render_run_summary,
)
from free_claude_code.terminal.approval import ApprovalTokenIssuer

PROMPT = "fcc> "

_HELP = """session commands:
  :help               show this help
  :context            reprint the collected workspace context
  :history TASK_ID    print the recorded run history for a task
  :quit               end the session (also: exit, quit, Ctrl-D)
anything else is run as a task: plan -> policy -> approve -> run -> verify"""


def _print_report(report: RunReport, *, as_json: bool) -> None:
    """Render one turn's outcome; mirrors the ``fcc-run`` output shape."""

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


def _run_task(
    task: str,
    *,
    model: Callable[[str], str],
    context: str | None,
    engine: RunEngine,
    workspace: str,
    runs_dir: Path,
    model_attempts: int,
    repair_attempts: int,
    as_json: bool,
) -> int:
    """Plan, execute, optionally repair and render one task; return its exit code."""

    try:
        plan = propose_plan_with_feedback(
            task,
            model,
            workspace=workspace,
            context=context,
            attempts=model_attempts,
        )
    except PlanModelError as exc:
        print(f"fcc-shell: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except PlanError as exc:
        print(f"fcc-shell: model did not produce a valid plan: {exc}", file=sys.stderr)
        return EXIT_BAD_PLAN
    task_id = uuid.uuid4().hex
    report = engine.run(plan, task_id=task_id)
    record_path = append_run_record(runs_dir, report)
    repairs_left = repair_attempts
    while report.status in ("failed", "verify_failed") and repairs_left > 0:
        repairs_left -= 1
        print(
            f"run {report.status}; asking the model for a repair plan "
            f"({repairs_left} left)",
            file=sys.stderr,
        )
        try:
            plan = propose_repair(
                task,
                report,
                model,
                workspace=workspace,
                context=context,
                attempts=model_attempts,
            )
        except PlanModelError as exc:
            print(f"fcc-shell: {exc}", file=sys.stderr)
            return EXIT_ERROR
        except PlanError as exc:
            print(
                f"fcc-shell: model did not produce a valid repair: {exc}",
                file=sys.stderr,
            )
            return EXIT_BAD_PLAN
        report = engine.run(plan, task_id=task_id)
        record_path = append_run_record(runs_dir, report)
    _print_report(report, as_json=as_json)
    print(f"run recorded: {record_path}", file=sys.stderr)
    if report.status == "approval_required":
        print(
            "approval required; approve with fcc-approvals, then re-issue "
            "the task in this session",
            file=sys.stderr,
        )
    return _STATUS_EXIT.get(report.status, EXIT_ERROR)


def main(argv: Sequence[str] | None = None) -> None:
    """CLI for the interactive agent session."""

    parser = argparse.ArgumentParser(
        prog="fcc-shell",
        description=(
            "Interactive terminal agent session: one task per line, planned "
            "and executed through policy, approvals and audit."
        ),
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Working directory and confinement root (default: cwd)",
    )
    parser.add_argument(
        "--model-attempts",
        type=int,
        default=2,
        help="Plan-generation attempts per task before giving up (default: 2)",
    )
    parser.add_argument(
        "--repair-attempts",
        type=int,
        default=0,
        help="Model-driven repair rounds after a failed run (default: 0)",
    )
    parser.add_argument(
        "--no-context",
        action="store_true",
        help="Do not attach observed workspace context to plan prompts",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON reports")
    parser.add_argument("--approvals-file", type=Path, default=None)
    parser.add_argument("--secret-file", type=Path, default=None)
    parser.add_argument("--audit-file", type=Path, default=None)
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="Directory for persisted run reports (default: config runs dir)",
    )

    args = parser.parse_args(argv)
    workspace = args.workspace.resolve()

    try:
        model = plan_model_from_env()
    except PlanModelError as exc:
        print(f"fcc-shell: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR) from None

    context = None
    if not args.no_context:
        context = render_env_context(collect_env_context(str(workspace)))

    sink = JsonlAuditSink(
        args.audit_file if args.audit_file is not None else audit_log_path()
    )
    store = FileApprovalStore(
        args.approvals_file if args.approvals_file is not None else approvals_path()
    )
    secret_path = (
        args.secret_file if args.secret_file is not None else approval_secret_path()
    )
    issuer = ApprovalTokenIssuer(load_or_create_secret(secret_path))
    engine = RunEngine(workspace=str(workspace), store=store, issuer=issuer, sink=sink)
    runs_dir = args.runs_dir if args.runs_dir is not None else runs_dir_path()

    print(
        f"fcc-shell: workspace {workspace}; one task per line, "
        f"':help' for commands, 'exit' or Ctrl-D to leave",
        file=sys.stderr,
    )

    exit_code = 0
    while True:
        try:
            print(PROMPT, end="", file=sys.stderr, flush=True)
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            print(file=sys.stderr)
            break
        if not line:
            break
        turn = line.strip()
        if not turn:
            continue
        if turn in ("exit", "quit", ":exit", ":quit"):
            break
        if turn == ":help":
            print(_HELP)
            continue
        if turn == ":context":
            print(context if context is not None else "(context disabled)")
            continue
        if turn.startswith(":history"):
            parts = turn.split(maxsplit=1)
            if len(parts) != 2:
                print("fcc-shell: usage :history TASK_ID", file=sys.stderr)
                exit_code = exit_code or EXIT_ERROR
                continue
            history = load_run_history(runs_dir, parts[1])
            if history is None:
                print(
                    f"fcc-shell: no recorded run for task {parts[1]}",
                    file=sys.stderr,
                )
                exit_code = exit_code or EXIT_ERROR
                continue
            if args.json:
                print(json.dumps(history, indent=2, sort_keys=True))
            else:
                print(render_run_summary(history))
            continue
        if turn.startswith(":"):
            print(f"fcc-shell: unknown command {turn!r}; try ':help'", file=sys.stderr)
            continue
        code = _run_task(
            turn,
            model=model,
            context=context,
            engine=engine,
            workspace=str(workspace),
            runs_dir=runs_dir,
            model_attempts=args.model_attempts,
            repair_attempts=args.repair_attempts,
            as_json=args.json,
        )
        exit_code = exit_code or code
    raise SystemExit(exit_code)
