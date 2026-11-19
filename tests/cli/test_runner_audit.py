"""Approval and failure audit events in the fcc-exec runner."""

import json
import sys
from pathlib import Path

import pytest

from free_claude_code.cli import approvals as approvals_cli
from free_claude_code.cli.runner import (
    EXIT_POLICY_DENIED,
    main,
)
from free_claude_code.terminal.approval_store import FileApprovalStore


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


def kinds(paths: dict[str, Path]) -> list[str]:
    lines = paths["audit"].read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["kind"] for line in lines]


def test_approval_request_and_grant_are_audited(paths, capsys) -> None:
    command = inline_python("print('granted')")
    run([*base_args(paths), "--task-id", "t-1", "--", *command])
    capsys.readouterr()
    assert kinds(paths) == ["approval_requested"]

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
    capsys.readouterr()

    code = run([*base_args(paths), "--task-id", "t-1", "--", *command])
    assert code == 0
    capsys.readouterr()
    assert kinds(paths) == [
        "approval_requested",
        "approved",
        "planned",
        "started",
        "finished",
    ]


def test_denied_rerun_is_audited(paths, capsys) -> None:
    command = inline_python("print('nope')")
    run([*base_args(paths), "--task-id", "t-2", "--", *command])
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
    capsys.readouterr()

    code = run([*base_args(paths), "--task-id", "t-2", "--", *command])
    assert code == EXIT_POLICY_DENIED
    assert kinds(paths) == ["approval_requested", "denied"]


def test_pending_rerun_does_not_duplicate_request_event(paths, capsys) -> None:
    command = inline_python("print('x')")
    run([*base_args(paths), "--task-id", "t-3", "--", *command])
    run([*base_args(paths), "--task-id", "t-3", "--", *command])
    capsys.readouterr()
    assert kinds(paths) == ["approval_requested"]


def test_approved_command_with_nonzero_exit_records_failed(paths, capsys) -> None:
    command = inline_python("raise SystemExit(2)")
    run([*base_args(paths), "--task-id", "t-4", "--", *command])
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
    capsys.readouterr()

    code = run([*base_args(paths), "--task-id", "t-4", "--", *command])
    assert code == 2
    capsys.readouterr()
    assert kinds(paths) == [
        "approval_requested",
        "approved",
        "planned",
        "started",
        "failed",
    ]
