"""``fcc-ops``: read-only DevOps diagnostics and incident evidence.

Every subcommand assembles fixed read-only probes from the ops package
and runs them through the terminal executor under the R0-R3 policy:
anything the policy gates raises instead of running, and this command
never writes - to files, clusters, or clouds.
"""

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from free_claude_code.ops import (
    DiagnosticsRunner,
    DriftStatus,
    aws_diagnostics,
    azure_diagnostics,
    build_runbook,
    compose_diagnostics,
    docker_diagnostics,
    gcloud_diagnostics,
    incident_plan,
    kubernetes_diagnostics,
    parse_events,
    parse_pods,
    plan_probe,
    review_drift,
    runbook_names,
    state_list_probe,
    synthesize_timeline,
    terraform_diagnostics,
    unhealthy_pods,
    warning_events,
)

_PLANS = {
    "docker": docker_diagnostics,
    "compose": compose_diagnostics,
    "terraform": terraform_diagnostics,
    "aws": aws_diagnostics,
    "azure": azure_diagnostics,
    "gcloud": gcloud_diagnostics,
}


def _report_json(report) -> dict[str, Any]:
    return {
        "subject": report.subject,
        "ok": report.ok,
        "results": [
            {
                "name": r.name,
                "argv": list(r.argv),
                "ok": r.ok,
                "exit_code": r.exit_code,
                "output_tail": r.output_tail,
                "timed_out": r.timed_out,
            }
            for r in report.results
        ],
    }


def _print_report(report, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(_report_json(report), indent=2))
    else:
        print(f"== {report.subject} ==")
        print(report.summary())


def _detect(runner: DiagnosticsRunner, *, as_json: bool) -> None:
    statuses = runner.detect_all()
    if as_json:
        print(
            json.dumps(
                {
                    "tools": [
                        {
                            "tool": s.kind,
                            "available": s.available,
                            "version": s.version_line,
                        }
                        for s in statuses
                    ]
                },
                indent=2,
            )
        )
        return
    for s in statuses:
        mark = "ok" if s.available else "missing"
        print(f"{s.kind}: {mark} {s.version_line}")


def _k8s(runner: DiagnosticsRunner, namespace: str | None, *, as_json: bool) -> None:
    report = runner.run_plan("kubernetes", kubernetes_diagnostics(namespace))
    _print_report(report, as_json=as_json)


def _incident(
    runner: DiagnosticsRunner,
    workload: str,
    namespace: str | None,
    pods: tuple[str, ...],
    *,
    as_json: bool,
) -> None:
    plan = incident_plan(workload, namespace=namespace, pods=pods)
    report = runner.run_plan(plan.subject, plan.probes)

    pods_json: dict[str, Any] = {}
    events_json: dict[str, Any] = {}
    for result in report.results:
        if result.name == "k8s-pods-json" and result.ok:
            pods_json = json.loads(result.output_tail)
        if result.name == "k8s-events-json" and result.ok:
            events_json = json.loads(result.output_tail)

    unhealthy = unhealthy_pods(parse_pods(pods_json)) if pods_json else ()
    warnings = warning_events(parse_events(events_json)) if events_json else ()

    if as_json:
        print(
            json.dumps(
                {
                    "report": _report_json(report),
                    "unhealthy_pods": [p.__dict__ for p in unhealthy],
                    "warning_events": [e.__dict__ for e in warnings],
                },
                indent=2,
            )
        )
        return

    print(f"== {report.subject} ==")
    print(report.summary())
    if unhealthy:
        print("\nUnhealthy pods:")
        for pod in unhealthy:
            print(
                f"  {pod.namespace}/{pod.name} {pod.phase} ready={pod.ready} restarts={pod.restarts}"
            )
    if warnings:
        print("\nWarning timeline (oldest first):")
        print(synthesize_timeline(warnings))


def _runbook(
    runner: DiagnosticsRunner,
    name: str | None,
    *,
    workload: str | None,
    namespace: str | None,
    unit: str | None,
    as_json: bool,
) -> None:
    if name is None:
        if as_json:
            print(json.dumps({"runbooks": list(runbook_names())}, indent=2))
        else:
            for known in runbook_names():
                print(known)
        return
    params: dict[str, Any] = {}
    if workload is not None:
        params["workload"] = workload
    if namespace is not None:
        params["namespace"] = namespace
    if unit is not None:
        params["unit"] = unit
    try:
        book = build_runbook(name, **params)
    except KeyError as error:
        print(error.args[0], file=sys.stderr)
        sys.exit(2)
    except TypeError as error:
        print(f"runbook {name}: bad arguments: {error}", file=sys.stderr)
        sys.exit(2)
    if as_json:
        results = []
        for step in book.steps:
            report = runner.run_plan(f"{book.name}:{step.label}", step.probes)
            results.append({"step": step.label, "report": _report_json(report)})
        print(json.dumps({"runbook": book.name, "steps": results}, indent=2))
        return
    print(f"== runbook: {book.name} - {book.summary} ==")
    for step in book.steps:
        print(f"-- {step.label}")
        _print_report(
            runner.run_plan(f"{book.name}:{step.label}", step.probes), as_json=False
        )


