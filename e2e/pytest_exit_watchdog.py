"""Run pytest and bound how long its process may linger after the verdict.

On Windows CI (cpython 3.14 proactor loop), the pytest process intermittently
never exits after printing its final summary: the Playwright driver's event
loop hangs in ProactorEventLoop.close() waiting on IOCP completions that
never arrive. The job then dies at its timeout with the suite fully green
(PR #75: cancels at 50m13s, 50m12s and 75m with "242 passed" logged).

This wrapper streams the child's output, watches for pytest's final summary
line, and force-kills the process tree GRACE_SECONDS after that line if the
child has not exited, exiting with the verdict the summary reported.

The summary is not the only hang posture: a single test can hang mid-run in
the same proactor close (PR #75 head 864b715d: one test stuck in the
pytest_playwright fixture teardown, 68 minutes of total silence until the
job-level cancel). A genuinely stuck suite produces no output at all, so the
wrapper also force-kills the tree after SILENCE_SECONDS without a line of
output and exits 1; healthy suites stream continuously, and pytest's own
faulthandler dumps keep slow-but-alive tests from ever going silent that
long. On healthy runs this is a pure pass-through: stream output, propagate
the child's exit code.
"""

import os
import re
import signal
import subprocess
import sys
import threading
import time
from typing import TextIO

GRACE_SECONDS = 30.0
SILENCE_SECONDS = 600.0

_SUMMARY = re.compile(r"^=+ .*\bin \d")
_FAILED = re.compile(r"\d+ (failed|error)")


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            check=False,
            capture_output=True,
            timeout=30,
        )
    else:
        # The child runs in its own session so xdist workers and browsers die
        # with it instead of holding the output pipe open.
        os.killpg(proc.pid, signal.SIGKILL)


def run_pytest_with_watchdog(
    argv: list[str],
    *,
    grace_seconds: float = GRACE_SECONDS,
    silence_seconds: float = SILENCE_SECONDS,
    stream: TextIO | None = None,
    cwd: str | None = None,
) -> int:
    """Run pytest as a child process; return its exit code or summary verdict."""
    out = stream if stream is not None else sys.stdout
    proc = subprocess.Popen(
        [sys.executable, "-m", "pytest", *argv],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=cwd,
        start_new_session=sys.platform != "win32",
    )
    assert proc.stdout is not None
    child_out = proc.stdout
    summary_at: float | None = None
    verdict_failed = False
    last_output_at = time.monotonic()

    def _reader() -> None:
        nonlocal summary_at, verdict_failed, last_output_at
        try:
            for line in child_out:
                out.write(line)
                out.flush()
                last_output_at = time.monotonic()
                if summary_at is None and _SUMMARY.search(line):
                    summary_at = time.monotonic()
                    verdict_failed = bool(_FAILED.search(line))
        finally:
            child_out.close()

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    while True:
        code = proc.poll()
        if code is not None:
            reader.join(timeout=5)
            return code
        if summary_at is not None and time.monotonic() - summary_at >= grace_seconds:
            _kill_tree(proc)
            proc.wait(timeout=30)
            reader.join(timeout=5)
            return 1 if verdict_failed else 0
        if summary_at is None and time.monotonic() - last_output_at >= silence_seconds:
            out.write(
                "[pytest-exit-watchdog] no output for "
                f"{silence_seconds:.0f}s before a summary line; "
                "killing the stuck process tree\n"
            )
            out.flush()
            _kill_tree(proc)
            proc.wait(timeout=30)
            reader.join(timeout=5)
            return 1
        time.sleep(0.2)


def main() -> int:
    return run_pytest_with_watchdog(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
