"""Skill packs: declarative manifests, validation, discovery and trust."""

from free_claude_code.skills.discovery import SkillPack, discover_packs, load_pack
from free_claude_code.skills.guidance import merged_packs, render_plan_guidance
from free_claude_code.skills.manifest import (
    MANIFEST_NAME,
    PLATFORMS,
    SkillManifest,
    ToolDef,
    load_manifest,
    parse_manifest,
    parse_risk,
)
from free_claude_code.skills.permissions import evaluate_skill_command

__all__ = [
    "MANIFEST_NAME",
    "PLATFORMS",
    "SkillManifest",
    "SkillPack",
    "ToolDef",
    "discover_packs",
    "evaluate_skill_command",
    "load_manifest",
    "load_pack",
    "merged_packs",
    "parse_manifest",
    "parse_risk",
    "render_plan_guidance",
]
