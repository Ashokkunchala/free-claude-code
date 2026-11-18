"""Cross-platform process spawn and tree termination for the executor.

Executor children always run isolated from the caller's signal handling:
their own process group on POSIX, ``CREATE_NEW_PROCESS_GROUP`` on Windows.
That lets the executor terminate a whole tree on timeout or cancellation
without touching unrelated processes. Interactive harness launchers keep
their existing behavior until they migrate.
"""

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
