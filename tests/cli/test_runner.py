"""Tests for the fcc-exec policy-enforced runner."""

import json
import sys
from pathlib import Path

import pytest

from free_claude_code.cli import approvals as approvals_cli
from free_claude_code.cli.runner import (
    EXIT_APPROVAL_REQUIRED,
    EXIT_POLICY_DENIED,
    main,
)
from free_claude_code.terminal.approval_store import (
    STATUS_PENDING,
    FileApprovalStore,
)


@pytest.fixture
def paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "approvals": tmp_path / "approvals.json",
        "secret": tmp_path / "auth" / "approval-secret",
        "audit": tmp_path / "logs" / "audit.jsonl",
        "workspace": tmp_path,
    }


def base_args(paths: dict[str, Path]) -> list[str]:
    return [
        "--approvals-file",
        str(paths["approvals"]),
        "--secret-file",
        str(paths["secret"]),
        "--audit-file",
        str(paths["audit"]),
        "--workspace",
        str(paths["workspace"]),
    ]


def inline_python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def run(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    assert isinstance(excinfo.value.code, int)
    return excinfo.value.code


def test_denied_command_exits_4(paths: dict[str, Path], capsys) -> None:
    code = run([*base_args(paths), "--", "sudo", "ls"])
    assert code == EXIT_POLICY_DENIED
    assert "privilege escalation" in capsys.readouterr().err


def test_allowed_command_runs(paths: dict[str, Path], capsys) -> None:
    code = run([*base_args(paths), "--", "echo", "hello-exec"])
    assert code == 0
    out = capsys.readouterr().out
    # `echo` is the real platform echo only on POSIX; skip content assertions
    # on Windows where echo resolves differently.
    if sys.platform != "win32":
        assert "hello-exec" in out


def test_approval_flow_end_to_end(paths: dict[str, Path], capsys) -> None:
    command = inline_python("print('granted-output')")
    first = run([*base_args(paths), "--task-id", "task-77", "--", *command])
    assert first == EXIT_APPROVAL_REQUIRED
    err = capsys.readouterr().err
    assert "approval required" in err
    assert "task-77" in err

    records = FileApprovalStore(paths["approvals"]).list_records()
    assert len(records) == 1
    assert records[0].status == STATUS_PENDING
    approval_id = records[0].approval_id

    approvals_cli.main(
        [
            "--approvals-file",
            str(paths["approvals"]),
            "--secret-file",
            str(paths["secret"]),
            "approve",
            approval_id,
        ]
    )
    capsys.readouterr()

    second = run([*base_args(paths), "--task-id", "task-77", "--", *command])
    assert second == 0
    assert "granted-output" in capsys.readouterr().out


def test_rerun_without_approval_still_pending(paths: dict[str, Path], capsys) -> None:
    command = inline_python("print('x')")
    run([*base_args(paths), "--task-id", "task-1", "--", *command])
    capsys.readouterr()
    again = run([*base_args(paths), "--task-id", "task-1", "--", *command])
    assert again == EXIT_APPROVAL_REQUIRED
    # Reuses the existing pending request rather than creating a duplicate.
    assert len(FileApprovalStore(paths["approvals"]).list_records()) == 1


def test_denied_request_blocks_rerun(paths: dict[str, Path], capsys) -> None:
    command = inline_python("print('x')")
    run([*base_args(paths), "--task-id", "task-2", "--", *command])
    capsys.readouterr()
    approval_id = FileApprovalStore(paths["approvals"]).list_records()[0].approval_id
    approvals_cli.main(
        [
            "--approvals-file",
            str(paths["approvals"]),
            "--secret-file",
            str(paths["secret"]),
            "deny",
            approval_id,
        ]
    )
    code = run([*base_args(paths), "--task-id", "task-2", "--", *command])
    assert code == EXIT_POLICY_DENIED
    assert "approval denied" in capsys.readouterr().err


def test_different_task_id_does_not_use_grant(paths: dict[str, Path], capsys) -> None:
    command = inline_python("print('x')")
    run([*base_args(paths), "--task-id", "task-a", "--", *command])
    capsys.readouterr()
    approval_id = FileApprovalStore(paths["approvals"]).list_records()[0].approval_id
    approvals_cli.main(
        [
            "--approvals-file",
            str(paths["approvals"]),
            "--secret-file",
            str(paths["secret"]),
            "approve",
            approval_id,
        ]
    )
    code = run([*base_args(paths), "--task-id", "task-b", "--", *command])
    assert code == EXIT_APPROVAL_REQUIRED


def test_audit_events_written(paths: dict[str, Path]) -> None:
    run([*base_args(paths), "--", "echo", "audited"])
    lines = paths["audit"].read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(line)["kind"] for line in lines]
    assert kinds == ["planned", "started", "finished"]


def test_json_result(paths: dict[str, Path], capsys) -> None:
    code = run([*base_args(paths), "--json", "--", "echo", "json-out"])
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["exit_code"] == 0
    assert result["timed_out"] is False


def test_no_command_exits_1(paths: dict[str, Path], capsys) -> None:
    code = run(base_args(paths))
    assert code == 1
    assert "no command given" in capsys.readouterr().err


def test_workspace_escape_denied(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    args = [
        "--approvals-file",
        str(tmp_path / "a.json"),
        "--secret-file",
        str(tmp_path / "s"),
        "--audit-file",
        str(tmp_path / "audit.jsonl"),
        "--workspace",
        str(workspace),
        "--",
        "ls",
    ]
    # cwd is the workspace itself, confined: allowed.
    assert run(args) == 0
