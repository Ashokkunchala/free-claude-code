"""Kubernetes inspection: JSON parsing for read-only kubectl probes.

Probe builders emit ``-o json`` forms of the read-only get/describe/logs
commands; parsers turn the JSON payloads into typed summaries the agent
can reason over without re-reading raw tables. Everything here stays
read-only - mutating kubectl commands never appear in a plan.
"""

from dataclasses import dataclass
from typing import Any

from free_claude_code.ops.diagnostics import Probe


@dataclass(frozen=True)
class PodSummary:
    """One pod's identity and health, from ``kubectl get pods -o json``."""

    name: str
    namespace: str
    phase: str
    ready: str
    restarts: int
    node: str = ""


@dataclass(frozen=True)
class EventSummary:
    """One cluster event, from ``kubectl get events -o json``."""

    reason: str
    type: str
    object: str
    message: str
    count: int
    last_timestamp: str


@dataclass(frozen=True)
class DeploymentSummary:
    """One deployment's desired vs actual state."""

    name: str
    namespace: str
    replicas: int
    ready_replicas: int
    updated_replicas: int
    available: bool


def get_pods_json(namespace: str | None = None) -> Probe:
    """All pods as JSON, cluster-wide or in one namespace."""

    scope = ("-n", namespace) if namespace else ("-A",)
    return Probe("k8s-pods-json", ("kubectl", "get", "pods", *scope, "-o", "json"))


def get_events_json(namespace: str | None = None) -> Probe:
    """Recent events as JSON, cluster-wide or in one namespace."""

    scope = ("-n", namespace) if namespace else ("-A",)
    return Probe("k8s-events-json", ("kubectl", "get", "events", *scope, "-o", "json"))


def get_deployments_json(namespace: str | None = None) -> Probe:
    """All deployments as JSON, cluster-wide or in one namespace."""

    scope = ("-n", namespace) if namespace else ("-A",)
    return Probe(
        "k8s-deployments-json", ("kubectl", "get", "deployments", *scope, "-o", "json")
    )


def describe_pod(pod: str, namespace: str | None = None) -> Probe:
    """One pod's describe output (read-only)."""

    argv = ("kubectl", "describe", "pod", pod)
    if namespace:
        argv = (*argv, "-n", namespace)
    return Probe("k8s-describe-pod", argv)


def pod_logs(
    pod: str,
    namespace: str | None = None,
    *,
    tail: int = 50,
    previous: bool = False,
) -> Probe:
    """A bounded tail of one pod's logs (read-only).

    ``previous`` reads the last terminated container's logs - the
    evidence that matters for a crash-looping pod.
    """

    argv = ("kubectl", "logs", pod, f"--tail={tail}")
    if previous:
        argv = (*argv, "--previous")
    if namespace:
        argv = (*argv, "-n", namespace)
    return Probe("k8s-pod-logs" + ("-previous" if previous else ""), argv)


def parse_pods(payload: dict[str, Any]) -> tuple[PodSummary, ...]:
    """Parse the items of a ``kubectl get pods -o json`` payload."""

    pods = []
    for item in payload.get("items", []):
        meta = item.get("metadata", {})
        status = item.get("status", {})
        spec = item.get("spec", {})
        containers = status.get("containerStatuses", [])
        ready_count = sum(1 for c in containers if c.get("ready"))
        restarts = sum(int(c.get("restartCount", 0)) for c in containers)
        pods.append(
            PodSummary(
                name=meta.get("name", ""),
                namespace=meta.get("namespace", ""),
                phase=status.get("phase", ""),
                ready=f"{ready_count}/{len(containers)}",
                restarts=restarts,
                node=spec.get("nodeName", ""),
            )
        )
    return tuple(pods)


def parse_events(payload: dict[str, Any]) -> tuple[EventSummary, ...]:
    """Parse and order (oldest first) a ``get events -o json`` payload."""

    events = []
    for item in payload.get("items", []):
        ref = item.get("involvedObject", {})
        events.append(
            EventSummary(
                reason=item.get("reason", ""),
                type=item.get("type", ""),
                object=f"{ref.get('kind', '')}/{ref.get('name', '')}",
                message=item.get("message", ""),
                count=int(item.get("count", 1)),
                last_timestamp=item.get("lastTimestamp", ""),
            )
        )
    return tuple(sorted(events, key=lambda e: e.last_timestamp))


def parse_deployments(payload: dict[str, Any]) -> tuple[DeploymentSummary, ...]:
    """Parse the items of a ``kubectl get deployments -o json`` payload."""

    deployments = []
    for item in payload.get("items", []):
        meta = item.get("metadata", {})
        spec = item.get("spec", {})
        status = item.get("status", {})
        replicas = int(spec.get("replicas", 0))
        ready = int(status.get("readyReplicas", 0))
        deployments.append(
            DeploymentSummary(
                name=meta.get("name", ""),
                namespace=meta.get("namespace", ""),
                replicas=replicas,
                ready_replicas=ready,
                updated_replicas=int(status.get("updatedReplicas", 0)),
                available=ready >= replicas > 0,
            )
        )
    return tuple(deployments)


def unhealthy_pods(pods: tuple[PodSummary, ...]) -> tuple[PodSummary, ...]:
    """Pods that are not Running, not fully ready, or restarting."""

    def bad(pod: PodSummary) -> bool:
        total = pod.ready.partition("/")[2]
        return (
            pod.phase not in ("Running", "Succeeded")
            or pod.ready != f"{total}/{total}"
            or pod.restarts > 0
        )

    return tuple(pod for pod in pods if bad(pod))
