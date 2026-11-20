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


def _fail_transport(url, payload, headers, timeout):
    raise PlanModelError(f"plan model endpoint unreachable: {url}")


def test_fallback_uses_next_leg_after_failure():
    from free_claude_code.terminal.plan_model import FallbackPlanModel

    primary = ChatCompletionsModel(
        base_url="http://down.test/v1",
        model="planner-1",
        transport=_fail_transport,
    )
    backup = ChatCompletionsModel(
        base_url="http://model.test/v1",
        model="planner-2",
        api_key="secret-key",
        transport=transport_ok,
    )
    model = FallbackPlanModel([("planner-1", primary), ("planner-2", backup)])
    assert json.loads(model("plan this")) == _PLAN


def test_fallback_error_names_every_leg():
    from free_claude_code.terminal.plan_model import FallbackPlanModel

    model = FallbackPlanModel(
        [
            (
                "planner-1",
                ChatCompletionsModel(
                    base_url="http://down-a.test/v1",
                    model="planner-1",
                    transport=_fail_transport,
                ),
            ),
            (
                "planner-2",
                ChatCompletionsModel(
                    base_url="http://down-b.test/v1",
                    model="planner-2",
                    transport=_fail_transport,
                ),
            ),
        ]
    )
    with pytest.raises(PlanModelError) as excinfo:
        model("plan this")
    message = str(excinfo.value)
    assert "all plan models failed" in message
    assert "planner-1" in message and "down-a.test" in message
    assert "planner-2" in message and "down-b.test" in message


def test_from_env_returns_primary_alone_without_fallbacks():
    model = plan_model_from_env(_ENV)
    assert isinstance(model, ChatCompletionsModel)


def test_from_env_builds_fallback_chain():
    from free_claude_code.terminal.plan_model import FallbackPlanModel

    model = plan_model_from_env(
        {
            **_ENV,
            "FCC_PLAN_FALLBACKS": "http://local.test/v1|llama-local, "
            "http://other.test/v1 | qwen-backup | other-key",
        }
    )
    assert isinstance(model, FallbackPlanModel)


def test_from_env_fallback_chain_actually_fails_over():
    from free_claude_code.terminal.plan_model import FallbackPlanModel

    real_transport = ChatCompletionsModel.__init__

    def route_aware_init(
        self, *, base_url, model, api_key=None, timeout=120.0, transport=None
    ):
        chosen = _fail_transport if "down" in base_url else transport_ok
        real_transport(
            self,
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout=timeout,
            transport=chosen,
        )

    original = ChatCompletionsModel.__init__
    ChatCompletionsModel.__init__ = route_aware_init
    try:
        model = plan_model_from_env(
            {
                "FCC_PLAN_BASE_URL": "http://down.test/v1",
                "FCC_PLAN_MODEL": "planner-1",
                "FCC_PLAN_FALLBACKS": "http://model.test/v1|planner-2|secret-key",
            }
        )
        assert isinstance(model, FallbackPlanModel)
        assert json.loads(model("plan this")) == _PLAN
    finally:
        ChatCompletionsModel.__init__ = original


def test_from_env_rejects_bad_fallback_entry():
    with pytest.raises(PlanModelError, match="FCC_PLAN_FALLBACKS entry 2"):
        plan_model_from_env(
            {
                **_ENV,
                "FCC_PLAN_FALLBACKS": "http://a.test/v1|ok-model, missing-model-only",
            }
        )


def _http_error(code, body=b"", retry_after=None):
    import email.message
    import io
    import urllib.error

    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "http://model.test/v1/chat/completions",
        code,
        "error",
        headers,
        io.BytesIO(body),
    )


