"""Terminal execution core: structured, auditable, cancellable commands."""

from free_claude_code.terminal.audit import (
    AuditEvent,
    AuditSink,
    InMemoryAuditSink,
    JsonlAuditSink,
    NullAuditSink,
)
from free_claude_code.terminal.errors import (
    PolicyRejectedError,
    SpawnError,
    TerminalExecutorError,
)
from free_claude_code.terminal.executor import TerminalExecutor
from free_claude_code.terminal.redaction import Redactor
from free_claude_code.terminal.result import CommandResult, OutputChunk
from free_claude_code.terminal.spec import (
    DEFAULT_POLICY_DECISION,
    CommandSpec,
    PolicyDecision,
)

__all__ = [
    "DEFAULT_POLICY_DECISION",
    "AuditEvent",
    "AuditSink",
    "CommandResult",
    "CommandSpec",
    "InMemoryAuditSink",
    "JsonlAuditSink",
    "NullAuditSink",
    "OutputChunk",
    "PolicyDecision",
    "PolicyRejectedError",
    "Redactor",
    "SpawnError",
    "TerminalExecutor",
    "TerminalExecutorError",
]
