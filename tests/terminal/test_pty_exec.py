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
