"""Runbook plans and postmortem rendering."""

import pytest

from free_claude_code.ops import (
    AlarmSummary,
    Postmortem,
    build_runbook,
    pod_logs,
    runbook_names,
)
from free_claude_code.terminal.policy import RiskLevel, classify


def test_builtin_runbooks() -> None:
    assert runbook_names() == (
        "deployment-stuck",
        "node-pressure",
        "pod-crashloop",
        "terraform-drift",
    )


def test_every_runbook_probe_is_policy_clean() -> None:
    books = [
        build_runbook("pod-crashloop", workload="web", namespace="prod"),
        build_runbook("deployment-stuck", workload="web"),
        build_runbook("node-pressure", unit="nginx"),
        build_runbook("node-pressure"),
        build_runbook("terraform-drift"),
    ]
    for book in books:
        assert book.steps, book.name
        for probe in book.probes:
            level, rule = classify(probe.argv)
            assert level in (RiskLevel.R0_READ_ONLY, RiskLevel.R1_REVERSIBLE), (
                probe.argv,
                rule,
            )


def test_crashloop_includes_previous_logs() -> None:
    book = build_runbook("pod-crashloop", workload="web-1", namespace="prod")
    argvs = [probe.argv for probe in book.probes]
    assert any("--previous" in argv for argv in argvs)
    assert all("prod" in argv for argv in argvs if "-n" in argv)


def test_unknown_runbook_lists_the_known_names() -> None:
    with pytest.raises(KeyError, match="pod-crashloop"):
        build_runbook("disk-full")


def test_pod_logs_previous() -> None:
    probe = pod_logs("web-1", "prod", previous=True)
    assert "--previous" in probe.argv
    assert probe.name == "k8s-pod-logs-previous"
    level, _ = classify(probe.argv)
    assert level == RiskLevel.R0_READ_ONLY


def test_postmortem_render_full() -> None:
    postmortem = Postmortem(
        title="web 5xx spike",
        summary="Deploy v3 rolled back after 5xx spike.",
        impact="12% of requests failed for 8 minutes.",
        timeline="10:01 [Warning] BackOff pod/web-1 (x3): Back-off restarting",
        alarms=(
            AlarmSummary(
                name="web-5xx",
                state="ALARM",
                reason="threshold crossed",
                updated="2026-11-19T10:02:00Z",
            ),
        ),
        action_items=("Add a canary step", "Page on burn rate"),
    )
    text = postmortem.render()
    assert text.startswith("# Postmortem: web 5xx spike\n")
    assert "## Impact\n12% of requests failed" in text
    assert "web-5xx (ALARM)" in text
    assert "- [ ] Add a canary step" in text
    assert text.endswith("\n")


def test_postmortem_render_minimal() -> None:
    postmortem = Postmortem(
        title="t", summary="s", impact="i", timeline="", alarms=(), action_items=()
    )
    text = postmortem.render()
    assert "(no events collected)" in text
    assert "## Alarms" not in text
    assert "## Action items" not in text
