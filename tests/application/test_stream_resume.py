"""AnthropicStreamResumePolicy: streaming failover and resume mechanics."""

import json

from free_claude_code.application.stream_resume import (
    AnthropicStreamResumePolicy,
    ResumePlan,
)
from free_claude_code.core.anthropic.models import Message, MessagesRequest
from free_claude_code.core.anthropic.streaming.emitter import format_sse_event
from free_claude_code.core.failures import FailureKind


def sse(event: str, data: dict) -> str:
    return format_sse_event(event, data)


MESSAGE_START = sse(
    "message_start",
    {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": "provider-model",
            "usage": {"input_tokens": 3, "output_tokens": 1},
        },
    },
)
MESSAGE_DELTA = sse(
    "message_delta",
    {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn"},
        "usage": {"output_tokens": 7},
    },
)
MESSAGE_STOP = sse("message_stop", {"type": "message_stop"})


def text_start(index: int = 0) -> str:
    return sse(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "text", "text": ""},
        },
    )


def thinking_start(index: int = 0) -> str:
    return sse(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "thinking", "thinking": ""},
        },
    )


def tool_start(index: int = 0) -> str:
    return sse(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "Bash",
                "input": {},
            },
        },
    )


def text_delta(text: str, index: int = 0) -> str:
    return sse(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        },
    )


def json_delta(partial: str, index: int = 0) -> str:
    return sse(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "input_json_delta", "partial_json": partial},
        },
    )


def block_stop(index: int = 0) -> str:
    return sse("content_block_stop", {"type": "content_block_stop", "index": index})


def event_names(frames: tuple[str, ...] | list[str]) -> list[str]:
    names = []
    for frame in frames:
        names.extend(
            line.removeprefix("event: ")
            for line in frame.splitlines()
            if line.startswith("event: ")
        )
    return names


def frame_payload(frame: str) -> dict:
    for line in frame.splitlines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    raise AssertionError(f"frame without data: {frame!r}")


def test_text_stream_passes_through_unchanged() -> None:
    policy = AnthropicStreamResumePolicy()
    frames = [MESSAGE_START, text_start(), text_delta("Hello"), block_stop()]
    for frame in frames:
        assert policy.transform(frame) == (frame,)
    # Terminal frames are held until the candidate finishes, so an empty or
    # truncated response can still fail over without ending the client stream.
    assert policy.transform(MESSAGE_DELTA) == ()
    assert policy.transform(MESSAGE_STOP) == ()
    assert policy.completed_cleanly
    assert policy.finish() == (MESSAGE_DELTA, MESSAGE_STOP)
    assert policy.take_pending_failure() is None


def test_tool_use_block_is_held_until_its_stop() -> None:
    policy = AnthropicStreamResumePolicy()
    assert policy.transform(MESSAGE_START) == (MESSAGE_START,)
    assert policy.transform(tool_start()) == ()
    assert policy.transform(json_delta('{"command": "ls')) == ()
    stop = block_stop()
    flushed = policy.transform(stop)
    assert event_names(flushed) == [
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
    ]
    # A later text block streams live again.
    assert policy.transform(text_start(1)) == (text_start(1),)


def test_split_frames_are_buffered_until_complete() -> None:
    whole = AnthropicStreamResumePolicy()
    split = AnthropicStreamResumePolicy()
    stream = [
        MESSAGE_START,
        text_start(),
        text_delta("Hello world"),
        block_stop(),
        MESSAGE_STOP,
    ]
    whole_out: list[str] = []
    for frame in stream:
        whole_out.extend(whole.transform(frame))
    whole_out.extend(whole.finish())
    joined = "".join(stream)
    split_out: list[str] = []
    for cut in (joined[:7], joined[7:53], joined[53:101], joined[101:]):
        split_out.extend(split.transform(cut))
    split_out.extend(split.finish())
    assert event_names(split_out) == event_names(whole_out)


def test_finish_flushes_an_unterminated_final_frame() -> None:
    policy = AnthropicStreamResumePolicy()
    # Drop the trailing blank line: the frame is incomplete and must be held.
    assert policy.transform(MESSAGE_START[:-2]) == ()
    flushed = policy.finish()
    assert event_names(flushed) == ["message_start"]


def test_error_event_becomes_a_candidate_failure() -> None:
    policy = AnthropicStreamResumePolicy()
    policy.transform(MESSAGE_START)
    frame = sse(
        "error",
        {
            "type": "error",
            "error": {"type": "rate_limit_error", "message": "slow down"},
        },
    )
    assert policy.transform(frame) == ()
    failure = policy.take_pending_failure()
    assert failure is not None
    assert failure.kind is FailureKind.RATE_LIMIT
    assert failure.message == "slow down"
    assert policy.take_pending_failure() is None


