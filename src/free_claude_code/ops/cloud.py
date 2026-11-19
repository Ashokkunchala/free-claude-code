"""Cloud read-only inventory: probe builders and JSON parsers.

Probes use the CLIs' read verbs (describe/list/show) so the policy
classifies them R0, and ask for JSON output so parsers can build typed
inventories. No creation, modification or deletion command is ever part
of an inventory plan.
"""

from dataclasses import dataclass
from typing import Any

from free_claude_code.ops.diagnostics import Probe


@dataclass(frozen=True)
class InstanceSummary:
    """One compute instance across providers."""

    provider: str
    id: str
    name: str
    state: str
    type: str
    zone: str


def aws_instances_probe() -> Probe:
    """All EC2 instances as JSON (read-only)."""

    return Probe(
        "aws-instances",
        ("aws", "ec2", "describe-instances", "--output", "json"),
    )


def aws_security_groups_probe() -> Probe:
    """All EC2 security groups as JSON (read-only)."""

    return Probe(
        "aws-security-groups",
        ("aws", "ec2", "describe-security-groups", "--output", "json"),
    )


def gcloud_instances_probe() -> Probe:
    """All GCE instances as JSON (read-only)."""

    return Probe(
        "gcloud-instances",
        ("gcloud", "compute", "instances", "list", "--format=json"),
    )


def azure_vms_probe() -> Probe:
    """All Azure VMs with power state as JSON (read-only)."""

    return Probe("az-vms", ("az", "vm", "list", "-d", "-o", "json"))


def _tag_name(tags: list[dict[str, Any]]) -> str:
    for tag in tags:
        if tag.get("Key") == "Name":
            return str(tag.get("Value", ""))
    return ""


def parse_aws_instances(payload: dict[str, Any]) -> tuple[InstanceSummary, ...]:
    """Parse ``aws ec2 describe-instances`` JSON into instances."""

    return tuple(
        InstanceSummary(
            provider="aws",
            id=instance.get("InstanceId", ""),
            name=_tag_name(instance.get("Tags", [])),
            state=instance.get("State", {}).get("Name", ""),
            type=instance.get("InstanceType", ""),
            zone=instance.get("Placement", {}).get("AvailabilityZone", ""),
        )
        for reservation in payload.get("Reservations", [])
        for instance in reservation.get("Instances", [])
    )


def parse_gcloud_instances(
    payload: list[dict[str, Any]],
) -> tuple[InstanceSummary, ...]:
    """Parse ``gcloud compute instances list --format=json`` output."""

    return tuple(
        InstanceSummary(
            provider="gcloud",
            id=str(item.get("id", "")),
            name=item.get("name", ""),
            state=item.get("status", ""),
            type=str(item.get("machineType", "")).rpartition("/")[2],
            zone=str(item.get("zone", "")).rpartition("/")[2],
        )
        for item in payload
    )


def parse_azure_vms(payload: list[dict[str, Any]]) -> tuple[InstanceSummary, ...]:
    """Parse ``az vm list -d -o json`` output."""

    return tuple(
        InstanceSummary(
            provider="azure",
            id=item.get("id", ""),
            name=item.get("name", ""),
            state=str(item.get("powerState", "")).removeprefix("VM "),
            type=item.get("hardwareProfile", {}).get("vmSize", ""),
            zone=item.get("location", ""),
        )
        for item in payload
    )


def not_running(instances: tuple[InstanceSummary, ...]) -> tuple[InstanceSummary, ...]:
    """Instances whose state is not the provider's running state."""

    running = {"running", "RUNNING", "running."}
    return tuple(i for i in instances if i.state.lower() not in running)
