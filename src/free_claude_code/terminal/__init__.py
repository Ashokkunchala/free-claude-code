"""Terminal execution core: structured, auditable, cancellable commands."""

from free_claude_code.terminal.approval import (
    ApprovalCheck,
    ApprovalTokenIssuer,
    command_fingerprint,
)
from free_claude_code.terminal.approval_store import (
    ApprovalRecord,
    FileApprovalStore,
)
from free_claude_code.terminal.audit import (
    AuditEvent,
    AuditSink,
    InMemoryAuditSink,
    JsonlAuditSink,
    NullAuditSink,
)
from free_claude_code.terminal.env_context import (
    EnvContext,
    collect_env_context,
    render_env_context,
)
from free_claude_code.terminal.errors import (
    PolicyRejectedError,
    SpawnError,
    TerminalExecutorError,
)
from free_claude_code.terminal.executor import TerminalExecutor
from free_claude_code.terminal.plan_gen import (
    build_plan_prompt,
    build_repair_prompt,
    extract_plan_json,
    plan_from_response,
    propose_plan,
)
from free_claude_code.terminal.plan_model import (
    ChatCompletionsModel,
    FallbackPlanModel,
    PlanModelError,
    PlanModelRateLimitError,
    plan_model_from_env,
    propose_plan_with_feedback,
    propose_repair,
)
from free_claude_code.terminal.policy import (
    PolicyConfig,
    PolicyEngine,
    PolicyVerdict,
    Requirement,
    RiskLevel,
)
from free_claude_code.terminal.redaction import Redactor
from free_claude_code.terminal.report import (
    append_run_record,
    load_run_history,
    render_run_summary,
)
from free_claude_code.terminal.result import CommandResult, OutputChunk
from free_claude_code.terminal.run import (
    PlanError,
    RunEngine,
    RunPlan,
    RunReport,
    RunStep,
    StepReport,
    VerifySpec,
)
from free_claude_code.terminal.scheduler import (
    CommandScheduler,
    QueueFullError,
    SchedulerConfig,
    SchedulerStats,
)
from free_claude_code.terminal.spec import (
    DEFAULT_POLICY_DECISION,
    CommandSpec,
    PolicyDecision,
)
from free_claude_code.terminal.system_inspect import (
    PortListener,
    ProcessInfo,
    ServiceInfo,
    SystemInspectError,
    collect_error_logs,
    collect_listeners,
    collect_processes,
    collect_services,
    render_listeners,
    render_processes,
    render_services,
)
from free_claude_code.terminal.toolchain import (
    ToolInfo,
    ToolSpec,
    collect_toolchain,
    render_toolchain,
)

__all__ = [
    "DEFAULT_POLICY_DECISION",
    "ApprovalCheck",
    "ApprovalRecord",
    "ApprovalTokenIssuer",
    "AuditEvent",
    "AuditSink",
    "ChatCompletionsModel",
    "CommandResult",
    "CommandScheduler",
    "CommandSpec",
    "EnvContext",
    "FallbackPlanModel",
    "FileApprovalStore",
    "InMemoryAuditSink",
    "JsonlAuditSink",
    "NullAuditSink",
    "OutputChunk",
    "PlanError",
    "PlanModelError",
    "PlanModelRateLimitError",
    "PolicyConfig",
    "PolicyDecision",
    "PolicyEngine",
    "PolicyRejectedError",
    "PolicyVerdict",
    "PortListener",
    "ProcessInfo",
    "QueueFullError",
    "Redactor",
    "Requirement",
    "RiskLevel",
    "RunEngine",
    "RunPlan",
    "RunReport",
    "RunStep",
    "SchedulerConfig",
    "SchedulerStats",
    "ServiceInfo",
    "SpawnError",
    "StepReport",
    "SystemInspectError",
    "TerminalExecutor",
    "TerminalExecutorError",
    "ToolInfo",
    "ToolSpec",
    "VerifySpec",
    "append_run_record",
    "build_plan_prompt",
    "build_repair_prompt",
    "collect_env_context",
    "collect_error_logs",
    "collect_listeners",
    "collect_processes",
    "collect_services",
    "collect_toolchain",
    "command_fingerprint",
    "extract_plan_json",
    "load_run_history",
    "plan_from_response",
    "plan_model_from_env",
    "propose_plan",
    "propose_plan_with_feedback",
    "propose_repair",
    "render_env_context",
    "render_listeners",
    "render_processes",
    "render_run_summary",
    "render_services",
    "render_toolchain",
]
