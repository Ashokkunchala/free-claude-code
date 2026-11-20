"""Skill-pack guidance for plan-generation prompts (roadmap sections 5+6+8).

Enabled, usable packs carry operator knowledge (how to triage Docker, how to
review Terraform drift) that the plan model cannot know from the environment
snapshot alone. ``render_plan_guidance`` condenses those instructions into a
bounded text block the planning prompt can include; disabled, malformed or
quarantined packs never reach the model, and both per-pack and total length
are capped so a large pack collection cannot flood the prompt.
"""

from pathlib import Path

from free_claude_code.skills.discovery import SkillPack, discover_packs
from free_claude_code.skills.state import SkillState, bundled_packs_dir

#: Per-pack instruction cap; long packs keep their opening guidance.
MAX_PACK_INSTRUCTIONS_CHARS = 1500
#: Total guidance cap across all packs in one prompt.
MAX_TOTAL_GUIDANCE_CHARS = 8000


def merged_packs(
    packs_root: Path,
    *,
    trusted_roots: tuple[Path, ...] | None = None,
    include_bundled: bool = True,
) -> list[SkillPack]:
    """Bundled packs plus user packs; same merge rule as ``fcc-skills list``."""

    root = packs_root.resolve()
    trusted = trusted_roots if trusted_roots is not None else (root,)
    packs = discover_packs(root, trusted)
    if not include_bundled:
        return packs
    bundled_root = bundled_packs_dir()
    if bundled_root.resolve() == root:
        return packs
    merged = {pack.name: pack for pack in discover_packs(bundled_root, trusted)}
    for pack in packs:  # user packs shadow bundled packs of the same name
        merged[pack.name] = pack
    return sorted(merged.values(), key=lambda pack: pack.name)


def render_plan_guidance(
    packs: list[SkillPack],
    state: SkillState,
    *,
    per_pack_cap: int = MAX_PACK_INSTRUCTIONS_CHARS,
    total_cap: int = MAX_TOTAL_GUIDANCE_CHARS,
) -> str:
    """Bounded guidance text from every enabled, usable pack with instructions."""

    sections: list[str] = []
    used = 0
    omitted = 0
    for pack in packs:
        if not pack.usable or not state.is_enabled(pack.name):
            continue
        instructions = pack.instructions.strip()
        if not instructions:
            continue
        if len(instructions) > per_pack_cap:
            instructions = instructions[:per_pack_cap].rstrip() + "\n(truncated)"
        section = f"### {pack.name}\n{instructions}"
        if used + len(section) > total_cap:
            omitted += 1
            continue
        sections.append(section)
        used += len(section)
    if omitted:
        sections.append(f"({omitted} more pack(s) omitted: guidance budget reached)")
    return "\n\n".join(sections)
