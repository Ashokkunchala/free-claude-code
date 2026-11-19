"""Observability probes, parsers and SLO arithmetic."""

import pytest

from free_claude_code.ops import (
    alarming,
    burn_rate,
    cloudwatch_alarms_probe,
    cloudwatch_metrics_probe,
    docker_stats_probe,
    error_budget_remaining,
    gcloud_logs_probe,
    journalctl_probe,
    kube_top_probe,
    parse_cloudwatch_alarms,
    parse_cloudwatch_metrics,
    parse_docker_stats,
    parse_kubectl_top,
)
from free_claude_code.terminal.policy import RiskLevel, classify


def test_probes_are_policy_clean() -> None:
    probes = [
        docker_stats_probe(),
        kube_top_probe(),
        kube_top_probe("nodes"),
        kube_top_probe("pods", "prod"),
        journalctl_probe("nginx", since="1h ago", priority="err"),
        journalctl_probe(),
        cloudwatch_metrics_probe(
            "AWS/EC2",
            "CPUUtilization",
            start_time="2026-11-19T00:00:00Z",
            end_time="2026-11-19T01:00:00Z",
        ),
        cloudwatch_alarms_probe(),
        cloudwatch_alarms_probe("web-"),
        gcloud_logs_probe("severity>=ERROR"),
    ]
    for probe in probes:
        level, rule = classify(probe.argv)
        assert level == RiskLevel.R0_READ_ONLY, (probe.argv, rule)


def test_journalctl_mutations_stay_gated() -> None:
    for argv in [
        ("journalctl", "--vacuum-size=100M"),
        ("journalctl", "--vacuum-time=1d"),
        ("journalctl", "--rotate"),
        ("journalctl", "--flush"),
        ("journalctl", "--sync"),
    ]:
        level, _ = classify(argv)
        assert level == RiskLevel.R2_PRIVILEGED, argv


def test_journalctl_probe_is_bounded_and_pager_free() -> None:
    probe = journalctl_probe("nginx", lines=50)
    assert "--no-pager" in probe.argv
    assert "50" in probe.argv


def test_gcloud_logging_read_is_read_only() -> None:
    level, rule = classify(("gcloud", "logging", "read", "x", "--limit", "5"))
    assert level == RiskLevel.R0_READ_ONLY
    assert rule == "gcloud:read"


def test_parse_kubectl_top_skips_header() -> None:
    output = "NAME      CPU(cores)   MEMORY(bytes)\nweb-1     125m         256Mi\nweb-2     7m           64Mi\n"
    rows = parse_kubectl_top(output)
    assert [row.name for row in rows] == ["web-1", "web-2"]
    assert rows[0].cpu == "125m"
    assert rows[1].memory == "64Mi"


def test_parse_kubectl_top_empty() -> None:
    assert parse_kubectl_top("") == ()


def test_parse_docker_stats_json_lines() -> None:
    output = (
        '{"Name":"web","CPUPerc":"1.50%","MemPerc":"0.25%","MemUsage":"10MiB / 4GiB"}\n'
        '{"Name":"db","CPUPerc":"0.00%","MemPerc":"12.00%","MemUsage":"500MiB / 4GiB"}\n'
    )
    rows = parse_docker_stats(output)
    assert rows[0].name == "web"
    assert rows[0].cpu_percent == 1.5
    assert rows[1].memory_percent == 12.0
    assert rows[1].memory_usage == "500MiB / 4GiB"


def test_parse_cloudwatch_metrics_sorts_oldest_first() -> None:
    payload = {
        "Label": "CPUUtilization",
        "Datapoints": [
            {"Timestamp": "2026-11-19T01:00:00Z", "Average": 9.0},
            {"Timestamp": "2026-11-19T00:00:00Z", "Average": 3.5},
        ],
    }
    series = parse_cloudwatch_metrics(payload)
    assert series.metric == "CPUUtilization"
    assert [point.value for point in series.datapoints] == [3.5, 9.0]
    assert series.latest is not None
    assert series.latest.value == 9.0


def test_parse_cloudwatch_metrics_empty() -> None:
    series = parse_cloudwatch_metrics({"Label": "X", "Datapoints": []})
    assert series.latest is None


def test_parse_cloudwatch_alarms_and_filter() -> None:
    payload = {
        "MetricAlarms": [
            {
                "AlarmName": "web-5xx",
                "StateValue": "ALARM",
                "StateReason": "threshold crossed",
                "StateUpdatedTimestamp": "2026-11-19T00:05:00Z",
            },
            {
                "AlarmName": "web-cpu",
                "StateValue": "OK",
                "StateReason": "",
                "StateUpdatedTimestamp": "2026-11-18T23:00:00Z",
            },
        ]
    }
    alarms = parse_cloudwatch_alarms(payload)
    assert len(alarms) == 2
    assert [alarm.name for alarm in alarming(alarms)] == ["web-5xx"]
    assert alarming(alarms)[0].reason == "threshold crossed"


def test_error_budget_arithmetic() -> None:
    # 2% errors against a 99% SLO spends the budget twice over.
    assert error_budget_remaining(1000, 20, 0.99) == pytest.approx(-1.0)
    # 0.5% errors against a 99% SLO leaves half the budget.
    assert error_budget_remaining(1000, 5, 0.99) == pytest.approx(0.5)
    # No traffic leaves the budget untouched.
    assert error_budget_remaining(0, 0, 0.99) == 1.0


def test_burn_rate_arithmetic() -> None:
    assert burn_rate(1000, 10, 0.99) == pytest.approx(1.0)
    assert burn_rate(1000, 20, 0.99) == pytest.approx(2.0)
    assert burn_rate(0, 0, 0.99) == 0.0


def test_slo_target_must_be_a_fraction() -> None:
    with pytest.raises(ValueError, match="SLO target"):
        error_budget_remaining(100, 1, 99.0)
    with pytest.raises(ValueError, match="SLO target"):
        burn_rate(100, 1, 1.0)
