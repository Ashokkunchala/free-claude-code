"""DevOps tool detection.

Each tool is probed with a fixed version command that the terminal
policy classifies R0, so detection never runs anything the policy would
gate. A tool that is not installed, or whose daemon is unreachable, is
reported unavailable - never an error.
"""

import enum
from dataclasses import dataclass

from free_claude_code.terminal.spec import DEFAULT_TIMEOUT_SECONDS

_PROBE_TIMEOUT_SECONDS = 15.0


class ToolKind(enum.StrEnum):
    """The DevOps tools the agent knows how to diagnose."""

    DOCKER = "docker"
    KUBECTL = "kubectl"
    HELM = "helm"
    TERRAFORM = "terraform"
    AWS = "aws"
    AZ = "az"
    GCLOUD = "gcloud"


@dataclass(frozen=True)
class ToolProbe:
    """How to detect one tool: a read-only version command."""

    kind: ToolKind
    argv: tuple[str, ...]
    timeout_seconds: float = _PROBE_TIMEOUT_SECONDS


@dataclass(frozen=True)
class ToolStatus:
    """Whether one tool answered its version probe, and what it said."""

    kind: ToolKind
    argv: tuple[str, ...]
    available: bool
    version_line: str = ""
    exit_code: int | None = None


DEVOPS_TOOLS: tuple[ToolProbe, ...] = (
    ToolProbe(ToolKind.DOCKER, ("docker", "--version")),
    ToolProbe(ToolKind.KUBECTL, ("kubectl", "version", "--client")),
    ToolProbe(ToolKind.HELM, ("helm", "version", "--short")),
    ToolProbe(ToolKind.TERRAFORM, ("terraform", "version")),
    ToolProbe(ToolKind.AWS, ("aws", "--version")),
    ToolProbe(ToolKind.AZ, ("az", "version")),
    ToolProbe(ToolKind.GCLOUD, ("gcloud", "--version")),
)

assert DEFAULT_TIMEOUT_SECONDS >= _PROBE_TIMEOUT_SECONDS
