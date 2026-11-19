"""Plan-driven run engine: the model-agnostic core of ``fcc run``.

Design choice (roadmap section 6): the execution loop is plan-driven and
model-agnostic. A run is a ``RunPlan`` - an ordered list of steps, each a
command plus an optional verification command - and the engine walks it
through the roadmap lifecycle: policy-check every step, require a bound
approval token for R2/R3 commands, execute through the terminal executor,
verify postconditions, stop safely on the first failure, and report what
ran, what was decided and what remains. Nothing here talks to a model:
plans arrive as data (today: a JSON file via ``fcc-run``; a later slice
adds model-generated plans against this same schema). This keeps the
safety semantics - policy, approvals, verification, audit - deterministic
and testable before any model is asked to produce a plan.
"""

import time
from dataclasses import dataclass
from typing import Any

from free_claude_code.terminal.approval import ApprovalTokenIssuer
from free_claude_code.terminal.approval_store import (
    STATUS_APPROVED,
    FileApprovalStore,
)
from free_claude_code.terminal.approval_store import (
    STATUS_DENIED as APPROVAL_DENIED,
)
from free_claude_code.terminal.audit import (
    AuditSink,
    NullAuditSink,
    make_event,
    record_decision,
)
from free_claude_code.terminal.executor import TerminalExecutor
from free_claude_code.terminal.policy import (
    PolicyConfig,
    PolicyEngine,
    Requirement,
)
from free_claude_code.terminal.result import CommandResult
from free_claude_code.terminal.spec import CommandSpec

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_VERIFY_FAILED = "verify_failed"
STATUS_DENIED = "denied"
STATUS_APPROVAL_REQUIRED = "approval_required"


class PlanError(ValueError):
    """A plan document failed validation."""


@dataclass(frozen=True)
class VerifySpec:
    """Postcondition check for one step: run argv, expect an exit code."""

    argv: tuple[str, ...]
    expect_exit: int = 0

    @classmethod
    def from_dict(cls, data: Any, *, where: str) -> VerifySpec:
        if not isinstance(data, dict):
            raise PlanError(f"{where}: verify must be an object")
        argv = _argv_of(data.get("argv"), where=f"{where}.verify")
        expect_exit = data.get("expect_exit", 0)
        if not isinstance(expect_exit, int) or isinstance(expect_exit, bool):
            raise PlanError(f"{where}: verify.expect_exit must be an integer")
        return cls(argv=argv, expect_exit=expect_exit)


@dataclass(frozen=True)
class RunStep:
    """One ordered step: a command plus an optional verification."""

    step_id: str
    title: str
    argv: tuple[str, ...]
    cwd: str | None = None
    timeout_seconds: float | None = None
    verify: VerifySpec | None = None

    @classmethod
    def from_dict(cls, data: Any, *, index: int) -> RunStep:
        where = f"steps[{index}]"
        if not isinstance(data, dict):
            raise PlanError(f"{where}: each step must be an object")
        step_id = data.get("id", f"step-{index + 1}")
        if not isinstance(step_id, str) or not step_id:
            raise PlanError(f"{where}: id must be a non-empty string")
        title = data.get("title", step_id)
        if not isinstance(title, str):
            raise PlanError(f"{where}: title must be a string")
        cwd = data.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            raise PlanError(f"{where}: cwd must be a string")
        timeout = data.get("timeout_seconds")
        if timeout is not None and (
            not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
        ):
            raise PlanError(f"{where}: timeout_seconds must be a number")
        verify_raw = data.get("verify")
        verify = (
            VerifySpec.from_dict(verify_raw, where=where)
            if verify_raw is not None
            else None
        )
        return cls(
            step_id=step_id,
            title=title,
            argv=_argv_of(data.get("argv"), where=where),
            cwd=cwd,
            timeout_seconds=float(timeout) if timeout is not None else None,
            verify=verify,
        )


@dataclass(frozen=True)
class RunPlan:
    """A goal and its ordered steps."""

    goal: str
    steps: tuple[RunStep, ...]

    @classmethod
    def from_dict(cls, data: Any) -> RunPlan:
        if not isinstance(data, dict):
            raise PlanError("plan must be a JSON object")
        goal = data.get("goal", "")
        if not isinstance(goal, str):
            raise PlanError("goal must be a string")
        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise PlanError("steps must be a non-empty array")
        steps = tuple(
            RunStep.from_dict(entry, index=index)
            for index, entry in enumerate(raw_steps)
        )
        ids = [step.step_id for step in steps]
        if len(set(ids)) != len(ids):
            raise PlanError("step ids must be unique")
        return cls(goal=goal, steps=steps)


