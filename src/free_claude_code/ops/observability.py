"""Observability: metrics, alarms and log probes, parsers, SLO arithmetic.

Probes stay read-only - the terminal policy classifies every argument
vector here R0 - and ask for machine-readable output so parsers can
build typed summaries. SLO helpers are pure arithmetic over observed
counts; they never touch a live system.
"""

import json
from dataclasses import dataclass
from typing import Any

from free_claude_code.ops.diagnostics import Probe


@dataclass(frozen=True)
class ResourceUsage:
    """One ``kubectl top`` row."""

    name: str
    cpu: str
    memory: str


@dataclass(frozen=True)
class ContainerStats:
    """One ``docker stats --no-stream --format json`` row."""

    name: str
    cpu_percent: float
    memory_percent: float
    memory_usage: str


@dataclass(frozen=True)
class DataPoint:
    """One metric datapoint (UTC timestamp string and value)."""

    timestamp: str
    value: float


@dataclass(frozen=True)
class MetricSeries:
    """One metric series, datapoints oldest-first."""

    metric: str
    datapoints: tuple[DataPoint, ...]

    @property
    def latest(self) -> DataPoint | None:
        return self.datapoints[-1] if self.datapoints else None


@dataclass(frozen=True)
class AlarmSummary:
    """One CloudWatch alarm."""

    name: str
    state: str
    reason: str
    updated: str


def docker_stats_probe() -> Probe:
    """One-shot container resource usage as JSON lines (read-only)."""

    return Probe(
        "docker-stats",
        ("docker", "stats", "--no-stream", "--format", "json"),
    )


def kube_top_probe(resource: str = "pods", namespace: str | None = None) -> Probe:
    """Node or pod resource usage (read-only, needs metrics-server)."""

    argv = ["kubectl", "top", resource]
    if namespace is not None:
        argv += ["-n", namespace]
    return Probe(f"k8s-top-{resource}", tuple(argv))


def journalctl_probe(
    unit: str | None = None,
    *,
    since: str | None = None,
    priority: str | None = None,
    lines: int = 200,
) -> Probe:
    """Bounded systemd journal read (read-only)."""

    argv = ["journalctl", "--no-pager", "-o", "short-iso", "-n", str(lines)]
    if unit is not None:
        argv += ["-u", unit]
    if since is not None:
        argv += ["--since", since]
    if priority is not None:
        argv += ["-p", priority]
    return Probe("journalctl" + (f"-{unit}" if unit else ""), tuple(argv))


def cloudwatch_metrics_probe(
    namespace: str,
    metric: str,
    *,
    start_time: str,
    end_time: str,
    statistic: str = "Average",
    period: int = 300,
) -> Probe:
    """One CloudWatch metric window as JSON (read-only).

    ``start_time``/``end_time`` are ISO-8601 UTC timestamps; the caller
    owns the clock.
    """

    return Probe(
        f"cw-{namespace.replace('/', '-')}-{metric}",
        (
            "aws",
            "cloudwatch",
            "get-metric-statistics",
            "--namespace",
            namespace,
            "--metric-name",
            metric,
            "--statistics",
            statistic,
            "--period",
            str(period),
            "--start-time",
            start_time,
            "--end-time",
            end_time,
            "--output",
            "json",
        ),
    )


def cloudwatch_alarms_probe(prefix: str | None = None) -> Probe:
    """All CloudWatch alarms (optionally name-prefixed) as JSON."""

    argv = ["aws", "cloudwatch", "describe-alarms", "--output", "json"]
    if prefix is not None:
        argv += ["--alarm-name-prefix", prefix]
    return Probe("cw-alarms", tuple(argv))


def gcloud_logs_probe(filter_expression: str, *, limit: int = 100) -> Probe:
    """Bounded Cloud Logging read as JSON (read-only)."""

    return Probe(
        "gcloud-logs",
        (
            "gcloud",
            "logging",
            "read",
            filter_expression,
            "--limit",
            str(limit),
            "--format=json",
            "--freshness=1h",
        ),
    )


def parse_kubectl_top(output: str) -> tuple[ResourceUsage, ...]:
    """Parse ``kubectl top`` table output (header line + rows)."""

    rows: list[ResourceUsage] = []
    lines = [line for line in output.splitlines() if line.strip()]
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 3:
            rows.append(ResourceUsage(parts[0], parts[1], parts[2]))
    return tuple(rows)


def parse_docker_stats(output: str) -> tuple[ContainerStats, ...]:
    """Parse ``docker stats --format json`` output (one JSON object per line)."""

    rows: list[ContainerStats] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        rows.append(
            ContainerStats(
                name=str(payload.get("Name", "")),
                cpu_percent=_percent(payload.get("CPUPerc", "0")),
                memory_percent=_percent(payload.get("MemPerc", "0")),
                memory_usage=str(payload.get("MemUsage", "")),
            )
        )
    return tuple(rows)


def parse_cloudwatch_metrics(payload: dict[str, Any]) -> MetricSeries:
    """Parse ``get-metric-statistics`` JSON, datapoints oldest-first.

    The response carries only the metric ``Label`` (no namespace), so
    the series takes its name from the label.
    """

    points = sorted(
        (
            DataPoint(str(point.get("Timestamp", "")), float(point.get("Average", 0.0)))
            for point in payload.get("Datapoints", [])
        ),
        key=lambda point: point.timestamp,
    )
    return MetricSeries(
        metric=str(payload.get("Label", "")),
        datapoints=tuple(points),
    )


def parse_cloudwatch_alarms(payload: dict[str, Any]) -> tuple[AlarmSummary, ...]:
    """Parse ``describe-alarms`` JSON into alarm summaries."""

    return tuple(
        AlarmSummary(
            name=str(alarm.get("AlarmName", "")),
            state=str(alarm.get("StateValue", "")),
            reason=str(alarm.get("StateReason", "")),
            updated=str(alarm.get("StateUpdatedTimestamp", "")),
        )
        for alarm in payload.get("MetricAlarms", [])
    )


def alarming(alarms: tuple[AlarmSummary, ...]) -> tuple[AlarmSummary, ...]:
    """Alarms currently in ALARM state."""

    return tuple(alarm for alarm in alarms if alarm.state == "ALARM")


def _percent(raw: Any) -> float:
    return float(str(raw).rstrip("%") or 0.0)


def error_budget_remaining(total: int, bad: int, target: float) -> float:
    """Fraction of the error budget left (1.0 = untouched, <=0 = spent).

    ``target`` is the SLO as a fraction (0.999 = 99.9%). With no
    requests observed the budget is untouched.
    """

    if total <= 0:
        return 1.0
    if not 0.0 < target < 1.0:
        raise ValueError("SLO target must be between 0 and 1")
    allowed = 1.0 - target
    observed = bad / total
    return 1.0 - observed / allowed


def burn_rate(total: int, bad: int, target: float) -> float:
    """Observed error ratio relative to the budgeted ratio (1.0 = at SLO)."""

    if total <= 0:
        return 0.0
    if not 0.0 < target < 1.0:
        raise ValueError("SLO target must be between 0 and 1")
    return (bad / total) / (1.0 - target)
