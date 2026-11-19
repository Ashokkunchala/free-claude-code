"""Per-skill permission wiring on top of the terminal policy engine.

A skill pack declares the highest risk tier it may request (``max_risk``)
and the tier at which approvals begin (``approval_from``). Evaluation runs
the command through the base :class:`PolicyEngine` first, then applies the
pack's envelope on top: a pack can only tighten the global policy, never
loosen it. Disabled or quarantined packs are denied before any command
classification happens.
"""

from free_claude_code.skills.discovery import SkillPack
from free_claude_code.skills.state import SkillState
from free_claude_code.terminal.policy import (
    PolicyEngine,
    PolicyVerdict,
    Requirement,
    RiskLevel,
)
from free_claude_code.terminal.spec import CommandSpec


def evaluate_skill_command(
    engine: PolicyEngine,
    pack: SkillPack,
    state: SkillState,
    spec: CommandSpec,
) -> PolicyVerdict:
    """Evaluate one command attributed to a skill pack.

    Denial order: disabled packs, then unusable (broken or quarantined)
    packs, then the base engine verdict, then the pack's declared risk
    envelope, then the pack's approval threshold. Approvals the base
    engine already requires are never weakened.
    """

    if not state.is_enabled(pack.name):
        return PolicyVerdict(
            level=RiskLevel.R0_READ_ONLY,
            requirement=Requirement.DENY,
            reason=f"skill pack {pack.name!r} is disabled",
            policy_id="deny:skill-disabled",
        )
    if pack.manifest is None or not pack.usable:
        return PolicyVerdict(
            level=RiskLevel.R0_READ_ONLY,
            requirement=Requirement.DENY,
            reason=f"skill pack {pack.name!r} is not usable: {'; '.join(pack.issues)}",
            policy_id="deny:skill-unusable",
        )
    verdict = engine.evaluate(spec)
    if verdict.requirement is Requirement.DENY:
        return verdict
    manifest = pack.manifest
    if verdict.level > manifest.max_risk:
        return PolicyVerdict(
            level=verdict.level,
            requirement=Requirement.DENY,
            reason=(
                f"{verdict.reason}; skill pack {pack.name!r} declares "
                f"max_risk {manifest.max_risk.name}"
            ),
            policy_id="deny:skill-risk-envelope",
        )
    if (
        verdict.requirement is Requirement.ALLOW
        and verdict.level >= manifest.approval_from
    ):
        return PolicyVerdict(
            level=verdict.level,
            requirement=Requirement.APPROVE,
            reason=(
                f"{verdict.reason}; skill pack {pack.name!r} requires "
                f"approval from {manifest.approval_from.name}"
            ),
            policy_id="approve:skill-threshold",
        )
    return verdict
