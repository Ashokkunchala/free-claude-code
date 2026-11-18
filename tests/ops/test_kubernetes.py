"""Kubernetes JSON parsing and read-only probe builders."""

from free_claude_code.ops import (
    describe_pod,
    get_deployments_json,
    get_events_json,
    get_pods_json,
    parse_deployments,
    parse_events,
    parse_pods,
    pod_logs,
    unhealthy_pods,
)
from free_claude_code.terminal.policy import RiskLevel, classify

PODS_JSON = {
    "items": [
        {
            "metadata": {"name": "web-1", "namespace": "prod"},
            "spec": {"nodeName": "node-a"},
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {"ready": True, "restartCount": 0},
                    {"ready": True, "restartCount": 1},
                ],
            },
        },
        {
            "metadata": {"name": "web-2", "namespace": "prod"},
            "spec": {"nodeName": "node-b"},
            "status": {
                "phase": "CrashLoopBackOff",
                "containerStatuses": [{"ready": False, "restartCount": 7}],
            },
        },
        {
            "metadata": {"name": "job-1", "namespace": "batch"},
            "spec": {},
            "status": {"phase": "Succeeded", "containerStatuses": [{"ready": True}]},
        },
    ]
}

EVENTS_JSON = {
    "items": [
        {
            "reason": "Started",
            "type": "Normal",
            "involvedObject": {"kind": "Pod", "name": "web-1"},
            "message": "Started container",
            "count": 1,
            "lastTimestamp": "2026-11-19T05:00:10Z",
        },
        {
            "reason": "BackOff",
            "type": "Warning",
            "involvedObject": {"kind": "Pod", "name": "web-2"},
            "message": "Back-off restarting failed container",
            "count": 5,
            "lastTimestamp": "2026-11-19T05:01:00Z",
        },
        {
            "reason": "Scheduled",
            "type": "Normal",
            "involvedObject": {"kind": "Pod", "name": "web-2"},
            "message": "Assigned to node-b",
            "count": 1,
            "lastTimestamp": "2026-11-19T05:00:00Z",
        },
    ]
}

DEPLOYMENTS_JSON = {
    "items": [
        {
            "metadata": {"name": "web", "namespace": "prod"},
            "spec": {"replicas": 3},
            "status": {"readyReplicas": 2, "updatedReplicas": 3},
        },
        {
            "metadata": {"name": "worker", "namespace": "prod"},
            "spec": {"replicas": 1},
            "status": {"readyReplicas": 1, "updatedReplicas": 1},
        },
    ]
}


def test_probe_builders_are_read_only() -> None:
    probes = (
        get_pods_json(),
        get_pods_json("prod"),
        get_events_json(),
        get_deployments_json("prod"),
        describe_pod("web-1"),
        describe_pod("web-1", "prod"),
        pod_logs("web-1"),
        pod_logs("web-1", "prod", tail=10),
    )
    for probe in probes:
        level, rule = classify(probe.argv)
        assert level is RiskLevel.R0_READ_ONLY, (probe.argv, rule)
    assert get_pods_json("prod").argv == (
        "kubectl",
        "get",
        "pods",
        "-n",
        "prod",
        "-o",
        "json",
    )
    assert pod_logs("web-1", tail=10).argv == ("kubectl", "logs", "web-1", "--tail=10")


def test_parse_pods_summarizes_health() -> None:
    pods = parse_pods(PODS_JSON)
    assert len(pods) == 3
    assert pods[0].ready == "2/2"
    assert pods[0].restarts == 1
    assert pods[1].phase == "CrashLoopBackOff"
    assert pods[2].node == ""


def test_unhealthy_pods_flags_restarts_and_bad_phases() -> None:
    bad = unhealthy_pods(parse_pods(PODS_JSON))
    assert [pod.name for pod in bad] == ["web-1", "web-2"]


def test_parse_events_orders_oldest_first() -> None:
    events = parse_events(EVENTS_JSON)
    assert [e.reason for e in events] == ["Scheduled", "Started", "BackOff"]
    assert events[2].object == "Pod/web-2"
    assert events[2].count == 5


def test_parse_deployments_tracks_availability() -> None:
    deployments = parse_deployments(DEPLOYMENTS_JSON)
    assert deployments[0].available is False
    assert deployments[0].ready_replicas == 2
    assert deployments[1].available is True
