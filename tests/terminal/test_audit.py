"""Audit sink tests."""

import json
from pathlib import Path

from free_claude_code.terminal import CommandSpec, JsonlAuditSink, TerminalExecutor
from free_claude_code.terminal.audit import make_event


def test_jsonl_sink_writes_parseable_events(tmp_path: Path) -> None:
    path = tmp_path / "audit" / "events.jsonl"
    sink = JsonlAuditSink(path)
    sink.record(make_event("planned", "t1", ("echo", "hi"), None))
    sink.record(make_event("finished", "t1", ("echo", "hi"), None, exit_code=0))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["kind"] == "planned"
    assert first["argv"] == ["echo", "hi"]
    second = json.loads(lines[1])
    assert second["exit_code"] == 0


def test_jsonl_sink_records_full_run(
    tmp_path: Path, echo_stdout: tuple[str, ...]
) -> None:
    path = tmp_path / "events.jsonl"
    TerminalExecutor(sink=JsonlAuditSink(path)).run(CommandSpec(argv=echo_stdout))
    kinds = [
        json.loads(line)["kind"]
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert kinds == ["planned", "started", "finished"]


def test_make_event_timestamps() -> None:
    event = make_event("started", "t", ("x",), "/tmp")
    assert event.timestamp > 0
    assert event.as_dict()["task_id"] == "t"
