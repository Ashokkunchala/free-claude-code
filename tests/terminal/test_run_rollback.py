"""RunEngine recovery: authorized rollback of completed steps on failure."""

import subprocess
import sys
from pathlib import Path

import pytest

from free_claude_code.terminal import (
    InMemoryAuditSink,
    PlanError,
    RunEngine,
    RunPlan,
)
from free_claude_code.terminal.policy import PolicyConfig, PolicyEngine
from tests.terminal.conftest import py

# Policy basenames lowercase and strip ".exe"; match that normalization.
PY_NAME = Path(sys.executable).name.lower().removesuffix(".exe")


def plan(steps):
    return RunPlan.from_dict({"goal": "g", "steps": steps})


def engine(tmp_path, sink=None, allow_python=True):
    policy = PolicyEngine(
        PolicyConfig(
            workspace_root=str(tmp_path),
            extra_allow=frozenset({PY_NAME}) if allow_python else frozenset(),
        )
    )
    return RunEngine(
        workspace=str(tmp_path),
        sink=sink if sink is not None else InMemoryAuditSink(),
        engine=policy,
    )


def write_step(step_id, path, content):
    return {
        "id": step_id,
        "argv": list(py(f"open({str(path)!r},'w').write({content!r})")),
        "rollback": {
            "argv": list(
                py(
                    f"import os; os.path.exists({str(path)!r})"
                    f" and os.remove({str(path)!r})"
                )
            )
        },
    }


def order_step(step_id, order_file, content_file):
    return {
        "id": step_id,
        "argv": list(py(f"open({str(content_file)!r},'w').write('x')")),
        "rollback": {
            "argv": list(py(f"open({str(order_file)!r},'a').write({step_id!r} + ';')"))
        },
    }


def test_rollback_runs_newest_first_on_failure(tmp_path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    order = tmp_path / "order.txt"
    report = engine(tmp_path).run(
        plan(
            [
                order_step("s1", order, first),
                order_step("s2", order, second),
                {"id": "bad", "argv": list(py("raise SystemExit(3)"))},
            ]
        ),
        task_id="t1",
    )
    assert report.status == "failed"
    assert order.read_text() == "s2;s1;"
    by_id = {s.step_id: s for s in report.steps}
    assert by_id["s1"].rollback_status == "ok"
    assert by_id["s2"].rollback_status == "ok"
    assert by_id["bad"].rollback_status == ""


def test_rollback_removes_completed_work(tmp_path) -> None:
    marker = tmp_path / "marker.txt"
    report = engine(tmp_path).run(
        plan(
            [
                write_step("s1", marker, "x"),
                {"id": "bad", "argv": list(py("raise SystemExit(3)"))},
            ]
        ),
        task_id="t1",
    )
    assert report.status == "failed"
    assert not marker.exists()


def test_rollback_not_run_on_success(tmp_path) -> None:
    marker = tmp_path / "marker.txt"
    report = engine(tmp_path).run(
        plan([write_step("s1", marker, "x")]),
        task_id="t1",
    )
    assert report.status == "ok"
    assert marker.read_text() == "x"
    assert report.steps[0].rollback_status == ""


def test_verify_failure_rolls_back_the_step_itself(tmp_path) -> None:
    marker = tmp_path / "marker.txt"
    report = engine(tmp_path).run(
        plan(
            [
                {
                    "id": "s1",
                    "argv": list(py(f"open({str(marker)!r},'w').write('x')")),
                    "verify": {"argv": list(py("raise SystemExit(1)"))},
                    "rollback": {
                        "argv": list(py(f"import os; os.remove({str(marker)!r})"))
                    },
                }
            ]
        ),
        task_id="t1",
    )
    assert report.status == "verify_failed"
    assert not marker.exists()
    assert report.steps[0].status == "verify_failed"
    assert report.steps[0].rollback_status == "ok"


def test_no_rollback_when_policy_gates_it(tmp_path) -> None:
    # Python is R2 without extra_allow, so the rollback is skipped, not run.
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    marker = tmp_path / "marker.txt"
    report = engine(tmp_path, allow_python=False).run(
        plan(
            [
                {
                    "id": "s1",
                    "argv": ["git", "status"],
                    "rollback": {
                        "argv": list(py(f"open({str(marker)!r},'w').write('x')"))
                    },
                },
                {"id": "bad", "argv": ["git", "log"]},
            ]
        ),
        task_id="t1",
    )
    assert report.status == "failed"
    assert not marker.exists()
    step = report.steps[0]
    assert step.rollback_status == "skipped"
    assert "rollback requires" in step.rollback_reason


def test_denied_stop_does_not_roll_back(tmp_path) -> None:
    marker = tmp_path / "marker.txt"
    report = engine(tmp_path).run(
        plan(
            [
                write_step("s1", marker, "x"),
                {"id": "elevated", "argv": ["sudo", "true"]},
            ]
        ),
        task_id="t1",
    )
    assert report.status == "denied"
    assert marker.read_text() == "x"
    assert report.steps[0].rollback_status == ""


def test_failing_rollback_is_reported_not_fatal(tmp_path) -> None:
    report = engine(tmp_path).run(
        plan(
            [
                {
                    "id": "s1",
                    "argv": list(py("pass")),
                    "rollback": {"argv": list(py("raise SystemExit(7)"))},
                },
                {"id": "bad", "argv": list(py("raise SystemExit(3)"))},
            ]
        ),
        task_id="t1",
    )
    assert report.status == "failed"
    step = report.steps[0]
    assert step.rollback_status == "failed"
    assert step.rollback_exit_code == 7
    assert report.steps[1].exit_code == 3


def test_rollback_spec_validation() -> None:
    with pytest.raises(PlanError, match="rollback must be an object"):
        RunPlan.from_dict(
            {"steps": [{"argv": ["true"], "rollback": ["not", "an", "object"]}]}
        )
    with pytest.raises(PlanError, match=r"rollback\.expect_exit"):
        RunPlan.from_dict(
            {
                "steps": [
                    {
                        "argv": ["true"],
                        "rollback": {"argv": ["true"], "expect_exit": "0"},
                    }
                ]
            }
        )


def test_report_dict_carries_rollback_fields(tmp_path) -> None:
    report = engine(tmp_path).run(
        plan(
            [
                {
                    "id": "s1",
                    "argv": list(py("pass")),
                    "rollback": {"argv": list(py("pass"))},
                },
                {"id": "bad", "argv": list(py("raise SystemExit(3)"))},
            ]
        ),
        task_id="t1",
    )
    data = report.as_dict()
    step = data["steps"][0]
    assert step["rollback_status"] == "ok"
    assert step["rollback_exit_code"] == 0
    assert data["steps"][1]["rollback_status"] == ""
