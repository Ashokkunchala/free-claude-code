"""Provider adapter for plan generation: prompt in, raw text out.

``terminal.plan_gen`` defines the seam (``PlanModelPort``); this module
supplies the production adapter: one non-streaming chat-completions call
against any OpenAI-compatible endpoint. It uses only the standard
library so the terminal package keeps its zero-dependency contract, and
it never puts credentials into error messages.
"""

import email.utils
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

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
_ENV_FALLBACKS = "FCC_PLAN_FALLBACKS"
_ENV_PROFILES = "FCC_PLAN_PROFILES"

_DEFAULT_TIMEOUT = 120.0

#: First-class local-model aliases (roadmap section 9): name -> OpenAI-
#: compatible endpoint. A local server needs no API key and keeps prompts
#: on the machine; offline operation depends on the server running.
_LOCAL_ALIASES = {
    "ollama": "http://localhost:11434/v1",
}


def _resolve_base_url(raw: str) -> str:
    """Map a local-model alias to its endpoint; pass URLs through."""

    return _LOCAL_ALIASES.get(raw.strip().lower(), raw)


class PlanModelError(Exception):
    """The model endpoint could not supply a usable response."""


class PlanModelRateLimitError(PlanModelError):
    """The endpoint refused the call: rate limit or quota exhausted.

    Roadmap section 9: quota and rate-limit behavior is exposed honestly.
    Carries the provider's Retry-After hint in seconds when it sent one, so
    the operator sees a real backoff instead of a generic HTTP error.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _parse_retry_after(value: str | None) -> float | None:
    """Seconds from a Retry-After header: integer delay or HTTP date."""

    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except TypeError, ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    return max(0.0, seconds)


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
        # Python 3.14: HTTPError is a tempfile-backed response wrapper whose
        # __del__ warns when it is garbage-collected unclosed; close it.
        try:
            excerpt = exc.read(512).decode("utf-8", errors="replace")
        finally:
            exc.close()
        if exc.code == 429:
            retry_after = _parse_retry_after(exc.headers.get("Retry-After"))
            hint = (
                f"; the provider asks for a ~{retry_after:.0f}s backoff"
                if retry_after is not None
                else "; the provider gave no retry hint"
            )
            raise PlanModelRateLimitError(
                "plan model endpoint rate limited or out of quota (HTTP 429)"
                f"{hint}: {excerpt.strip()}",
                retry_after=retry_after,
            ) from None
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


class FallbackPlanModel:
    """Try plan models in order until one answers (roadmap section 9).

    A planner that dies with its endpoint is not gracefully degraded; it is
    just down. Each leg is tried in turn on :class:`PlanModelError`; when
    every leg fails, the raised error names each leg and its failure so the
    operator sees the whole chain, not just the last error.
    """

    def __init__(self, legs: list[tuple[str, ChatCompletionsModel]]) -> None:
        if not legs:
            raise ValueError("FallbackPlanModel needs at least one leg")
        self._legs = legs

    def __call__(self, prompt: str) -> str:
        failures: list[str] = []
        every_leg_rate_limited = True
        retry_hints: list[float] = []
        for label, model in self._legs:
            try:
                return model(prompt)
            except PlanModelError as exc:
                failures.append(f"{label}: {exc}")
                if isinstance(exc, PlanModelRateLimitError):
                    if exc.retry_after is not None:
                        retry_hints.append(exc.retry_after)
                else:
                    every_leg_rate_limited = False
        message = "all plan models failed -> " + " | ".join(failures)
        if every_leg_rate_limited:
            # Every leg is throttled: say so, with the earliest honest backoff.
            raise PlanModelRateLimitError(
                message,
                retry_after=min(retry_hints) if retry_hints else None,
            ) from None
        raise PlanModelError(message) from None


class SelectingPlanModel:
    """Route each call to a pool leg by prompt size and availability.

    Roadmap section 9: provider/model selection based on task needs,
    context size, latency and availability. Each leg declares a context
    budget in characters; a call goes to the smallest-budget leg that
    still fits the prompt, which steers small prompts to small, fast or
    local models and large prompts to large-context endpoints. A leg
    without a budget fits anything and ranks last. On
    :class:`PlanModelError` the call falls through to the next fitting
    leg; when every fitting leg fails, the raised error names each
    attempt, and an all-rate-limited failure surfaces as
    :class:`PlanModelRateLimitError` with the earliest honest backoff.
    """

    def __init__(
        self, legs: list[tuple[str, int | None, ChatCompletionsModel]]
    ) -> None:
        if not legs:
            raise ValueError("SelectingPlanModel needs at least one leg")
        self._legs = legs

    def __call__(self, prompt: str) -> str:
        fitting = [
            (label, model)
            for label, budget, model in sorted(
                self._legs,
                key=lambda leg: (leg[1] is None, leg[1] if leg[1] is not None else 0),
            )
            if budget is None or budget >= len(prompt)
        ]
        failures: list[str] = []
        every_leg_rate_limited = True
        retry_hints: list[float] = []
        for label, model in fitting:
            try:
                return model(prompt)
            except PlanModelError as exc:
                failures.append(f"{label}: {exc}")
                if isinstance(exc, PlanModelRateLimitError):
                    if exc.retry_after is not None:
                        retry_hints.append(exc.retry_after)
                else:
                    every_leg_rate_limited = False
        if not fitting:
            raise PlanModelError(
                "no plan model profile fits a prompt of "
                f"{len(prompt)} characters; raise a profile budget in "
                f"{_ENV_PROFILES} or add an unbounded leg"
            ) from None
        message = "all plan model profiles failed -> " + " | ".join(failures)
        if every_leg_rate_limited:
            raise PlanModelRateLimitError(
                message,
                retry_after=min(retry_hints) if retry_hints else None,
            ) from None
        raise PlanModelError(message) from None


def _parse_fallbacks(raw: str) -> list[tuple[str, str, str | None]]:
    """Parse FCC_PLAN_FALLBACKS: ``base_url|model[|api_key]`` per comma entry."""

    entries: list[tuple[str, str, str | None]] = []
    for index, chunk in enumerate(raw.split(",")):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [part.strip() for part in chunk.split("|")]
        if len(parts) not in (2, 3) or not parts[0] or not parts[1]:
            raise PlanModelError(
                f"{_ENV_FALLBACKS} entry {index + 1} must be base_url|model"
                f"[|api_key], got {chunk!r}"
            )
        api_key = (parts[2] or None) if len(parts) == 3 else None
        entries.append((_resolve_base_url(parts[0]), parts[1], api_key))
    return entries


def _parse_profiles(raw: str) -> list[tuple[str, str, str, str | None, int | None]]:
    """Parse FCC_PLAN_PROFILES entries.

    Semicolon-separated ``label=base_url|model|budget[|api_key]`` entries,
    where ``budget`` is the context size in characters the leg accepts, or
    ``unbounded`` for no limit. Base URLs accept the same local aliases as
    the primary config.
    """

    entries: list[tuple[str, str, str, str | None, int | None]] = []
    for index, chunk in enumerate(raw.split(";")):
        chunk = chunk.strip()
        if not chunk:
            continue
        label, sep, rest = chunk.partition("=")
        label = label.strip()
        if not sep or not label:
            raise PlanModelError(
                f"{_ENV_PROFILES} entry {index + 1} must be "
                f"label=base_url|model|budget[|api_key], got {chunk!r}"
            )
        parts = [part.strip() for part in rest.split("|")]
        if len(parts) not in (3, 4) or not parts[0] or not parts[1] or not parts[2]:
            raise PlanModelError(
                f"{_ENV_PROFILES} entry {index + 1} must be "
                f"label=base_url|model|budget[|api_key], got {chunk!r}"
            )
        budget: int | None
        if parts[2].lower() == "unbounded":
            budget = None
        else:
            try:
                budget = int(parts[2])
            except ValueError:
                raise PlanModelError(
                    f"{_ENV_PROFILES} entry {index + 1} budget must be a "
                    f"character count or 'unbounded', got {parts[2]!r}"
                ) from None
            if budget <= 0:
                raise PlanModelError(
                    f"{_ENV_PROFILES} entry {index + 1} budget must be positive"
                )
        api_key = (parts[3] or None) if len(parts) == 4 else None
        entries.append((label, _resolve_base_url(parts[0]), parts[1], api_key, budget))
    if not entries:
        raise PlanModelError(f"{_ENV_PROFILES} is set but has no usable entries")
    return entries


def plan_model_from_env(
    environ: Mapping[str, str] | None = None,
) -> ChatCompletionsModel | FallbackPlanModel | SelectingPlanModel:
    """Build the adapter from FCC_PLAN_* variables, or say what is missing.

    With FCC_PLAN_FALLBACKS set, the primary is tried first and each
    fallback in order; without it, the primary adapter is returned alone.
    A base URL may be a local-model alias ("ollama") for offline/private
    planning against a local server.

    With FCC_PLAN_PROFILES set, the pool replaces the primary config: a
    SelectingPlanModel routes each call by prompt size and availability
    (roadmap section 9), and FCC_PLAN_BASE_URL/FCC_PLAN_MODEL are not
    required.
    """

    env = os.environ if environ is None else environ
    raw_profiles = env.get(_ENV_PROFILES, "").strip()
    if raw_profiles:
        timeout = _env_timeout(env)
        legs: list[tuple[str, int | None, ChatCompletionsModel]] = [
            (
                label,
                budget,
                ChatCompletionsModel(
                    base_url=base_url,
                    model=model_name,
                    api_key=api_key,
                    timeout=timeout,
                ),
            )
            for label, base_url, model_name, api_key, budget in _parse_profiles(
                raw_profiles
            )
        ]
        return SelectingPlanModel(legs)
    missing = [name for name in (_ENV_BASE_URL, _ENV_MODEL) if not env.get(name)]
    if missing:
        raise PlanModelError(
            "plan model is not configured; set "
            + " and ".join(missing)
            + " (OpenAI-compatible endpoint)"
        )
    timeout = _env_timeout(env)
    primary = ChatCompletionsModel(
        base_url=_resolve_base_url(env[_ENV_BASE_URL]),
        model=env[_ENV_MODEL],
        api_key=env.get(_ENV_API_KEY) or None,
        timeout=timeout,
    )
    raw_fallbacks = env.get(_ENV_FALLBACKS, "").strip()
    if not raw_fallbacks:
        return primary
    legs: list[tuple[str, ChatCompletionsModel]] = [(env[_ENV_MODEL], primary)]
    for base_url, model_name, api_key in _parse_fallbacks(raw_fallbacks):
        legs.append(
            (
                model_name,
                ChatCompletionsModel(
                    base_url=base_url,
                    model=model_name,
                    api_key=api_key,
                    timeout=timeout,
                ),
            )
        )
    return FallbackPlanModel(legs)


def _env_timeout(env: Mapping[str, str]) -> float:
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
    return timeout


def propose_plan_with_feedback(
    task: str,
    model: Callable[[str], str],
    *,
    workspace: str | None = None,
    context: str | None = None,
    skills: str | None = None,
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
                prompt_task,
                model,
                workspace=workspace,
                context=context,
                skills=skills,
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
    skills: str | None = None,
    attempts: int = 2,
) -> RunPlan:
    """Ask the model for a residual plan after a failed run, with feedback."""

    last_error: PlanError | None = None
    prompt = build_repair_prompt(
        task, report, workspace=workspace, context=context, skills=skills
    )
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
