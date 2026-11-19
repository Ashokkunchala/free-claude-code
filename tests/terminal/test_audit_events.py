"""Extended audit coverage: failure kinds and approval decision events."""

import json
import sys

import pytest

from free_claude_code.terminal import (
    CommandSpec,
    InMemoryAuditSink,
    SpawnError,
    TerminalExecutor,
)
from free_claude_code.terminal.audit import record_decision


def py(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def test_nonzero_exit_records_failed() -> None:
    sink = InMemoryAuditSink()
    result = TerminalExecutor(sink=sink).run(
        CommandSpec(argv=py("raise SystemExit(3)"))
    )
    assert result.exit_code == 3
    assert not result.ok
    kinds = [event.kind for event in sink.events()]
    assert kinds == ["planned", "started", "failed"]
    assert sink.events()[-1].exit_code == 3


def test_zero_exit_still_records_finished() -> None:
    sink = InMemoryAuditSink()
    TerminalExecutor(sink=sink).run(CommandSpec(argv=py("print('ok')")))
    kinds = [event.kind for event in sink.events()]
    assert kinds == ["planned", "started", "finished"]


def test_spawn_failure_records_failed() -> None:
    sink = InMemoryAuditSink()
    with pytest.raises(SpawnError):
        TerminalExecutor(sink=sink).run(
            CommandSpec(argv=("definitely-not-a-real-binary-fcc",))
        )
    kinds = [event.kind for event in sink.events()]
    assert kinds == ["planned", "failed"]
    assert sink.events()[-1].exit_code is None


def test_record_decision_granted() -> None:
    sink = InMemoryAuditSink()
    record_decision(
        sink,
        granted=True,
        task_id="t1",
        argv=("git", "push"),
        cwd=None,
        policy_id="policy:git:push",
    )
    (event,) = sink.events()
    assert event.kind == "approved"
    assert event.task_id == "t1"
    assert event.policy_id == "policy:git:push"


def test_record_decision_denied() -> None:
    sink = InMemoryAuditSink()
    record_decision(sink, granted=False, task_id="t2", argv=("rm", "-rf"), cwd=None)
    (event,) = sink.events()
    assert event.kind == "denied"


def test_jsonl_sink_round_trips_new_kinds(tmp_path) -> None:
    from free_claude_code.terminal import JsonlAuditSink

    path = tmp_path / "audit" / "events.jsonl"
    sink = JsonlAuditSink(path)
    record_decision(sink, granted=True, task_id="t9", argv=("ls",), cwd=None)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["kind"] == "approved"
    assert rows[0]["task_id"] == "t9"