def test_error_event_kind_mapping_defaults_to_upstream() -> None:
    policy = AnthropicStreamResumePolicy()
    frame = sse(
        "error", {"type": "error", "error": {"type": "weird_new_error", "message": "x"}}
    )
    policy.transform(frame)
    failure = policy.take_pending_failure()
    assert failure is not None
    assert failure.kind is FailureKind.UPSTREAM


def test_overloaded_error_maps_to_overloaded_failure() -> None:
    policy = AnthropicStreamResumePolicy()
    policy.transform(
        sse(
            "error",
            {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}},
        )
    )
    failure = policy.take_pending_failure()
    assert failure is not None
    assert failure.kind is FailureKind.OVERLOADED


def test_empty_stream_is_detectable() -> None:
    policy = AnthropicStreamResumePolicy()
    assert not policy.saw_content_block
    assert not policy.completed_cleanly
    policy.transform(MESSAGE_START)
    assert not policy.saw_content_block
    policy.transform(text_start())
    assert policy.saw_content_block


def test_begin_resume_after_partial_text_plans_prefill_and_continuation() -> None:
    policy = AnthropicStreamResumePolicy()
    policy.transform(MESSAGE_START)
    policy.transform(text_start())
    policy.transform(text_delta("Hello, wor"))
    plan = policy.begin_resume()
    assert plan is not None
    assert plan.prefill_text == "Hello, wor"
    assert plan.continue_text_block == 0
    assert plan.close_block is None
    assert plan.next_block_offset == 1
    assert plan.forward_message_start is False


def test_begin_resume_after_only_thinking_drops_the_prefill() -> None:
    policy = AnthropicStreamResumePolicy()
    policy.transform(MESSAGE_START)
    policy.transform(thinking_start())
    policy.transform(
        sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "hmm"},
            },
        )
    )
    plan = policy.begin_resume()
    assert plan is not None
    assert plan.prefill_text is None
    assert plan.continue_text_block is None
    assert plan.close_block == 0


def test_begin_resume_before_message_start_forwards_it() -> None:
    policy = AnthropicStreamResumePolicy()
    plan = policy.begin_resume()
    assert plan is not None
    assert plan.prefill_text is None
    assert plan.forward_message_start is True


def test_begin_resume_is_unsafe_after_a_delivered_tool_block() -> None:
    policy = AnthropicStreamResumePolicy()
    policy.transform(MESSAGE_START)
    policy.transform(tool_start())
    policy.transform(json_delta("{}"))
    policy.transform(block_stop())
    assert policy.begin_resume() is None


def test_resume_plan_apply_appends_an_assistant_prefill() -> None:
    request = MessagesRequest(model="m", messages=[Message(role="user", content="hi")])
    plan = ResumePlan(
        prefill_text="partial",
        continue_text_block=0,
        close_block=None,
        next_block_offset=1,
        forward_message_start=False,
    )
    updated = plan.apply(request)
    assert len(updated.messages) == 2
    assert updated.messages[-1].role == "assistant"
    assert updated.messages[-1].content == "partial"
    # The original request is untouched.
    assert len(request.messages) == 1


def test_resume_plan_apply_merges_into_a_trailing_assistant_message() -> None:
    request = MessagesRequest(
        model="m",
        messages=[
            Message(role="user", content="hi"),
            Message(role="assistant", content="existing"),
        ],
    )
    plan = ResumePlan(
        prefill_text=" more",
        continue_text_block=0,
        close_block=None,
        next_block_offset=1,
        forward_message_start=False,
    )
    updated = plan.apply(request)
    assert len(updated.messages) == 2
    assert updated.messages[-1].content == "existing more"


def test_resume_plan_without_prefill_returns_the_request() -> None:
    request = MessagesRequest(model="m", messages=[Message(role="user", content="hi")])
    plan = ResumePlan(
        prefill_text=None,
        continue_text_block=None,
        close_block=0,
        next_block_offset=1,
        forward_message_start=False,
    )
    assert plan.apply(request) is request


def test_splice_continues_the_open_text_block_and_trims_repeated_prefill() -> None:
    policy = AnthropicStreamResumePolicy()
    policy.transform(MESSAGE_START)
    policy.transform(text_start())
    policy.transform(text_delta("Hello, wor"))
    plan = policy.begin_resume()
    assert plan is not None

    # Resumed candidate: its own message_start is dropped, block 0 is a text
    # continuation, and the model repeats the prefill before adding new text.
    assert policy.transform(MESSAGE_START) == ()
    assert policy.transform(text_start()) == ()
    out = policy.transform(text_delta("Hello, wor"))
    assert out == ()  # still repeating the prefill: hold
    out = policy.transform(text_delta("ld!"))
    assert event_names(out) == ["content_block_delta"]
    assert frame_payload(out[0])["delta"] == {"type": "text_delta", "text": "ld!"}
    assert frame_payload(out[0])["index"] == 0
    # The resumed stop closes the original open block.
    out = policy.transform(block_stop())
    assert event_names(out) == ["content_block_stop"]
    assert frame_payload(out[0])["index"] == 0
    # Subsequent blocks are renumbered behind the continued one.
    out = policy.transform(tool_start(1))
    assert out == ()  # held as a tool block
    policy.transform(json_delta("{}", 1))
    flushed = policy.transform(block_stop(1))
    assert [frame_payload(f)["index"] for f in flushed] == [1, 1, 1]


