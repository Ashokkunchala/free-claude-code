"""Tests for the fcc-ops CLI (runner is faked; no real tools are run)."""

import json
from pathlib import Path

import pytest

from free_claude_code.cli import ops as ops_cli
from free_claude_code.ops import (
    DiagnosticReport,
    ProbeResult,
    ToolKind,
    ToolStatus,
)


class FakeRunner:
    """Records plans and returns canned reports/statuses."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.plans: list[str] = []

    def detect_all(self, probes=None, *, task_id=None):
        return (
            ToolStatus(ToolKind.DOCKER, ("docker", "--version"), True, "27.0", 0),
            ToolStatus(ToolKind.KUBECTL, ("kubectl", "version"), False, "", 1),
        )

    def run_plan(self, subject, probes, *, task_id=None):
        self.plans.append(subject)
        results = []
        for probe in probes:
            ok = not probe.name.endswith("containers")
            output = '{"items": []}' if probe.name.endswith("-json") else "ok\n"
            results.append(
                ProbeResult(probe.name, probe.argv, ok, 0 if ok else 1, output)
            )
        return DiagnosticReport(subject, tuple(results))


@pytest.fixture
def runner(monkeypatch, tmp_path: Path) -> FakeRunner:
    fake = FakeRunner(tmp_path)
    monkeypatch.setattr(ops_cli, "DiagnosticsRunner", lambda root: fake)
    return fake


def test_detect_plain(runner: FakeRunner, capsys) -> None:
    ops_cli.main(["detect"])
    out = capsys.readouterr().out
    assert "docker: ok 27.0" in out
    assert "kubectl: missing" in out


def test_detect_json(runner: FakeRunner, capsys) -> None:
    ops_cli.main(["--json", "detect"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["tools"][0] == {
        "tool": "docker",
        "available": True,
        "version": "27.0",
    }


def test_plan_subcommand(runner: FakeRunner, capsys) -> None:
    ops_cli.main(["docker"])
    out = capsys.readouterr().out
    assert "docker-info: ok" in out
    assert "docker-containers: FAIL" in out
    assert runner.plans == ["docker"]


def test_all_runs_only_available_tools(runner: FakeRunner) -> None:
    ops_cli.main(["all"])
    assert runner.plans == ["docker", "compose"]
    assert "kubernetes" not in runner.plans


def test_k8s_namespace_passed_through(runner: FakeRunner, capsys) -> None:
    ops_cli.main(["k8s", "-n", "prod"])
    assert runner.plans == ["kubernetes"]
    capsys.readouterr()


def test_incident_renders_report(runner: FakeRunner, capsys) -> None:
    ops_cli.main(["incident", "web", "-n", "prod", "--pods", "web-1,web-2"])
    out = capsys.readouterr().out
    assert "incident:web" in out
    assert "k8s-pod-logs" in out
    assert "--tail=100" in out


def test_incident_json_shape(runner: FakeRunner, capsys) -> None:
    ops_cli.main(["--json", "incident", "web"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["report"]["subject"] == "incident:web"
    assert payload["unhealthy_pods"] == []
    assert payload["warning_events"] == []


def test_drift_clean(runner: FakeRunner, capsys) -> None:
    ops_cli.main(["drift"])
    assert "plan: clean" in capsys.readouterr().out


def test_drift_json(runner: FakeRunner, capsys) -> None:
    ops_cli.main(["--json", "drift", "--tool", "tofu"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "clean"
    assert payload["to_destroy"] == 0
