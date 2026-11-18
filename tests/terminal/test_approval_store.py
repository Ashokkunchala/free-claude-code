"""Approval store tests: request/decide lifecycle and persistence."""

import json
from pathlib import Path

import pytest

from free_claude_code.terminal import CommandSpec
from free_claude_code.terminal.approval_store import (
    STATUS_APPROVED,
    STATUS_DENIED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    FileApprovalStore,
)


def make_store(tmp_path: Path) -> FileApprovalStore:
    return FileApprovalStore(tmp_path / "approvals.json")


def make_spec(*argv: str) -> CommandSpec:
    return CommandSpec(argv=tuple(argv))


def test_request_persists_pending_record(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = store.request(
        make_spec("git", "push"),
        task_id="t1",
        level="R2_PRIVILEGED",
        reason="git:push requires approval",
        policy_id="policy:git:push",
    )
    assert record.status == STATUS_PENDING
    loaded = make_store(tmp_path).get(record.approval_id)
    assert loaded.argv == ("git", "push")
    assert loaded.task_id == "t1"
    assert loaded.policy_id == "policy:git:push"


def test_list_hides_decided_by_default(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = store.request(
        make_spec("ls"), task_id="t", level="R0", reason="r", policy_id="p"
    )
    store.request(make_spec("pwd"), task_id="t", level="R0", reason="r", policy_id="p")
    store.decide(first.approval_id, approve=True, token="tok")
    pending = store.list_records()
    assert len(pending) == 1
    assert len(store.list_records(include_decided=True)) == 2


def test_approve_requires_token(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = store.request(
        make_spec("ls"), task_id="t", level="R0", reason="r", policy_id="p"
    )
    with pytest.raises(ValueError, match="token"):
        store.decide(record.approval_id, approve=True)


def test_approve_stores_token(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = store.request(
        make_spec("ls"), task_id="t", level="R0", reason="r", policy_id="p"
    )
    decided = store.decide(record.approval_id, approve=True, token="tok-1")
    assert decided.status == STATUS_APPROVED
    assert decided.token == "tok-1"
    assert make_store(tmp_path).get(record.approval_id).token == "tok-1"


def test_deny_records_reason(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = store.request(
        make_spec("ls"), task_id="t", level="R0", reason="r", policy_id="p"
    )
    decided = store.decide(
        record.approval_id, approve=False, decision_reason="too risky"
    )
    assert decided.status == STATUS_DENIED
    assert decided.decision_reason == "too risky"


def test_double_decide_rejected(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = store.request(
        make_spec("ls"), task_id="t", level="R0", reason="r", policy_id="p"
    )
    store.decide(record.approval_id, approve=False)
    with pytest.raises(ValueError, match="already denied"):
        store.decide(record.approval_id, approve=True, token="tok")


def test_unknown_id_raises(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(KeyError):
        store.get("nope")
    with pytest.raises(KeyError):
        store.decide("nope", approve=False)


def test_pending_request_expires_lazily(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = store.request(
        make_spec("ls"),
        task_id="t",
        level="R0",
        reason="r",
        policy_id="p",
        ttl_seconds=10.0,
        now=1000.0,
    )
    assert store.get(record.approval_id, now=1005.0).status == STATUS_PENDING
    assert store.get(record.approval_id, now=1011.0).status == STATUS_EXPIRED
    with pytest.raises(ValueError, match="already expired"):
        store.decide(record.approval_id, approve=True, token="t", now=1011.0)


def test_corrupt_store_file_reads_empty(tmp_path: Path) -> None:
    path = tmp_path / "approvals.json"
    path.write_text("{not json", encoding="utf-8")
    store = FileApprovalStore(path)
    assert store.list_records(include_decided=True) == []
    record = store.request(
        make_spec("ls"), task_id="t", level="R0", reason="r", policy_id="p"
    )
    assert store.get(record.approval_id).status == STATUS_PENDING


def test_store_document_shape(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.request(make_spec("ls"), task_id="t", level="R0", reason="r", policy_id="p")
    document = json.loads((tmp_path / "approvals.json").read_text(encoding="utf-8"))
    assert document["version"] == 1
    assert len(document["records"]) == 1