def test_transport_429_raises_rate_limit_with_retry_hint(monkeypatch):
    import urllib.request

    from free_claude_code.terminal.plan_model import PlanModelRateLimitError

    def fake_urlopen(request, timeout):
        raise _http_error(429, b'{"error": "slow down"}', retry_after="30")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    model = ChatCompletionsModel(base_url="http://model.test/v1", model="m")
    with pytest.raises(PlanModelRateLimitError) as excinfo:
        model("p")
    assert excinfo.value.retry_after == 30.0
    message = str(excinfo.value)
    assert "rate limited or out of quota" in message
    assert "30s backoff" in message
    assert "slow down" in message


def test_transport_429_without_hint_says_so(monkeypatch):
    import urllib.request

    from free_claude_code.terminal.plan_model import PlanModelRateLimitError

    def fake_urlopen(request, timeout):
        raise _http_error(429, b"quota exceeded")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    model = ChatCompletionsModel(base_url="http://model.test/v1", model="m")
    with pytest.raises(PlanModelRateLimitError) as excinfo:
        model("p")
    assert excinfo.value.retry_after is None
    assert "no retry hint" in str(excinfo.value)


def test_transport_429_parses_http_date_hint(monkeypatch):
    import urllib.request

    from free_claude_code.terminal.plan_model import PlanModelRateLimitError

    def fake_urlopen(request, timeout):
        raise _http_error(429, retry_after="Fri, 01 Jan 2038 00:00:00 GMT")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    model = ChatCompletionsModel(base_url="http://model.test/v1", model="m")
    with pytest.raises(PlanModelRateLimitError) as excinfo:
        model("p")
    assert excinfo.value.retry_after is not None
    assert excinfo.value.retry_after > 0


def test_transport_non_429_stays_plain_error(monkeypatch):
    import urllib.request

    from free_claude_code.terminal.plan_model import PlanModelRateLimitError

    def fake_urlopen(request, timeout):
        raise _http_error(500, b"boom")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    model = ChatCompletionsModel(base_url="http://model.test/v1", model="m")
    with pytest.raises(PlanModelError) as excinfo:
        model("p")
    assert not isinstance(excinfo.value, PlanModelRateLimitError)
    assert "HTTP 500" in str(excinfo.value)


def test_fallback_all_rate_limited_raises_rate_limit_with_earliest_hint():
    from free_claude_code.terminal.plan_model import (
        FallbackPlanModel,
        PlanModelRateLimitError,
    )

    def limited(seconds):
        def transport(url, payload, headers, timeout):
            raise PlanModelRateLimitError("throttled", retry_after=seconds)

        return transport

    model = FallbackPlanModel(
        [
            (
                "planner-1",
                ChatCompletionsModel(
                    base_url="http://a.test/v1", model="p1", transport=limited(30.0)
                ),
            ),
            (
                "planner-2",
                ChatCompletionsModel(
                    base_url="http://b.test/v1", model="p2", transport=limited(10.0)
                ),
            ),
        ]
    )
    with pytest.raises(PlanModelRateLimitError) as excinfo:
        model("plan this")
    assert excinfo.value.retry_after == 10.0
    message = str(excinfo.value)
    assert "all plan models failed" in message
    assert "planner-1" in message and "planner-2" in message


def test_fallback_mixed_failures_stay_plain_error():
    from free_claude_code.terminal.plan_model import (
        FallbackPlanModel,
        PlanModelRateLimitError,
    )

    def limited(url, payload, headers, timeout):
        raise PlanModelRateLimitError("throttled", retry_after=5.0)

    model = FallbackPlanModel(
        [
            (
                "planner-1",
                ChatCompletionsModel(
                    base_url="http://a.test/v1", model="p1", transport=limited
                ),
            ),
            (
                "planner-2",
                ChatCompletionsModel(
                    base_url="http://down.test/v1",
                    model="p2",
                    transport=_fail_transport,
                ),
            ),
        ]
    )
    with pytest.raises(PlanModelError) as excinfo:
        model("plan this")
    assert type(excinfo.value) is PlanModelError
    assert "planner-1" in str(excinfo.value) and "planner-2" in str(excinfo.value)
