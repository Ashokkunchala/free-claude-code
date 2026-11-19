"""Persisted run reports: the Report stage of the agent execution loop.

Every engine run is recorded as one attempt under its task id, so repair
rounds accumulate into a single history: what commands ran, their exit
codes and durations, verification results, and how each attempt ended.
Records are metadata only (no command output), mirroring the audit log's
shareable-by-design rule.
"""

import json
import time
from pathlib import Path
from typing import Any

from free_claude_code.terminal.run import RunReport


def _record_path(runs_dir: Path, task_id: str) -> Path:
    safe = "".join(c for c in task_id if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError("task id has no usable characters")
    return runs_dir / f"{safe}.json"


def append_run_record(runs_dir: Path, report: RunReport) -> Path:
    """Append one attempt to the task's history file; return its path."""

    runs_dir.mkdir(parents=True, exist_ok=True)
    path = _record_path(runs_dir, report.task_id)
    history: dict[str, Any]
    if path.exists():
        try:
            history = json.loads(path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            history = {}
    else:
        history = {}
    if not isinstance(history.get("attempts"), list):
        history = {"task_id": report.task_id, "goal": report.goal, "attempts": []}
    attempt = report.as_dict()
    attempt["recorded_at"] = round(time.time(), 3)
    history["attempts"].append(attempt)
    history["goal"] = report.goal
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(history, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return path


def load_run_history(runs_dir: Path, task_id: str) -> dict[str, Any] | None:
    """Return the stored history for a task id, or None when absent."""

    try:
        path = _record_path(runs_dir, task_id)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        history = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if not isinstance(history, dict) or not isinstance(history.get("attempts"), list):
        return None
    return history


def render_run_summary(history: dict[str, Any]) -> str:
    """Human-readable summary of a stored run history."""

    lines = [f"task {history.get('task_id', '?')}: {history.get('goal', '')}"]
    attempts = history.get("attempts", [])
    for index, attempt in enumerate(attempts, start=1):
        label = f"attempt {index}" if len(attempts) > 1 else "attempt"
        lines.append(f"{label}: {attempt.get('status', '?')}")
        for step in attempt.get("steps", []):
            parts = [f"  [{step.get('status', '?')}] {step.get('id', '?')}"]
            argv = step.get("argv")
            if argv:
                parts.append(f"cmd: {' '.join(str(a) for a in argv)}")
            if step.get("exit_code") is not None:
                parts.append(f"exit {step['exit_code']}")
            if step.get("verify_exit_code") is not None:
                parts.append(f"verify exit {step['verify_exit_code']}")
            if step.get("duration_seconds"):
                parts.append(f"{step['duration_seconds']}s")
            if step.get("reason"):
                parts.append(f"- {step['reason']}")
            lines.append(" ".join(parts))
        residual = attempt.get("residual") or []
        if residual:
            lines.append(f"  residual: {', '.join(str(r) for r in residual)}")
    return "\n".join(lines)
