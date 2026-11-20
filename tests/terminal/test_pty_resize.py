"""Terminal-size handling for pty runs (roadmap section 4: resize).

A fresh pty defaults to a 0x0 window, which full-screen and interactive
programs mishandle. spawn copies the caller's terminal size onto the pty,
and the executor forwards SIGWINCH resizes to the child while it runs.
"""

import contextlib
import os
import select
import shutil
import signal
import sys
import threading
import time

import pytest

from free_claude_code.terminal import processes
from free_claude_code.terminal.executor import TerminalExecutor
from free_claude_code.terminal.spec import CommandSpec

POSIX = os.name != "nt"
HAS_SIGWINCH = hasattr(signal, "SIGWINCH")

SIZE_CHILD = (
    sys.executable,
    "-c",
    "import os, sys; s = os.get_terminal_size(sys.stdout.fileno()); "
    "print(s.columns, s.lines)",
)


def _size(columns: int, lines: int) -> os.terminal_size:
    return os.terminal_size((columns, lines))


@pytest.mark.skipif(not POSIX, reason="PTY allocation is POSIX-only")
def test_spawn_pty_applies_terminal_size(monkeypatch) -> None:
    monkeypatch.setattr(
        shutil, "get_terminal_size", lambda fallback=(80, 24): _size(100, 40)
    )
    result = TerminalExecutor().run(
        CommandSpec(argv=SIZE_CHILD, pty=True), allow_pty=True
    )
    assert result.exit_code == 0
    assert b"100 40" in result.stdout


@pytest.mark.skipif(not POSIX, reason="PTY allocation is POSIX-only")
def test_spawn_pty_falls_back_without_terminal() -> None:
    result = TerminalExecutor().run(
        CommandSpec(argv=SIZE_CHILD, pty=True), allow_pty=True
    )
    assert result.exit_code == 0
    # Not a tty in CI: the 80x24 fallback applies instead of the pty's 0x0.
    assert b"0 0" not in result.stdout


def _read_until(fd: int, marker: bytes, timeout: float = 5.0) -> bytes:
    import time

    out = b""
    deadline = time.monotonic() + timeout
    while marker not in out and time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    return out


@pytest.mark.skipif(
    not POSIX or not HAS_SIGWINCH, reason="SIGWINCH forwarding is POSIX-only"
)
def test_forward_resize_delivers_sigwinch(monkeypatch, tmp_path) -> None:
    # The child reports through os.write to a log file and os._exit: printing
    # to the pty from inside a Python signal handler can reenter the buffered
    # writer the main flow is using, which is a test artifact, not the
    # forwarding path under test.
    log = tmp_path / "sigwinch.log"
    child = (
        sys.executable,
        "-c",
        "import os, signal, sys, time\n"
        f"log_fd = os.open({str(log)!r}, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)\n"
        "def on_winch(*_a):\n"
        "    s = os.get_terminal_size(sys.stdout.fileno())\n"
        "    os.write(log_fd, b'resized %d %d\\n' % (s.columns, s.lines))\n"
        "    os._exit(0)\n"
        "signal.signal(signal.SIGWINCH, on_winch)\n"
        "print('ready', flush=True)\n"
        "while True:\n"
        "    time.sleep(0.05)\n",
    )
    monkeypatch.setattr(
        shutil, "get_terminal_size", lambda fallback=(80, 24): _size(100, 40)
    )
    process = processes.spawn(child, pty=True)
    try:
        assert process.stdout is not None
        fd = process.stdout.fileno()
        assert b"ready" in _read_until(fd, b"ready")
        monkeypatch.setattr(
            shutil, "get_terminal_size", lambda fallback=(80, 24): _size(120, 50)
        )
        with processes.forward_resize(fd):
            os.kill(os.getpid(), signal.SIGWINCH)
            deadline = time.monotonic() + 5.0
            while not log.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
        process.wait(timeout=5)
        assert log.read_text().strip() == "resized 120 50"
        # A dead child's pty master can raise EIO on close; close it here,
        # suppressed, so the finalizer does not leak an unraisable warning.
        with contextlib.suppress(OSError):
            process.stdout.close()
    finally:
        processes.kill_tree(process)
        processes.untrack(process)


@pytest.mark.skipif(
    not POSIX or not HAS_SIGWINCH, reason="SIGWINCH forwarding is POSIX-only"
)
def test_forward_resize_is_noop_off_main_thread() -> None:
    before = signal.getsignal(signal.SIGWINCH)
    observed = []

    def check() -> None:
        with processes.forward_resize(1):
            observed.append(signal.getsignal(signal.SIGWINCH))

    worker = threading.Thread(target=check)
    worker.start()
    worker.join(timeout=5)
    assert observed == [before]
    assert signal.getsignal(signal.SIGWINCH) is before
