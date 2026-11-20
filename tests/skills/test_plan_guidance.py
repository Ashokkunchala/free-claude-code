"""Skill-pack guidance rendering and plan-prompt wiring (roadmap 5+6+8)."""

from pathlib import Path

from free_claude_code.skills import merged_packs, render_plan_guidance
from free_claude_code.skills.state import SkillState
from free_claude_code.terminal.plan_gen import build_plan_prompt, build_repair_prompt
from free_claude_code.terminal.run import RunReport, StepReport

MANIFEST = """
name = "{name}"
version = "0.1.0"
purpose = "Test pack"
platforms = ["linux"]
max_risk = "r0"
executable = false
"""


def write_pack(root: Path, name: str, instructions: str | None = None) -> Path:
    pack = root / name
    pack.mkdir(parents=True)
    (pack / "skill.toml").write_text(MANIFEST.format(name=name), encoding="utf-8")
    if instructions is not None:
        (pack / "SKILL.md").write_text(instructions, encoding="utf-8")
    return pack


def packs_in(root: Path):
    return merged_packs(root, include_bundled=False)


def test_guidance_includes_enabled_pack_instructions(tmp_path):
    write_pack(tmp_path, "docker-triage", "# Triage\n\nSteps here.\n")
    text = render_plan_guidance(packs_in(tmp_path), SkillState(tmp_path / "state.json"))
    assert "### docker-triage" in text
    assert "Steps here." in text


def test_guidance_excludes_disabled_packs(tmp_path):
    write_pack(tmp_path, "docker-triage", "Steps here.")
    state = SkillState(tmp_path / "state.json")
    state.set_enabled("docker-triage", False)
    assert render_plan_guidance(packs_in(tmp_path), state) == ""


def test_guidance_excludes_quarantined_packs(tmp_path):
    pack = write_pack(tmp_path, "broken", "Steps here.")
    (pack / "skill.toml").write_text("name = [", encoding="utf-8")
    assert (
        render_plan_guidance(packs_in(tmp_path), SkillState(tmp_path / "s.json")) == ""
    )


def test_guidance_skips_packs_without_instructions(tmp_path):
    write_pack(tmp_path, "manifest-only")
    assert (
        render_plan_guidance(packs_in(tmp_path), SkillState(tmp_path / "s.json")) == ""
    )


def test_guidance_truncates_long_pack_instructions(tmp_path):
    write_pack(tmp_path, "wordy", "x" * 5000)
    text = render_plan_guidance(
        packs_in(tmp_path), SkillState(tmp_path / "s.json"), per_pack_cap=100
    )
    assert "x" * 100 in text
    assert "x" * 101 not in text
    assert "(truncated)" in text


def test_guidance_total_cap_omits_packs_with_note(tmp_path):
    write_pack(tmp_path, "aaa-pack", "guidance for aaa")
    write_pack(tmp_path, "bbb-pack", "guidance for bbb")
    packs = packs_in(tmp_path)
    full = render_plan_guidance(packs, SkillState(tmp_path / "s.json"))
    text = render_plan_guidance(packs, SkillState(tmp_path / "s.json"), total_cap=50)
    assert len(full) > 50
    assert "aaa-pack" in text
    assert "bbb-pack" not in text
    assert "guidance budget reached" in text


def test_merged_packs_include_bundled_by_default(tmp_path):
    write_pack(tmp_path, "local-only", "Steps.")
    names = [pack.name for pack in merged_packs(tmp_path)]
    assert "local-only" in names
    assert "docker-triage" in names


def test_merged_packs_user_pack_shadows_bundled(tmp_path):
    write_pack(tmp_path, "docker-triage", "User override guidance.")
    packs = {pack.name: pack for pack in merged_packs(tmp_path)}
    assert packs["docker-triage"].instructions == "User override guidance."


def test_plan_prompt_includes_skills_block():
    prompt = build_plan_prompt("t", skills="### docker-triage\nSteps here.")
    assert "Enabled skill packs" in prompt
    assert "### docker-triage" in prompt


def test_plan_prompt_unchanged_without_skills():
    assert "Enabled skill packs" not in build_plan_prompt("t")
    assert "Enabled skill packs" not in build_plan_prompt("t", skills="")


def test_repair_prompt_includes_skills_block():
    report = RunReport(
        goal="g",
        task_id="t1",
        status="failed",
        steps=(StepReport(step_id="s1", title="t", status="failed", exit_code=1),),
        residual=(),
    )
    prompt = build_repair_prompt("t", report, skills="### docker-triage\nSteps.")
    assert "Enabled skill packs" in prompt
    assert "### docker-triage" in prompt


def test_repair_prompt_unchanged_without_skills():
    report = RunReport(
        goal="g",
        task_id="t1",
        status="failed",
        steps=(StepReport(step_id="s1", title="t", status="failed", exit_code=1),),
        residual=(),
    )
    assert "Enabled skill packs" not in build_repair_prompt("t", report)
