"""Persisted run reports: recording, accumulation, rendering."""

from free_claude_code.terminal.report import (
    append_run_record,
    load_run_history,
    render_run_summary,
)
from free_claude_code.terminal.run import RunReport, StepReport


def report(status, task_id="t1", exit_code=0):
    return RunReport(
        goal="install widget",
        task_id=task_id,
        status=status,
        steps=(
            StepReport(
                step_id="s1",
                title="copy binary",
                status="ok" if exit_code == 0 else "failed",
                argv=("cp", "a", "b"),
                exit_code=exit_code,
                duration_seconds=0.5,
                reason="" if exit_code == 0 else "command failed",
            ),
        ),
        residual=("s2",) if exit_code else (),
    )


def test_record_roundtrip(tmp_path):
    path = append_run_record(tmp_path, report("ok"))
    history = load_run_history(tmp_path, "t1")
    assert history is not None
    assert history["task_id"] == "t1"
    assert history["attempts"][0]["status"] == "ok"
    assert history["attempts"][0]["steps"][0]["argv"] == ["cp", "a", "b"]
    assert path.name == "t1.json"


def test_attempts_accumulate_under_one_task(tmp_path):
    append_run_record(tmp_path, report("failed", exit_code=1))
    append_run_record(tmp_path, report("ok"))
    history = load_run_history(tmp_path, "t1")
    assert history is not None
    assert [a["status"] for a in history["attempts"]] == ["failed", "ok"]


def test_missing_and_corrupt_history(tmp_path):
    assert load_run_history(tmp_path, "nope") is None
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    assert load_run_history(tmp_path, "bad") is None
    assert load_run_history(tmp_path, "!!!") is None


def test_render_summary_shows_commands_and_exits(tmp_path):
    append_run_record(tmp_path, report("failed", exit_code=1))
    append_run_record(tmp_path, report("ok"))
    history = load_run_history(tmp_path, "t1")
    assert history is not None
    text = render_run_summary(history)
    assert "install widget" in text
    assert "attempt 1: failed" in text
    assert "attempt 2: ok" in text
    assert "cp a b" in text
    assert "exit 1" in text
    assert "residual: s2" in text
