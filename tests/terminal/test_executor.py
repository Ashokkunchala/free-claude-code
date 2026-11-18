"""TerminalExecutor happy-path and structure tests."""

import threading

import pytest

from free_claude_code.terminal import (
    CommandSpec,
    InMemoryAuditSink,
    PolicyDecision,
    PolicyRejectedError,
    TerminalExecutor,
)
from free_claude_code.terminal.result import OutputChunk


def test_run_captures_stdout_and_exit_code(echo_stdout: tuple[str, ...]) -> None:
    result = TerminalExecutor().run(CommandSpec(argv=echo_stdout))
    assert result.ok
    assert result.exit_code == 0
    assert result.stdout_text.strip() == "hello-terminal"
    assert result.duration_seconds > 0


def test_run_captures_stderr(echo_stderr: tuple[str, ...]) -> None:
    result = TerminalExecutor().run(CommandSpec(argv=echo_stderr))
    assert "oops-terminal" in result.stderr_text


def test_run_mirrors_exit_code(exit_three: tuple[str, ...]) -> None:
    result = TerminalExecutor().run(CommandSpec(argv=exit_three))
    assert result.exit_code == 3
    assert not result.ok


def test_run_replaces_environment() -> None:
    import sys

    spec = CommandSpec(
        argv=(
            sys.executable,
            "-c",
            "import os; print(os.environ.get('FCC_TEST_FLAG'))",
        ),
        env={"FCC_TEST_FLAG": "set-by-spec"},
    )
    result = TerminalExecutor().run(spec)
    assert result.stdout_text.strip() == "set-by-spec"


def test_run_streams_redacted_chunks(echo_stdout: tuple[str, ...]) -> None:
    chunks: list[OutputChunk] = []
    done = threading.Event()

    def on_output(chunk: OutputChunk) -> None:
        chunks.append(chunk)
        done.set()

    result = TerminalExecutor().run(CommandSpec(argv=echo_stdout), on_output=on_output)
    assert result.ok
    assert done.is_set()
    assert b"hello-terminal" in b"".join(chunk.data for chunk in chunks)
    assert all(chunk.stream == "stdout" for chunk in chunks)


def test_policy_rejection_raises_and_audits(echo_stdout: tuple[str, ...]) -> None:
    sink = InMemoryAuditSink()
    executor = TerminalExecutor(sink=sink)
    with pytest.raises(PolicyRejectedError):
        executor.run(
            CommandSpec(argv=echo_stdout),
            policy=PolicyDecision(allowed=False, reason="not approved", policy_id="R2"),
        )
    kinds = [event.kind for event in sink.events()]
    assert kinds == ["cancelled"]
    assert sink.events()[0].policy_id == "R2"


def test_audit_event_sequence(echo_stdout: tuple[str, ...]) -> None:
    sink = InMemoryAuditSink()
    TerminalExecutor(sink=sink).run(CommandSpec(argv=echo_stdout))
    kinds = [event.kind for event in sink.events()]
    assert kinds == ["planned", "started", "finished"]
    finished = sink.events()[-1]
    assert finished.exit_code == 0
    assert finished.duration_seconds is not None


def test_missing_binary_raises_spawn_error() -> None:
    from free_claude_code.terminal import SpawnError

    with pytest.raises(SpawnError):
        TerminalExecutor().run(CommandSpec(argv=("definitely-not-a-real-binary-fcc",)))


def test_run_inherit_stdio_captures_nothing(echo_stdout: tuple[str, ...]) -> None:
    chunks: list[OutputChunk] = []
    result = TerminalExecutor().run(
        CommandSpec(argv=echo_stdout, inherit_stdio=True),
        on_output=chunks.append,
    )
    assert result.ok
    assert result.stdout == b""
    assert result.stderr == b""
    assert not result.stdout_truncated
    assert chunks == []


def test_run_non_isolated_child_shares_process_group(
    echo_stdout: tuple[str, ...],
) -> None:
    import os

    if os.name == "nt":
        pytest.skip("process-group check is POSIX-only")
    result = TerminalExecutor().run(
        CommandSpec(argv=echo_stdout, isolated=False),
    )
    assert result.ok


def test_spec_rejects_still_validates_with_new_fields() -> None:
    import sys

    spec = CommandSpec.of(
        [sys.executable, "-c", "pass"], inherit_stdio=True, isolated=False
    )
    assert spec.inherit_stdio
    assert not spec.isolated
    assert spec.timeout_seconds > 0
