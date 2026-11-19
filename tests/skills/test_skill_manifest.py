"""Skill manifest parsing and validation."""

import pytest

from free_claude_code.skills import parse_manifest, parse_risk
from free_claude_code.terminal.policy import RiskLevel


def valid(**overrides):
    data = {
        "name": "docker-triage",
        "version": "1.2.3",
        "purpose": "Diagnose container failures",
        "platforms": ["linux", "macos"],
        "required_tools": ["docker"],
        "max_risk": "r1",
        "approval_from": "r1",
        "timeout_seconds": 120,
        "rollback": "Stop the container it started",
        "verification": ["docker ps shows the container"],
        "executable": False,
        "tools": [
            {
                "name": "docker-inspect",
                "description": "Inspect one container",
                "timeout_seconds": 30,
            }
        ],
    }
    data.update(overrides)
    return data


def test_valid_manifest_parses():
    manifest = parse_manifest(valid())
    assert manifest.name == "docker-triage"
    assert manifest.version == "1.2.3"
    assert manifest.platforms == ("linux", "macos")
    assert manifest.max_risk == RiskLevel.R1_REVERSIBLE
    assert manifest.approval_from == RiskLevel.R1_REVERSIBLE
    assert manifest.tools[0].name == "docker-inspect"
    assert manifest.tools[0].timeout_seconds == 30
    assert manifest.tools[0].max_output_bytes == 1_048_576


@pytest.mark.parametrize(
    ("raw", "level"),
    [
        ("r0", RiskLevel.R0_READ_ONLY),
        ("R2", RiskLevel.R2_PRIVILEGED),
        ("r3_destructive", RiskLevel.R3_DESTRUCTIVE),
        (" r1 ", RiskLevel.R1_REVERSIBLE),
    ],
)
def test_parse_risk_accepts_aliases(raw, level):
    assert parse_risk(raw) == level


def test_parse_risk_rejects_unknown():
    with pytest.raises(ValueError, match="Unknown risk tier"):
        parse_risk("r9")


@pytest.mark.parametrize(
    "override",
    [
        {"name": "Bad Name"},
        {"name": "1leading-digit"},
        {"version": "1.2"},
        {"version": "v1.2.3"},
        {"purpose": "  "},
        {"platforms": []},
        {"platforms": ["linux", "amiga"]},
        {"required_tools": ["docker", 7]},
        {"timeout_seconds": 0},
        {"timeout_seconds": 7200},
        {"executable": "yes"},
        {"tools": {}},
    ],
)
def test_invalid_fields_are_rejected(override):
    with pytest.raises(ValueError, match="Invalid skill manifest"):
        parse_manifest(valid(**override))


def test_every_issue_is_reported_at_once():
    with pytest.raises(ValueError) as caught:
        parse_manifest(valid(name="Bad", version="1", platforms=[]))
    message = str(caught.value)
    assert "name" in message
    assert "version" in message
    assert "platforms" in message


def test_approval_from_above_max_risk_means_no_approval_needed():
    manifest = parse_manifest(valid(max_risk="r0", approval_from="r2"))
    assert manifest.max_risk == RiskLevel.R0_READ_ONLY
    assert manifest.approval_from == RiskLevel.R2_PRIVILEGED


def test_duplicate_tool_names_are_rejected():
    tool = {"name": "inspect", "description": "Look"}
    with pytest.raises(ValueError, match="duplicate tool definition"):
        parse_manifest(valid(tools=[tool, tool]))


def test_tool_defaults_and_validation():
    manifest = parse_manifest(valid(tools=[{"name": "probe", "description": "Read"}]))
    assert manifest.tools[0].timeout_seconds == 60
    with pytest.raises(ValueError, match="timeout_seconds"):
        parse_manifest(
            valid(
                tools=[{"name": "probe", "description": "Read", "timeout_seconds": -1}]
            )
        )
    with pytest.raises(ValueError, match="needs a description"):
        parse_manifest(valid(tools=[{"name": "probe", "description": ""}]))
