"""Timeout-guarded Playwright session lifecycle.

On Windows CI (cpython 3.14 proactor loop), stopping the session Playwright
instance intermittently hangs inside ProactorEventLoop.close(): the proactor
waits in IocpProactor._poll for overlapped completions that never arrive
(issue #82). The worker then goes silent in teardown until the exit watchdog
kills the process tree, and xdist reports the worker's last test as failed
even though it already passed.

Stopping on a daemon thread with a timeout bounds the teardown: when the
proactor drains normally the stop completes as before, and when it hangs the
worker finishes and exits with the stuck thread abandoned (the process is
tearing down anyway).
"""

import threading
from collections.abc import Generator
from typing import Protocol

from playwright.sync_api import Playwright, sync_playwright

STOP_TIMEOUT_SECONDS = 30.0


class _Stoppable(Protocol):
    def stop(self) -> None: ...


def stop_with_timeout(
    playwright: _Stoppable, timeout: float = STOP_TIMEOUT_SECONDS
) -> bool:
    """Stop Playwright, returning False when the stop does not finish in time."""
    finished = threading.Event()

    def stop() -> None:
        try:
            playwright.stop()
        except Exception as exc:  # teardown reports but never raises
            print(f"[playwright-session] stop raised {exc!r}; continuing teardown")
        finally:
            finished.set()

    threading.Thread(target=stop, daemon=True).start()
    return finished.wait(timeout)


def session_playwright() -> Generator[Playwright]:
    """Yield a session Playwright whose stop is bounded by STOP_TIMEOUT_SECONDS."""
    pw = sync_playwright().start()
    try:
        yield pw
    finally:
        if not stop_with_timeout(pw, timeout=STOP_TIMEOUT_SECONDS):
            print(
                "[playwright-session] stop exceeded "
                f"{STOP_TIMEOUT_SECONDS:.0f}s; abandoning the hung proactor "
                "teardown (issue #82)"
            )