def _drift(runner: DiagnosticsRunner, tool: str, *, as_json: bool) -> None:
    report = runner.run_plan(
        "terraform-drift", (plan_probe(tool=tool), state_list_probe(tool=tool))
    )
    drift = review_drift(report.results[0])
    if as_json:
        print(
            json.dumps(
                {
                    "status": drift.status,
                    "exit_code": drift.exit_code,
                    "to_add": drift.to_add,
                    "to_change": drift.to_change,
                    "to_destroy": drift.to_destroy,
                    "state_resources": report.results[1].output_tail.splitlines()
                    if report.results[1].ok
                    else [],
                },
                indent=2,
            )
        )
        return
    print(f"plan: {drift.status}")
    if drift.status is DriftStatus.DRIFT:
        print(
            f"  add={drift.to_add} change={drift.to_change} destroy={drift.to_destroy}"
        )
        if drift.has_destroy:
            print("  WARNING: plan includes destroys - review before any apply")


def main(argv: Sequence[str] | None = None) -> None:
    """CLI for read-only DevOps diagnostics."""

    import argparse

    parser = argparse.ArgumentParser(
        prog="fcc-ops",
        description="Read-only DevOps diagnostics and incident evidence.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Workspace root for command confinement (default: cwd)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("detect", help="Detect available DevOps tools")
    subparsers.add_parser("all", help="Run diagnostics for every detected tool")
    for name in _PLANS:
        subparsers.add_parser(name, help=f"Run the {name} diagnostic plan")

    k8s_parser = subparsers.add_parser("k8s", help="Kubernetes cluster diagnostics")
    k8s_parser.add_argument("-n", "--namespace", default=None)

    incident_parser = subparsers.add_parser(
        "incident", help="Collect read-only evidence for one workload"
    )
    incident_parser.add_argument("workload", help="Deployment name")
    incident_parser.add_argument("-n", "--namespace", default=None)
    incident_parser.add_argument(
        "--pods",
        default="",
        help="Comma-separated pods to describe and tail logs from",
    )

    drift_parser = subparsers.add_parser(
        "drift", help="Terraform plan with detailed exit code (drift review)"
    )
    drift_parser.add_argument(
        "--tool", choices=("terraform", "tofu"), default="terraform"
    )

    runbook_parser = subparsers.add_parser(
        "runbook", help="Run an incident runbook (read-only evidence steps)"
    )
    runbook_parser.add_argument(
        "name", nargs="?", help="Runbook name (omit to list the built-ins)"
    )
    runbook_parser.add_argument("--workload", default=None, help="Workload/pod name")
    runbook_parser.add_argument("-n", "--namespace", default=None)
    runbook_parser.add_argument("--unit", default=None, help="systemd unit name")

    args = parser.parse_args(argv)
    runner = DiagnosticsRunner(args.workspace.resolve())

    try:
        if args.command == "detect":
            _detect(runner, as_json=args.json)
        elif args.command == "all":
            statuses = runner.detect_all()
            available = {s.kind for s in statuses if s.available}
            for name, plan in _PLANS.items():
                tool = "docker" if name == "compose" else name
                if tool in available:
                    _print_report(runner.run_plan(name, plan()), as_json=args.json)
            if "kubectl" in available:
                _k8s(runner, None, as_json=args.json)
        elif args.command == "k8s":
            _k8s(runner, args.namespace, as_json=args.json)
        elif args.command == "incident":
            pods = tuple(p for p in args.pods.split(",") if p)
            _incident(runner, args.workload, args.namespace, pods, as_json=args.json)
        elif args.command == "drift":
            _drift(runner, args.tool, as_json=args.json)
        elif args.command == "runbook":
            _runbook(
                runner,
                args.name,
                workload=args.workload,
                namespace=args.namespace,
                unit=args.unit,
                as_json=args.json,
            )
        else:
            _print_report(
                runner.run_plan(args.command, _PLANS[args.command]()),
                as_json=args.json,
            )
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main(sys.argv[1:])
