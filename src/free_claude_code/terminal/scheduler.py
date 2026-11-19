"""Bounded-concurrency scheduling for terminal commands (roadmap section 9).

``CommandScheduler`` wraps an executor with a hard cap on concurrent runs
and a bounded queue ahead of it. When both are full, submission applies
backpressure by raising :class:`QueueFullError` immediately instead of
letting work pile up unboundedly - callers surface that to the user and
retry, rather than discovering a stall later. Queue lifecycle is audible:
enqueued/dequeued/rejected submissions are recorded through the audit sink
so queue pressure shows up in the same event stream as command runs.
"""

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

from free_claude_code.terminal.audit import AuditSink, NullAuditSink, make_event
from free_claude_code.terminal.result import CommandResult
from free_claude_code.terminal.spec import (
    DEFAULT_POLICY_DECISION,
    CommandSpec,
    PolicyDecision,
)


class QueueFullError(RuntimeError):
    """Submission rejected: running and queued slots are both exhausted."""


class _Runner(Protocol):
    def run(
        self,
        spec: CommandSpec,
        *,
        policy: PolicyDecision = DEFAULT_POLICY_DECISION,
        cancel_event: threading.Event | None = None,
        on_output: Callable | None = None,
        task_id: str | None = None,
    ) -> CommandResult: ...


@dataclass(frozen=True)
class SchedulerConfig:
    """Bounds for one scheduler: concurrent runs and queued submissions."""

    max_running: int = 4
    max_queued: int = 32

    def __post_init__(self) -> None:
        if self.max_running < 1:
            raise ValueError("SchedulerConfig.max_running must be >= 1")
        if self.max_queued < 0:
            raise ValueError("SchedulerConfig.max_queued must be >= 0")


@dataclass(frozen=True)
class SchedulerStats:
    """Point-in-time scheduler occupancy."""

    running: int
    queued: int


class CommandScheduler:
    """Bounded-concurrency front-end for a terminal executor."""

    def __init__(
        self,
        executor: _Runner,
        config: SchedulerConfig | None = None,
        *,
        sink: AuditSink | None = None,
    ) -> None:
        self._executor = executor
        self._config = config if config is not None else SchedulerConfig()
        self._sink = sink if sink is not None else NullAuditSink()
        self._pool = ThreadPoolExecutor(
            max_workers=self._config.max_running,
            thread_name_prefix="fcc-scheduler",
        )
        self._lock = threading.Lock()
        self._inflight = 0
        self._running = 0
        self._shutdown = False

    @property
    def config(self) -> SchedulerConfig:
        return self._config

    def stats(self) -> SchedulerStats:
        with self._lock:
            return SchedulerStats(
                running=self._running, queued=self._inflight - self._running
            )

    def submit(
        self,
        spec: CommandSpec,
        *,
        policy: PolicyDecision = DEFAULT_POLICY_DECISION,
        cancel_event: threading.Event | None = None,
        task_id: str | None = None,
    ) -> Future[CommandResult]:
        """Queue one command, or apply backpressure when the queue is full."""

        with self._lock:
            if self._shutdown:
                raise QueueFullError("scheduler is shut down")
            capacity = self._config.max_running + self._config.max_queued
            if self._inflight >= capacity:
                self._sink.record(
                    make_event(
                        "queue_full",
                        task_id or "-",
                        spec.argv,
                        spec.cwd,
                        policy_id=policy.policy_id,
                    )
                )
                raise QueueFullError(
                    f"scheduler queue is full ({self._inflight}/{capacity} in flight)"
                )
            self._inflight += 1
        self._sink.record(
            make_event(
                "enqueued",
                task_id or "-",
                spec.argv,
                spec.cwd,
                policy_id=policy.policy_id,
            )
        )
        future: Future[CommandResult] = self._pool.submit(
            self._run_one, spec, policy, cancel_event, task_id
        )
        return future

    def _run_one(
        self,
        spec: CommandSpec,
        policy: PolicyDecision,
        cancel_event: threading.Event | None,
        task_id: str | None,
    ) -> CommandResult:
        with self._lock:
            self._running += 1
        self._sink.record(
            make_event(
                "dequeued",
                task_id or "-",
                spec.argv,
                spec.cwd,
                policy_id=policy.policy_id,
            )
        )
        try:
            return self._executor.run(
                spec,
                policy=policy,
                cancel_event=cancel_event,
                task_id=task_id,
            )
        finally:
            with self._lock:
                self._running -= 1
                self._inflight -= 1

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop accepting submissions; optionally wait for in-flight work."""

        with self._lock:
            self._shutdown = True
        self._pool.shutdown(wait=wait)
