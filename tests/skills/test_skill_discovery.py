"""Skill-pack discovery, trust gating and quarantine."""

from pathlib import Path

from free_claude_code.skills import discover_packs, load_pack

MANIFEST = """
name = "docker-triage"
version = "0.1.0"
purpose = "Diagnose container failures"
platforms = ["linux"]
max_risk = "r0"
executable = {executable}
"""


def write_pack(
    root: Path, name: str, *, executable: bool = False, body=None, title=None
) -> Path:
    pack = root / name
    pack.mkdir(parents=True)
    if body is None:
        body = MANIFEST.format(executable=str(executable).lower())
    if title is not None:
        body = body.replace('name = "docker-triage"', f'name = "{title}"')
    (pack / "skill.toml").write_text(body, encoding="utf-8")
    return pack


def test_discover_loads_valid_pack(tmp_path):
    write_pack(tmp_path, "docker-triage")
    packs = discover_packs(tmp_path)
    assert len(packs) == 1
    pack = packs[0]
    assert pack.name == "docker-triage"
    assert pack.manifest is not None
    assert not pack.issues
    assert pack.usable


def test_instructions_are_loaded(tmp_path):
    pack_dir = write_pack(tmp_path, "docker-triage")
    (pack_dir / "SKILL.md").write_text("# Triage\n\nSteps here.\n", encoding="utf-8")
    pack = load_pack(pack_dir, ())
    assert "Steps here." in pack.instructions


def test_broken_manifest_is_quarantined_not_raised(tmp_path):
    write_pack(tmp_path, "broken", body="name = [")
    packs = discover_packs(tmp_path)
    assert len(packs) == 1
    assert packs[0].manifest is None
    assert packs[0].issues
    assert not packs[0].usable


def test_missing_manifest_is_reported(tmp_path):
    (tmp_path / "empty-pack").mkdir()
    pack = load_pack(tmp_path / "empty-pack", ())
    assert pack.manifest is None
    assert any("missing skill.toml" in issue for issue in pack.issues)


def test_executable_pack_outside_trusted_roots_is_quarantined(tmp_path):
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    untrusted_dir = write_pack(tmp_path / "downloads", "evil-pack", executable=True)
    pack = load_pack(untrusted_dir, (trusted,))
    assert not pack.trusted
    assert not pack.usable
    assert any("quarantined" in issue for issue in pack.issues)


def test_executable_pack_inside_trusted_root_is_usable(tmp_path):
    trusted = tmp_path / "trusted"
    pack_dir = write_pack(trusted, "runner-pack", executable=True)
    pack = load_pack(pack_dir, (trusted,))
    assert pack.trusted
    assert pack.usable


def test_nonexecutable_pack_is_usable_anywhere(tmp_path):
    pack_dir = write_pack(tmp_path / "downloads", "notes-pack")
    pack = load_pack(pack_dir, ())
    assert pack.usable


def test_packs_are_sorted_and_missing_root_is_empty(tmp_path):
    write_pack(tmp_path, "zeta")
    write_pack(tmp_path, "alpha", title="alpha-triage")
    assert [pack.name for pack in discover_packs(tmp_path)] == [
        "alpha-triage",
        "docker-triage",
    ]
    assert discover_packs(tmp_path / "nowhere") == []
