"""Cross-platform process spawn and tree termination for the executor.

Executor children always run isolated from the caller's signal handling:
their own process group on POSIX, ``CREATE_NEW_PROCESS_GROUP`` on Windows.
That lets the executor terminate a whole tree on timeout or cancellation
without touching unrelated processes. Interactive harness launchers keep
their existing behavior until they migrate.
"""

import atexit
import contextlib
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, cast

from loguru import logger

_KILL_ESCALATION_SECONDS = 2.0

_lock = threading.Lock()
_tracked: dict[int, subprocess.Popen[bytes]] = {}


def pty_supported() -> bool:
    """PTY allocation uses the stdlib pty module, which is POSIX-only."""

    return os.name != "nt"


#: Parent-held pty slave fds by child pid; see ``close_pty_slave``.
_pty_slaves: dict[int, int] = {}


def close_pty_slave(process: subprocess.Popen[bytes]) -> None:
    """Close the parent-held pty slave fd, if one is open.

    The parent keeps its copy of the slave open for the whole run so the
    master cannot signal end-of-file before buffered child output is read:
    the moment the LAST slave fd closes, the kernel lets a master read fail
    with EIO, and on macOS that discards any undrained output - racing the
    reader thread and losing bytes. Close this only after the child has
    exited (or been killed); the master then drains in full before the
    reader sees the stream end. Idempotent: the fd is forgotten first, so a
    second call can never double-close a reused fd number.
    """

    if process.pid is None:
        return
    with _lock:
        slave_fd = _pty_slaves.pop(process.pid, None)
    if slave_fd is not None:
        with contextlib.suppress(OSError):
            os.close(slave_fd)


def apply_terminal_size(master_fd: int) -> None:
    """Copy the caller's current terminal size onto a pty master (POSIX only).

    Interactive children lay out their output against the pty's window size;
    a fresh pty defaults to 0x0, which full-screen programs mishandle. The
    size comes from ``shutil.get_terminal_size`` (COLUMNS/LINES, then the
    real terminal) with an 80x24 fallback when the caller has no terminal.
    """

    if os.name == "nt":
        return
    import fcntl
    import struct
    import termios

    size = shutil.get_terminal_size(fallback=(80, 24))
    winsz = struct.pack("HHHH", size.lines, size.columns, 0, 0)
    with contextlib.suppress(OSError):
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsz)


@contextlib.contextmanager
def forward_resize(master_fd: int) -> Iterator[None]:
    """Forward terminal resizes (SIGWINCH) to a pty master while active.

    Signal handlers are process-global and installable only on the main
    thread, so off the main thread (and on Windows, which has no SIGWINCH)
    this is a no-op: the pty keeps the size captured at spawn.
    """

    if os.name == "nt" or threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.signal(
        signal.SIGWINCH, lambda *_args: apply_terminal_size(master_fd)
    )
    try:
        yield
    finally:
        signal.signal(signal.SIGWINCH, previous)


