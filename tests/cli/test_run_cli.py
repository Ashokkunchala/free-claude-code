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
