"""Skill packs: declarative manifests, validation, discovery and trust."""

from free_claude_code.skills.discovery import SkillPack, discover_packs, load_pack
from free_claude_code.skills.manifest import (
    MANIFEST_NAME,
    PLATFORMS,
    SkillManifest,
    ToolDef,
    load_manifest,
    parse_manifest,
    parse_risk,
)

__all__ = [
    "MANIFEST_NAME",
    "PLATFORMS",
    "SkillManifest",
    "SkillPack",
    "ToolDef",
    "discover_packs",
    "load_manifest",
    "load_pack",
    "parse_manifest",
    "parse_risk",
]
