"""Plan-model adapter: env config, chat transport, feedback retry."""

import json

import pytest

from free_claude_code.terminal.plan_model import (
    ChatCompletionsModel,
    PlanModelError,
    plan_model_from_env,
    propose_plan_with_feedback,
)
from free_claude_code.terminal.run import PlanError

_ENV = {
    "FCC_PLAN_BASE_URL": "http://model.test/v1",
    "FCC_PLAN_MODEL": "planner-1",
    "FCC_PLAN_API_KEY": "secret-key",
}

_PLAN = {"goal": "demo", "steps": [{"id": "s1", "title": "list", "argv": ["ls"]}]}


def transport_ok(url, payload, headers, timeout):
    assert url == "http://model.test/v1/chat/completions"
    assert headers["Authorization"] == "Bearer secret-key"
    assert payload["stream"] is False
    return {"choices": [{"message": {"content": json.dumps(_PLAN)}}]}


def test_model_posts_prompt_and_returns_content():
    seen = {}

    def transport(url, payload, headers, timeout):
        seen.update(payload)
        return transport_ok(url, payload, headers, timeout)

    model = ChatCompletionsModel(
        base_url="http://model.test/v1/",
        model="planner-1",
        api_key="secret-key",
        transport=transport,
    )
    text = model("plan this")
    assert json.loads(text) == _PLAN
    assert seen["messages"][0] == {"role": "user", "content": "plan this"}
    assert seen["model"] == "planner-1"


def test_model_omits_auth_header_without_key():
    captured = {}

    def transport(url, payload, headers, timeout):
        captured.update(headers)
        return {"choices": [{"message": {"content": "{}"}}]}

    model = ChatCompletionsModel(
        base_url="http://model.test", model="m", transport=transport
    )
    model("p")
    assert "Authorization" not in captured


def test_model_rejects_empty_and_malformed_responses():
    for body in ({}, {"choices": []}, {"choices": [{"message": {"content": "  "}}]}):
        model = ChatCompletionsModel(
            base_url="http://model.test",
            model="m",
            transport=lambda *a, body=body: body,
        )
        with pytest.raises(PlanModelError):
            model("p")


def test_from_env_builds_adapter():
    model = plan_model_from_env(_ENV)
    assert isinstance(model, ChatCompletionsModel)


def test_from_env_names_every_missing_variable():
    with pytest.raises(PlanModelError) as excinfo:
        plan_model_from_env({})
    message = str(excinfo.value)
    assert "FCC_PLAN_BASE_URL" in message
    assert "FCC_PLAN_MODEL" in message
    assert "secret" not in message.lower()


def test_from_env_rejects_bad_timeout():
    with pytest.raises(PlanModelError):
        plan_model_from_env({**_ENV, "FCC_PLAN_TIMEOUT": "soon"})
    with pytest.raises(PlanModelError):
        plan_model_from_env({**_ENV, "FCC_PLAN_TIMEOUT": "-1"})


def test_feedback_retry_recovers_from_junk_first_response():
    prompts = []

    def model(prompt):
        prompts.append(prompt)
        return "let me think about that" if len(prompts) == 1 else json.dumps(_PLAN)

    plan = propose_plan_with_feedback("list files", model)
    assert plan.goal == "demo"
    assert len(prompts) == 2
    assert "rejected" in prompts[1]


def test_feedback_retry_raises_last_plan_error():
    def model(prompt):
        return "still not json"

    with pytest.raises(PlanError):
        propose_plan_with_feedback("list files", model, attempts=2)
