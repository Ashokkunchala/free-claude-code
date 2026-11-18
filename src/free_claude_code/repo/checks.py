"""Quality-gate orchestration and verified edits for a repository.

``CheckRunner`` runs test/lint/typecheck/build gates as plain argument
vectors through the terminal executor, so every gate gets the policy
verdict, timeout, capture and audit trail of any other command. Gates are
expected to classify R0/R1 under the default policy (pytest, ruff, ty,
mypy, tsc, ...); a gate the policy does not allow raises
``PolicyRejectedError`` instead of running.

``apply_and_verify`` is the repair-loop primitive: apply one edit, run the
gates, and roll the edit back automatically when a gate fails, so the tree
never keeps an edit that does not pass its own checks.
"""

import enum
from dataclasses import dataclass
from pathlib import Path

from free_claude_code.repo.files import EditResult, edit_file, rollback
from free_claude_code.terminal import (
    CommandResult,
    CommandSpec,
    PolicyConfig,
    PolicyEngine,
    PolicyRejectedError,
    Requirement,
    TerminalExecutor,
)
from free_claude_code.terminal.spec import DEFAULT_TIMEOUT_SECONDS

_TAIL_CHARS = 4000


class CheckKind(enum.StrEnum):
    """The quality gates a repository can run."""

    TESTS = "tests"
    LINT = "lint"
    FORMAT = "format"
    TYPECHECK = "typecheck"
    BUILD = "build"


@dataclass(frozen=True)
class CheckSpec:
    """One quality gate: an argument vector and its timeout."""

    kind: CheckKind
    argv: tuple[str, ...]
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class CheckOutcome:
    """Structured result of one gate run."""

    kind: CheckKind
    argv: tuple[str, ...]
    exit_code: int | None
    ok: bool
    duration_seconds: float
    output_tail: str
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False


@dataclass(frozen=True)
class CheckReport:
    """The outcomes of a gate suite, in run order."""

    outcomes: tuple[CheckOutcome, ...]

    @property
    def ok(self) -> bool:
        return bool(self.outcomes) and all(outcome.ok for outcome in self.outcomes)

    @property
    def failed(self) -> tuple[CheckOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if not outcome.ok)

    def summary(self) -> str:
        lines = []
        for outcome in self.outcomes:
            status = "ok" if outcome.ok else f"FAIL (exit {outcome.exit_code})"
            lines.append(f"{outcome.kind}: {status} {' '.join(outcome.argv)}")
        return "\n".join(lines)


class CheckRunner:
    """Runs quality gates confined to one repository root."""

    def __init__(
        self,
        root: Path,
        *,
        executor: TerminalExecutor | None = None,
        engine: PolicyEngine | None = None,
    ) -> None:
        self._root = Path(root)
        self._executor = executor if executor is not None else TerminalExecutor()
        self._engine = (
            engine
            if engine is not None
            else PolicyEngine(PolicyConfig(workspace_root=str(self._root)))
        )

    @property
    def root(self) -> Path:
        return self._root

    def run_one(self, check: CheckSpec, *, task_id: str | None = None) -> CheckOutcome:
        """Run one gate after policy evaluation."""

        spec = CommandSpec(
            argv=check.argv,
            cwd=str(self._root),
            timeout_seconds=check.timeout_seconds,
        )
        verdict = self._engine.evaluate(spec)
        if verdict.requirement is not Requirement.ALLOW:
            decision = verdict.to_decision()
            raise PolicyRejectedError(check.argv, verdict.reason or decision.reason)
        result = self._executor.run(spec, policy=verdict.to_decision(), task_id=task_id)
        return _outcome_from_result(check.kind, result)

    def run_all(
        self,
        checks: tuple[CheckSpec, ...] | list[CheckSpec],
        *,
        stop_on_failure: bool = False,
        task_id: str | None = None,
    ) -> CheckReport:
        """Run gates in order; optionally stop at the first failure."""

        outcomes: list[CheckOutcome] = []
        for check in checks:
            outcome = self.run_one(check, task_id=task_id)
            outcomes.append(outcome)
            if stop_on_failure and not outcome.ok:
                break
        return CheckReport(tuple(outcomes))


def python_checks() -> tuple[CheckSpec, ...]:
    """Standard gates for a Python repository (format, lint, types, tests)."""

    return (
        CheckSpec(CheckKind.FORMAT, ("ruff", "format", "--check", ".")),
        CheckSpec(CheckKind.LINT, ("ruff", "check", ".")),
        CheckSpec(CheckKind.TYPECHECK, ("ty", "check", ".")),
        CheckSpec(CheckKind.TESTS, ("pytest", "-q")),
    )


@dataclass(frozen=True)
class VerifiedEdit:
    """An edit plus the gate report that verified (or reverted) it."""

    edit: EditResult
    report: CheckReport
    rolled_back: bool


def apply_and_verify(
    root: Path,
    path: str | Path,
    content: str,
    *,
    runner: CheckRunner,
    checks: tuple[CheckSpec, ...] | list[CheckSpec],
    rollback_on_failure: bool = True,
) -> VerifiedEdit:
    """Apply one edit, run the gates, and roll back on gate failure.

    The gates run with ``stop_on_failure`` so a red gate short-circuits the
    rest of the suite; the edit is only kept when every gate passes.
    """

    edit = edit_file(root, path, content)
    report = runner.run_all(checks, stop_on_failure=True)
    if report.ok or not rollback_on_failure:
        return VerifiedEdit(edit=edit, report=report, rolled_back=False)
    rollback(edit)
    return VerifiedEdit(edit=edit, report=report, rolled_back=True)


def _outcome_from_result(kind: CheckKind, result: CommandResult) -> CheckOutcome:
    combined = result.stdout_text
    if result.stderr_text:
        combined = combined + ("\n" if combined else "") + result.stderr_text
    return CheckOutcome(
        kind=kind,
        argv=result.argv,
        exit_code=result.exit_code,
        ok=result.ok,
        duration_seconds=result.duration_seconds,
        output_tail=combined[-_TAIL_CHARS:],
        timed_out=result.timed_out,
        cancelled=result.cancelled,
        truncated=result.stdout_truncated or result.stderr_truncated,
    )


__all__ = [
    "CheckKind",
    "CheckOutcome",
    "CheckReport",
    "CheckRunner",
    "CheckSpec",
    "VerifiedEdit",
    "apply_and_verify",
    "python_checks",
]