def _argv_of(value: Any, *, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise PlanError(f"{where}: argv must be a non-empty array of strings")
    if any(not isinstance(part, str) or not part for part in value):
        raise PlanError(f"{where}: argv must be a non-empty array of strings")
    return tuple(value)


@dataclass(frozen=True)
class StepReport:
    """Outcome of one step (or one unrun step)."""

    step_id: str
    title: str
    status: str
    argv: tuple[str, ...] = ()
    reason: str = ""
    risk_level: str = ""
    exit_code: int | None = None
    duration_seconds: float = 0.0
    verify_exit_code: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.step_id,
            "title": self.title,
            "status": self.status,
            "argv": list(self.argv),
            "reason": self.reason,
            "risk_level": self.risk_level,
            "exit_code": self.exit_code,
            "duration_seconds": round(self.duration_seconds, 3),
            "verify_exit_code": self.verify_exit_code,
        }


@dataclass(frozen=True)
class RunReport:
    """Whole-run outcome: per-step reports plus the residual steps."""

    goal: str
    task_id: str
    status: str
    steps: tuple[StepReport, ...]
    residual: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "task_id": self.task_id,
            "status": self.status,
            "steps": [step.as_dict() for step in self.steps],
            "residual": list(self.residual),
        }


class RunEngine:
    """Walk one plan through policy, approval, execution and verification."""

    def __init__(
        self,
        *,
        workspace: str,
        executor: TerminalExecutor | None = None,
        store: FileApprovalStore | None = None,
        issuer: ApprovalTokenIssuer | None = None,
        sink: AuditSink | None = None,
        engine: PolicyEngine | None = None,
    ) -> None:
        self._workspace = workspace
        self._sink = sink if sink is not None else NullAuditSink()
        self._executor = (
            executor if executor is not None else TerminalExecutor(sink=self._sink)
        )
        self._engine = (
            engine
            if engine is not None
            else PolicyEngine(PolicyConfig(workspace_root=workspace))
        )
        self._store = store
        self._issuer = issuer

    def _spec_for(self, step: RunStep) -> CommandSpec:
        kwargs: dict[str, Any] = {}
        if step.timeout_seconds is not None:
            kwargs["timeout_seconds"] = step.timeout_seconds
        return CommandSpec(argv=step.argv, cwd=step.cwd or self._workspace, **kwargs)

    def _approved(self, spec: CommandSpec, task_id: str) -> bool | None:
        """True with a valid grant, False on a recorded denial, None otherwise."""

        if self._store is None or self._issuer is None:
            return None
        records = self._store.list_records(include_decided=True)
        for record in records:
            if (
                record.task_id == task_id
                and record.status == STATUS_APPROVED
                and record.token is not None
                and record.argv == spec.argv
                and record.cwd == spec.cwd
                and self._issuer.verify(record.token, record.spec(), task_id).ok
            ):
                return True
        for record in records:
            if (
                record.task_id == task_id
                and record.status == APPROVAL_DENIED
                and record.argv == spec.argv
                and record.cwd == spec.cwd
            ):
                return False
        return None

    def _request(self, spec: CommandSpec, task_id: str, verdict: Any) -> None:
        if self._store is None:
            return
        self._sink.record(
            make_event(
                "approval_requested",
                task_id,
                spec.argv,
                spec.cwd,
                policy_id=verdict.policy_id,
            )
        )
        self._store.request(
            spec,
            task_id=task_id,
            level=verdict.level.name,
            reason=verdict.reason,
            policy_id=verdict.policy_id,
        )

    def _verify(self, step: RunStep, task_id: str) -> CommandResult:
        assert step.verify is not None
        spec = CommandSpec(argv=step.verify.argv, cwd=self._workspace)
        verdict = self._engine.evaluate(spec)
        if verdict.requirement is not Requirement.ALLOW:
            return CommandResult(
                argv=spec.argv,
                cwd=spec.cwd,
                exit_code=None,
                duration_seconds=0.0,
                stdout=b"",
                stderr=b"",
                cancelled=True,
            )
        return self._executor.run(spec, policy=verdict.to_decision(), task_id=task_id)

    def run(self, plan: RunPlan, *, task_id: str) -> RunReport:
        """Execute a plan; stop safely at the first step that cannot pass."""

        reports: list[StepReport] = []
        status = STATUS_OK
        for index, step in enumerate(plan.steps):
            residual = tuple(s.step_id for s in plan.steps[index + 1 :])
            try:
                spec = self._spec_for(step)
            except ValueError as exc:
                reports.append(
                    StepReport(
                        step.step_id,
                        step.title,
                        STATUS_FAILED,
                        argv=step.argv,
                        reason=str(exc),
                    )
                )
                status = STATUS_FAILED
                return RunReport(plan.goal, task_id, status, tuple(reports), residual)
            verdict = self._engine.evaluate(spec)
            if verdict.requirement is Requirement.DENY:
                record_decision(
                    self._sink,
                    granted=False,
                    task_id=task_id,
                    argv=spec.argv,
                    cwd=spec.cwd,
                    policy_id=verdict.policy_id,
                )
                reports.append(
                    StepReport(
                        step.step_id,
                        step.title,
                        STATUS_DENIED,
                        argv=step.argv,
                        reason=verdict.reason,
                        risk_level=verdict.level.name,
                    )
                )
                return RunReport(
                    plan.goal, task_id, STATUS_DENIED, tuple(reports), residual
                )
            approved = False
            if verdict.requirement is not Requirement.ALLOW:
                grant = self._approved(spec, task_id)
                if grant is False:
                    record_decision(
                        self._sink,
                        granted=False,
                        task_id=task_id,
                        argv=spec.argv,
                        cwd=spec.cwd,
                        policy_id=verdict.policy_id,
                    )
                    reports.append(
                        StepReport(
                            step.step_id,
                            step.title,
                            STATUS_DENIED,
                            argv=step.argv,
                            reason="approval denied",
                            risk_level=verdict.level.name,
                        )
                    )
                    return RunReport(
                        plan.goal, task_id, STATUS_DENIED, tuple(reports), residual
                    )
                if grant is None:
                    self._request(spec, task_id, verdict)
                    reports.append(
                        StepReport(
                            step.step_id,
                            step.title,
                            STATUS_APPROVAL_REQUIRED,
                            argv=step.argv,
                            reason=verdict.reason,
                            risk_level=verdict.level.name,
                        )
                    )
                    return RunReport(
                        plan.goal,
                        task_id,
                        STATUS_APPROVAL_REQUIRED,
                        tuple(reports),
                        residual,
                    )
                approved = True
                record_decision(
                    self._sink,
                    granted=True,
                    task_id=task_id,
                    argv=spec.argv,
                    cwd=spec.cwd,
                    policy_id=verdict.policy_id,
                )
            started = time.monotonic()
            try:
                result = self._executor.run(
                    spec,
                    policy=verdict.to_decision(approved=approved),
                    task_id=task_id,
                )
            except Exception as exc:  # spawn/shutdown failures fail the step
                reports.append(
                    StepReport(
                        step.step_id,
                        step.title,
                        STATUS_FAILED,
                        argv=step.argv,
                        reason=f"execution error: {exc}",
                        risk_level=verdict.level.name,
                    )
                )
                return RunReport(
                    plan.goal, task_id, STATUS_FAILED, tuple(reports), residual
                )
            duration = time.monotonic() - started
            if result.exit_code != 0 or result.timed_out:
                reason = "command timed out" if result.timed_out else "command failed"
                reports.append(
                    StepReport(
                        step.step_id,
                        step.title,
                        STATUS_FAILED,
                        argv=step.argv,
                        reason=reason,
                        exit_code=result.exit_code,
                        duration_seconds=duration,
                        risk_level=verdict.level.name,
                    )
                )
                return RunReport(
                    plan.goal, task_id, STATUS_FAILED, tuple(reports), residual
                )
            if step.verify is not None:
                check = self._verify(step, task_id)
                if check.exit_code != step.verify.expect_exit:
                    reports.append(
                        StepReport(
                            step.step_id,
                            step.title,
                            STATUS_VERIFY_FAILED,
                            argv=step.argv,
                            reason=(
                                "verification did not pass"
                                if not check.cancelled
                                else "verification needs privileges"
                            ),
                            exit_code=result.exit_code,
                            duration_seconds=duration,
                            verify_exit_code=check.exit_code,
                            risk_level=verdict.level.name,
                        )
                    )
                    return RunReport(
                        plan.goal,
                        task_id,
                        STATUS_VERIFY_FAILED,
                        tuple(reports),
                        residual,
                    )
                reports.append(
                    StepReport(
                        step.step_id,
                        step.title,
                        STATUS_OK,
                        argv=step.argv,
                        exit_code=result.exit_code,
                        duration_seconds=duration,
                        verify_exit_code=check.exit_code,
                        risk_level=verdict.level.name,
                    )
                )
                continue
            reports.append(
                StepReport(
                    step.step_id,
                    step.title,
                    STATUS_OK,
                    argv=step.argv,
                    exit_code=result.exit_code,
                    duration_seconds=duration,
                    risk_level=verdict.level.name,
                )
            )
        return RunReport(plan.goal, task_id, status, tuple(reports), ())
