"""Audit events for terminal command execution.

Every command emits structured events through an ``AuditSink``: planned,
started, finished, and (when applicable) timed_out or cancelled. Sinks never
see raw output - only metadata - so audit files stay shareable.
"""

import json
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class AuditEvent:
    """One command-lifecycle event."""

    kind: str  # planned | started | finished | timed_out | cancelled
    task_id: str
    argv: tuple[str, ...]
    cwd: str | None
    timestamp: float
    duration_seconds: float | None = None
    exit_code: int | None = None
    policy_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "task_id": self.task_id,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "timestamp": self.timestamp,
            "duration_seconds": self.duration_seconds,
            "exit_code": self.exit_code,
            "policy_id": self.policy_id,
        }


class AuditSink(Protocol):
    def record(self, event: AuditEvent) -> None: ...


class NullAuditSink:
    """Drops every event (default until a sink is configured)."""

    def record(self, event: AuditEvent) -> None:
        pass


class InMemoryAuditSink:
    """Collects events for tests and short-lived sessions."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = threading.Lock()

    def record(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def events(self) -> list[AuditEvent]:
        with self._lock:
            return list(self._events)


class JsonlAuditSink:
    """Appends redacted-safe audit events as JSON lines."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def record(self, event: AuditEvent) -> None:
        line = json.dumps(event.as_dict(), sort_keys=True)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def make_event(
    kind: str,
    task_id: str,
    argv: tuple[str, ...],
    cwd: str | None,
    *,
    duration_seconds: float | None = None,
    exit_code: int | None = None,
    policy_id: str | None = None,
) -> AuditEvent:
    return AuditEvent(
        kind=kind,
        task_id=task_id,
        argv=argv,
        cwd=cwd,
        timestamp=time.time(),
        duration_seconds=duration_seconds,
        exit_code=exit_code,
        policy_id=policy_id,
    )


def sink_from_mapping(mapping: Mapping[str, AuditSink] | None, key: str) -> AuditSink:
    """Resolve a named sink or fall back to the null sink."""

    if mapping is None:
        return NullAuditSink()
    return mapping.get(key, NullAuditSink())