def test_splice_trim_emits_only_the_suffix_beyond_the_overlap() -> None:
    policy = AnthropicStreamResumePolicy()
    policy.transform(MESSAGE_START)
    policy.transform(text_start())
    policy.transform(text_delta("the quick brown fox"))
    plan = policy.begin_resume()
    assert plan is not None

    policy.transform(MESSAGE_START)
    policy.transform(text_start())
    # Model repeats only a tail of the prefill, then diverges.
    out = policy.transform(text_delta("brown fox jumps"))
    assert event_names(out) == ["content_block_delta"]
    assert frame_payload(out[0])["delta"]["text"] == " jumps"


def test_splice_closes_the_open_block_when_the_resume_starts_with_a_tool() -> None:
    policy = AnthropicStreamResumePolicy()
    policy.transform(MESSAGE_START)
    policy.transform(text_start())
    policy.transform(text_delta("let me check"))
    plan = policy.begin_resume()
    assert plan is not None

    policy.transform(MESSAGE_START)
    out = policy.transform(tool_start())
    assert event_names(out) == ["content_block_stop"]
    assert frame_payload(out[0])["index"] == 0
    # The tool block itself is held, then flushed at its own index.
    policy.transform(json_delta("{}"))
    flushed = policy.transform(block_stop())
    assert [frame_payload(f)["index"] for f in flushed] == [1, 1, 1]


def test_splice_closes_open_thinking_before_new_blocks() -> None:
    policy = AnthropicStreamResumePolicy()
    policy.transform(MESSAGE_START)
    policy.transform(thinking_start())
    policy.transform(
        sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "hmm"},
            },
        )
    )
    plan = policy.begin_resume()
    assert plan is not None

    policy.transform(MESSAGE_START)
    out = policy.transform(text_start())
    assert event_names(out) == ["content_block_stop", "content_block_start"]
    assert frame_payload(out[0])["index"] == 0
    assert frame_payload(out[1])["index"] == 1


def test_splice_forwards_message_start_when_the_first_candidate_never_sent_one() -> (
    None
):
    policy = AnthropicStreamResumePolicy()
    plan = policy.begin_resume()
    assert plan is not None
    assert policy.transform(MESSAGE_START) == (MESSAGE_START,)
    out = policy.transform(text_start())
    assert event_names(out) == ["content_block_start"]


def test_double_resume_tracks_updated_history() -> None:
    policy = AnthropicStreamResumePolicy()
    policy.transform(MESSAGE_START)
    policy.transform(text_start())
    policy.transform(text_delta("first part, "))
    plan = policy.begin_resume()
    assert plan is not None
    policy.transform(MESSAGE_START)
    policy.transform(text_start())
    policy.transform(text_delta("first part, second part, "))
    # Second candidate dies mid-continuation; the plan must include both parts.
    plan2 = policy.begin_resume()
    assert plan2 is not None
    assert plan2.prefill_text == "first part, second part,"
    assert plan2.continue_text_block == 0


def test_error_event_during_a_splice_also_becomes_a_failure() -> None:
    policy = AnthropicStreamResumePolicy()
    policy.transform(MESSAGE_START)
    policy.transform(text_start())
    policy.transform(text_delta("partial"))
    policy.begin_resume()
    policy.transform(MESSAGE_START)
    frame = sse(
        "error",
        {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}},
    )
    assert policy.transform(frame) == ()
    failure = policy.take_pending_failure()
    assert failure is not None
    assert failure.kind is FailureKind.OVERLOADED


def test_reset_forgets_all_history() -> None:
    policy = AnthropicStreamResumePolicy()
    policy.transform(MESSAGE_START)
    policy.transform(text_start())
    policy.transform(text_delta("partial"))
    policy.reset()
    assert not policy.saw_content_block
    assert not policy.completed_cleanly
    plan = policy.begin_resume()
    assert plan is not None
    assert plan.prefill_text is None
    assert plan.forward_message_start is True
    # And a fresh stream passes through cleanly.
    assert policy.transform(MESSAGE_START) == (MESSAGE_START,)
