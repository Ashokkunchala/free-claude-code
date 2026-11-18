"""Tests for the persisted daily usage ledger."""

from collections.abc import Iterator
from typing import Any

import pytest

from free_claude_code.cli import usage_ledger


@pytest.fixture(autouse=True)
def _clean_ledger() -> Iterator[None]:
    usage_ledger.reset()
    yield
    usage_ledger.reset()


def _status(
    *,
    calls: int = 0,
    rate_limit_hits: int = 0,
    recovery_episodes: int = 0,
    input_tokens: int = 0,
) -> dict[str, Any]:
    return {
        "admission": {
            "nvidia_nim": {
                "usage": {
                    "calls_total": calls,
                    "rate_limit_hits": rate_limit_hits,
                    "recovery_episodes": recovery_episodes,
                }
            }
        },
        "usage": {"input_tokens": {"nvidia_nim": input_tokens}},
    }


def test_empty_ledger_when_file_missing() -> None:
    ledger = usage_ledger.load()
    assert ledger["providers"] == {}
    assert ledger["seen"] == {}


def test_accumulate_folds_counters_as_deltas() -> None:
    usage_ledger.accumulate(_status(calls=5, rate_limit_hits=1))
    ledger = usage_ledger.accumulate(_status(calls=7, rate_limit_hits=1))

    provider = ledger["providers"]["nvidia_nim"]
    assert provider["calls"] == 7
    assert provider["rate_limit_hits"] == 1


def test_accumulate_is_idempotent_for_same_snapshot() -> None:
    status = _status(calls=5)
    usage_ledger.accumulate(status)
    ledger = usage_ledger.accumulate(status)

    assert ledger["providers"]["nvidia_nim"]["calls"] == 5


def test_accumulate_rebaselines_when_counter_moves_backwards() -> None:
    usage_ledger.accumulate(_status(calls=10))
    ledger = usage_ledger.accumulate(_status(calls=3))

    # The negative delta is dropped; totals keep the high-water mark.
    assert ledger["providers"]["nvidia_nim"]["calls"] == 10

    # The lower value becomes the new baseline for future growth.
    ledger = usage_ledger.accumulate(_status(calls=5))
    assert ledger["providers"]["nvidia_nim"]["calls"] == 12


def test_accumulate_folds_input_tokens() -> None:
    usage_ledger.accumulate(_status(input_tokens=100))
    ledger = usage_ledger.accumulate(_status(input_tokens=250))

    provider = ledger["providers"]["nvidia_nim"]
    assert provider["input_tokens"] == 250


def test_accumulate_rebaselines_input_tokens_on_backwards_move() -> None:
    usage_ledger.accumulate(_status(input_tokens=1000))
    ledger = usage_ledger.accumulate(_status(input_tokens=10))

    provider = ledger["providers"]["nvidia_nim"]
    assert provider["input_tokens"] == 1010


def test_accumulate_tolerates_unrelated_status_shape() -> None:
    ledger = usage_ledger.accumulate({"status": "running"})

    assert ledger["providers"] == {}


def test_reset_discards_ledger() -> None:
    usage_ledger.accumulate(_status(calls=3))
    usage_ledger.reset()

    assert usage_ledger.load()["providers"] == {}
