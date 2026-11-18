"""Track and clean up spawned CLI subprocesses.

This is a safety net for cases where the server is interrupted (Ctrl+C) and the
FastAPI lifespan cleanup doesn't run to completion. We only track processes we
spawn so we don't accidentally kill unrelated system processes.

The implementation lives in :mod:`free_claude_code.terminal.processes` (T2 of
the terminal-agent roadmap); this module keeps its public API as a shim.
"""

from free_claude_code.terminal import processes as _processes
from free_claude_code.terminal.processes import (
    ensure_atexit_registered as ensure_atexit_registered,
)
from free_claude_code.terminal.processes import (
    kill_all_best_effort as kill_all_best_effort,
)
from free_claude_code.terminal.processes import (
    kill_pid_tree_best_effort as kill_pid_tree_best_effort,
)
from free_claude_code.terminal.processes import (
    register_pid as register_pid,
)
from free_claude_code.terminal.processes import (
    unregister_pid as unregister_pid,
)

# Live alias for the registry's PID set (inspected by tests).
_pids = _processes._registry_pids
