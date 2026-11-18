"""Terraform/OpenTofu plan inspection and drift review.

``plan_probe`` runs ``plan -refresh=false -detailed-exitcode`` - a pure
read of the configuration against the last known state, whose exit code
(0 clean, 2 drift, 1 error) is data, not a failure. ``review_drift``
interprets that exit code and parses the human-readable summary line.
State mutation and apply/destroy stay behind the policy engine's R2/R3
gates and never appear here.
"""

import enum
import re
from dataclasses import dataclass

from free_claude_code.ops.diagnostics import Probe, ProbeResult

_PLAN_TIMEOUT_SECONDS = 300.0
_SUMMARY_RE = re.compile(
    r"Plan: (?P<add>\d+) to add, (?P<change>\d+) to change, "
    r"(?P<destroy>\d+) to destroy"
)


class DriftStatus(enum.StrEnum):
    """What the plan's detailed exit code says about drift."""

    CLEAN = "clean"
    DRIFT = "drift"
    ERROR = "error"


@dataclass(frozen=True)
class DriftReport:
    """A reviewed plan: status plus the parsed change counts."""

    status: DriftStatus
    exit_code: int | None
    to_add: int
    to_change: int
    to_destroy: int
    output_tail: str

    @property
    def has_destroy(self) -> bool:
        """Planned destroys always deserve a human's eyes."""

        return self.to_destroy > 0


def plan_probe(
    *, tool: str = "terraform", timeout: float = _PLAN_TIMEOUT_SECONDS
) -> Probe:
    """Read-only plan with detailed exit code (0 clean, 2 drift, 1 error)."""

    return Probe(
        "terraform-plan",
        (tool, "plan", "-refresh=false", "-detailed-exitcode", "-no-color"),
        timeout_seconds=timeout,
    )


def state_list_probe(*, tool: str = "terraform") -> Probe:
    """Resource addresses in the current state (read-only)."""

    return Probe("terraform-state-list", (tool, "state", "list"))


def parse_plan_summary(output: str) -> tuple[int, int, int] | None:
    """Parse "Plan: X to add, Y to change, Z to destroy." if present."""

    match = _SUMMARY_RE.search(output)
    if match is None:
        return None
    return (
        int(match.group("add")),
        int(match.group("change")),
        int(match.group("destroy")),
    )


def review_drift(result: ProbeResult) -> DriftReport:
    """Interpret a plan probe result: exit 2 is drift, not failure."""

    counts = parse_plan_summary(result.output_tail) or (0, 0, 0)
    if result.exit_code == 0:
        status = DriftStatus.CLEAN
    elif result.exit_code == 2:
        status = DriftStatus.DRIFT
    else:
        status = DriftStatus.ERROR
    return DriftReport(
        status=status,
        exit_code=result.exit_code,
        to_add=counts[0],
        to_change=counts[1],
        to_destroy=counts[2],
        output_tail=result.output_tail,
    )
