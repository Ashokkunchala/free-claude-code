"""Enabled/disabled state persistence for skill packs."""

import json

from free_claude_code.skills.state import SkillState


def test_packs_default_to_enabled(tmp_path):
    state = SkillState(tmp_path / "skills.json")
    assert state.is_enabled("anything")


def test_disable_persists_across_instances(tmp_path):
    path = tmp_path / "skills.json"
    SkillState(path).set_enabled("docker-triage", False)
    assert not SkillState(path).is_enabled("docker-triage")
    assert SkillState(path).is_enabled("other")


def test_enable_removes_the_flag(tmp_path):
    path = tmp_path / "skills.json"
    state = SkillState(path)
    state.set_enabled("docker-triage", False)
    state.set_enabled("docker-triage", True)
    assert SkillState(path).is_enabled("docker-triage")
    assert json.loads(path.read_text(encoding="utf-8")) == {"disabled": []}


def test_state_file_is_valid_sorted_json(tmp_path):
    path = tmp_path / "nested" / "skills.json"
    state = SkillState(path)
    state.set_enabled("zeta", False)
    state.set_enabled("alpha", False)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "disabled": ["alpha", "zeta"]
    }


def test_missing_parent_directories_are_created(tmp_path):
    path = tmp_path / "deep" / "deeper" / "skills.json"
    SkillState(path).set_enabled("pack", False)
    assert path.is_file()
