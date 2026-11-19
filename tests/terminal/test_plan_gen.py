"""Plan generation: prompt shape, response parsing, validation."""

import json

import pytest

from free_claude_code.terminal import PlanError
from free_claude_code.terminal.plan_gen import (
    build_plan_prompt,
    extract_plan_json,
    plan_from_response,
    propose_plan,
)

GOOD = json.dumps(
    {
        "goal": "run tests",
        "steps": [
            {
                "id": "test",
                "title": "Run the suite",
                "argv": ["pytest", "-q"],
                "verify": {"argv": ["git", "status"], "expect_exit": 0},
            }
        ],
    }
)


def test_prompt_carries_task_schema_and_rules() -> None:
    prompt = build_plan_prompt("fix the flaky test", workspace="/repo")
    assert "fix the flaky test" in prompt
    assert "/repo" in prompt
    assert '"argv"' in prompt and "verify" in prompt
    assert "argument vector" in prompt
    assert "no Markdown" in prompt or "no prose" in prompt


def test_extract_bare_object() -> None:
    data = extract_plan_json(GOOD)
    assert data["goal"] == "run tests"


def test_extract_fenced_object() -> None:
    data = extract_plan_json(f"```json\n{GOOD}\n```")
    assert data["steps"][0]["id"] == "test"


def test_extract_leading_prose_tolerated_when_object_runs_to_end() -> None:
    data = extract_plan_json(f"Here is the plan:\n{GOOD}")
    assert data["goal"] == "run tests"


def test_extract_rejects_junk() -> None:
    with pytest.raises(PlanError, match="empty"):
        extract_plan_json("   ")
    with pytest.raises(PlanError, match="no JSON object"):
        extract_plan_json("no json here")
    with pytest.raises(PlanError, match="not valid JSON"):
        extract_plan_json("{truncated")
    with pytest.raises(PlanError, match="object"):
        extract_plan_json("[1, 2]")
    with pytest.raises(PlanError, match="not closed"):
        extract_plan_json(f"```json\n{GOOD}")


def test_plan_from_response_validates_schema() -> None:
    with pytest.raises(PlanError, match="argv"):
        plan_from_response('{"goal": "x", "steps": [{"argv": "ls"}]}')
    plan = plan_from_response(GOOD)
    assert plan.steps[0].step_id == "test"
    assert plan.steps[0].verify is not None
    assert plan.steps[0].verify.expect_exit == 0


def test_propose_plan_round_trip() -> None:
    seen = []

    def fake_model(prompt: str) -> str:
        seen.append(prompt)
        return GOOD

    plan = propose_plan("run tests", fake_model, workspace="/repo")
    assert plan.goal == "run tests"
    assert "/repo" in seen[0]
