"""Best-effort auto-start of the FCC proxy for client launchers.

When a launcher finds the proxy unreachable, the user's first instinct is to
run the client anyway, not to open a second terminal. This module starts a
detached ``fcc-server`` process in the background and reports readiness, so
launchers can surface a health banner instead of a hard failure. Failures to
start degrade to the normal "start it with fcc-server" hint the launcher
already shows.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from free_claude_code.config.paths import server_log_path

AUTOSTART_ENV = "FCC_AUTOSTART"
AUTOSTART_TIMEOUT_SECONDS = 45.0
_POLL_INTERVAL_SECONDS = 0.5


def autostart_enabled(env: dict[str, str] | None = None) -> bool:
    """Report whether background auto-start is allowed (opt-out via FCC_AUTOSTART=0)."""

    source = os.environ if env is None else env
    return source.get(AUTOSTART_ENV, "1").strip().lower() not in {"0", "false", "off"}


def ensure_server_running(proxy_root_url: str, *, announce=print) -> str | None:
    """Return an error string when the proxy stays down; None once healthy.

    Starts a detached ``fcc-server`` when the proxy is unreachable, then polls
    its health endpoint until it responds or the budget expires. ``announce``
    receives human-readable progress on stderr-bound channels.
    """

    from free_claude_code.cli.launchers.common import preflight_proxy

    error = preflight_proxy(proxy_root_url)
    if error is None:
        return None
    if error.startswith("returned HTTP"):
        # Something is already listening; a second server would only hit EADDRINUSE.
        return error
    if not autostart_enabled():
        return error
    if not _spawn_detached_server():
        return error
    announce(
        f"fcc: proxy not reachable ({error}); starting fcc-server in the background..."
    )
    deadline = time.monotonic() + AUTOSTART_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        error = preflight_proxy(proxy_root_url)
        if error is None:
            announce(f"fcc: proxy is ready at {proxy_root_url}")
            return None
        time.sleep(_POLL_INTERVAL_SECONDS)
    return error


def _spawn_detached_server() -> bool:
    """Launch a detached fcc-server process; report whether a spawn was attempted."""

    command = _server_command()
    if command is None:
        return False
    log = _open_log()
    owned = log is not subprocess.DEVNULL
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
        "cwd": str(Path.home()),
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(command, **kwargs)
    except OSError:
        return False
    finally:
        if owned:
            log.close()
    return True


def _server_command() -> list[str] | None:
    executable = shutil.which("fcc-server")
    if executable:
        return [executable]
    return [
        sys.executable,
        "-c",
        "from free_claude_code.cli.entrypoints import serve; serve()",
    ]


def _open_log():
    try:
        path = server_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("ab")
    except OSError:
        return subprocess.DEVNULL


__all__ = ["AUTOSTART_ENV", "autostart_enabled", "ensure_server_running"]
