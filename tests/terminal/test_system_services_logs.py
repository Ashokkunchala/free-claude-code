"""Tests for read-only service and error-log inspection."""

import pytest

from free_claude_code.terminal.system_inspect import (
    ServiceInfo,
    SystemInspectError,
    collect_error_logs,
    collect_services,
    parse_launchctl,
    parse_sc_query,
    parse_systemctl,
    render_services,
)

SYSTEMCTL_OUTPUT = """\
apparmor.service            loaded    inactive dead    Load AppArmor profiles
apache2.service             loaded    active   running The Apache HTTP Server
postgresql.service          loaded    failed   failed  PostgreSQL RDBMS
apt-daily.service           loaded    inactive dead    Daily apt download activities
oneshot.service             loaded    active   exited  One-shot setup
"""

LAUNCHCTL_OUTPUT = """\
PID\tStatus\tLabel
123\t0\tcom.apple.WindowServer
-\t0\tcom.apple.cfprefsd.xpc.daemon
-\t2\tcom.apple.crashed
"""

SC_QUERY_OUTPUT = """\
SERVICE_NAME: wuauserv
DISPLAY_NAME: Windows Update
        TYPE               : 30  WIN32
        STATE              : 4  RUNNING
                                (STOPPABLE, NOT_PAUSABLE, ACCEPTS_SHUTDOWN)

SERVICE_NAME: spooler
DISPLAY_NAME: Print Spooler
        TYPE               : 110  WIN32_OWN_PROCESS  (interactive)
        STATE              : 1  STOPPED

"""

JOURNAL_OUTPUT = """\
-- Logs begin at Thu 2026-11-19 10:00:00 UTC, end at Fri 2026-11-20 02:00:00 UTC. --
Nov 20 01:59:01 host kernel: something odd
Nov 20 01:59:02 host app[412]: auth failed for token sk-abcdefghijklmnopqrstuvwx
"""


def test_parse_systemctl_extracts_fields() -> None:
    services = parse_systemctl(SYSTEMCTL_OUTPUT)
    assert len(services) == 5
    apache = next(s for s in services if s.name == "apache2.service")
    assert apache.state == "active/running"
    assert apache.description == "The Apache HTTP Server"
    pg = next(s for s in services if s.name == "postgresql.service")
    assert pg.state == "failed/failed"


def test_parse_launchctl_extracts_fields() -> None:
    services = parse_launchctl(LAUNCHCTL_OUTPUT)
    assert len(services) == 3
    running = services[0]
    assert running.name == "com.apple.WindowServer"
    assert running.state == "running"
    stopped = services[2]
    assert stopped.state == "stopped (exit 2)"


def test_parse_sc_query_extracts_fields() -> None:
    services = parse_sc_query(SC_QUERY_OUTPUT)
    assert len(services) == 2
    wu = services[0]
    assert wu.name == "wuauserv"
    assert wu.state == "running"
    assert wu.description == "Windows Update"
    spooler = services[1]
    assert spooler.state == "stopped"


def test_collect_services_linux() -> None:
    seen: list[list[str]] = []

    def runner(argv: list[str]) -> str:
        seen.append(argv)
        return SYSTEMCTL_OUTPUT

    services = collect_services(runner=runner, system="Linux")
    assert seen[0][0] == "systemctl"
    assert "--type=service" in seen[0]
    assert len(services) == 5


def test_collect_services_macos() -> None:
    services = collect_services(runner=lambda argv: LAUNCHCTL_OUTPUT, system="Darwin")
    assert services[0].state == "running"


def test_collect_services_windows() -> None:
    services = collect_services(runner=lambda argv: SC_QUERY_OUTPUT, system="Windows")
    assert services[0].name == "wuauserv"


def test_collect_services_unsupported_platform() -> None:
    with pytest.raises(SystemInspectError, match="not supported"):
        collect_services(runner=lambda argv: "", system="Plan9")


def test_collect_error_logs_linux_redacts() -> None:
    seen: list[list[str]] = []

    def runner(argv: list[str]) -> str:
        seen.append(argv)
        return JOURNAL_OUTPUT

    lines = collect_error_logs(runner=runner, system="Linux", lines=25)
    assert seen == [["journalctl", "-n", "25", "-p", "warning", "--no-pager"]]
    text = "\n".join(lines)
    assert "something odd" in text
    assert "sk-abcdefghijklmnopqrstuvwx" not in text
    assert "<redacted>" in text
    assert "-- Logs begin" not in text


def test_collect_error_logs_windows() -> None:
    output = "Event[0]:\r\n  Log Name: System\r\n  Source: Test\r\n  Message: disk warning\r\n"
    lines = collect_error_logs(runner=lambda argv: output, system="Windows", lines=10)
    assert any("disk warning" in line for line in lines)


def test_collect_error_logs_macos_unsupported() -> None:
    with pytest.raises(SystemInspectError, match="not supported"):
        collect_error_logs(runner=lambda argv: "", system="Darwin", lines=10)


def test_render_services_failed_first() -> None:
    rows = [
        ServiceInfo(name="ok.service", state="active/running", description="fine"),
        ServiceInfo(name="bad.service", state="failed/failed", description="broken"),
    ]
    text = render_services(rows, total=2)
    assert text.index("bad.service") < text.index("ok.service")
    assert "failed/failed" in text
