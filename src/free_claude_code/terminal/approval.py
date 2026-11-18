"""Approval tokens bind one exact command to one task with an expiry.

A token is ``base64url(payload).base64url(hmac-sha256(payload))``. The
payload records the task id, a fingerprint of the exact argv + cwd, and an
expiry, so an approval for one command never authorizes a different command,
a different task, or a later time. Verification is constant-time on the
signature and strict on every binding field.
"""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from free_claude_code.terminal.spec import CommandSpec

_MIN_SECRET_BYTES = 16
_DEFAULT_TTL_SECONDS = 300.0


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def command_fingerprint(spec: CommandSpec) -> str:
    """Stable digest of the exact command a token authorizes."""

    canonical = json.dumps([list(spec.argv), spec.cwd], separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApprovalCheck:
    """Result of verifying a token against a command and task."""

    ok: bool
    reason: str | None = None


class ApprovalTokenIssuer:
    """Mints and verifies approval tokens with one shared secret."""

    def __init__(self, secret: bytes) -> None:
        if len(secret) < _MIN_SECRET_BYTES:
            raise ValueError(
                f"approval secret must be at least {_MIN_SECRET_BYTES} bytes"
            )
        self._secret = secret

    def mint(
        self,
        spec: CommandSpec,
        task_id: str,
        *,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        note: str = "",
    ) -> str:
        payload = {
            "cmd": command_fingerprint(spec),
            "exp": time.time() + ttl_seconds,
            "note": note,
            "task": task_id,
            "v": 1,
        }
        body = _b64encode(json.dumps(payload, sort_keys=True).encode("utf-8"))
        signature = hmac.new(
            self._secret, body.encode("ascii"), hashlib.sha256
        ).digest()
        return f"{body}.{_b64encode(signature)}"

    def verify(
        self,
        token: str,
        spec: CommandSpec,
        task_id: str,
        *,
        now: float | None = None,
    ) -> ApprovalCheck:
        parts = token.split(".")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return ApprovalCheck(ok=False, reason="malformed token")
        body, signature = parts
        expected = hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest()
        try:
            given = _b64decode(signature)
        except ValueError:
            return ApprovalCheck(ok=False, reason="malformed signature")
        if not hmac.compare_digest(expected, given):
            return ApprovalCheck(ok=False, reason="signature mismatch")
        try:
            payload = json.loads(_b64decode(body))
        except ValueError, UnicodeDecodeError:
            return ApprovalCheck(ok=False, reason="malformed payload")
        if not isinstance(payload, dict):
            return ApprovalCheck(ok=False, reason="malformed payload")
        moment = time.time() if now is None else now
        expiry = payload.get("exp")
        if not isinstance(expiry, int | float) or moment >= float(expiry):
            return ApprovalCheck(ok=False, reason="token expired")
        if payload.get("task") != task_id:
            return ApprovalCheck(ok=False, reason="token bound to another task")
        if payload.get("cmd") != command_fingerprint(spec):
            return ApprovalCheck(ok=False, reason="token not bound to this command")
        return ApprovalCheck(ok=True)