def spawn(
    argv: Sequence[str],
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    inherit_stdio: bool = False,
    isolated: bool = True,
    track: bool = True,
    max_cpu_seconds: float | None = None,
    max_memory_bytes: int | None = None,
    pipe_stdin: bool = False,
    pty: bool = False,
) -> subprocess.Popen[bytes]:
    """Start a child process.

    ``isolated`` (default) gives the child its own process group on POSIX /
    CREATE_NEW_PROCESS_GROUP on Windows so tree kills never touch the caller;
    interactive launchers pass ``isolated=False`` to keep Ctrl+C semantics.
    ``inherit_stdio`` leaves stdin/stdout/stderr attached to the caller's
    terminal instead of piping them for capture. ``track`` adds the child to
    the executor's orphan-sweep table; callers managing their own lifecycle
    (launchers, detached servers) opt out. ``max_cpu_seconds`` /
    ``max_memory_bytes`` cap the child's CPU time and address space via POSIX
    rlimits; both are a no-op on Windows. ``pty`` allocates a pseudo-terminal
    (POSIX only, ignored with ``inherit_stdio``): the child's stdin/stdout/
    stderr all attach to the slave end and the parent's master is exposed as
    ``process.stdout`` with ``process.stderr`` set to None, so output arrives
    merged on one stream. The slave becomes the child's controlling terminal
    (so resizes arrive as SIGWINCH) and starts at the caller's current
    terminal size instead of the kernel's 0x0 default. The parent keeps its
    own slave fd open so the master cannot signal end-of-file before
    buffered output is drained; the caller closes it with
    ``close_pty_slave`` once the child has exited.
    """

    kwargs: dict[str, Any] = {}
    if cwd is not None:
        kwargs["cwd"] = cwd
    if env is not None:
        kwargs["env"] = dict(env)
    master_fd: int | None = None
    slave_fd: int | None = None
    use_pty = pty and not inherit_stdio and pty_supported()
    if use_pty:
        import pty as pty_module

        master_fd, slave_fd = pty_module.openpty()
        apply_terminal_size(master_fd)
        kwargs["stdin"] = slave_fd
        kwargs["stdout"] = slave_fd
        kwargs["stderr"] = slave_fd
    elif not inherit_stdio:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if pipe_stdin and not use_pty:
        kwargs["stdin"] = subprocess.PIPE
    if isolated:
        if os.name == "nt":
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            )
        else:
            kwargs["start_new_session"] = True
    preexec = _make_limit_preexec(max_cpu_seconds, max_memory_bytes)
    if use_pty:
        preexec = _make_ctty_preexec(preexec)
    if preexec is not None:
        kwargs["preexec_fn"] = preexec
    try:
        process = cast("subprocess.Popen[bytes]", subprocess.Popen(argv, **kwargs))
    except BaseException:
        for fd in (master_fd, slave_fd):
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
        raise
    if master_fd is not None:
        process.stdout = os.fdopen(master_fd, "rb", buffering=0)
        process.stderr = None
        # Parent keeps the slave open until the child exits; the executor
        # closes it via close_pty_slave before draining the master.
        assert slave_fd is not None
        if process.pid:
            with _lock:
                _pty_slaves[process.pid] = slave_fd
    if track and process.pid:
        with _lock:
            _tracked[process.pid] = process
    return process


def _make_ctty_preexec(limit_preexec: Any) -> Any:
    """Preexec making the pty slave the child's controlling terminal.

    Without a controlling terminal the kernel has no foreground process
    group to signal, so winsize changes never reach the child as SIGWINCH
    and job-control programs cannot find /dev/tty. subprocess runs
    ``start_new_session``'s setsid before preexec_fn, so the child is a
    session leader here and TIOCSCTTY attaches the slave (fd 0) cleanly.
    """

    def make_ctty() -> None:
        if limit_preexec is not None:
            limit_preexec()
        import fcntl
        import termios

        # Best effort: without start_new_session (isolated=False) the child
        # is no session leader and TIOCSCTTY fails with EPERM; the pty still
        # works, only resize signalling is lost.
        with contextlib.suppress(OSError):
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    return make_ctty


def _make_limit_preexec(
    max_cpu_seconds: float | None,
    max_memory_bytes: int | None,
) -> Any:
    """Build a POSIX preexec_fn applying rlimits; None when no limits/Windows."""

    if os.name == "nt" or (max_cpu_seconds is None and max_memory_bytes is None):
        return None
    import resource

    def apply_limits() -> None:
        if max_cpu_seconds is not None:
            secs = max(1, int(max_cpu_seconds + 0.999999))
            resource.setrlimit(resource.RLIMIT_CPU, (secs, secs))
        # RLIMIT_AS is unreliable on macOS: dyld's shared cache maps far more
        # virtual memory than any sensible budget, so a limit either fails to
        # set or makes the child unlaunchable. Enforce it on Linux only.
        if max_memory_bytes is not None and sys.platform != "darwin":
            limit = int(max_memory_bytes)
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    return apply_limits


