"""Bundled skill packs ship valid, declarative, discoverable manifests."""

from free_claude_code.skills.discovery import discover_packs
from free_claude_code.skills.state import bundled_packs_dir


def test_bundled_packs_dir_exists() -> None:
    assert bundled_packs_dir().is_dir()


def test_bundled_packs_all_load_without_issues() -> None:
    packs = discover_packs(bundled_packs_dir())
    assert packs, "expected at least one bundled skill pack"
    for pack in packs:
        assert pack.manifest is not None, f"{pack.path.name}: {pack.issues}"
        assert not pack.issues, f"{pack.path.name}: {pack.issues}"
        assert pack.usable, f"{pack.path.name} must be usable out of the box"
        assert pack.instructions.strip(), f"{pack.path.name} needs SKILL.md"


def test_bundled_packs_are_declarative_only() -> None:
    for pack in discover_packs(bundled_packs_dir()):
        assert pack.manifest is not None
        assert not pack.manifest.executable, (
            f"bundled pack {pack.name} must not declare executable code"
        )


def test_bundled_pack_names_are_unique() -> None:
    names = [pack.name for pack in discover_packs(bundled_packs_dir())]
    assert len(names) == len(set(names))


def test_bundled_packs_cover_the_roadmap_families() -> None:
    names = {pack.name for pack in discover_packs(bundled_packs_dir())}
    assert {
        "aws-inventory",
        "ci-triage",
        "docker-triage",
        "k8s-inspect",
        "log-correlation",
        "terraform-drift",
    } <= names
