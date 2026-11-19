"""Tests for read-only process and listening-port inspection."""

import pytest

from free_claude_code.terminal.system_inspect import (
    PortListener,
    ProcessInfo,
    SystemInspectError,
    collect_listeners,
    collect_processes,
    parse_netstat_macos,
    parse_netstat_windows,
    parse_ps,
    parse_ss,
    parse_tasklist,
    render_listeners,
    render_processes,
)

PS_OUTPUT = """\
    1      0   2100 /sbin/init /sbin/init splash
  412      1 512000 /usr/bin/python3 /usr/bin/python3 -m api --key sk-abcdefghijklmnopqrstuvwx
   77      1    900 sshd /usr/sbin/sshd -D
garbage line
"""

TASKLIST_OUTPUT = """\
"System Idle Process","0","Services","0","8 K"
"python.exe","4321","Console","1","512,000 K"
"""

SS_OUTPUT = """\
tcp   LISTEN 0      4096        127.0.0.1:8000        0.0.0.0:*
udp   UNCONN 0      0      127.0.0.53%lo:53           0.0.0.0:*
tcp   LISTEN 0      511                 *:8080              *:*
tcp   ESTAB  0      0         10.0.0.2:55512    93.184.216.34:443
"""

NETSTAT_MACOS_OUTPUT = """\
Active Internet connections (including servers)
Proto Recv-Q Send-Q  Local Address          Foreign Address        (state)
tcp4       0      0  127.0.0.1.8000         *.*                    LISTEN
tcp6       0      0  *.8080                 *.*                    LISTEN
udp4       0      0  *.5353                 *.*
tcp4       0      0  192.168.1.5.51234      93.184.216.34.443      ESTABLISHED
"""

NETSTAT_WINDOWS_OUTPUT = """\
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:8000         0.0.0.0:0              LISTENING       4321
  TCP    10.0.0.2:55512         93.184.216.34:443      ESTABLISHED     5555
  UDP    0.0.0.0:500            *:*                                    1234
"""


def test_parse_ps_extracts_fields() -> None:
    processes = parse_ps(PS_OUTPUT)
    assert len(processes) == 3
    init = processes[0]
    assert init.pid == 1
    assert init.ppid == 0
    assert init.rss_bytes == 2100 * 1024
    assert init.name == "/sbin/init"
    assert init.command == "/sbin/init splash"


def test_parse_ps_skips_malformed_lines() -> None:
    assert parse_ps("garbage line\n") == []


def test_parse_tasklist_extracts_fields() -> None:
    processes = parse_tasklist(TASKLIST_OUTPUT)
    assert len(processes) == 2
    python = processes[1]
    assert python.pid == 4321
    assert python.ppid is None
    assert python.rss_bytes == 512000 * 1024
    assert python.name == "python.exe"


def test_parse_ss_keeps_only_listeners() -> None:
    listeners = parse_ss(SS_OUTPUT)
    by_port = {listener.port: listener for listener in listeners}
    assert set(by_port) == {8000, 53, 8080}
    assert by_port[8000].protocol == "tcp"
    assert by_port[8000].address == "127.0.0.1"
    assert by_port[53].protocol == "udp"
    assert by_port[8080].address == "*"


def test_parse_netstat_macos() -> None:
    listeners = parse_netstat_macos(NETSTAT_MACOS_OUTPUT)
    by_port = {listener.port: listener for listener in listeners}
    assert set(by_port) == {8000, 8080, 5353}
    assert by_port[8000].protocol == "tcp"
    assert by_port[8080].protocol == "tcp"
    assert by_port[5353].protocol == "udp"
    assert by_port[8000].address == "127.0.0.1"


def test_parse_netstat_windows() -> None:
    listeners = parse_netstat_windows(NETSTAT_WINDOWS_OUTPUT)
    by_port = {listener.port: listener for listener in listeners}
    assert set(by_port) == {8000, 500}
    assert by_port[8000].protocol == "tcp"
    assert by_port[8000].pid == 4321
    assert by_port[500].protocol == "udp"
    assert by_port[500].pid == 1234


def test_collect_processes_linux_uses_ps() -> None:
    seen: list[list[str]] = []

    def runner(argv: list[str]) -> str:
        seen.append(argv)
        return PS_OUTPUT

    processes = collect_processes(runner=runner, system="Linux")
    assert seen == [["ps", "-axo", "pid=,ppid=,rss=,comm=,args="]]
    assert len(processes) == 3


def test_collect_processes_macos_uses_ps() -> None:
    processes = collect_processes(runner=lambda argv: PS_OUTPUT, system="Darwin")
    assert processes[0].pid == 1


def test_collect_processes_windows_uses_tasklist() -> None:
    processes = collect_processes(runner=lambda argv: TASKLIST_OUTPUT, system="Windows")
    assert processes[1].name == "python.exe"


def test_collect_processes_redacts_command_lines() -> None:
    processes = collect_processes(runner=lambda argv: PS_OUTPUT, system="Linux")
    api = processes[1]
    assert "sk-abcdefghijklmnopqrstuvwx" not in api.command
    assert "<redacted>" in api.command


def test_collect_processes_unsupported_platform() -> None:
    with pytest.raises(SystemInspectError, match="not supported"):
        collect_processes(runner=lambda argv: "", system="Plan9")


def test_collect_listeners_linux_uses_ss() -> None:
    seen: list[list[str]] = []

    def runner(argv: list[str]) -> str:
        seen.append(argv)
        return SS_OUTPUT

    listeners = collect_listeners(runner=runner, system="Linux")
    assert seen == [["ss", "-tulnH"]]
    assert {listener.port for listener in listeners} == {8000, 53, 8080}


def test_collect_listeners_macos_uses_netstat() -> None:
    listeners = collect_listeners(
        runner=lambda argv: NETSTAT_MACOS_OUTPUT, system="Darwin"
    )
    assert {listener.port for listener in listeners} == {8000, 8080, 5353}


def test_collect_listeners_windows_uses_netstat() -> None:
    listeners = collect_listeners(
        runner=lambda argv: NETSTAT_WINDOWS_OUTPUT, system="Windows"
    )
    assert {listener.port for listener in listeners} == {8000, 500}


def test_collect_listeners_unsupported_platform() -> None:
    with pytest.raises(SystemInspectError, match="not supported"):
        collect_listeners(runner=lambda argv: "", system="Plan9")


def test_collect_propagates_runner_failure() -> None:
    def runner(argv: list[str]) -> str:
        raise SystemInspectError("'ss' is not installed on this system")

    with pytest.raises(SystemInspectError, match="not installed"):
        collect_listeners(runner=runner, system="Linux")


def test_render_processes() -> None:
    rows = [
        ProcessInfo(
            pid=4321,
            ppid=1,
            rss_bytes=512 * 1024 * 1024,
            name="python",
            command="python app.py",
        )
    ]
    text = render_processes(rows, total=60)
    assert "4321" in text
    assert "512.0MB" in text
    assert "python app.py" in text
    assert "59 more" in text


def test_render_listeners() -> None:
    rows = [PortListener(protocol="tcp", address="127.0.0.1", port=8000, pid=4321)]
    assert "tcp" in render_listeners(rows)
    assert "127.0.0.1:8000" in render_listeners(rows)
    assert "pid 4321" in render_listeners(rows)
    assert render_listeners([]) == "no listening ports found"
