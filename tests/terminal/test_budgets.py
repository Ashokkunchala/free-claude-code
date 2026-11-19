"""Per-task resource budgets: spec validation and POSIX rlimit enforcement."""

import sys

import pytest

from free_claude_code.terminal import (
    CommandSpec,
    InMemoryAuditSink,
    TerminalExecutor,
)


def py(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def test_spec_rejects_nonpositive_cpu_budget() -> None:
    with pytest.raises(ValueError, match="max_cpu_seconds"):
        CommandSpec(argv=py("pass"), max_cpu_seconds=0)
    with pytest.raises(ValueError, match="max_cpu_seconds"):
        CommandSpec(argv=py("pass"), max_cpu_seconds=-1)


def test_spec_rejects_nonpositive_memory_budget() -> None:
    with pytest.raises(ValueError, match="max_memory_bytes"):
        CommandSpec(argv=py("pass"), max_memory_bytes=0)
    with pytest.raises(ValueError, match="max_memory_bytes"):
        CommandSpec(argv=py("pass"), max_memory_bytes=-1)


def test_spec_budgets_default_to_none() -> None:
    spec = CommandSpec.of([sys.executable, "-c", "pass"])
    assert spec.max_cpu_seconds is None
    assert spec.max_memory_bytes is None


def test_spec_of_accepts_budgets() -> None:
    spec = CommandSpec.of(
        [sys.executable, "-c", "pass"],
        max_cpu_seconds=5,
        max_memory_bytes=64 * 1024 * 1024,
    )
    assert spec.max_cpu_seconds == 5
    assert spec.max_memory_bytes == 64 * 1024 * 1024


@pytest.mark.skipif(sys.platform == "win32", reason="rlimits are POSIX-only")
def test_cpu_budget_kills_busy_loop() -> None:
    sink = InMemoryAuditSink()
    result = TerminalExecutor(sink=sink).run(
        CommandSpec(argv=py("while True: pass"), timeout_seconds=30, max_cpu_seconds=1)
    )
    assert not result.ok
    assert result.exit_code != 0
    assert result.duration_seconds < 30
    kinds = [event.kind for event in sink.events()]
    assert kinds[-1] == "failed"


@pytest.mark.skipif(
    sys.platform != "linux", reason="memory budget uses RLIMIT_AS, Linux-only"
)
def test_memory_budget_fails_large_allocation() -> None:
    sink = InMemoryAuditSink()
    result = TerminalExecutor(sink=sink).run(
        CommandSpec(
            argv=py("b = bytearray(2 * 1024 * 1024 * 1024)"),
            timeout_seconds=30,
            max_memory_bytes=512 * 1024 * 1024,
        )
    )
    assert not result.ok
    assert result.exit_code != 0
    kinds = [event.kind for event in sink.events()]
    assert kinds[-1] == "failed"


@pytest.mark.skipif(sys.platform == "win32", reason="rlimits are POSIX-only")
def test_budgets_do_not_affect_compliant_child(echo_stdout: tuple[str, ...]) -> None:
    result = TerminalExecutor().run(
        CommandSpec(
            argv=echo_stdout,
            max_cpu_seconds=10,
            max_memory_bytes=512 * 1024 * 1024,
        )
    )
    assert result.ok
    assert result.exit_code == 0
