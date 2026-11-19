"""Skill-pack discovery: find, load and trust-gate packs on disk.

Discovery scans a packs directory for ``*/skill.toml`` manifests.
Every pack reports its issues instead of raising, so one broken pack
never hides the healthy ones. Packs declaring executable code are
only usable from a trusted source root; anything else is quarantined
with an issue instead of being imported (roadmap section 8).
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from free_claude_code.skills.manifest import (
    INSTRUCTIONS_NAME,
    MANIFEST_NAME,
    SkillManifest,
    load_manifest,
)


@dataclass(frozen=True)
class SkillPack:
    """One discovered skill pack: manifest, location and trust state."""

    manifest: SkillManifest | None
    path: Path
    instructions: str
    trusted: bool
    issues: tuple[str, ...] = field(default_factory=tuple)

    @property
    def name(self) -> str:
        """Manifest name, or the directory name when the manifest failed."""

        if self.manifest is not None:
            return self.manifest.name
        return self.path.name

    @property
    def usable(self) -> bool:
        """Valid packs are usable; executable packs must also be trusted."""

        if self.manifest is None or self.issues:
            return False
        return not self.manifest.executable or self.trusted


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def load_pack(path: Path, trusted_roots: tuple[Path, ...]) -> SkillPack:
    """Load one pack directory, never raising for pack-side problems."""

    issues: list[str] = []
    manifest: SkillManifest | None = None
    manifest_path = path / MANIFEST_NAME
    if not manifest_path.is_file():
        issues.append(f"missing {MANIFEST_NAME}")
    else:
        try:
            manifest = load_manifest(manifest_path)
        except (ValueError, tomllib.TOMLDecodeError) as exc:
            issues.append(str(exc))
    instructions_path = path / INSTRUCTIONS_NAME
    instructions = ""
    if instructions_path.is_file():
        instructions = instructions_path.read_text(encoding="utf-8")
    trusted = any(_is_within(path, root) for root in trusted_roots)
    if manifest is not None and manifest.executable and not trusted:
        issues.append(
            "pack declares executable code but is outside every trusted "
            "source root; it stays quarantined"
        )
    return SkillPack(
        manifest=manifest,
        path=path,
        instructions=instructions,
        trusted=trusted,
        issues=tuple(issues),
    )


def discover_packs(root: Path, trusted_roots: tuple[Path, ...] = ()) -> list[SkillPack]:
    """Load every pack directory under ``root``, sorted by pack name."""

    if not root.is_dir():
        return []
    packs = [
        load_pack(child, trusted_roots) for child in root.iterdir() if child.is_dir()
    ]
    return sorted(packs, key=lambda pack: pack.name)
