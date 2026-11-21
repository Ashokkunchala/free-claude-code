"""Provider execution shared by inbound API adapters."""

import asyncio
import math
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from time import monotonic
from types import MappingProxyType
from typing import Literal

from loguru import logger

from free_claude_code.core.anthropic import (
    anthropic_request_snapshot,
    get_token_count,
)
from free_claude_code.core.anthropic.tokens import TokenCounter
from free_claude_code.core.failures import ExecutionFailure, FailureKind
from free_claude_code.core.openai_responses import (
    OpenAIResponsesRequest,
    estimate_responses_input_tokens,
)
from free_claude_code.core.reasoning import ReasoningPolicy
from free_claude_code.core.request_outcomes import record_request_route
from free_claude_code.core.trace import (
    close_stream_input,
    trace_event,
    traced_async_stream,
)
from free_claude_code.core.usage import record_provider_input_tokens

from .ports import ModelInfoLookup, ProviderResolver
from .routing import (
    ProviderModelTarget,
    ResolvedModelRoute,
    RoutedMessagesRequest,
    RoutedNativeMessagesRequest,
    RoutedResponsesRequest,
)
from .stream_resume import AnthropicStreamResumePolicy, ResumePlan

ResponsesTokenCounter = Callable[[OpenAIResponsesRequest], int]
WireApi = Literal["messages", "responses"]
CandidateStreamOpener = Callable[
    [int, ProviderModelTarget], Awaitable[AsyncIterator[str]]
]


def _default_retry_backoff(attempt: int) -> float:
    """Bounded exponential pause between candidates after rate limiting."""

    return min(0.5 * (2**attempt), 4.0)


def _is_automatic_route_failure(failure: ExecutionFailure) -> bool:
    """Allow another provider/model after any upstream execution failure."""
    return failure.kind in {
        FailureKind.INVALID_REQUEST,
        FailureKind.CONTEXT_WINDOW_EXCEEDED,
        FailureKind.AUTHENTICATION,
        FailureKind.PERMISSION,
        FailureKind.RATE_LIMIT,
        FailureKind.OVERLOADED,
        FailureKind.TIMEOUT,
        FailureKind.UPSTREAM,
        FailureKind.UNAVAILABLE,
    }


