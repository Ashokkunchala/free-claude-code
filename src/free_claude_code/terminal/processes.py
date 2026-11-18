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
import threading
import time
from collections.abc import Mapping
from typing import Any, cast

from loguru import logger

_KILL_ESCALATION_SECONDS = 2.0

_lock = threading.Lock()
_tracked: dict[int, subprocess.Popen[bytes]] = {}


def spawn(
    argv: tuple[str, ...],
    *,
    cwd: str | None,
    env: Mapping[str, str] | None,
) -> subprocess.Popen[bytes]:
    """Start a child with piped output in its own process group."""

    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "env": dict(env) if env is not None else None,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        )
    else:
        kwargs["start_new_session"] = True
    process = cast(subprocess.Popen[bytes], subprocess.Popen(argv, **kwargs))
    with _lock:
        _tracked[process.pid] = process
    return process


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
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGTERM)
    deadline = time.monotonic() + _KILL_ESCALATION_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


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
