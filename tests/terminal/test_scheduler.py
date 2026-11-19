"""Bounded-concurrency scheduling: caps, backpressure, audit events."""

import threading
import time

import pytest

from free_claude_code.terminal.audit import InMemoryAuditSink
from free_claude_code.terminal.result import CommandResult
from free_claude_code.terminal.scheduler import (
    CommandScheduler,
    QueueFullError,
    SchedulerConfig,
)
from free_claude_code.terminal.spec import CommandSpec


def make_result(spec: CommandSpec) -> CommandResult:
    return CommandResult(
        argv=spec.argv,
        cwd=spec.cwd,
        exit_code=0,
        duration_seconds=0.0,
        stdout=b"",
        stderr=b"",
    )


class StubExecutor:
    """Executor double with an instrumentable run()."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.concurrent = 0
        self.max_concurrent = 0
        self._lock = threading.Lock()
        self.calls: list[tuple[str, ...]] = []

    def run(self, spec, *, policy=None, cancel_event=None, on_output=None, task_id=None):
        with self._lock:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            self.calls.append(spec.argv)
        try:
            time.sleep(self.delay)
            return make_result(spec)
        finally:
            with self._lock:
                self.concurrent -= 1


def spec(*argv: str) -> CommandSpec:
    return CommandSpec(argv=argv)


def test_config_validation():
    with pytest.raises(ValueError, match="max_running"):
        SchedulerConfig(max_running=0)
    with pytest.raises(ValueError, match="max_queued"):
        SchedulerConfig(max_queued=-1)
    assert SchedulerConfig().max_running == 4


def test_submissions_run_and_resolve():
    scheduler = CommandScheduler(StubExecutor())
    futures = [scheduler.submit(spec("echo", str(i))) for i in range(3)]
    results = [future.result(timeout=5) for future in futures]
    assert all(result.ok for result in results)
    scheduler.shutdown()


def test_running_cap_is_respected():
    executor = StubExecutor(delay=0.05)
    scheduler = CommandScheduler(executor, SchedulerConfig(max_running=2, max_queued=8))
    futures = [scheduler.submit(spec("sleep")) for _ in range(8)]
    for future in futures:
        future.result(timeout=10)
    assert executor.max_concurrent <= 2
    scheduler.shutdown()


def test_backpressure_raises_when_full():
    executor = StubExecutor(delay=0.2)
    sink = InMemoryAuditSink()
    scheduler = CommandScheduler(
        executor, SchedulerConfig(max_running=1, max_queued=1), sink=sink
    )
    scheduler.submit(spec("first"), task_id="t1")
    deadline = time.monotonic() + 5
    while scheduler.stats().running == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    scheduler.submit(spec("second"), task_id="t2")
    with pytest.raises(QueueFullError, match="queue is full"):
        scheduler.submit(spec("third"), task_id="t3")
    kinds = [event.kind for event in sink.events()]
    assert "queue_full" in kinds
    scheduler.shutdown()


def test_queue_slots_free_up_as_runs_finish():
    executor = StubExecutor(delay=0.02)
    scheduler = CommandScheduler(executor, SchedulerConfig(max_running=1, max_queued=0))
    first = scheduler.submit(spec("one"))
    deadline = time.monotonic() + 5
    while scheduler.stats().running == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    with pytest.raises(QueueFullError):
        scheduler.submit(spec("two"))
    first.result(timeout=5)
    deadline = time.monotonic() + 5
    while scheduler.stats().running > 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    third = scheduler.submit(spec("three"))
    assert third.result(timeout=5).ok
    scheduler.shutdown()


def test_stats_track_running_and_queued():
    executor = StubExecutor(delay=0.2)
    scheduler = CommandScheduler(executor, SchedulerConfig(max_running=1, max_queued=4))
    futures = [scheduler.submit(spec("x", str(i))) for i in range(3)]
    deadline = time.monotonic() + 5
    while scheduler.stats().running == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    stats = scheduler.stats()
    assert stats.running == 1
    assert stats.queued == 2
    for future in futures:
        future.result(timeout=10)
    scheduler.shutdown()


def test_lifecycle_events_are_recorded():
    sink = InMemoryAuditSink()
    scheduler = CommandScheduler(StubExecutor(), sink=sink)
    scheduler.submit(spec("echo", "hi"), task_id="task-1").result(timeout=5)
    scheduler.shutdown()
    kinds = [event.kind for event in sink.events()]
    assert kinds == ["enqueued", "dequeued"]
    assert sink.events()[0].task_id == "task-1"


def test_shutdown_rejects_new_submissions():
    scheduler = CommandScheduler(StubExecutor())
    scheduler.shutdown()
    with pytest.raises(QueueFullError, match="shut down"):
        scheduler.submit(spec("echo"))


def test_executor_failures_propagate_and_free_slots():
    class Failing:
        def run(self, spec, *, policy=None, cancel_event=None, on_output=None, task_id=None):
            raise RuntimeError("boom")

    scheduler = CommandScheduler(
        Failing(), SchedulerConfig(max_running=1, max_queued=0)
    )
    with pytest.raises(RuntimeError, match="boom"):
        scheduler.submit(spec("bad")).result(timeout=5)
    deadline = time.monotonic() + 5
    while scheduler.stats().running > 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert scheduler.stats() == scheduler.stats().__class__(running=0, queued=0)
    scheduler.shutdown()
