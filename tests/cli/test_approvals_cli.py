"""Tests for the fcc-approvals CLI."""

import json
import os
import stat
from pathlib import Path

import pytest

from free_claude_code.cli.approvals import load_or_create_secret, main
from free_claude_code.terminal import CommandSpec
from free_claude_code.terminal.approval import ApprovalTokenIssuer
from free_claude_code.terminal.approval_store import (
    STATUS_APPROVED,
    STATUS_DENIED,
    FileApprovalStore,
)


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "approvals.json"


@pytest.fixture
def secret_path(tmp_path: Path) -> Path:
    return tmp_path / "auth" / "approval-secret"


def cli_args(store_path: Path, secret_path: Path, *args: str) -> list[str]:
    return [
        "--approvals-file",
        str(store_path),
        "--secret-file",
        str(secret_path),
        *args,
    ]


def seed_request(store_path: Path, *argv: str) -> str:
    store = FileApprovalStore(store_path)
    record = store.request(
        CommandSpec(argv=tuple(argv)),
        task_id="task-9",
        level="R2_PRIVILEGED",
        reason="git:push requires approval",
        policy_id="policy:git:push",
    )
    return record.approval_id


def test_secret_created_private(secret_path: Path) -> None:
    secret = load_or_create_secret(secret_path)
    assert len(secret) == 32
    assert load_or_create_secret(secret_path) == secret
    if os.name != "nt":
        mode = stat.S_IMODE(secret_path.stat().st_mode)
        assert mode == 0o600


def test_list_empty(store_path: Path, secret_path: Path, capsys) -> None:
    main(cli_args(store_path, secret_path, "list"))
    assert "no pending approvals" in capsys.readouterr().out


def test_approve_then_token_verifies(
    store_path: Path, secret_path: Path, capsys
) -> None:
    approval_id = seed_request(store_path, "git", "push")
    main(cli_args(store_path, secret_path, "approve", approval_id))
    assert "approved" in capsys.readouterr().out

    record = FileApprovalStore(store_path).get(approval_id)
    assert record.status == STATUS_APPROVED
    assert record.token is not None
    issuer = ApprovalTokenIssuer(load_or_create_secret(secret_path))
    assert issuer.verify(record.token, record.spec(), "task-9").ok


def test_deny_records_decision(store_path: Path, secret_path: Path, capsys) -> None:
    approval_id = seed_request(store_path, "git", "push")
    main(cli_args(store_path, secret_path, "deny", approval_id, "--reason", "no"))
    assert "denied" in capsys.readouterr().out
    record = FileApprovalStore(store_path).get(approval_id)
    assert record.status == STATUS_DENIED
    assert record.decision_reason == "no"


def test_approve_twice_fails(store_path: Path, secret_path: Path) -> None:
    approval_id = seed_request(store_path, "git", "push")
    main(cli_args(store_path, secret_path, "approve", approval_id))
    with pytest.raises(SystemExit):
        main(cli_args(store_path, secret_path, "approve", approval_id))


def test_unknown_id_fails(store_path: Path, secret_path: Path, capsys) -> None:
    with pytest.raises(SystemExit):
        main(cli_args(store_path, secret_path, "show", "nope"))
    assert "unknown approval id" in capsys.readouterr().err


def test_list_json(store_path: Path, secret_path: Path, capsys) -> None:
    seed_request(store_path, "git", "push")
    main(cli_args(store_path, secret_path, "list", "--json"))
    records = json.loads(capsys.readouterr().out)
    assert len(records) == 1
    assert records[0]["argv"] == ["git", "push"]
    assert records[0]["status"] == "pending"


def test_show_json(store_path: Path, secret_path: Path, capsys) -> None:
    approval_id = seed_request(store_path, "git", "push")
    main(cli_args(store_path, secret_path, "show", approval_id, "--json"))
    record = json.loads(capsys.readouterr().out)
    assert record["approval_id"] == approval_id
    assert record["task_id"] == "task-9"
