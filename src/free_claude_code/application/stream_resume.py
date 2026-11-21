"""Mid-stream failover support for Anthropic Messages SSE streams.

Streaming requests normally commit a candidate at the first delivered chunk,
so a mid-stream failure had to surface to the client.  This policy tracks the
emitted Anthropic SSE so a failed candidate can be resumed by the next one:

* Text and thinking blocks stream live.  Emitted text is remembered so the
  next candidate can continue it through an assistant-prefill request.
* ``tool_use`` blocks are held back until their ``content_block_stop`` so a
  partially written tool call never reaches the client; a failure inside a
  tool block is recovered by restarting that block on the next candidate.
* In-band ``error`` events become candidate failures instead of reaching the
  client, so the chain is tried before any error is shown.
"""

from dataclasses import dataclass, field

from free_claude_code.core.anthropic.models import Message, MessagesRequest
from free_claude_code.core.anthropic.stream_contracts import parse_sse_text
from free_claude_code.core.anthropic.streaming.decoder import AnthropicSSEDecoder
from free_claude_code.core.anthropic.streaming.emitter import format_sse_event
from free_claude_code.core.failures import ExecutionFailure, FailureKind

_TRIM_BUFFER_LIMIT = 8192

_ERROR_KIND_BY_TYPE = {
    "overloaded_error": FailureKind.OVERLOADED,
    "rate_limit_error": FailureKind.RATE_LIMIT,
    "authentication_error": FailureKind.AUTHENTICATION,
    "permission_error": FailureKind.PERMISSION,
    "invalid_request_error": FailureKind.INVALID_REQUEST,
    "not_found_error": FailureKind.INVALID_REQUEST,
    "request_too_large": FailureKind.CONTEXT_WINDOW_EXCEEDED,
    "timeout_error": FailureKind.TIMEOUT,
    "api_error": FailureKind.UPSTREAM,
    "billing_error": FailureKind.PERMISSION,
}


@dataclass(frozen=True, slots=True)
class ResumePlan:
    """How the next candidate must continue a partially delivered stream."""

    prefill_text: str | None
    continue_text_block: int | None
    close_block: int | None
    next_block_offset: int
    forward_message_start: bool

    def apply(self, request: MessagesRequest) -> MessagesRequest:
        """Append the accumulated partial assistant text as a prefill."""

        if not self.prefill_text:
            return request
        messages = list(request.messages)
        last = messages[-1] if messages else None
        if (
            last is not None
            and last.role == "assistant"
            and isinstance(last.content, str)
        ):
            messages[-1] = last.model_copy(
                update={"content": last.content + self.prefill_text}
            )
        else:
            messages.append(Message(role="assistant", content=self.prefill_text))
        return request.model_copy(update={"messages": messages})


@dataclass(slots=True)
class _SpliceState:
    plan: ResumePlan
    continuing: bool = False
    trimming: bool = False
    trim_parts: list[str] = field(default_factory=list)
    block_zero_seen: bool = False
    offset: int = 0
    offset_set: bool = False
    junction_ws: int = 0


