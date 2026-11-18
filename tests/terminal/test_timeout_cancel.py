"""Timeout, cancellation, output-cap and orphan tests."""

import os
import threading
import time

import pytest

from free_claude_code.terminal import CommandSpec, InMemoryAuditSink, TerminalExecutor
from free_claude_code.terminal.processes import tracked_pids


def test_timeout_kills_process(hang_forever: tuple[str, ...]) -> None:
    sink = InMemoryAuditSink()
    started = time.monotonic()
    result = TerminalExecutor(sink=sink).run(
        CommandSpec(argv=hang_forever, timeout_seconds=0.3)
    )
    elapsed = time.monotonic() - started
    assert result.timed_out
    assert not result.cancelled
    assert result.exit_code is not None  # killed, not still running
    assert elapsed < 30
    assert [event.kind for event in sink.events()][-1] == "timed_out"
    assert tracked_pids() == []


def test_cancellation_kills_process(hang_forever: tuple[str, ...]) -> None:
    cancel = threading.Event()
    timer = threading.Timer(0.3, cancel.set)
    timer.start()
    try:
        result = TerminalExecutor().run(
            CommandSpec(argv=hang_forever, timeout_seconds=60), cancel_event=cancel
        )
    finally:
        timer.cancel()
    assert result.cancelled
    assert not result.timed_out
    assert tracked_pids() == []


def test_output_cap_truncates(big_output: tuple[str, ...]) -> None:
    result = TerminalExecutor().run(
        CommandSpec(argv=big_output, output_cap_bytes=10_000)
    )
    assert result.stdout_truncated
    assert len(result.stdout) == 10_000


@pytest.mark.skipif(os.name == "nt", reason="process-group orphan check is POSIX-only")
def test_timeout_kills_grandchildren(spawn_child_then_hang: tuple[str, ...]) -> None:
    result = TerminalExecutor().run(
        CommandSpec(argv=spawn_child_then_hang, timeout_seconds=0.5)
    )
    assert result.timed_out
    grandchild_pid = int(result.stdout_text.strip())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild_pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    raise AssertionError(f"grandchild {grandchild_pid} survived tree kill")
