"""Diagnostics: read-only plans, policy gating and tool detection."""

import threading
from pathlib import Path

import pytest

from free_claude_code.ops import (
    DEVOPS_TOOLS,
    DiagnosticsRunner,
    Probe,
    aws_diagnostics,
    azure_diagnostics,
    compose_diagnostics,
    docker_diagnostics,
    gcloud_diagnostics,
    kubernetes_diagnostics,
    rollout_status,
    terraform_diagnostics,
)
from free_claude_code.terminal import (
    CommandResult,
    CommandSpec,
    PolicyDecision,
    PolicyRejectedError,
    TerminalExecutor,
)


def _result(
    argv: tuple[str, ...], *, exit_code: int = 0, stdout: bytes = b"ok\n"
) -> CommandResult:
    return CommandResult(
        argv=argv,
        cwd=None,
        exit_code=exit_code,
        duration_seconds=0.1,
        stdout=stdout,
        stderr=b"" if exit_code == 0 else b"error\n",
    )


class FakeExecutor(TerminalExecutor):
    """Returns canned results in order and records the specs it ran."""

    def __init__(self, results: list[CommandResult]) -> None:
        super().__init__()
        self._results = list(results)
        self.specs: list[CommandSpec] = []

    def run(
        self,
        spec: CommandSpec,
        *,
        policy: PolicyDecision | None = None,
        cancel_event: threading.Event | None = None,
        on_output: object = None,
        task_id: str | None = None,
    ) -> CommandResult:
        self.specs.append(spec)
        return self._results.pop(0)


def test_every_planned_probe_is_read_only() -> None:
    plans = (
        docker_diagnostics()
        + compose_diagnostics()
        + kubernetes_diagnostics()
        + kubernetes_diagnostics(namespace="prod")
        + (rollout_status("web"), rollout_status("web", namespace="prod"))
        + terraform_diagnostics()
        + aws_diagnostics()
        + azure_diagnostics()
        + gcloud_diagnostics()
    )
    from free_claude_code.terminal.policy import RiskLevel, classify

    for probe in plans:
        level, rule = classify(probe.argv)
        assert level in (RiskLevel.R0_READ_ONLY, RiskLevel.R1_REVERSIBLE), (
            probe.argv,
            rule,
        )


def test_every_tool_probe_is_read_only() -> None:
    from free_claude_code.terminal.policy import RiskLevel, classify

    for probe in DEVOPS_TOOLS:
        level, rule = classify(probe.argv)
        assert level is RiskLevel.R0_READ_ONLY, (probe.argv, rule)


def test_run_plan_collects_results_without_stopping(tmp_path: Path) -> None:
    executor = FakeExecutor(
        [
            _result(("docker", "info")),
            _result(("docker", "ps", "-a"), exit_code=1, stdout=b""),
            _result(("docker", "images")),
        ]
    )
    runner = DiagnosticsRunner(tmp_path, executor=executor)
    report = runner.run_plan("docker", docker_diagnostics())
    assert len(report.results) == 3
    assert not report.ok
    assert [r.name for r in report.failed] == ["docker-containers"]
    assert report.results[0].output_tail == "ok\n"
    assert all(spec.cwd == str(tmp_path) for spec in executor.specs)


def test_policy_rejection_raises_instead_of_running(tmp_path: Path) -> None:
    executor = FakeExecutor([])
    runner = DiagnosticsRunner(tmp_path, executor=executor)
    with pytest.raises(PolicyRejectedError):
        runner.run_probe(Probe("nope", ("kubectl", "delete", "pod", "x")))
    assert executor.specs == []


def test_detect_all_reports_availability(tmp_path: Path) -> None:
    executor = FakeExecutor(
        [
            _result(probe.argv, exit_code=0 if i % 2 == 0 else 1)
            for i, probe in enumerate(DEVOPS_TOOLS)
        ]
    )
    runner = DiagnosticsRunner(tmp_path, executor=executor)
    statuses = runner.detect_all()
    assert len(statuses) == len(DEVOPS_TOOLS)
    assert [s.available for s in statuses] == [
        i % 2 == 0 for i in range(len(DEVOPS_TOOLS))
    ]
    assert statuses[0].version_line == "ok"
    assert statuses[1].exit_code == 1


def test_report_summary_lists_each_probe() -> None:
    from free_claude_code.ops import DiagnosticReport, ProbeResult

    report = DiagnosticReport(
        "k8s",
        (
            ProbeResult("a", ("kubectl", "get", "pods"), True, 0, "out"),
            ProbeResult("b", ("kubectl", "get", "events"), False, 1, "err"),
        ),
    )
    text = report.summary()
    assert "a: ok" in text
    assert "b: FAIL" in text
    assert report.failed == (report.results[1],)
