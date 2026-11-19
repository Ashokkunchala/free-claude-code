"""Incident evidence collection and timeline synthesis.

``incident_plan`` assembles the read-only probes that gather evidence
for one workload (rollout status, pods, events, logs tail); the parsers
in ``ops.kubernetes`` turn the JSON probes into typed data, and
``synthesize_timeline`` orders events oldest-first so an incident
narrative can be built without re-reading raw cluster output. Evidence
collection never mutates the cluster.
"""

from dataclasses import dataclass

from free_claude_code.ops.diagnostics import Probe, rollout_status
from free_claude_code.ops.kubernetes import (
    EventSummary,
    describe_pod,
    get_events_json,
    get_pods_json,
    pod_logs,
)

DEFAULT_LOG_TAIL = 100


@dataclass(frozen=True)
class IncidentPlan:
    """The probes and focus of one evidence-collection pass."""

    subject: str
    namespace: str | None
    probes: tuple[Probe, ...]


def incident_plan(
    workload: str,
    *,
    namespace: str | None = None,
    pods: tuple[str, ...] = (),
    log_tail: int = DEFAULT_LOG_TAIL,
) -> IncidentPlan:
    """Plan read-only evidence collection for one deployment.

    ``workload`` is the deployment name; ``pods`` names the specific
    pods to describe and tail (for example the unhealthy ones from a
    prior ``get pods`` pass). Every probe is read-only.
    """

    probes: list[Probe] = [
        rollout_status(workload, namespace),
        get_pods_json(namespace),
        get_events_json(namespace),
    ]
    for pod in pods:
        probes.append(describe_pod(pod, namespace))
        probes.append(pod_logs(pod, namespace, tail=log_tail))
    return IncidentPlan(f"incident:{workload}", namespace, tuple(probes))


def synthesize_timeline(events: tuple[EventSummary, ...]) -> str:
    """Render events oldest-first as a compact incident timeline."""

    return "\n".join(
        f"{event.last_timestamp or '-'} [{event.type}] "
        f"{event.reason} {event.object} (x{event.count}): {event.message}"
        for event in events
    )


def warning_events(events: tuple[EventSummary, ...]) -> tuple[EventSummary, ...]:
    """Only the Warning events - the usual incident signal."""

    return tuple(event for event in events if event.type == "Warning")
