"""Repair prompt construction and model-driven plan repair."""

import json

import pytest

from free_claude_code.terminal.plan_gen import build_repair_prompt
from free_claude_code.terminal.plan_model import propose_repair
from free_claude_code.terminal.run import PlanError, RunReport, StepReport


def report(status="failed"):
    return RunReport(
        goal="install widget",
        task_id="t1",
        status=status,
        steps=(
            StepReport(step_id="s1", title="check os", status="ok", exit_code=0),
            StepReport(
                step_id="s2",
                title="copy binary",
                status="failed",
                reason="no such file",
                exit_code=1,
            ),
        ),
        residual=("s3",),
    )


def test_repair_prompt_summarizes_run():
    prompt = build_repair_prompt("install widget", report(), workspace="/ws")
    assert "install widget" in prompt
    assert "s1: check os" in prompt
    assert "do not repeat" in prompt
    assert "s2: copy binary" in prompt
    assert "no such file" in prompt
    assert "exit 1" in prompt
    assert "s3" in prompt
    assert "ONLY the remaining work" in prompt


def test_repair_prompt_includes_context_block():
    prompt = build_repair_prompt("t", report(), context="Platform: Linux")
    assert "Observed environment" in prompt
    assert "Platform: Linux" in prompt


def test_propose_repair_validates_model_json():
    seen = []

    def model(prompt):
        seen.append(prompt)
        return json.dumps(
            {"goal": "g", "steps": [{"id": "r1", "title": "fix", "argv": ["ls"]}]}
        )

    plan = propose_repair("install widget", report(), model, workspace="/ws")
    assert plan.steps[0].step_id == "r1"
    assert "install widget" in seen[0]


def test_propose_repair_retries_with_feedback_then_raises():
    prompts = []

    def junk(prompt):
        prompts.append(prompt)
        return "not json"

    with pytest.raises(PlanError):
        propose_repair("t", report(), junk, attempts=2)
    assert len(prompts) == 2
    assert "rejected" in prompts[1]


def test_propose_repair_recovers_after_junk():
    calls = []

    def flaky(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return "```json\nnot closed"
        return json.dumps(
            {"goal": "g", "steps": [{"id": "r1", "title": "fix", "argv": ["ls"]}]}
        )

    plan = propose_repair("t", report(), flaky)
    assert plan.goal == "g"
