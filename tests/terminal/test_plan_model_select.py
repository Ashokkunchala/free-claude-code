"""SelectingPlanModel: prompt-size routing and availability fallthrough."""

import pytest

from free_claude_code.terminal import (
    ChatCompletionsModel,
    PlanModelError,
    PlanModelRateLimitError,
    SelectingPlanModel,
    plan_model_from_env,
)

RESPONSE = {"choices": [{"message": {"content": "ok"}}]}


def recording_model(calls: list[str], label: str) -> ChatCompletionsModel:
    def transport(url: str, payload: dict, headers: dict, timeout: float) -> dict:
        calls.append(label)
        return RESPONSE

    return ChatCompletionsModel(
        base_url="https://example.test/v1",
        model=label,
        transport=transport,
    )


def failing_model(exc: Exception) -> ChatCompletionsModel:
    def transport(url: str, payload: dict, headers: dict, timeout: float) -> dict:
        raise exc

    return ChatCompletionsModel(
        base_url="https://example.test/v1",
        model="broken",
        transport=transport,
    )


def test_smallest_fitting_budget_wins() -> None:
    calls: list[str] = []
    model = SelectingPlanModel(
        [
            ("big", 100_000, recording_model(calls, "big")),
            ("small", 100, recording_model(calls, "small")),
        ]
    )
    assert model("x" * 50) == "ok"
    assert calls == ["small"]
    assert model("x" * 500) == "ok"
    assert calls == ["small", "big"]


def test_unbounded_leg_ranks_last() -> None:
    calls: list[str] = []
    model = SelectingPlanModel(
        [
            ("unbounded", None, recording_model(calls, "unbounded")),
            ("tiny", 10, recording_model(calls, "tiny")),
        ]
    )
    assert model("short") == "ok"
    assert calls == ["tiny"]
    assert model("x" * 50) == "ok"
    assert calls == ["tiny", "unbounded"]


def test_rate_limit_falls_through_to_next_fitting_leg() -> None:
    calls: list[str] = []
    model = SelectingPlanModel(
        [
            (
                "small",
                100,
                failing_model(PlanModelRateLimitError("throttled", retry_after=3.0)),
            ),
            ("big", 100_000, recording_model(calls, "big")),
        ]
    )
    assert model("x" * 50) == "ok"
    assert calls == ["big"]


def test_all_rate_limited_surfaces_earliest_backoff() -> None:
    model = SelectingPlanModel(
        [
            ("a", 100, failing_model(PlanModelRateLimitError("slow", retry_after=9.0))),
            ("b", 100, failing_model(PlanModelRateLimitError("down", retry_after=2.0))),
        ]
    )
    with pytest.raises(PlanModelRateLimitError) as caught:
        model("x" * 10)
    assert caught.value.retry_after == 2.0
    assert "a: slow" in str(caught.value)
    assert "b: down" in str(caught.value)


def test_non_rate_limit_failure_beats_rate_limit_classification() -> None:
    model = SelectingPlanModel(
        [
            ("a", 100, failing_model(PlanModelRateLimitError("throttled"))),
            ("b", 100, failing_model(PlanModelError("connection refused"))),
        ]
    )
    with pytest.raises(PlanModelError) as caught:
        model("x" * 10)
    assert not isinstance(caught.value, PlanModelRateLimitError)
    assert "all plan model profiles failed" in str(caught.value)


def test_no_fitting_leg_is_an_honest_error() -> None:
    model = SelectingPlanModel(
        [("small", 10, failing_model(AssertionError("must not be called")))]
    )
    with pytest.raises(PlanModelError, match="no plan model profile fits"):
        model("x" * 100)


def test_profiles_env_replaces_primary_requirement() -> None:
    model = plan_model_from_env(
        {
            "FCC_PLAN_PROFILES": "local=ollama|qwen2.5-coder|unbounded",
        }
    )
    assert isinstance(model, SelectingPlanModel)


def test_profiles_env_parses_budgets_and_keys() -> None:
    model = plan_model_from_env(
        {
            "FCC_PLAN_PROFILES": (
                "fast=https://a.test/v1|small-model|100 ; "
                "deep=https://b.test/v1|big-model|unbounded|secret-key"
            ),
        }
    )
    assert isinstance(model, SelectingPlanModel)
    budgets = [budget for _, budget, _ in model._legs]
    assert sorted(b for b in budgets if b is not None) == [100]
    assert any(b is None for b in budgets)


def test_profiles_env_rejects_bad_entries() -> None:
    with pytest.raises(PlanModelError, match="FCC_PLAN_PROFILES entry 1"):
        plan_model_from_env({"FCC_PLAN_PROFILES": "no-label-here"})
    with pytest.raises(PlanModelError, match="budget"):
        plan_model_from_env({"FCC_PLAN_PROFILES": "x=https://a.test|m|lots"})
    with pytest.raises(PlanModelError, match="no usable entries"):
        plan_model_from_env({"FCC_PLAN_PROFILES": " ; ; "})


def test_primary_config_still_builds_without_profiles() -> None:
    model = plan_model_from_env(
        {
            "FCC_PLAN_BASE_URL": "https://a.test/v1",
            "FCC_PLAN_MODEL": "m",
        }
    )
    assert isinstance(model, ChatCompletionsModel)
