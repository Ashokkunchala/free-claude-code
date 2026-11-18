"""Passthrough relay frames native events verbatim after one serialization."""

import json
from typing import cast

import pytest

from free_claude_code.core.anthropic.native import NativeMessagesError
from free_claude_code.core.anthropic.passthrough import NativeMessagesPassthrough
from free_claude_code.core.json_types import JsonObject

_START: JsonObject = {
    "type": "message_start",
    "message": {
        "id": "upstream-id",
        "type": "message",
        "model": "upstream",
        "role": "assistant",
        "content": [],
        "usage": {"input_tokens": 3, "output_tokens": 0},
        "native_extension": "kept",
    },
}


def _start_frame() -> str:
    swapped = {
        **_START,
        "message": {**cast(JsonObject, _START["message"]), "model": "public"},
    }
    return f"event: message_start\ndata: {json.dumps(swapped, ensure_ascii=False)}\n\n"


def test_relay_frames_events_verbatim() -> None:
    relay = NativeMessagesPassthrough("public")
    assert relay.feed("message_start", _START) == _start_frame()
    delta: JsonObject = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {
            "type": "text_delta",
            "text": "left\u2028middle\u2029right",
        },
    }
    assert relay.feed("content_block_delta", delta) == (
        f"event: content_block_delta\ndata: {json.dumps(delta, ensure_ascii=False)}\n\n"
    )
    stop: JsonObject = {"type": "message_stop"}
    assert relay.feed("message_stop", stop) == (
        f"event: message_stop\ndata: {json.dumps(stop, ensure_ascii=False)}\n\n"
    )
    assert relay.completed


def test_message_start_swaps_public_model_without_mutating_input() -> None:
    relay = NativeMessagesPassthrough("public")
    relay.feed("message_start", _START)
    assert relay.started
    assert cast(JsonObject, _START["message"])["model"] == "upstream"
    block: JsonObject = {
        "type": "content_block_start",
        "index": 5,
        "content_block": {"type": "text", "text": "hello", "citations": []},
    }
    assert relay.feed("content_block_start", block) == (
        f"event: content_block_start\ndata: {json.dumps(block, ensure_ascii=False)}\n\n"
    )
    assert not relay.completed


def test_leading_ping_is_suppressed_but_later_events_are_rejected() -> None:
    relay = NativeMessagesPassthrough("public")
    ping: JsonObject = {"type": "ping"}
    assert relay.feed("ping", ping) is None
    delta: JsonObject = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "orphan"},
    }
    with pytest.raises(NativeMessagesError, match="before message_start"):
        relay.feed("content_block_delta", delta)


def test_invalid_lifecycles_are_rejected() -> None:
    relay = NativeMessagesPassthrough("public")
    with pytest.raises(NativeMessagesError, match="Invalid native message_start"):
        relay.feed("message_start", {"type": "message_start"})
    assert relay.feed("message_start", _START) == _start_frame()
    with pytest.raises(NativeMessagesError, match="Invalid native message_start"):
        relay.feed("message_start", _START)
    relay.feed("message_stop", {"type": "message_stop"})
    with pytest.raises(NativeMessagesError, match="after message_stop"):
        relay.feed("message_stop", {"type": "message_stop"})


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "message_start", "message": {"model": float("nan")}},
        {"type": "message_start", "message": {"model": "\ud800"}},
        {"type": "message_stop", "extra": float("nan")},
    ],
)
def test_non_encodable_events_are_rejected(payload: JsonObject) -> None:
    relay = NativeMessagesPassthrough("public")
    relay.feed("message_start", _START)
    with pytest.raises(NativeMessagesError):
        relay.feed(cast(str, payload["type"]), payload)


def test_event_kind_must_match_payload_type() -> None:
    relay = NativeMessagesPassthrough("public")
    with pytest.raises(NativeMessagesError, match="invalid payload type"):
        relay.feed("message_start", {"type": "message_delta"})
