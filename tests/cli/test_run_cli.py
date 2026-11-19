"""fcc-run CLI: plan loading, dry-run, execution, approval resume."""

import json

import pytest

from free_claude_code.cli.run import main
from tests.terminal.conftest import py


@pytest.fixture
def plan_file(tmp_path):
    def write(steps, name="plan.json"):
        path = tmp_path / name
        path.write_text(json.dumps({"goal": "demo", "steps": steps}), encoding="utf-8")
        return path

    return write


def invoke(args):
    try:
        main(args)
    except SystemExit as exc:
        return exc.code
    return 0


def base_args(tmp_path, plan_path):
    return [
        str(plan_path),
        "--workspace",
        str(tmp_path),
        "--approvals-file",
        str(tmp_path / "approvals.json"),
        "--secret-file",
        str(tmp_path / "secret"),
        "--audit-file",
        str(tmp_path / "audit.jsonl"),
        "--runs-dir",
        str(tmp_path / "runs"),
    ]


def test_bad_plan_exits_two(capsys, tmp_path, plan_file):
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    assert invoke(base_args(tmp_path, path)) == 2
    path2 = plan_file([], "empty.json")
    assert invoke(base_args(tmp_path, path2)) == 2
    assert "non-empty array" in capsys.readouterr().err


def test_dry_run_policy_checks_without_executing(capsys, tmp_path, plan_file):
    marker = tmp_path / "marker.txt"
    path = plan_file(
        [
            {"id": "s1", "argv": list(py(f"open({str(marker)!r},'w').write('x')"))},
            {"id": "s2", "argv": ["git", "push"]},
        ]
    )
    assert invoke([*base_args(tmp_path, path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert (
        "[ALLOW] s1" in out or "[APPROVE" in out
    )  # python -c is R3 -> APPROVE_DETAILED
    assert "s2" in out
    assert not marker.exists()


def test_full_run_ok_json(capsys, tmp_path, plan_file):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    path = plan_file(
        [{"id": "s1", "argv": ["git", "status"], "verify": {"argv": ["git", "status"]}}]
    )
    assert invoke([*base_args(tmp_path, path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "ok"
    assert data["steps"][0]["id"] == "s1"
    assert data["steps"][0]["verify_exit_code"] == 0


def test_approval_resume_flow(capsys, tmp_path, plan_file):
    path = plan_file([{"id": "push", "argv": ["git", "push"]}])
    code = invoke([*base_args(tmp_path, path), "--task-id", "t1"])
    assert code == 3
    assert "--task-id t1" in capsys.readouterr().err
    report_out = invoke([*base_args(tmp_path, path), "--task-id", "t1", "--json"])
    assert report_out == 3  # still pending
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "approval_required"


def test_print_prompt(capsys, tmp_path):
    code = invoke(["--workspace", str(tmp_path), "--print-prompt", "run tests"])
    assert code == 0
    out = capsys.readouterr().out
    assert "run tests" in out and '"argv"' in out


def test_from_response_validates(capsys, tmp_path):
    response = tmp_path / "response.txt"
    response.write_text(
        '{"goal": "g", "steps": [{"id": "a", "argv": ["git", "status"]}]}',
        encoding="utf-8",
    )
    code = invoke(
        [
            "--workspace",
            str(tmp_path),
            "--from-response",
            "check repo",
            "--response",
            str(response),
        ]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["steps"][0]["argv"] == ["git", "status"]


def test_from_response_rejects_bad_output(capsys, tmp_path):
    response = tmp_path / "response.txt"
    response.write_text("sorry, cannot help", encoding="utf-8")
    code = invoke(
        [
            "--workspace",
            str(tmp_path),
            "--from-response",
            "x",
            "--response",
            str(response),
        ]
    )
    assert code == 2
    assert "invalid model response" in capsys.readouterr().err


def test_task_generates_and_runs_plan(capsys, tmp_path, monkeypatch):
    import subprocess

    from free_claude_code.cli import run as run_cli

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    monkeypatch.setattr(run_cli, "plan_model_from_env", lambda: object())

    def fake_propose(task, model, *, workspace, context, attempts):
        assert task == "say hi"
        assert context is not None and "Platform:" in context
        return run_cli.RunPlan.from_dict(
            {
                "goal": "say hi",
                "steps": [{"id": "s1", "title": "echo", "argv": ["git", "status"]}],
            }
        )

    monkeypatch.setattr(run_cli, "propose_plan_with_feedback", fake_propose)
    code = invoke(["--task", "say hi", *base_args(tmp_path, tmp_path / "x")[1:]])
    assert code == 0
    assert "status: ok" in capsys.readouterr().out


def test_task_without_model_config_exits_one(capsys, tmp_path, monkeypatch):
    from free_claude_code.cli import run as run_cli

    def boom():
        raise run_cli.PlanModelError(
            "plan model is not configured; set FCC_PLAN_BASE_URL"
        )

    monkeypatch.setattr(run_cli, "plan_model_from_env", boom)
    code = invoke(["--task", "anything", *base_args(tmp_path, tmp_path / "x")[1:]])
    assert code == 1
    assert "not configured" in capsys.readouterr().err


def test_task_rejects_junk_model_response(capsys, tmp_path, monkeypatch):
    from free_claude_code.cli import run as run_cli

    monkeypatch.setattr(run_cli, "plan_model_from_env", lambda: object())

    def junk(task, model, *, workspace, context, attempts):
        raise run_cli.PlanError("model response is not valid JSON")

    monkeypatch.setattr(run_cli, "propose_plan_with_feedback", junk)
    code = invoke(["--task", "anything", *base_args(tmp_path, tmp_path / "x")[1:]])
    assert code == 2
    assert "did not produce a valid plan" in capsys.readouterr().err


def test_task_and_plan_file_are_exclusive(capsys, tmp_path, plan_file):
    path = plan_file([{"id": "s1", "title": "x", "argv": py("pass")}])
    code = invoke([*base_args(tmp_path, path), "--task", "both"])
    assert code == 1
    assert "not both" in capsys.readouterr().err


def test_task_no_context_passes_none(capsys, tmp_path, monkeypatch):
    import subprocess

    from free_claude_code.cli import run as run_cli

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.setattr(run_cli, "plan_model_from_env", lambda: object())

    def fake_propose(task, model, *, workspace, context, attempts):
        assert context is None
        return run_cli.RunPlan.from_dict(
            {
                "goal": "g",
                "steps": [{"id": "s1", "title": "t", "argv": ["git", "status"]}],
            }
        )

    monkeypatch.setattr(run_cli, "propose_plan_with_feedback", fake_propose)
    code = invoke(
        ["--task", "g", "--no-context", *base_args(tmp_path, tmp_path / "x")[1:]]
    )
    assert code == 0


def test_task_repair_loop_recovers(capsys, tmp_path, monkeypatch):
    import subprocess

    from free_claude_code.cli import run as run_cli

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.setattr(run_cli, "plan_model_from_env", lambda: object())

    def fake_propose(task, model, *, workspace, context, attempts):
        return run_cli.RunPlan.from_dict(
            {
                "goal": "g",
                "steps": [{"id": "s1", "title": "bad", "argv": ["false"]}],
            }
        )

    def fake_repair(task, report, model, *, workspace, context, attempts):
        assert report.status == "failed"
        return run_cli.RunPlan.from_dict(
            {
                "goal": "g",
                "steps": [{"id": "r1", "title": "ok", "argv": ["git", "status"]}],
            }
        )

    monkeypatch.setattr(run_cli, "propose_plan_with_feedback", fake_propose)
    monkeypatch.setattr(run_cli, "propose_repair", fake_repair)
    code = invoke(
        [
            "--task",
            "g",
            "--repair-attempts",
            "1",
            *base_args(tmp_path, tmp_path / "x")[1:],
        ]
    )
    assert code == 0
    assert "status: ok" in capsys.readouterr().out


def test_task_without_repair_attempts_stays_failed(capsys, tmp_path, monkeypatch):
    from free_claude_code.cli import run as run_cli

    monkeypatch.setattr(run_cli, "plan_model_from_env", lambda: object())

    def fake_propose(task, model, *, workspace, context, attempts):
        return run_cli.RunPlan.from_dict(
            {"goal": "g", "steps": [{"id": "s1", "title": "bad", "argv": ["false"]}]}
        )

    monkeypatch.setattr(run_cli, "propose_plan_with_feedback", fake_propose)
    code = invoke(["--task", "g", *base_args(tmp_path, tmp_path / "x")[1:]])
    assert code == 1
    assert "status: failed" in capsys.readouterr().out


def test_run_records_history_with_commands(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "goal": "demo",
                "steps": [{"id": "s1", "title": "ok", "argv": ["git", "status"]}],
            }
        ),
        encoding="utf-8",
    )
    code = invoke(base_args(tmp_path, plan))
    assert code == 0
    records = list((tmp_path / "runs").glob("*.json"))
    assert len(records) == 1
    history = json.loads(records[0].read_text(encoding="utf-8"))
    assert history["goal"] == "demo"
    assert len(history["attempts"]) == 1
    assert history["attempts"][0]["steps"][0]["argv"] == ["git", "status"]
    assert history["attempts"][0]["steps"][0]["exit_code"] == 0


def test_repair_loop_records_each_attempt(tmp_path, monkeypatch):
    import subprocess

    from free_claude_code.cli import run as run_cli

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    monkeypatch.setattr(run_cli, "plan_model_from_env", lambda: object())

    def fake_propose(task, model, *, workspace, context, attempts):
        return run_cli.RunPlan.from_dict(
            {"goal": "g", "steps": [{"id": "s1", "title": "bad", "argv": ["false"]}]}
        )

    def fake_repair(task, report, model, *, workspace, context, attempts):
        return run_cli.RunPlan.from_dict(
            {
                "goal": "g",
                "steps": [{"id": "r1", "title": "ok", "argv": ["git", "status"]}],
            }
        )

    monkeypatch.setattr(run_cli, "propose_plan_with_feedback", fake_propose)
    monkeypatch.setattr(run_cli, "propose_repair", fake_repair)
    code = invoke(
        [
            "--task",
            "g",
            "--repair-attempts",
            "1",
            "--task-id",
            "repair-history-task",
            *base_args(tmp_path, tmp_path / "x")[1:],
        ]
    )
    assert code == 0
    history = json.loads(
        (tmp_path / "runs" / "repair-history-task.json").read_text(encoding="utf-8")
    )
    assert len(history["attempts"]) == 2
    assert history["attempts"][0]["status"] == "failed"
    assert history["attempts"][1]["status"] == "ok"


def test_history_prints_recorded_run(capsys, tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "goal": "demo",
                "steps": [{"id": "s1", "title": "ok", "argv": ["git", "status"]}],
            }
        ),
        encoding="utf-8",
    )
    args = base_args(tmp_path, plan)
    assert invoke([*args, "--task-id", "hist-task"]) == 0
    capsys.readouterr()
    code = invoke(["--history", "hist-task", "--runs-dir", str(tmp_path / "runs")])
    assert code == 0
    out = capsys.readouterr().out
    assert "hist-task" in out
    assert "git status" in out


def test_history_unknown_task_fails(capsys, tmp_path):
    code = invoke(["--history", "nope", "--runs-dir", str(tmp_path / "runs")])
    assert code == 1
