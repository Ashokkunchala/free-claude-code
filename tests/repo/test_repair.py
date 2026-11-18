"""Repair-loop tests: bounded verified-edit iteration."""

import threading
from pathlib import Path

import pytest

from free_claude_code.repo import (
    CheckKind,
    CheckReport,
    CheckRunner,
    CheckSpec,
    EditProposal,
    read_file,
    repair_loop,
)
from free_claude_code.terminal import (
    CommandResult,
    CommandSpec,
    PolicyDecision,
    TerminalExecutor,
)


def _result(argv: tuple[str, ...], *, exit_code: int = 0) -> CommandResult:
    return CommandResult(
        argv=argv,
        cwd=None,
        exit_code=exit_code,
        duration_seconds=0.25,
        stdout=b"ok" if exit_code == 0 else b"",
        stderr=b"" if exit_code == 0 else b"FAILED test_app.py::main",
    )


class FakeExecutor(TerminalExecutor):
    """Returns canned results in order and records the specs it ran."""

    def __init__(self, results: list[CommandResult]) -> None:
        super().__init__()
        self._results = list(results)
        self.specs: list[CommandSpec] = []

    def run(
        self,
        spec: CommandSpec,
        *,
        policy: PolicyDecision | None = None,
        cancel_event: threading.Event | None = None,
        on_output: object = None,
        task_id: str | None = None,
    ) -> CommandResult:
        self.specs.append(spec)
        return self._results.pop(0)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_bytes(b"def main():\n    return 'hello'\n")
    return root


GATES = (CheckSpec(CheckKind.TESTS, ("pytest", "-q")),)


def test_already_green_runs_no_rounds(repo: Path) -> None:
    executor = FakeExecutor([_result(("pytest", "-q"))])
    runner = CheckRunner(repo, executor=executor)
    result = repair_loop(
        repo,
        runner=runner,
        checks=GATES,
        fix=lambda round_number, report: None,
    )
    assert result.repaired
    assert result.attempts == 0
    assert len(executor.specs) == 1


def test_first_proposal_passes(repo: Path) -> None:
    executor = FakeExecutor(
        [
            _result(("pytest", "-q"), exit_code=1),
            _result(("pytest", "-q")),
        ]
    )
    runner = CheckRunner(repo, executor=executor)

    def fix(round_number: int, report: CheckReport) -> EditProposal:
        assert "FAILED" in report.outcomes[-1].output_tail
        return EditProposal("app.py", "def main():\n    return 'fixed'\n")

    result = repair_loop(repo, runner=runner, checks=GATES, fix=fix)
    assert result.repaired
    assert result.attempts == 1
    assert not result.rounds[0].rolled_back
    assert read_file(repo, "app.py").content == "def main():\n    return 'fixed'\n"


def test_failed_round_rolls_back_before_next(repo: Path) -> None:
    executor = FakeExecutor(
        [
            _result(("pytest", "-q"), exit_code=1),
            _result(("pytest", "-q"), exit_code=1),
            _result(("pytest", "-q")),
        ]
    )
    runner = CheckRunner(repo, executor=executor)
    originals: list[str] = []

    def fix(round_number: int, report: CheckReport) -> EditProposal:
        originals.append(read_file(repo, "app.py").content)
        return EditProposal("app.py", f"def main():\n    return 'try{round_number}'\n")

    result = repair_loop(repo, runner=runner, checks=GATES, fix=fix)
    assert result.repaired
    assert result.attempts == 2
    assert result.rounds[0].rolled_back
    # Each round started from the original content, not the failed attempt.
    assert originals == ["def main():\n    return 'hello'\n"] * 2
    assert read_file(repo, "app.py").content == "def main():\n    return 'try2'\n"


def test_budget_exhausted_leaves_tree_clean(repo: Path) -> None:
    executor = FakeExecutor([_result(("pytest", "-q"), exit_code=1)] * 4)
    runner = CheckRunner(repo, executor=executor)

    def fix(round_number: int, report: CheckReport) -> EditProposal:
        return EditProposal("app.py", f"# attempt {round_number}\n")

    result = repair_loop(repo, runner=runner, checks=GATES, fix=fix, max_rounds=2)
    assert not result.repaired
    assert result.attempts == 2
    assert read_file(repo, "app.py").content == "def main():\n    return 'hello'\n"


def test_fix_can_stop_the_loop(repo: Path) -> None:
    executor = FakeExecutor([_result(("pytest", "-q"), exit_code=1)])
    runner = CheckRunner(repo, executor=executor)
    result = repair_loop(
        repo,
        runner=runner,
        checks=GATES,
        fix=lambda round_number, report: None,
    )
    assert not result.repaired
    assert result.attempts == 0


def test_round_task_ids_scoped(repo: Path) -> None:
    task_ids: list[str | None] = []

    class RecordingExecutor(FakeExecutor):
        def run(
            self,
            spec: CommandSpec,
            *,
            policy: PolicyDecision | None = None,
            cancel_event: threading.Event | None = None,
            on_output: object = None,
            task_id: str | None = None,
        ) -> CommandResult:
            task_ids.append(task_id)
            return super().run(
                spec,
                policy=policy,
                cancel_event=cancel_event,
                on_output=on_output,
                task_id=task_id,
            )

    executor = RecordingExecutor(
        [_result(("pytest", "-q"), exit_code=1), _result(("pytest", "-q"))]
    )
    runner = CheckRunner(repo, executor=executor)
    result = repair_loop(
        repo,
        runner=runner,
        checks=GATES,
        fix=lambda round_number, report: EditProposal("app.py", "x = 1\n"),
        task_id="repair-1",
    )
    assert result.repaired
    assert task_ids == ["repair-1-round-0", "repair-1-round-1"]
