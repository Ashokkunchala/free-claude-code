"""Incident plans: read-only evidence probes and timeline synthesis."""

from free_claude_code.ops import (
    incident_plan,
    parse_events,
    synthesize_timeline,
    warning_events,
)
from free_claude_code.terminal.policy import RiskLevel, classify
from tests.ops.test_kubernetes import EVENTS_JSON


def test_incident_plan_is_all_read_only() -> None:
    plan = incident_plan("web", namespace="prod", pods=("web-1", "web-2"), log_tail=20)
    assert plan.subject == "incident:web"
    names = [probe.name for probe in plan.probes]
    assert names == [
        "k8s-rollout-status",
        "k8s-pods-json",
        "k8s-events-json",
        "k8s-describe-pod",
        "k8s-pod-logs",
        "k8s-describe-pod",
        "k8s-pod-logs",
    ]
    for probe in plan.probes:
        level, rule = classify(probe.argv)
        assert level is RiskLevel.R0_READ_ONLY, (probe.argv, rule)
    assert "--tail=20" in plan.probes[4].argv


def test_incident_plan_defaults_to_cluster_scope() -> None:
    plan = incident_plan("web")
    assert plan.namespace is None
    assert all("-n" not in probe.argv for probe in plan.probes)


def test_synthesize_timeline_renders_oldest_first() -> None:
    events = parse_events(EVENTS_JSON)
    timeline = synthesize_timeline(events)
    lines = timeline.splitlines()
    assert lines[0].startswith("2026-11-19T05:00:00Z")
    assert "[Warning] BackOff Pod/web-2 (x5)" in lines[2]


def test_warning_events_filters_normals() -> None:
    events = parse_events(EVENTS_JSON)
    warnings = warning_events(events)
    assert [e.reason for e in warnings] == ["BackOff"]
