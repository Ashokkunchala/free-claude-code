"""Environment context collector and prompt integration."""

import json
import subprocess

from free_claude_code.terminal.env_context import (
    EnvContext,
    collect_env_context,
    render_env_context,
)
from free_claude_code.terminal.plan_gen import build_plan_prompt, propose_plan


def test_collects_platform_without_git(tmp_path):
    context = collect_env_context(str(tmp_path))
    assert context.os_name
    assert context.arch
    assert context.python_version.count(".") >= 1
    assert context.git_branch is None
    assert context.git_dirty == ()


def test_collects_git_state_and_listing(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    context = collect_env_context(str(tmp_path))
    assert context.git_branch is not None
    assert any("notes.txt" in entry for entry in context.git_dirty)
    assert "notes.txt" in context.top_level


def test_top_level_listing_is_bounded(tmp_path):
    for index in range(60):
        (tmp_path / f"f{index:02d}").touch()
    context = collect_env_context(str(tmp_path))
    assert len(context.top_level) == 50


def test_render_mentions_observed_facts(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    text = render_env_context(collect_env_context(str(tmp_path)))
    assert "Platform:" in text
    assert "Git branch:" in text
    assert "Workspace top level:" in text


def test_render_skips_absent_git():
    text = render_env_context(
        EnvContext(
            os_name="Linux",
            arch="x86_64",
            python_version="3.14.0",
            shell=None,
            git_branch=None,
        )
    )
    assert "Git branch" not in text
    assert "Shell" not in text


def test_prompt_carries_context_block():
    prompt = build_plan_prompt("tidy up", workspace="/ws", context="Platform: Linux")
    assert "Observed environment" in prompt
    assert "Platform: Linux" in prompt
    assert "tidy up" in prompt


def test_prompt_without_context_omits_block():
    prompt = build_plan_prompt("tidy up")
    assert "Observed environment" not in prompt


def test_propose_plan_threads_context_to_model():
    seen = []

    def model(prompt):
        seen.append(prompt)
        return json.dumps(
            {"goal": "g", "steps": [{"id": "s", "title": "t", "argv": ["ls"]}]}
        )

    plan = propose_plan("do it", model, workspace="/ws", context="Platform: Linux")
    assert plan.goal == "g"
    assert "Platform: Linux" in seen[0]
