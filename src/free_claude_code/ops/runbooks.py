"""Incident runbooks and postmortem synthesis.

A runbook is a named, ordered list of evidence-collection steps; every
step expands to read-only probes from the other ops modules, so running
a runbook can never mutate anything. ``render_postmortem`` turns the
collected evidence (timeline, alarms, action items) into a markdown
postmortem skeleton for the human to finish.
"""

from collections.abc import Callable
from dataclasses import dataclass

from free_claude_code.ops.diagnostics import Probe, rollout_status
from free_claude_code.ops.kubernetes import (
    describe_pod,
    get_deployments_json,
    get_events_json,
    get_pods_json,
    pod_logs,
)
from free_claude_code.ops.observability import (
    AlarmSummary,
    docker_stats_probe,
    journalctl_probe,
    kube_top_probe,
)
from free_claude_code.ops.terraform import plan_probe, state_list_probe


@dataclass(frozen=True)
class RunbookStep:
    """One labelled evidence-collection step and its probes."""

    label: str
    probes: tuple[Probe, ...]


@dataclass(frozen=True)
class Runbook:
    """A named incident runbook: what it is for and what it gathers."""

    name: str
    summary: str
    steps: tuple[RunbookStep, ...]

    @property
    def probes(self) -> tuple[Probe, ...]:
        """Every probe across all steps, in order."""

        return tuple(probe for step in self.steps for probe in step.probes)


def pod_crashloop(workload: str, namespace: str | None = None) -> Runbook:
    """A pod keeps restarting: logs, describe, events, node pressure."""

    return Runbook(
        "pod-crashloop",
        f"Why pods of {workload} keep restarting",
        (
            RunbookStep(
                "Current pod state and restart counts",
                (get_pods_json(namespace),),
            ),
            RunbookStep(
                "Recent Warning events",
                (get_events_json(namespace),),
            ),
            RunbookStep(
                "Failing pod details and last logs",
                (
                    describe_pod(workload, namespace),
                    pod_logs(workload, namespace),
                    pod_logs(workload, namespace, previous=True),
                ),
            ),
            RunbookStep(
                "Resource pressure on the workload's nodes",
                (kube_top_probe("pods", namespace), kube_top_probe("nodes")),
            ),
        ),
    )


def deployment_stuck(workload: str, namespace: str | None = None) -> Runbook:
    """A rollout is not progressing: rollout status, deployments, events."""

    return Runbook(
        "deployment-stuck",
        f"Why the {workload} rollout is not progressing",
        (
            RunbookStep(
                "Rollout status and history",
                (rollout_status(workload, namespace),),
            ),
            RunbookStep(
                "Deployment replica states",
                (get_deployments_json(namespace),),
            ),
            RunbookStep(
                "Pod scheduling state and events",
                (get_pods_json(namespace), get_events_json(namespace)),
            ),
        ),
    )


def node_pressure(unit: str | None = None) -> Runbook:
    """A host is under CPU/memory pressure: stats, top, system logs."""

    return Runbook(
        "node-pressure",
        "Host CPU/memory pressure evidence",
        (
            RunbookStep("Container resource usage", (docker_stats_probe(),)),
            RunbookStep(
                "Cluster resource usage",
                (kube_top_probe("nodes"), kube_top_probe("pods")),
            ),
            RunbookStep(
                "System journal errors",
                (journalctl_probe(unit, priority="err", since="1h ago"),),
            ),
        ),
    )


def terraform_drift() -> Runbook:
    """Infrastructure drifted from the Terraform state: plan + state."""

    return Runbook(
        "terraform-drift",
        "What drifted from the Terraform state",
        (
            RunbookStep(
                "Plan with detailed exit code (0 clean, 2 drift)",
                (plan_probe(),),
            ),
            RunbookStep("Resources in state", (state_list_probe(),)),
        ),
    )


_BUILDERS: dict[str, Callable[..., Runbook]] = {
    "pod-crashloop": pod_crashloop,
    "deployment-stuck": deployment_stuck,
    "node-pressure": node_pressure,
    "terraform-drift": terraform_drift,
}


def runbook_names() -> tuple[str, ...]:
    """The built-in runbook names."""

    return tuple(sorted(_BUILDERS))


def build_runbook(name: str, **params: object) -> Runbook:
    """Build one runbook by name, or raise KeyError listing the names."""

    try:
        builder = _BUILDERS[name]
    except KeyError:
        raise KeyError(
            f"Unknown runbook {name!r}; known: {', '.join(runbook_names())}"
        ) from None
    return builder(**params)


@dataclass(frozen=True)
class Postmortem:
    """A postmortem skeleton rendered from incident evidence."""

    title: str
    summary: str
    impact: str
    timeline: str
    alarms: tuple[AlarmSummary, ...]
    action_items: tuple[str, ...]

    def render(self) -> str:
        """Markdown postmortem with the evidence filled in."""

        sections = [
            f"# Postmortem: {self.title}",
            f"## Summary\n{self.summary}",
            f"## Impact\n{self.impact}",
            f"## Timeline\n{self.timeline or '(no events collected)'}",
        ]
        if self.alarms:
            lines = [
                f"- {alarm.name} ({alarm.state}) {alarm.updated}: {alarm.reason}"
                for alarm in self.alarms
            ]
            sections.append("## Alarms\n" + "\n".join(lines))
        if self.action_items:
            items = "\n".join(f"- [ ] {item}" for item in self.action_items)
            sections.append("## Action items\n" + items)
        return "\n\n".join(sections) + "\n"
