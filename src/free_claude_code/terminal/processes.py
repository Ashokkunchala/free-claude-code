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
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any, cast

from loguru import logger

_KILL_ESCALATION_SECONDS = 2.0

_lock = threading.Lock()
_tracked: dict[int, subprocess.Popen[bytes]] = {}


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
    rlimits; both are a no-op on Windows.
    """

    kwargs: dict[str, Any] = {}
    if cwd is not None:
        kwargs["cwd"] = cwd
    if env is not None:
        kwargs["env"] = dict(env)
    if not inherit_stdio:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if isolated:
        if os.name == "nt":
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            )
        else:
            kwargs["start_new_session"] = True
    preexec = _make_limit_preexec(max_cpu_seconds, max_memory_bytes)
    if preexec is not None:
        kwargs["preexec_fn"] = preexec
    process = cast("subprocess.Popen[bytes]", subprocess.Popen(argv, **kwargs))
    if track and process.pid:
        with _lock:
            _tracked[process.pid] = process
    return process


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