class ProviderExecutor:
    """Resolve a provider and execute one routed Anthropic Messages stream."""

    def __init__(
        self,
        provider_resolver: ProviderResolver,
        *,
        progress_timeout_seconds: float,
        token_counter: TokenCounter = get_token_count,
        responses_token_counter: ResponsesTokenCounter = estimate_responses_input_tokens,
        generation_id: int | None = None,
        log_raw_payloads: bool = False,
        request_headers: Mapping[str, str] | None = None,
        model_info_lookup: ModelInfoLookup | None = None,
        retry_backoff: Callable[[int], float] = _default_retry_backoff,
    ) -> None:
        if not math.isfinite(progress_timeout_seconds) or progress_timeout_seconds <= 0:
            raise ValueError("progress_timeout_seconds must be finite and positive")
        self._provider_resolver = provider_resolver
        self._model_info_lookup = model_info_lookup or (lambda _provider, _model: None)
        self._token_counter = token_counter
        self._responses_token_counter = responses_token_counter
        self._generation_id = generation_id
        self._log_raw_payloads = log_raw_payloads
        self._request_headers = MappingProxyType(dict(request_headers or {}))
        self._progress_timeout_seconds = float(progress_timeout_seconds)
        self._retry_backoff = retry_backoff

    def _progress_timeout_failure(
        self,
        *,
        request_id: str,
        provider_id: str,
    ) -> ExecutionFailure:
        trace_event(
            stage="execution",
            event="free_claude_code.provider.progress_timeout",
            source="application",
            request_id=request_id,
            provider_id=provider_id,
            timeout_seconds=self._progress_timeout_seconds,
        )
        timeout_text = f"{self._progress_timeout_seconds:g}"
        return ExecutionFailure(
            kind=FailureKind.TIMEOUT,
            status_code=504,
            message=(
                f"Provider execution made no progress for {timeout_text} seconds.\n\n"
                f"Request ID: {request_id}"
            ),
            retryable=False,
        )

    def _trace_fallback_started(
        self,
        *,
        request_id: str,
        wire_api: WireApi,
        failed: ProviderModelTarget,
        selected: ProviderModelTarget,
        failure: ExecutionFailure,
        candidate_index: int,
        candidate_count: int,
    ) -> None:
        fields: dict[str, object] = {
            "stage": "execution",
            "event": "free_claude_code.model_fallback.started",
            "source": "application",
            "request_id": request_id,
            "wire_api": wire_api,
            "from_provider_model_ref": failed.provider_model_ref,
            "to_provider_model_ref": selected.provider_model_ref,
            "candidate_index": candidate_index,
            "candidate_count": candidate_count,
            "failure_kind": failure.kind.value,
            "status_code": failure.status_code,
            "provider_retryable": failure.retryable,
        }
        if self._generation_id is not None:
            fields["generation_id"] = self._generation_id
        trace_event(**fields)
        logger.info(
            "Model fallback: request_id={} from={} to={} candidate={}/{} "
            "failure_kind={} status_code={}",
            request_id,
            failed.provider_model_ref,
            selected.provider_model_ref,
            candidate_index,
            candidate_count,
            failure.kind.value,
            failure.status_code,
        )

    def _trace_fallback_selected(
        self,
        *,
        request_id: str,
        wire_api: WireApi,
        selected: ProviderModelTarget,
        candidate_index: int,
        candidate_count: int,
    ) -> None:
        fields: dict[str, object] = {
            "stage": "execution",
            "event": "free_claude_code.model_fallback.selected",
            "source": "application",
            "request_id": request_id,
            "wire_api": wire_api,
            "selected_provider_model_ref": selected.provider_model_ref,
            "candidate_index": candidate_index,
            "candidate_count": candidate_count,
        }
        if self._generation_id is not None:
            fields["generation_id"] = self._generation_id
        trace_event(**fields)

    def stream_native_messages(
        self,
        routed: RoutedNativeMessagesRequest,
        *,
        request_id: str,
    ) -> AsyncIterator[str]:
        async def open_candidate(
            index: int, target: ProviderModelTarget
        ) -> AsyncIterator[str]:
            provider = await self._provider_resolver(target.provider_id)
            return provider.stream_native_messages(
                routed.request.with_model(target.provider_model),
                request_id=request_id,
                response_model=routed.resolved.original_model,
                request_headers=self._request_headers,
            )

        messages = routed.request.body["messages"]
        assert isinstance(messages, list)
        return self._stream_candidates(
            resolved=routed.resolved,
            reasoning=ReasoningPolicy.provider_default(),
            wire_api="messages",
            raw_log_label="FULL_NATIVE_MESSAGES_PAYLOAD",
            raw_log_payload=lambda: routed.request.body,
            request_snapshot=lambda: {
                "model": routed.request.model,
                "message_count": len(messages),
                "contract": "native",
            },
            ingress_count_name="message_count",
            ingress_count=len(messages),
            request_id=request_id,
            open_candidate=open_candidate,
        )

    def stream_messages(
        self,
        routed: RoutedMessagesRequest,
        *,
        raw_log_payload: Callable[[], object],
        request_id: str,
        buffered: bool = False,
        resume_policy: AnthropicStreamResumePolicy | None = None,
    ) -> AsyncIterator[str]:
        """Execute one Anthropic Messages request.

        With ``buffered=True`` each candidate's chunks are held until that
        candidate completes, so a mid-stream failure can still fall back to
        the next candidate: nothing has been delivered to the caller yet.
        Use it for consumers that aggregate the stream privately (for
        example non-streaming requests).

        With a ``resume_policy`` a streaming candidate that fails after
        delivering partial output can still fall back: the policy tracks
        what the client saw and the next candidate continues it through an
        assistant-prefill request.
        """

        primary_request = routed.request.model_copy(deep=True)
        input_tokens = self._token_counter(
            routed.request.messages,
            routed.request.system,
            routed.request.tools,
        )

        async def open_candidate(
            index: int,
            target: ProviderModelTarget,
        ) -> AsyncIterator[str]:
            provider = await self._provider_resolver(target.provider_id)
            record_provider_input_tokens(target.provider_id, input_tokens)
            request = (
                primary_request
                if index == 0
                else routed.request.model_copy(
                    update={"model": target.provider_model},
                    deep=True,
                )
            )
            resume_plan = (
                resume_policy.take_staged_plan() if resume_policy is not None else None
            )
            if resume_plan is not None:
                request = resume_plan.apply(request)
            return provider.stream_messages(
                request,
                input_tokens=input_tokens,
                request_id=request_id,
                response_model=routed.resolved.original_model,
                reasoning=routed.reasoning,
                model_info=self._model_info_lookup(
                    target.provider_id, target.provider_model
                ),
                request_headers=self._request_headers,
            )

        return self._stream_candidates(
            resolved=routed.resolved,
            reasoning=routed.reasoning,
            wire_api="messages",
            raw_log_label="FULL_PAYLOAD",
            raw_log_payload=raw_log_payload,
            request_snapshot=lambda: anthropic_request_snapshot(routed.request),
            ingress_count_name="message_count",
            ingress_count=len(routed.request.messages),
            request_id=request_id,
            open_candidate=open_candidate,
            buffered=buffered,
            resume_policy=resume_policy,
        )

    def stream_responses(
        self,
        routed: RoutedResponsesRequest,
        *,
        raw_log_payload: Callable[[], object],
        request_id: str,
    ) -> AsyncIterator[str]:
        """Execute one native OpenAI Responses request."""

        primary_request = routed.request.model_copy(deep=True)
        input_tokens = self._responses_token_counter(routed.request)

        async def open_candidate(
            index: int,
            target: ProviderModelTarget,
        ) -> AsyncIterator[str]:
            provider = await self._provider_resolver(target.provider_id)
            record_provider_input_tokens(target.provider_id, input_tokens)
            request = (
                primary_request
                if index == 0
                else routed.request.model_copy(
                    update={"model": target.provider_model},
                    deep=True,
                )
            )
            return provider.stream_responses(
                request,
                input_tokens=input_tokens,
                request_id=request_id,
                response_model=routed.resolved.original_model,
                reasoning=routed.reasoning,
                request_headers=self._request_headers,
                model_info=self._model_info_lookup(
                    target.provider_id, target.provider_model
                ),
            )

        raw_input = routed.request.input
        input_item_count = (
            len(raw_input)
            if isinstance(raw_input, list)
            else int(raw_input is not None)
        )
        return self._stream_candidates(
            resolved=routed.resolved,
            reasoning=routed.reasoning,
            wire_api="responses",
            raw_log_label="FULL_RESPONSES_PAYLOAD",
            raw_log_payload=raw_log_payload,
            request_snapshot=lambda: {
                "model": routed.request.model,
                "input_item_count": input_item_count,
                "tool_count": len(routed.request.tools or ()),
            },
            ingress_count_name="input_item_count",
            ingress_count=input_item_count,
            request_id=request_id,
            open_candidate=open_candidate,
        )

    def _stream_candidates(
        self,
        *,
        resolved: ResolvedModelRoute,
        reasoning: ReasoningPolicy,
        wire_api: WireApi,
        raw_log_label: str,
        raw_log_payload: Callable[[], object],
        request_snapshot: Callable[[], dict[str, object]],
        ingress_count_name: str,
        ingress_count: int,
        request_id: str,
        open_candidate: CandidateStreamOpener,
        buffered: bool = False,
        resume_policy: AnthropicStreamResumePolicy | None = None,
    ) -> AsyncIterator[str]:
        """Start and consume candidates through one protocol-blind lifecycle.

        With ``buffered=True`` candidate output is released only after the
        candidate finishes, which keeps mid-stream failures recoverable by
        the next candidate.  Without it the first chunk commits the
        candidate because the caller may already have received it.
        """

        primary = resolved.primary
        automatic_start = 1 + len(resolved.fallbacks)
        candidates = (
            primary,
            *resolved.fallbacks,
            *resolved.automatic_fallbacks,
        )
        gateway_model = resolved.original_model
        route_trace: dict[str, object] = {
            "stage": "routing",
            "event": "free_claude_code.api.route.resolved",
            "source": "api",
            "request_id": request_id,
            "provider_id": primary.provider_id,
            "provider_model": primary.provider_model,
            "provider_model_ref": primary.provider_model_ref,
            "fallback_count": len(resolved.fallbacks),
            "gateway_model": gateway_model,
            "reasoning_control": reasoning.control.value,
            "reasoning_effort": (
                reasoning.effort.value if reasoning.effort is not None else None
            ),
            "reasoning_budget_tokens": reasoning.budget_tokens,
        }
        if wire_api == "responses":
            route_trace["wire_api"] = "responses"
        if self._generation_id is not None:
            route_trace["generation_id"] = self._generation_id
        trace_event(**route_trace)

        ingress_trace: dict[str, object] = {
            "stage": "ingress",
            "event": (
                "free_claude_code.api.responses.request.received"
                if wire_api == "responses"
                else "free_claude_code.api.request.received"
            ),
            "source": "api",
            "request_id": request_id,
            ingress_count_name: ingress_count,
        }
        trace_event(
            lambda: {"snapshot": {**request_snapshot(), "model": gateway_model}},
            **ingress_trace,
        )

        if self._log_raw_payloads:
            logger.opt(lazy=True).debug(
                f"{raw_log_label} [{{}}]: {{}}", lambda: request_id, raw_log_payload
            )

        async def provider_body() -> AsyncIterator[str]:
            loop = asyncio.get_running_loop()
            progress_deadline = loop.time() + self._progress_timeout_seconds
            automatic_enabled = False
            stream_committed = False
            rate_limit_streak = 0
            resume_plan: ResumePlan | None = None
            for index, target in enumerate(candidates):
                record_request_route(target.provider_id, target.provider_model)
                provider_stream: AsyncIterator[str] | None = None
                candidate_committed = False
                candidate_failure: ExecutionFailure | None = None
                buffered_chunks: list[str] = []
                try:
                    opening_started = monotonic()
                    try:
                        if resume_plan is not None and resume_policy is not None:
                            resume_policy.stage_plan(resume_plan)
                        resume_plan = None
                        provider_stream = await open_candidate(index, target)
                    except ExecutionFailure as failure:
                        candidate_failure = failure
                    finally:
                        # Initialization has its own request budget. Upstream progress
                        # time is not spent waiting for a provider's startup task.
                        progress_deadline += monotonic() - opening_started

                    if provider_stream is None and candidate_failure is None:
                        raise TypeError(
                            "provider stream method must return an async iterator"
                        )
                    while provider_stream is not None:
                        if loop.time() >= progress_deadline:
                            raise self._progress_timeout_failure(
                                request_id=request_id,
                                provider_id=target.provider_id,
                            )
                        progress_timeout = asyncio.timeout_at(progress_deadline)
                        read_failure: ExecutionFailure | None = None
                        try:
                            async with progress_timeout:
                                try:
                                    chunk = await anext(provider_stream)
                                except ExecutionFailure as failure:
                                    read_failure = failure
                        except StopAsyncIteration:
                            break
                        except TimeoutError as exc:
                            if not progress_timeout.expired():
                                raise
                            raise self._progress_timeout_failure(
                                request_id=request_id,
                                provider_id=target.provider_id,
                            ) from exc
                        if progress_timeout.expired():
                            raise self._progress_timeout_failure(
                                request_id=request_id,
                                provider_id=target.provider_id,
                            )
                        if read_failure is not None:
                            candidate_failure = read_failure
                            break
                        if not chunk:
                            await asyncio.sleep(0)
                            continue
                        frames = (
                            resume_policy.transform(chunk)
                            if resume_policy is not None
                            else (chunk,)
                        )
                        if resume_policy is not None:
                            policy_failure = resume_policy.take_pending_failure()
                            if policy_failure is not None:
                                candidate_failure = policy_failure
                                break
                        if frames and not candidate_committed:
                            candidate_committed = True
                            stream_committed = True
                            if index > 0:
                                self._trace_fallback_selected(
                                    request_id=request_id,
                                    wire_api=wire_api,
                                    selected=target,
                                    candidate_index=index + 1,
                                    candidate_count=len(candidates),
                                )
                        if buffered:
                            buffered_chunks.extend(frames)
                        else:
                            for frame in frames:
                                yield frame
                        progress_deadline = loop.time() + self._progress_timeout_seconds
                finally:
                    if provider_stream is not None:
                        active_error = sys.exception()
                        preserved_error = active_error or candidate_failure
                        cleanup_timeout = asyncio.timeout_at(
                            progress_deadline if active_error is None else None
                        )
                        try:
                            async with cleanup_timeout:
                                await close_stream_input(
                                    provider_stream,
                                    owner="provider_executor",
                                    source="api",
                                    preserved_error=preserved_error,
                                )
                        except TimeoutError as exc:
                            if not cleanup_timeout.expired():
                                raise
                            raise self._progress_timeout_failure(
                                request_id=request_id,
                                provider_id=target.provider_id,
                            ) from exc

                if candidate_failure is None and resume_policy is not None:
                    finish_frames = resume_policy.finish()
                    policy_failure = resume_policy.take_pending_failure()
                    if policy_failure is not None:
                        candidate_failure = policy_failure
                    elif not resume_policy.saw_content_block and index + 1 < len(
                        candidates
                    ):
                        # An empty completion is a failed response: the chain
                        # must keep working.  The held terminal frames stay
                        # undelivered; the next candidate supplies its own.
                        candidate_failure = ExecutionFailure(
                            kind=FailureKind.UNAVAILABLE,
                            status_code=502,
                            message="Provider returned an empty response.",
                            retryable=True,
                        )
                    if candidate_failure is None:
                        if finish_frames and not candidate_committed:
                            candidate_committed = True
                            stream_committed = True
                            if index > 0:
                                self._trace_fallback_selected(
                                    request_id=request_id,
                                    wire_api=wire_api,
                                    selected=target,
                                    candidate_index=index + 1,
                                    candidate_count=len(candidates),
                                )
                        if buffered:
                            buffered_chunks.extend(finish_frames)
                        else:
                            for frame in finish_frames:
                                yield frame
                if (
                    candidate_failure is None
                    and resume_policy is None
                    and not candidate_committed
                    and not buffered_chunks
                    and index + 1 < len(candidates)
                ):
                    # Nothing was emitted at all: safe to try the next candidate.
                    candidate_failure = ExecutionFailure(
                        kind=FailureKind.UNAVAILABLE,
                        status_code=502,
                        message="Provider returned an empty response.",
                        retryable=True,
                    )
                if candidate_failure is None:
                    for buffered_chunk in buffered_chunks:
                        yield buffered_chunk
                    return
                automatic_enabled = automatic_enabled or _is_automatic_route_failure(
                    candidate_failure
                )
                # A resumable stream can still fall back after partial output:
                # the policy plans how the next candidate continues it.
                resume_plan = None
                if (
                    not buffered
                    and resume_policy is not None
                    and _is_automatic_route_failure(candidate_failure)
                ):
                    resume_plan = resume_policy.begin_resume()
                # Buffered delivery has released nothing, so a mid-stream
                # failure is still recoverable by the next candidate.
                if index + 1 >= len(candidates):
                    raise candidate_failure
                if resume_plan is None and stream_committed and not buffered:
                    raise candidate_failure
                if index + 1 >= automatic_start and not automatic_enabled:
                    raise candidate_failure
                if resume_policy is not None and resume_plan is None:
                    resume_policy.reset()
                if candidate_failure.kind in {
                    FailureKind.RATE_LIMIT,
                    FailureKind.OVERLOADED,
                }:
                    delay = self._retry_backoff(rate_limit_streak)
                    rate_limit_streak += 1
                    if delay > 0:
                        # The pause is backoff budget, not provider progress
                        # budget: return it to the shared deadline.
                        await asyncio.sleep(delay)
                        progress_deadline += delay
                else:
                    rate_limit_streak = 0
                next_target = candidates[index + 1]
                self._trace_fallback_started(
                    request_id=request_id,
                    wire_api=wire_api,
                    failed=target,
                    selected=next_target,
                    failure=candidate_failure,
                    candidate_index=index + 2,
                    candidate_count=len(candidates),
                )

        stream_trace: dict[str, object] = {
            "request_id": request_id,
            "initial_provider_id": primary.provider_id,
            "gateway_model": gateway_model,
        }
        if self._generation_id is not None:
            stream_trace["generation_id"] = self._generation_id

        return traced_async_stream(
            provider_body(),
            stage="egress",
            source="api",
            complete_event=(
                "free_claude_code.api.responses.stream_completed"
                if wire_api == "responses"
                else "free_claude_code.api.response.stream_completed"
            ),
            interrupted_event=(
                "free_claude_code.api.responses.stream_interrupted"
                if wire_api == "responses"
                else "free_claude_code.api.response.stream_interrupted"
            ),
            chunk_event=None,
            extra=stream_trace,
        )
