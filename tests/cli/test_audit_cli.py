"""Tests for the fcc-audit CLI."""

import json
from pathlib import Path

import pytest

from free_claude_code.cli.audit import main, read_events


def write_log(path: Path, events: list[dict[str, object]]) -> None:
    lines = [json.dumps(event) for event in events]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_event(task_id: str, kind: str, argv: list[str]) -> dict[str, object]:
    return {
        "kind": kind,
        "task_id": task_id,
        "argv": argv,
        "cwd": None,
        "timestamp": 1000.0,
        "duration_seconds": None,
        "exit_code": None,
        "policy_id": None,
    }


def test_read_events_skips_corrupt_lines(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        '{"kind": "planned", "task_id": "t", "argv": ["ls"]}\n{corrupt\n\n',
        encoding="utf-8",
    )
    assert len(read_events(path)) == 1


def test_read_events_missing_file(tmp_path: Path) -> None:
    assert read_events(tmp_path / "missing.jsonl") == []


def test_list_prints_events(tmp_path: Path, capsys) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path, [make_event("t1", "planned", ["ls", "-la"])])
    main(["--audit-file", str(path), "list"])
    out = capsys.readouterr().out
    assert "t1 planned: ls -la" in out


def test_list_respects_limit(tmp_path: Path, capsys) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path, [make_event(f"t{i}", "planned", ["ls"]) for i in range(10)])
    main(["--audit-file", str(path), "list", "--limit", "3"])
    out = capsys.readouterr().out
    assert "t9" in out
    assert "t0" not in out


def test_list_empty(tmp_path: Path, capsys) -> None:
    path = tmp_path / "audit.jsonl"
    main(["--audit-file", str(path), "list"])
    assert "no audit events" in capsys.readouterr().out


def test_show_filters_by_task(tmp_path: Path, capsys) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(
        path,
        [
            make_event("t1", "planned", ["ls"]),
            make_event("t2", "planned", ["pwd"]),
            make_event("t1", "finished", ["ls"]),
        ],
    )
    main(["--audit-file", str(path), "show", "t1"])
    out = capsys.readouterr().out
    assert out.count("t1") == 2
    assert "t2" not in out


def test_show_unknown_task_fails(tmp_path: Path, capsys) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path, [make_event("t1", "planned", ["ls"])])
    with pytest.raises(SystemExit):
        main(["--audit-file", str(path), "show", "t2"])
    assert "no audit events for task t2" in capsys.readouterr().err


def test_list_json(tmp_path: Path, capsys) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path, [make_event("t1", "planned", ["ls"])])
    main(["--audit-file", str(path), "list", "--json"])
    events = json.loads(capsys.readouterr().out)
    assert events[0]["task_id"] == "t1"


def test_export_json_to_stdout_redacts_tokens(tmp_path: Path, capsys) -> None:
    path = tmp_path / "audit.jsonl"
    event = make_event("t1", "planned", ["deploy", "--key", "sk-" + "a" * 24])
    write_log(path, [event])
    main(["--audit-file", str(path), "export"])
    out = capsys.readouterr().out
    exported = json.loads(out)
    assert exported[0]["argv"][2] == "<redacted>"
    assert "sk-" + "a" * 24 not in out


def test_export_jsonl_emits_one_line_per_event(tmp_path: Path, capsys) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(
        path,
        [make_event("t1", "planned", ["ls"]), make_event("t1", "finished", ["ls"])],
    )
    main(["--audit-file", str(path), "export", "--format", "jsonl"])
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 2
    assert all(json.loads(line)["task_id"] == "t1" for line in lines)


def test_export_text_renders_events(tmp_path: Path, capsys) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path, [make_event("t1", "planned", ["ls", "-la"])])
    main(["--audit-file", str(path), "export", "--format", "text"])
    out = capsys.readouterr().out
    assert "t1 planned: ls -la" in out


def test_export_writes_output_file(tmp_path: Path, capsys) -> None:
    path = tmp_path / "audit.jsonl"
    output = tmp_path / "report.json"
    write_log(path, [make_event("t1", "planned", ["ls"])])
    main(["--audit-file", str(path), "export", "--output", str(output)])
    assert capsys.readouterr().out == ""
    exported = json.loads(output.read_text(encoding="utf-8"))
    assert exported[0]["task_id"] == "t1"


def test_export_filters_by_task_id(tmp_path: Path, capsys) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(
        path,
        [make_event("t1", "planned", ["ls"]), make_event("t2", "planned", ["pwd"])],
    )
    main(["--audit-file", str(path), "export", "--task-id", "t2"])
    exported = json.loads(capsys.readouterr().out)
    assert [event["task_id"] for event in exported] == ["t2"]


def test_export_respects_limit(tmp_path: Path, capsys) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path, [make_event(f"t{i}", "planned", ["ls"]) for i in range(5)])
    main(["--audit-file", str(path), "export", "--limit", "2"])
    exported = json.loads(capsys.readouterr().out)
    assert [event["task_id"] for event in exported] == ["t3", "t4"]


def test_export_rejects_invalid_format(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path, [make_event("t1", "planned", ["ls"])])
    with pytest.raises(SystemExit):
        main(["--audit-file", str(path), "export", "--format", "yaml"])
