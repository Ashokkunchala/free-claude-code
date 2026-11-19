"""Provider adapter for plan generation: prompt in, raw text out.

``terminal.plan_gen`` defines the seam (``PlanModelPort``); this module
supplies the production adapter: one non-streaming chat-completions call
against any OpenAI-compatible endpoint. It uses only the standard
library so the terminal package keeps its zero-dependency contract, and
it never puts credentials into error messages.
"""

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping

from free_claude_code.terminal.plan_gen import (
    build_repair_prompt,
    plan_from_response,
    propose_plan,
)
from free_claude_code.terminal.run import PlanError, RunPlan, RunReport

#: Raw chat-completions POST: (url, payload, headers, timeout) -> decoded body.
ChatTransport = Callable[[str, dict, dict[str, str], float], dict]

_ENV_BASE_URL = "FCC_PLAN_BASE_URL"
_ENV_MODEL = "FCC_PLAN_MODEL"
_ENV_API_KEY = "FCC_PLAN_API_KEY"
_ENV_TIMEOUT = "FCC_PLAN_TIMEOUT"

_DEFAULT_TIMEOUT = 120.0


class PlanModelError(Exception):
    """The model endpoint could not supply a usable response."""


def _urllib_transport(
    url: str, payload: dict, headers: dict[str, str], timeout: float
) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        excerpt = exc.read(512).decode("utf-8", errors="replace")
        raise PlanModelError(
            f"plan model endpoint returned HTTP {exc.code}: {excerpt.strip()}"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PlanModelError(f"plan model endpoint unreachable: {exc}") from None
    except ValueError:
        raise PlanModelError("plan model endpoint returned invalid JSON") from None


class ChatCompletionsModel:
    """A PlanModelPort backed by one OpenAI-compatible chat completion."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        transport: ChatTransport = _urllib_transport,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport

    def __call__(self, prompt: str) -> str:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        data = self._transport(self._url, payload, headers, self._timeout)
        try:
            content = data["choices"][0]["message"]["content"]
        except KeyError, IndexError, TypeError:
            raise PlanModelError(
                "plan model response has no choices[0].message.content"
            ) from None
        if not isinstance(content, str) or not content.strip():
            raise PlanModelError("plan model response content is empty")
        return content


def plan_model_from_env(
    environ: Mapping[str, str] | None = None,
) -> ChatCompletionsModel:
    """Build the adapter from FCC_PLAN_* variables, or say what is missing."""

    env = os.environ if environ is None else environ
    missing = [name for name in (_ENV_BASE_URL, _ENV_MODEL) if not env.get(name)]
    if missing:
        raise PlanModelError(
            "plan model is not configured; set "
            + " and ".join(missing)
            + " (OpenAI-compatible endpoint)"
        )
    raw_timeout = env.get(_ENV_TIMEOUT, "")
    timeout = _DEFAULT_TIMEOUT
    if raw_timeout:
        try:
            timeout = float(raw_timeout)
        except ValueError:
            raise PlanModelError(
                f"{_ENV_TIMEOUT} must be a number, got {raw_timeout!r}"
            ) from None
        if timeout <= 0:
            raise PlanModelError(f"{_ENV_TIMEOUT} must be positive")
    return ChatCompletionsModel(
        base_url=env[_ENV_BASE_URL],
        model=env[_ENV_MODEL],
        api_key=env.get(_ENV_API_KEY) or None,
        timeout=timeout,
    )


def propose_plan_with_feedback(
    task: str,
    model: Callable[[str], str],
    *,
    workspace: str | None = None,
    context: str | None = None,
    attempts: int = 2,
) -> RunPlan:
    """propose_plan with one correction round when the response is not a plan."""

    last_error: PlanError | None = None
    for _ in range(max(1, attempts)):
        if last_error is None:
            prompt_task = task
        else:
            prompt_task = (
                f"{task}\n\nYour previous response was rejected: {last_error}. "
                "Answer with ONE JSON plan object and nothing else."
            )
        try:
            return propose_plan(
                prompt_task, model, workspace=workspace, context=context
            )
        except PlanError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def propose_repair(
    task: str,
    report: RunReport,
    model: Callable[[str], str],
    *,
    workspace: str | None = None,
    context: str | None = None,
    attempts: int = 2,
) -> RunPlan:
    """Ask the model for a residual plan after a failed run, with feedback."""

    last_error: PlanError | None = None
    prompt = build_repair_prompt(task, report, workspace=workspace, context=context)
    for _ in range(max(1, attempts)):
        candidate = prompt
        if last_error is not None:
            candidate = (
                f"{prompt}\n\nYour previous response was rejected: {last_error}. "
                "Answer with ONE JSON plan object and nothing else."
            )
        try:
            return plan_from_response(model(candidate))
        except PlanError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error
