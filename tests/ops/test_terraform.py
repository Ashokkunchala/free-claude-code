"""Terraform drift review: exit-code semantics and summary parsing."""

from free_claude_code.ops import (
    DriftStatus,
    ProbeResult,
    parse_plan_summary,
    plan_probe,
    review_drift,
    state_list_probe,
)
from free_claude_code.terminal.policy import RiskLevel, classify


def test_plan_and_state_list_probes_are_low_risk() -> None:
    for probe in (plan_probe(), plan_probe(tool="tofu"), state_list_probe()):
        level, rule = classify(probe.argv)
        assert level in (RiskLevel.R0_READ_ONLY, RiskLevel.R1_REVERSIBLE), (
            probe.argv,
            rule,
        )
    assert plan_probe().argv == (
        "terraform",
        "plan",
        "-refresh=false",
        "-detailed-exitcode",
        "-no-color",
    )


def test_state_mutations_stay_privileged() -> None:
    for argv in (
        ("terraform", "state", "mv", "a", "b"),
        ("terraform", "state", "rm", "aws_instance.x"),
        ("terraform", "state", "pull"),
        ("terraform", "apply"),
        ("terraform", "destroy"),
    ):
        level, _ = classify(argv)
        assert level in (RiskLevel.R2_PRIVILEGED, RiskLevel.R3_DESTRUCTIVE), argv


def test_parse_plan_summary() -> None:
    output = "aws_instance.web: Refreshing state...\n\nPlan: 2 to add, 1 to change, 0 to destroy.\n"
    assert parse_plan_summary(output) == (2, 1, 0)
    assert parse_plan_summary("No changes. Your infrastructure matches.") is None


def test_review_drift_interprets_exit_codes() -> None:
    clean = review_drift(
        ProbeResult("p", ("terraform", "plan"), True, 0, "No changes.")
    )
    assert clean.status is DriftStatus.CLEAN
    drift = review_drift(
        ProbeResult(
            "p",
            ("terraform", "plan"),
            False,
            2,
            "Plan: 1 to add, 0 to change, 1 to destroy.",
        )
    )
    assert drift.status is DriftStatus.DRIFT
    assert drift.has_destroy is True
    error = review_drift(
        ProbeResult("p", ("terraform", "plan"), False, 1, "Error: backend")
    )
    assert error.status is DriftStatus.ERROR
