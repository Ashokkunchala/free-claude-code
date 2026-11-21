"""PTY allocation in the executor (roadmap section 4: interactive commands)."""

import os
import sys

import pytest

from free_claude_code.terminal import processes
from free_claude_code.terminal.executor import TerminalExecutor
from free_claude_code.terminal.spec import CommandSpec

POSIX = os.name != "nt"

ISATTY = [
    sys.executable,
    "-c",
    "import os, sys; print('tty' if os.isatty(sys.stdout.fileno()) else 'pipes')",
]


@pytest.mark.skipif(not POSIX, reason="PTY allocation is POSIX-only")
def test_pty_child_sees_a_terminal():
    result = TerminalExecutor().run(
        CommandSpec(argv=tuple(ISATTY), pty=True), allow_pty=True
    )
    assert result.exit_code == 0
    assert b"tty" in result.stdout
    assert result.stderr == b""


@pytest.mark.skipif(not POSIX, reason="PTY allocation is POSIX-only")
def test_pty_merges_stderr_into_stdout():
    argv = (
        sys.executable,
        "-c",
        "import sys; sys.stdout.write('out\\n'); sys.stderr.write('err\\n')",
    )
    result = TerminalExecutor().run(CommandSpec(argv=argv, pty=True), allow_pty=True)
    assert result.exit_code == 0
    assert b"out" in result.stdout
    assert b"err" in result.stdout
    assert result.stderr == b""


def test_pty_request_ignored_without_allow_pty():
    result = TerminalExecutor().run(CommandSpec(argv=tuple(ISATTY), pty=True))
    assert result.exit_code == 0
    assert b"pipes" in result.stdout


def test_pty_request_ignored_when_unsupported(monkeypatch):
    monkeypatch.setattr(processes, "pty_supported", lambda: False)
    result = TerminalExecutor().run(
        CommandSpec(argv=tuple(ISATTY), pty=True), allow_pty=True
    )
    assert result.exit_code == 0
    assert b"pipes" in result.stdout


def test_inherit_stdio_ignores_pty(monkeypatch):
    called = []

    real_spawn = processes.spawn

    def spy(argv, **kwargs):
        called.append(kwargs.get("pty"))
        return real_spawn(argv, **kwargs)

    monkeypatch.setattr("free_claude_code.terminal.executor.processes.spawn", spy)
    result = TerminalExecutor().run(
        CommandSpec(argv=tuple(ISATTY), pty=True, inherit_stdio=True), allow_pty=True
    )
    assert result.exit_code == 0
    assert called == [False]


@pytest.mark.skipif(not POSIX, reason="PTY allocation is POSIX-only")
def test_pty_timeout_still_kills():
    argv = (sys.executable, "-c", "import time; time.sleep(60)")
    result = TerminalExecutor().run(
        CommandSpec(argv=argv, pty=True, timeout_seconds=0.5), allow_pty=True
    )
    assert result.timed_out


@pytest.mark.skipif(not POSIX, reason="PTY allocation is POSIX-only")
def test_pty_output_drained_after_immediate_exit():
    # The macOS flake behind test_pty_merges_stderr_into_stdout: when the
    # last slave fd closed, the kernel could EIO the master and discard
    # undrained output. The parent now holds its slave copy until the child
    # exits, so even a large burst written just before exit must arrive.
    payload = b"y" * 200_000
    argv = (
        sys.executable,
        "-c",
        "import sys; sys.stdout.buffer.write(b'y' * 200000); sys.stdout.buffer.flush()",
    )
    result = TerminalExecutor().run(CommandSpec(argv=argv, pty=True), allow_pty=True)
    assert result.exit_code == 0
    # No newlines in the payload, so cooked-mode ONLCR cannot alter it.
    assert result.stdout == payload


@pytest.mark.skipif(not POSIX, reason="PTY allocation is POSIX-only")
def test_pty_slave_fd_closed_after_run(monkeypatch):
    captured = {}
    original_spawn = processes.spawn

    def spy_spawn(*args, **kwargs):
        process = original_spawn(*args, **kwargs)
        captured["process"] = process
        return process

    monkeypatch.setattr(processes, "spawn", spy_spawn)
    argv = (sys.executable, "-c", "pass")
    result = TerminalExecutor().run(CommandSpec(argv=argv, pty=True), allow_pty=True)
    assert result.exit_code == 0
    # The executor closed the parent-held slave fd after the child exited.
    assert captured["process"].pid not in processes._pty_slaves


@pytest.mark.skipif(not POSIX, reason="PTY allocation is POSIX-only")
def test_close_pty_slave_is_idempotent():
    process = processes.spawn((sys.executable, "-c", "pass"), pty=True)
    try:
        assert process.pid in processes._pty_slaves
        processes.close_pty_slave(process)
        assert process.pid not in processes._pty_slaves
        processes.close_pty_slave(process)  # second call is a no-op
        process.wait(timeout=10)
    finally:
        processes.close_pty_slave(process)
        if process.stdout is not None:
            process.stdout.close()
        if process.poll() is None:
            processes.kill_tree(process)
        # spawn(track=True by default) registered the child; release it so
        # later tracked_pids() assertions in this worker see a clean table.
        processes.untrack(process)