class AnthropicStreamResumePolicy:
    """Make streaming candidates resumable across model fallbacks."""

    def __init__(self) -> None:
        self._decoder = AnthropicSSEDecoder()
        self._reset_history()
        self._reset_transient()
        self._pending_failure: ExecutionFailure | None = None
        self._staged_plan: ResumePlan | None = None

    def stage_plan(self, plan: ResumePlan) -> None:
        # Hold a resume plan for the next candidate opener to claim.
        self._staged_plan = plan

    def take_staged_plan(self) -> ResumePlan | None:
        # Claim the staged resume plan, if any, exactly once.
        plan = self._staged_plan
        self._staged_plan = None
        return plan

    def _reset_history(self) -> None:
        self._message_start_emitted = False
        self._text_parts: list[str] = []
        self._next_block_index = 0
        self._open_block_index: int | None = None
        self._open_block_type: str | None = None
        self._tool_block_emitted = False
        self.saw_content_block = False
        self._saw_message_stop = False

    def _reset_transient(self) -> None:
        self._held_frames: list[str] = []
        self._holding_tool_block = False
        self._held_terminal: list[str] = []
        self._splice: _SpliceState | None = None

    def reset(self) -> None:
        """Forget everything; the next candidate starts a brand-new response."""

        self._decoder = AnthropicSSEDecoder()
        self._reset_history()
        self._reset_transient()
        self._pending_failure = None
        self._staged_plan = None

    @property
    def completed_cleanly(self) -> bool:
        """True when the candidate stream reached a terminal message_stop."""

        return self._saw_message_stop

    def take_pending_failure(self) -> ExecutionFailure | None:
        """Return and clear an in-band error event seen since the last call."""

        failure = self._pending_failure
        self._pending_failure = None
        return failure

    def transform(self, chunk: str) -> tuple[str, ...]:
        """Consume one provider chunk and return frames safe to deliver."""

        emitted: list[str] = []
        for raw in self._decoder.feed_frames(chunk):
            emitted.extend(self._handle_frame(raw))
        return tuple(emitted)

    def finish(self) -> tuple[str, ...]:
        """Flush a final unterminated frame and the held terminal frames.

        ``message_delta`` and ``message_stop`` are held back while the
        candidate runs so an empty or truncated response never delivers a
        terminal frame before the executor decides the candidate failed.
        They flush here only when the candidate produced content.
        """

        emitted: list[str] = []
        for raw in self._decoder.finish_frames():
            emitted.extend(self._handle_frame(raw))
        if self._saw_message_stop:
            emitted.extend(self._held_terminal)
        self._held_terminal = []
        return tuple(emitted)

    def begin_resume(self) -> ResumePlan | None:
        """Plan the next candidate's continuation, or None when unsafe.

        A fully delivered ``tool_use`` block cannot be reconstructed for the
        prefill, so resuming after one has been delivered is unsafe.
        """

        if self._tool_block_emitted:
            return None
        emitted_text = "".join(self._text_parts)
        prefill = emitted_text.rstrip() or None
        plan = ResumePlan(
            prefill_text=prefill,
            continue_text_block=(
                self._open_block_index if self._open_block_type == "text" else None
            ),
            close_block=(
                self._open_block_index if self._open_block_type == "thinking" else None
            ),
            next_block_offset=self._next_block_index,
            forward_message_start=not self._message_start_emitted,
        )
        self._decoder = AnthropicSSEDecoder()
        self._held_frames = []
        self._holding_tool_block = False
        self._held_terminal = []
        self._splice = _SpliceState(plan=plan)
        if prefill is not None:
            self._splice.junction_ws = len(emitted_text) - len(prefill)
        return plan

    def _handle_frame(self, raw: str) -> list[str]:
        events = parse_sse_text(raw)
        event = events[0] if events else None
        name = event.event if event is not None else ""
        data = event.data if event is not None else {}
        if self._splice is not None:
            return self._handle_splice_frame(raw, name, data)
        return self._handle_normal_frame(raw, name, data)

    def _handle_normal_frame(
        self, raw: str, name: str, data: dict[str, object]
    ) -> list[str]:
        if name == "message_start":
            self._message_start_emitted = True
            return [raw]
        if name == "content_block_start":
            return self._handle_block_start(raw, data, offset=0)
        if name == "content_block_delta":
            return self._handle_block_delta(raw, data, offset=0)
        if name == "content_block_stop":
            return self._handle_block_stop(raw, data, offset=0)
        if name == "message_delta" or name == "message_stop":
            if name == "message_stop":
                self._saw_message_stop = True
            self._held_terminal.append(raw)
            return []
        if name == "error":
            self._pending_failure = self._error_event_failure(data)
            return []
        return [raw]

    def _handle_block_start(
        self, raw: str, data: dict[str, object], *, offset: int
    ) -> list[str]:
        block = data.get("content_block")
        block_type = block.get("type", "") if isinstance(block, dict) else ""
        index = data.get("index")
        if not isinstance(index, int):
            index = self._next_block_index
        new_index = index + offset
        self.saw_content_block = True
        frame = raw if new_index == index else self._reindexed(raw, data, new_index)
        if block_type == "tool_use":
            self._holding_tool_block = True
            self._held_frames = [frame]
            return []
        self._open_block_index = new_index
        self._open_block_type = str(block_type)
        self._next_block_index = new_index + 1
        return [frame]

    def _handle_block_delta(
        self, raw: str, data: dict[str, object], *, offset: int
    ) -> list[str]:
        index = data.get("index")
        if not isinstance(index, int):
            index = self._open_block_index or 0
        new_index = index + offset
        frame = raw if new_index == index else self._reindexed(raw, data, new_index)
        if self._holding_tool_block:
            self._held_frames.append(frame)
            return []
        if self._open_block_type == "text":
            delta = data.get("delta")
            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                text = delta.get("text")
                if isinstance(text, str):
                    self._text_parts.append(text)
        return [frame]

    def _handle_block_stop(
        self, raw: str, data: dict[str, object], *, offset: int
    ) -> list[str]:
        index = data.get("index")
        if not isinstance(index, int):
            index = self._open_block_index or 0
        new_index = index + offset
        frame = raw if new_index == index else self._reindexed(raw, data, new_index)
        if self._holding_tool_block:
            self._held_frames.append(frame)
            self._holding_tool_block = False
            self._tool_block_emitted = True
            self._next_block_index = new_index + 1
            flushed = self._held_frames
            self._held_frames = []
            return flushed
        self._open_block_index = None
        self._open_block_type = None
        return [frame]

    def _handle_splice_frame(
        self, raw: str, name: str, data: dict[str, object]
    ) -> list[str]:
        splice = self._splice
        assert splice is not None
        plan = splice.plan
        if name == "message_start":
            if plan.forward_message_start:
                self._message_start_emitted = True
                return [raw]
            return []
        if name == "content_block_start":
            block = data.get("content_block")
            block_type = block.get("type", "") if isinstance(block, dict) else ""
            index = data.get("index")
            if not isinstance(index, int):
                index = 0
            prefix: list[str] = []
            if not splice.block_zero_seen:
                splice.block_zero_seen = True
                if index == 0 and plan.continue_text_block is not None:
                    if block_type == "text":
                        # The prefilled candidate continues the open text
                        # block: drop its start frame and merge the deltas.
                        # Its blocks are numbered behind the continued one.
                        splice.continuing = True
                        splice.trimming = True
                        splice.offset = plan.next_block_offset - 1
                        splice.offset_set = True
                        return []
                    # It opened something else: close our open block first.
                    prefix.append(self._close_frame(plan.continue_text_block))
                    plan = ResumePlan(
                        prefill_text=plan.prefill_text,
                        continue_text_block=None,
                        close_block=plan.close_block,
                        next_block_offset=plan.next_block_offset,
                        forward_message_start=plan.forward_message_start,
                    )
                    splice.plan = plan
                if plan.close_block is not None:
                    prefix.append(self._close_frame(plan.close_block))
                    plan = ResumePlan(
                        prefill_text=plan.prefill_text,
                        continue_text_block=plan.continue_text_block,
                        close_block=None,
                        next_block_offset=plan.next_block_offset,
                        forward_message_start=plan.forward_message_start,
                    )
                    splice.plan = plan
            if not splice.offset_set:
                splice.offset = splice.plan.next_block_offset
                splice.offset_set = True
            return prefix + self._handle_block_start(raw, data, offset=splice.offset)
        if name == "content_block_delta":
            index = data.get("index")
            if not isinstance(index, int):
                index = 0
            if splice.continuing and index == 0:
                return self._handle_continuing_delta(splice, data)
            return self._handle_block_delta(raw, data, offset=splice.offset)
        if name == "content_block_stop":
            index = data.get("index")
            if not isinstance(index, int):
                index = 0
            if splice.continuing and index == 0:
                splice.continuing = False
                frame = self._close_frame(plan.continue_text_block)
                self._open_block_index = None
                self._open_block_type = None
                return [frame]
            return self._handle_block_stop(raw, data, offset=splice.offset)
        if name == "message_delta" or name == "message_stop":
            if name == "message_stop":
                self._saw_message_stop = True
            self._held_terminal.append(raw)
            return []
        if name == "error":
            self._pending_failure = self._error_event_failure(data)
            return []
        return [raw]

    def _handle_continuing_delta(
        self, splice: _SpliceState, data: dict[str, object]
    ) -> list[str]:
        plan = splice.plan
        index = plan.continue_text_block
        assert index is not None
        delta = data.get("delta")
        text = ""
        if isinstance(delta, dict) and delta.get("type") == "text_delta":
            value = delta.get("text")
            text = value if isinstance(value, str) else ""
        if not splice.trimming:
            self._text_parts.append(text)
            return [
                format_sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "text_delta", "text": text},
                    },
                )
            ]
        splice.trim_parts.append(text)
        new_text = "".join(splice.trim_parts)
        prefill = plan.prefill_text or ""
        if len(new_text) <= _TRIM_BUFFER_LIMIT and prefill.startswith(new_text):
            # The candidate is repeating the prefill; keep buffering.
            return []
        splice.trimming = False
        overlap = 0
        if prefill and new_text.startswith(prefill):
            # The candidate echoed the whole prefill before continuing.
            overlap = len(prefill)
        else:
            limit = min(len(new_text), len(prefill))
            for size in range(limit, 0, -1):
                if prefill.endswith(new_text[:size]):
                    overlap = size
                    break
        remainder = new_text[overlap:]
        if splice.junction_ws:
            # The prefill was rstripped, so the echo the client already saw
            # ends at the rstripped whitespace; the candidate regenerates
            # that whitespace at the start of its continuation.
            stripped = 0
            while (
                stripped < len(remainder)
                and stripped < splice.junction_ws
                and remainder[stripped].isspace()
            ):
                stripped += 1
            remainder = remainder[stripped:]
            splice.junction_ws = 0
        if not remainder:
            return []
        self._text_parts.append(remainder)
        return [
            format_sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "text_delta", "text": remainder},
                },
            )
        ]

    def _close_frame(self, index: int | None) -> str:
        return format_sse_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": index if index is not None else 0},
        )

    @staticmethod
    def _reindexed(raw: str, data: dict[str, object], new_index: int) -> str:
        events = parse_sse_text(raw)
        name = events[0].event if events else ""
        payload = dict(data)
        payload["index"] = new_index
        return format_sse_event(name, payload)

    @staticmethod
    def _error_event_failure(data: dict[str, object]) -> ExecutionFailure:
        error = data.get("error")
        error_type = ""
        message = "Provider reported an error mid-stream."
        if isinstance(error, dict):
            raw_type = error.get("type")
            if isinstance(raw_type, str):
                error_type = raw_type
            raw_message = error.get("message")
            if isinstance(raw_message, str) and raw_message:
                message = raw_message
        kind = _ERROR_KIND_BY_TYPE.get(error_type, FailureKind.UPSTREAM)
        return ExecutionFailure(
            kind=kind,
            status_code=502,
            message=message,
            retryable=True,
            wire_error_type=error_type or None,
        )
