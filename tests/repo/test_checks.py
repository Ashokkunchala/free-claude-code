"""Quality-gate runner and verified-edit orchestration tests."""

import threading
from pathlib import Path

import pytest

from free_claude_code.repo import (
    CheckKind,
    CheckRunner,
    CheckSpec,
    apply_and_verify,
    python_checks,
    read_file,
)
from free_claude_code.terminal import (
    CommandResult,
    CommandSpec,
    PolicyDecision,
    PolicyRejectedError,
    TerminalExecutor,
)


def _result(
    argv: tuple[str, ...],
    *,
    exit_code: int | None = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
    timed_out: bool = False,
) -> CommandResult:
    return CommandResult(
        argv=argv,
        cwd=None,
        exit_code=exit_code,
        duration_seconds=0.25,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
    )


class FakeExecutor(TerminalExecutor):
    """Returns canned results and records the specs it was asked to run."""

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
        allow_pty: bool = False,
    ) -> CommandResult:
        self.specs.append(spec)
        return self._results.pop(0)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("def main():\n    return 'hello'\n", encoding="utf-8")
    return root


def _gate(kind: CheckKind = CheckKind.TESTS, *argv: str) -> CheckSpec:
    return CheckSpec(kind, argv or ("pytest", "-q"))


def test_run_one_success(repo: Path) -> None:
    executor = FakeExecutor([_result(("pytest", "-q"), stdout=b"1 passed")])
    runner = CheckRunner(repo, executor=executor)
    outcome = runner.run_one(_gate())
    assert outcome.ok
    assert outcome.exit_code == 0
    assert outcome.output_tail == "1 passed"
    assert executor.specs[0].cwd == str(repo)
    assert executor.specs[0].argv == ("pytest", "-q")


def test_run_one_failure_captures_output_tail(repo: Path) -> None:
    executor = FakeExecutor(
        [_result(("pytest", "-q"), exit_code=1, stderr=b"FAILED test_app.py::x")]
    )
    runner = CheckRunner(repo, executor=executor)
    outcome = runner.run_one(_gate())
    assert not outcome.ok
    assert outcome.exit_code == 1
    assert "FAILED test_app.py::x" in outcome.output_tail


def test_run_one_timed_out_is_not_ok(repo: Path) -> None:
    executor = FakeExecutor([_result(("pytest", "-q"), exit_code=None, timed_out=True)])
    runner = CheckRunner(repo, executor=executor)
    outcome = runner.run_one(_gate())
    assert not outcome.ok
    assert outcome.timed_out


def test_run_all_stop_on_failure(repo: Path) -> None:
    executor = FakeExecutor(
        [
            _result(("ruff", "check", "."), exit_code=1, stderr=b"E501"),
            _result(("pytest", "-q")),
        ]
    )
    runner = CheckRunner(repo, executor=executor)
    report = runner.run_all(
        (
            _gate(CheckKind.LINT, "ruff", "check", "."),
            _gate(CheckKind.TESTS, "pytest", "-q"),
        ),
        stop_on_failure=True,
    )
    assert not report.ok
    assert [outcome.kind for outcome in report.failed] == [CheckKind.LINT]
    assert len(executor.specs) == 1
    assert "lint: FAIL" in report.summary()


def test_run_all_continues_without_stop_on_failure(repo: Path) -> None:
    executor = FakeExecutor(
        [
            _result(("ruff", "check", "."), exit_code=1),
            _result(("pytest", "-q")),
        ]
    )
    runner = CheckRunner(repo, executor=executor)
    report = runner.run_all(
        (
            _gate(CheckKind.LINT, "ruff", "check", "."),
            _gate(CheckKind.TESTS, "pytest", "-q"),
        )
    )
    assert not report.ok
    assert len(executor.specs) == 2
    assert report.outcomes[1].ok


def test_policy_rejection_raises(repo: Path) -> None:
    executor = FakeExecutor([])
    runner = CheckRunner(repo, executor=executor)
    with pytest.raises(PolicyRejectedError):
        runner.run_one(CheckSpec(CheckKind.BUILD, ("rm", "-rf", ".")))
    assert executor.specs == []


def test_python_checks_shape() -> None:
    gates = python_checks()
    assert [gate.kind for gate in gates] == [
        CheckKind.FORMAT,
        CheckKind.LINT,
        CheckKind.TYPECHECK,
        CheckKind.TESTS,
    ]
    assert all(gate.argv for gate in gates)


def test_apply_and_verify_keeps_passing_edit(repo: Path) -> None:
    executor = FakeExecutor([_result(("pytest", "-q"), stdout=b"1 passed")])
    runner = CheckRunner(repo, executor=executor)
    verified = apply_and_verify(
        repo,
        "app.py",
        "def main():\n    return 'bye'\n",
        runner=runner,
        checks=(_gate(),),
    )
    assert verified.report.ok
    assert not verified.rolled_back
    assert read_file(repo, "app.py").content == "def main():\n    return 'bye'\n"


def test_apply_and_verify_rolls_back_failing_edit(repo: Path) -> None:
    original = read_file(repo, "app.py").content
    executor = FakeExecutor(
        [_result(("pytest", "-q"), exit_code=1, stderr=b"FAILED test_app.py::main")]
    )
    runner = CheckRunner(repo, executor=executor)
    verified = apply_and_verify(
        repo,
        "app.py",
        "def main():\n    return 'broken'\n",
        runner=runner,
        checks=(_gate(),),
    )
    assert not verified.report.ok
    assert verified.rolled_back
    assert read_file(repo, "app.py").content == original


def test_apply_and_verify_removes_new_file_on_failure(repo: Path) -> None:
    executor = FakeExecutor([_result(("pytest", "-q"), exit_code=1)])
    runner = CheckRunner(repo, executor=executor)
    verified = apply_and_verify(
        repo,
        "new.py",
        "x = 1\n",
        runner=runner,
        checks=(_gate(),),
    )
    assert verified.rolled_back
    assert not (repo / "new.py").exists()


def test_apply_and_verify_can_keep_failing_edit(repo: Path) -> None:
    executor = FakeExecutor([_result(("pytest", "-q"), exit_code=1)])
    runner = CheckRunner(repo, executor=executor)
    verified = apply_and_verify(
        repo,
        "app.py",
        "def main():\n    return 'broken'\n",
        runner=runner,
        checks=(_gate(),),
        rollback_on_failure=False,
    )
    assert not verified.rolled_back
    assert read_file(repo, "app.py").content == "def main():\n    return 'broken'\n"
