"""File-backed store of pending and decided approval requests.

The agent loop records one ``ApprovalRecord`` when policy requires approval;
a human (or an authorized caller) approves or denies it through the CLI; the
runner then reads the decision before executing. Approval attaches the minted
token to the record so the grant stays bound to the exact argv + cwd, task id
and expiry - a stored approval can never authorize a different command.

The store is a single JSON document written atomically (temp file + replace),
so concurrent readers never see a partial document.
"""

import json
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from free_claude_code.terminal.spec import CommandSpec

_VERSION = 1
DEFAULT_REQUEST_TTL_SECONDS = 3600.0

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DENIED = "denied"
STATUS_EXPIRED = "expired"


@dataclass(frozen=True)
class ApprovalRecord:
    """One approval request and its decision state."""

    approval_id: str
    argv: tuple[str, ...]
    cwd: str | None
    task_id: str
    level: str
    reason: str
    policy_id: str
    status: str
    created_at: float
    expires_at: float
    token: str | None = None
    decision_reason: str | None = None

    def spec(self) -> CommandSpec:
        return CommandSpec(argv=self.argv, cwd=self.cwd)

    def as_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "task_id": self.task_id,
            "level": self.level,
            "reason": self.reason,
            "policy_id": self.policy_id,
            "status": self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "token": self.token,
            "decision_reason": self.decision_reason,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ApprovalRecord:
        argv = data.get("argv")
        if not isinstance(argv, list) or not all(
            isinstance(part, str) for part in argv
        ):
            raise ValueError("approval record has an invalid argv")
        for key in ("approval_id", "task_id", "level", "reason", "policy_id"):
            if not isinstance(data.get(key), str):
                raise ValueError(f"approval record is missing {key}")
        status = data.get("status")
        if status not in (
            STATUS_PENDING,
            STATUS_APPROVED,
            STATUS_DENIED,
            STATUS_EXPIRED,
        ):
            raise ValueError(f"approval record has an invalid status: {status!r}")
        created_at = data.get("created_at")
        expires_at = data.get("expires_at")
        if not isinstance(created_at, int | float) or not isinstance(
            expires_at, int | float
        ):
            raise ValueError("approval record has invalid timestamps")
        token = data.get("token")
        decision_reason = data.get("decision_reason")
        cwd = data.get("cwd")
        return ApprovalRecord(
            approval_id=str(data["approval_id"]),
            argv=tuple(argv),
            cwd=str(cwd) if isinstance(cwd, str) else None,
            task_id=str(data["task_id"]),
            level=str(data["level"]),
            reason=str(data["reason"]),
            policy_id=str(data["policy_id"]),
            status=str(status),
            created_at=float(created_at),
            expires_at=float(expires_at),
            token=str(token) if isinstance(token, str) else None,
            decision_reason=(
                str(decision_reason) if isinstance(decision_reason, str) else None
            ),
        )


class FileApprovalStore:
    """Persists approval records in one JSON document at ``path``."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def request(
        self,
        spec: CommandSpec,
        *,
        task_id: str,
        level: str,
        reason: str,
        policy_id: str,
        ttl_seconds: float = DEFAULT_REQUEST_TTL_SECONDS,
        now: float | None = None,
    ) -> ApprovalRecord:
        """Record a new pending approval request and return it."""

        moment = time.time() if now is None else now
        record = ApprovalRecord(
            approval_id=secrets.token_hex(8),
            argv=spec.argv,
            cwd=spec.cwd,
            task_id=task_id,
            level=level,
            reason=reason,
            policy_id=policy_id,
            status=STATUS_PENDING,
            created_at=moment,
            expires_at=moment + ttl_seconds,
        )
        records = self._load()
        records[record.approval_id] = record
        self._write(records)
        return record

    def get(self, approval_id: str, *, now: float | None = None) -> ApprovalRecord:
        records = self._load()
        record = records.get(approval_id)
        if record is None:
            raise KeyError(f"unknown approval id: {approval_id}")
        return self._with_lazy_expiry(record, now)

    def list_records(
        self, *, include_decided: bool = False, now: float | None = None
    ) -> list[ApprovalRecord]:
        records = [
            self._with_lazy_expiry(record, now) for record in self._load().values()
        ]
        records.sort(key=lambda record: record.created_at)
        if include_decided:
            return records
        return [record for record in records if record.status == STATUS_PENDING]

    def decide(
        self,
        approval_id: str,
        *,
        approve: bool,
        token: str | None = None,
        decision_reason: str | None = None,
        now: float | None = None,
    ) -> ApprovalRecord:
        """Approve (with a minted token) or deny a pending request."""

        records = self._load()
        record = records.get(approval_id)
        if record is None:
            raise KeyError(f"unknown approval id: {approval_id}")
        record = self._with_lazy_expiry(record, now)
        if record.status != STATUS_PENDING:
            raise ValueError(f"approval {approval_id} is already {record.status}")
        decided = ApprovalRecord(
            approval_id=record.approval_id,
            argv=record.argv,
            cwd=record.cwd,
            task_id=record.task_id,
            level=record.level,
            reason=record.reason,
            policy_id=record.policy_id,
            status=STATUS_APPROVED if approve else STATUS_DENIED,
            created_at=record.created_at,
            expires_at=record.expires_at,
            token=token,
            decision_reason=decision_reason,
        )
        if approve and token is None:
            raise ValueError("approving requires a minted approval token")
        records[approval_id] = decided
        self._write(records)
        return decided

    def _with_lazy_expiry(
        self, record: ApprovalRecord, now: float | None
    ) -> ApprovalRecord:
        moment = time.time() if now is None else now
        if record.status == STATUS_PENDING and moment >= record.expires_at:
            return ApprovalRecord(
                approval_id=record.approval_id,
                argv=record.argv,
                cwd=record.cwd,
                task_id=record.task_id,
                level=record.level,
                reason=record.reason,
                policy_id=record.policy_id,
                status=STATUS_EXPIRED,
                created_at=record.created_at,
                expires_at=record.expires_at,
                token=record.token,
                decision_reason=record.decision_reason,
            )
        return record

    def _load(self) -> dict[str, ApprovalRecord]:
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            return {}
        if not isinstance(document, dict):
            return {}
        raw_records = document.get("records")
        if not isinstance(raw_records, dict):
            return {}
        records: dict[str, ApprovalRecord] = {}
        for key, value in raw_records.items():
            if not isinstance(value, dict):
                continue
            try:
                record = ApprovalRecord.from_dict(value)
            except ValueError:
                continue
            records[str(key)] = record
        return records

    def _write(self, records: dict[str, ApprovalRecord]) -> None:
        document = {
            "version": _VERSION,
            "records": {key: record.as_dict() for key, record in records.items()},
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=self._path.name + ".", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(document, output, ensure_ascii=True, indent=2)
                output.write("\n")
            os.replace(temp_name, self._path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise


__all__ = [
    "DEFAULT_REQUEST_TTL_SECONDS",
    "STATUS_APPROVED",
    "STATUS_DENIED",
    "STATUS_EXPIRED",
    "STATUS_PENDING",
    "ApprovalRecord",
    "FileApprovalStore",
]
