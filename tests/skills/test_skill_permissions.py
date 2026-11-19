"""Per-skill permission wiring over the terminal policy engine."""

from pathlib import Path

from free_claude_code.skills import load_pack, parse_manifest
from free_claude_code.skills.permissions import evaluate_skill_command
from free_claude_code.skills.state import SkillState
from free_claude_code.terminal.policy import (
    PolicyEngine,
    Requirement,
    RiskLevel,
)
from free_claude_code.terminal.spec import CommandSpec

MANIFEST = """
name = "docker-triage"
version = "0.1.0"
purpose = "Diagnose container failures"
platforms = ["linux"]
max_risk = "{max_risk}"
approval_from = "{approval_from}"
executable = false
"""


def make_pack(
    root: Path,
    *,
    max_risk: str = "r2",
    approval_from: str = "r2",
    body=None,
):
    pack_dir = root / "docker-triage"
    pack_dir.mkdir()
    manifest = (
        body
        if body is not None
        else MANIFEST.format(max_risk=max_risk, approval_from=approval_from)
    )
    (pack_dir / "skill.toml").write_text(manifest, encoding="utf-8")
    return load_pack(pack_dir, ())


def make_state(root: Path) -> SkillState:
    return SkillState(root / "skills.json")


def spec(*argv: str) -> CommandSpec:
    return CommandSpec(argv=argv)


def test_disabled_pack_is_denied_before_classification(tmp_path):
    pack = make_pack(tmp_path)
    state = make_state(tmp_path)
    state.set_enabled("docker-triage", False)
    verdict = evaluate_skill_command(PolicyEngine(), pack, state, spec("ls"))
    assert verdict.requirement is Requirement.DENY
    assert verdict.policy_id == "deny:skill-disabled"


def test_broken_manifest_pack_is_denied(tmp_path):
    pack = make_pack(tmp_path, body="name = [")
    state = make_state(tmp_path)
    verdict = evaluate_skill_command(PolicyEngine(), pack, state, spec("ls"))
    assert verdict.requirement is Requirement.DENY
    assert verdict.policy_id == "deny:skill-unusable"


def test_quarantined_executable_pack_is_denied(tmp_path):
    body = MANIFEST.format(max_risk="r0", approval_from="r0").replace(
        "executable = false", "executable = true"
    )
    pack = make_pack(tmp_path, body=body)
    state = make_state(tmp_path)
    verdict = evaluate_skill_command(PolicyEngine(), pack, state, spec("ls"))
    assert verdict.requirement is Requirement.DENY
    assert verdict.policy_id == "deny:skill-unusable"


def test_within_envelope_passes_base_verdict_through(tmp_path):
    pack = make_pack(tmp_path, max_risk="r2", approval_from="r2")
    state = make_state(tmp_path)
    verdict = evaluate_skill_command(PolicyEngine(), pack, state, spec("ls"))
    assert verdict.requirement is Requirement.ALLOW
    assert verdict.level is RiskLevel.R0_READ_ONLY


def test_command_above_max_risk_is_denied(tmp_path):
    pack = make_pack(tmp_path, max_risk="r0", approval_from="r0")
    state = make_state(tmp_path)
    verdict = evaluate_skill_command(PolicyEngine(), pack, state, spec("pytest"))
    assert verdict.requirement is Requirement.DENY
    assert verdict.policy_id == "deny:skill-risk-envelope"
    assert verdict.level is RiskLevel.R1_REVERSIBLE


def test_approval_from_tightens_global_allow(tmp_path):
    pack = make_pack(tmp_path, max_risk="r1", approval_from="r1")
    state = make_state(tmp_path)
    verdict = evaluate_skill_command(PolicyEngine(), pack, state, spec("pytest"))
    assert verdict.requirement is Requirement.APPROVE
    assert verdict.policy_id == "approve:skill-threshold"


def test_approval_verdict_gates_execution_until_approved(tmp_path):
    pack = make_pack(tmp_path, max_risk="r1", approval_from="r1")
    state = make_state(tmp_path)
    verdict = evaluate_skill_command(PolicyEngine(), pack, state, spec("pytest"))
    assert not verdict.to_decision().allowed
    assert verdict.to_decision(approved=True).allowed


def test_base_denial_is_never_weakened(tmp_path):
    pack = make_pack(tmp_path, max_risk="r3", approval_from="r0")
    state = make_state(tmp_path)
    verdict = evaluate_skill_command(PolicyEngine(), pack, state, spec("sudo", "ls"))
    assert verdict.requirement is Requirement.DENY
    assert verdict.policy_id == "deny:elevation"


def test_base_approval_requirement_is_never_weakened(tmp_path):
    pack = make_pack(tmp_path, max_risk="r2", approval_from="r0")
    state = make_state(tmp_path)
    verdict = evaluate_skill_command(
        PolicyEngine(), pack, state, spec("git", "push", "origin", "main")
    )
    assert verdict.requirement is Requirement.APPROVE
    assert verdict.policy_id == "policy:git:push"


def test_detailed_approval_survives_the_envelope(tmp_path):
    pack = make_pack(tmp_path, max_risk="r3", approval_from="r0")
    state = make_state(tmp_path)
    verdict = evaluate_skill_command(
        PolicyEngine(), pack, state, spec("git", "clean", "-fd")
    )
    assert verdict.requirement is Requirement.APPROVE_DETAILED
    assert verdict.policy_id == "policy:git:clean"


def test_manifest_parse_risk_levels_feed_the_envelope():
    manifest = parse_manifest(
        {
            "name": "pack",
            "version": "1.0.0",
            "purpose": "demo",
            "platforms": ["linux"],
            "max_risk": "r1",
            "approval_from": "r2",
        }
    )
    assert manifest.max_risk is RiskLevel.R1_REVERSIBLE
    assert manifest.approval_from is RiskLevel.R2_PRIVILEGED
