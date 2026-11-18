"""Repair loops: iterate verified edits until the quality gates pass.

A repair loop couples a caller-supplied fix function with
:func:`apply_and_verify`: each round the fix function receives the latest
failing gate report and proposes the next edit (path + full content), or
returns ``None`` to stop. The loop is bounded (``max_rounds``), every round
runs through the policy-checked gate path with its own audit task id, and
failed rounds roll back, so the tree only ever keeps an edit whose checks
pass. When the budget runs out (or the fix function gives up) the tree is
back to its original state.

This is the engine behind the roadmap's software-engineering repair flow:
reproduce the failure with the gates, apply a minimal patch, and validate
with the full suite - with the model (or a human script) supplying the
patch each round.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from free_claude_code.repo.checks import (
    CheckReport,
    CheckRunner,
    CheckSpec,
    apply_and_verify,
)

DEFAULT_MAX_ROUNDS = 3


@dataclass(frozen=True)
class EditProposal:
    """One proposed repair: replace ``path`` with ``content``."""

    path: str
    content: str


@dataclass(frozen=True)
class RepairRound:
    """One repair attempt and how it ended."""

    round_number: int
    path: str
    report: CheckReport
    rolled_back: bool


@dataclass(frozen=True)
class RepairResult:
    """Every round attempted, and whether the gates pass at the end."""

    rounds: tuple[RepairRound, ...]
    repaired: bool

    @property
    def attempts(self) -> int:
        return len(self.rounds)


FixFunc = Callable[[int, CheckReport], EditProposal | None]
"""Given the round number and the latest failing report, propose an edit.

Return ``None`` to stop the loop without another edit (reported as
unrepaired). The content replaces the whole file; use ``read_file`` from
the repo package to build it from current state.
"""


def repair_loop(
    root: Path,
    *,
    runner: CheckRunner,
    checks: tuple[CheckSpec, ...] | list[CheckSpec],
    fix: FixFunc,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    task_id: str | None = None,
) -> RepairResult:
    """Run the bounded repair loop against the given gates.

    A green initial gate run returns immediately with zero rounds. Each
    round runs ``apply_and_verify`` with rollback enabled and a round-scoped
    audit task id (``<task_id>-round-N``) when ``task_id`` is given.
    """

    report = runner.run_all(
        checks, stop_on_failure=True, task_id=_round_task(task_id, 0)
    )
    if report.ok:
        return RepairResult(rounds=(), repaired=True)
    rounds: list[RepairRound] = []
    for round_number in range(1, max_rounds + 1):
        proposal = fix(round_number, report)
        if proposal is None:
            break
        verified = apply_and_verify(
            root,
            proposal.path,
            proposal.content,
            runner=runner,
            checks=checks,
            task_id=_round_task(task_id, round_number),
        )
        rounds.append(
            RepairRound(
                round_number=round_number,
                path=proposal.path,
                report=verified.report,
                rolled_back=verified.rolled_back,
            )
        )
        if verified.report.ok:
            return RepairResult(rounds=tuple(rounds), repaired=True)
        report = verified.report
    return RepairResult(rounds=tuple(rounds), repaired=False)


def _round_task(task_id: str | None, round_number: int) -> str | None:
    if task_id is None:
        return None
    return f"{task_id}-round-{round_number}"


__all__ = [
    "DEFAULT_MAX_ROUNDS",
    "EditProposal",
    "FixFunc",
    "RepairResult",
    "RepairRound",
    "repair_loop",
]