def spawn_detached(
    argv: Sequence[str],
    *,
    cwd: str,
    stdin: Any,
    stdout: Any,
    stderr: Any,
) -> subprocess.Popen[bytes]:
    """Start a fully detached background process (e.g. the autostarted server).

    Detached processes outlive the caller by design, so they are neither
    tracked for orphan sweeps nor registered in the atexit net.
    """

    kwargs: dict[str, Any] = {
        "stdin": stdin,
        "stdout": stdout,
        "stderr": stderr,
        "close_fds": True,
        "cwd": cwd,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
            if os.name == "nt"
            else 0
        )
    else:
        kwargs["start_new_session"] = True
    return cast("subprocess.Popen[bytes]", subprocess.Popen(list(argv), **kwargs))


def untrack(process: subprocess.Popen[bytes]) -> None:
    with _lock:
        _tracked.pop(process.pid, None)


def tracked_pids() -> list[int]:
    """Snapshot of executor-owned child PIDs (orphan sweeps, tests)."""

    with _lock:
        return list(_tracked)


def kill_tree(process: subprocess.Popen[bytes]) -> None:
    """Terminate the child's process tree, escalating after a grace period."""

    if os.name == "nt":
        _kill_tree_windows(process)
    else:
        _kill_tree_posix(process)


def _kill_tree_windows(process: subprocess.Popen[bytes]) -> None:
    try:
        # /T kills child processes, /F forces termination.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except Exception as e:
        logger.debug("terminal.processes: taskkill failed pid=%s: %s", process.pid, e)


def _kill_tree_posix(process: subprocess.Popen[bytes]) -> None:
    pid = process.pid
    # Isolated children lead their own group; a non-isolated child (launcher
    # port) is signalled directly so we never hit the caller's group.
    try:
        leader = os.getpgid(pid) == pid
    except ProcessLookupError:
        return
    except PermissionError:
        leader = False

    def term() -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if leader:
                os.killpg(pid, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)

    def kill() -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if leader:
                os.killpg(pid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)

    term()
    deadline = time.monotonic() + _KILL_ESCALATION_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.05)
    kill()


# --- PID registry: atexit safety net migrated from cli.process_registry ---
#
# The registry tracks PIDs (not Popen handles) so an interrupted caller can
# still clean up children it no longer holds references to. cli.process_registry
# re-exports these; its public API is unchanged.
_registry_lock = threading.Lock()
_registry_pids: set[int] = set()
_atexit_registered = False


def ensure_atexit_registered() -> None:
    global _atexit_registered
    with _registry_lock:
        if _atexit_registered:
            return
        atexit.register(kill_all_best_effort)
        _atexit_registered = True


def register_pid(pid: int) -> None:
    if not pid:
        return
    ensure_atexit_registered()
    with _registry_lock:
        _registry_pids.add(int(pid))


def unregister_pid(pid: int) -> None:
    if not pid:
        return
    with _registry_lock:
        _registry_pids.discard(int(pid))


def kill_pid_tree_best_effort(pid: int) -> None:
    """Kill a tracked process and its children where the platform supports it."""
    if not pid:
        return
    if os.name == "nt":
        try:
            # /T kills child processes, /F forces termination.
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as e:
            logger.debug("terminal.processes: taskkill failed pid=%s: %s", pid, e)
        return

    # POSIX: signal the whole group when the pid leads one (executor children
    # and detached servers start their own sessions); otherwise the single pid.
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    except PermissionError:
        pgid = -1
    try:
        if pgid == pid:
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception as e:
        logger.debug("terminal.processes: terminate failed pid=%s: %s", pid, e)


def kill_all_best_effort() -> None:
    """Kill any still-running registered pids (best-effort)."""
    with _registry_lock:
        pids = list(_registry_pids)
        _registry_pids.clear()

    for pid in pids:
        kill_pid_tree_best_effort(pid)
