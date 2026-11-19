"""Read-only process and listening-port inspection (roadmap sections 3 and 4).

``fcc-inspect processes`` and ``fcc-inspect ports`` answer "what is running
on this machine" and "what is accepting connections" without leaving the
terminal. Each collector shells out to the platform's own read-only tool
(``ps``, ``tasklist``, ``ss`` or ``netstat``) through an injectable runner,
so the parsers stay pure and tests stay hermetic. Process command lines
routinely carry tokens and passwords, so they pass through the default
redactor before leaving this module. A missing tool degrades to a clear
:class:`SystemInspectError` naming the tool and the platform.
"""

import csv
import platform
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from free_claude_code.terminal.redaction import default_redactor

_PROBE_TIMEOUT = 5.0

#: Runs one read-only inspection command and returns its stdout.
CommandRunner = Callable[[list[str]], str]


class SystemInspectError(RuntimeError):
    """The platform inspection tool is missing or failed."""


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    """One running process. ``ppid``/``rss_bytes`` are None where unknown."""

    pid: int
    ppid: int | None
    rss_bytes: int | None
    name: str
    command: str


@dataclass(frozen=True, slots=True)
class PortListener:
    """One listening socket. ``pid`` is None where the tool hides it."""

    protocol: str
    address: str
    port: int
    pid: int | None


def _default_runner(argv: list[str]) -> str:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        raise SystemInspectError(
            f"{argv[0]!r} is not installed on this system"
        ) from None
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemInspectError(f"{argv[0]!r} failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise SystemInspectError(f"{argv[0]!r} failed: {detail}")
    return result.stdout


def _split_host_port(local: str, separator: str) -> tuple[str, int] | None:
    """Split ``host:port`` (or ``host.port``) at the last separator."""

    address, found, port_text = local.rpartition(separator)
    if not found or not port_text.isdigit():
        return None
    return address, int(port_text)


def parse_ps(output: str) -> list[ProcessInfo]:
    """Parse ``ps -axo pid=,ppid=,rss=,comm=,args=`` output (Linux/macOS)."""

    processes: list[ProcessInfo] = []
    for line in output.splitlines():
        fields = line.split(None, 4)
        if len(fields) < 5:
            continue
        pid_text, ppid_text, rss_text, name, command = fields
        try:
            pid = int(pid_text)
            ppid = int(ppid_text)
            rss_kb = int(rss_text)
        except ValueError:
            continue
        processes.append(
            ProcessInfo(
                pid=pid,
                ppid=ppid,
                rss_bytes=rss_kb * 1024,
                name=name,
                command=command,
            )
        )
    return processes


def parse_tasklist(output: str) -> list[ProcessInfo]:
    """Parse ``tasklist /fo csv /nh`` output (Windows).

    ``tasklist`` exposes no parent pid or command line, so both degrade to
    None / the image name.
    """

    processes: list[ProcessInfo] = []
    for row in csv.reader(output.splitlines()):
        if len(row) < 5:
            continue
        name, pid_text = row[0], row[1]
        memory_text = "".join(ch for ch in row[4] if ch.isdigit())
        if not pid_text.isdigit():
            continue
        processes.append(
            ProcessInfo(
                pid=int(pid_text),
                ppid=None,
                rss_bytes=int(memory_text) * 1024 if memory_text else None,
                name=name,
                command=name,
            )
        )
    return processes


def parse_ss(output: str) -> list[PortListener]:
    """Parse ``ss -tulnH`` output (Linux)."""

    listeners: list[PortListener] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[0] not in ("tcp", "udp"):
            continue
        if fields[0] == "tcp" and fields[1] != "LISTEN":
            continue
        host_port = _split_host_port(fields[4], ":")
        if host_port is None:
            continue
        address, port = host_port
        listeners.append(
            PortListener(protocol=fields[0], address=address, port=port, pid=None)
        )
    return listeners


def parse_netstat_macos(output: str) -> list[PortListener]:
    """Parse ``netstat -an`` output (macOS): ``host.port`` and LISTEN state."""

    listeners: list[PortListener] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        proto = fields[0]
        if proto.startswith("tcp"):
            if len(fields) < 6 or fields[-1] != "LISTEN":
                continue
        elif not proto.startswith("udp"):
            continue
        host_port = _split_host_port(fields[3], ".")
        if host_port is None:
            continue
        address, port = host_port
        listeners.append(
            PortListener(
                protocol=proto.rstrip("46"), address=address, port=port, pid=None
            )
        )
    return listeners


def parse_netstat_windows(output: str) -> list[PortListener]:
    """Parse ``netstat -ano`` output (Windows): listeners carry a PID."""

    listeners: list[PortListener] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[0] not in ("TCP", "UDP"):
            continue
        if fields[0] == "TCP":
            if len(fields) < 5 or fields[3] != "LISTENING":
                continue
            pid_text = fields[4]
        else:
            pid_text = fields[3]
        host_port = _split_host_port(fields[1], ":")
        if host_port is None or not pid_text.isdigit():
            continue
        address, port = host_port
        listeners.append(
            PortListener(
                protocol=fields[0].lower(),
                address=address,
                port=port,
                pid=int(pid_text),
            )
        )
    return listeners


def collect_processes(
    *,
    runner: CommandRunner | None = None,
    system: str | None = None,
) -> list[ProcessInfo]:
    """Collect running processes; command lines are secret-redacted."""

    run = runner if runner is not None else _default_runner
    system = system if system is not None else platform.system()
    if system in ("Linux", "Darwin"):
        processes = parse_ps(run(["ps", "-axo", "pid=,ppid=,rss=,comm=,args="]))
    elif system == "Windows":
        processes = parse_tasklist(run(["tasklist", "/fo", "csv", "/nh"]))
    else:
        raise SystemInspectError(f"process inspection is not supported on {system}")
    redactor = default_redactor()
    return [
        replace(process, command=redactor.redact_text(process.command))
        for process in processes
    ]


def collect_listeners(
    *,
    runner: CommandRunner | None = None,
    system: str | None = None,
) -> list[PortListener]:
    """Collect listening TCP/UDP sockets."""

    run = runner if runner is not None else _default_runner
    system = system if system is not None else platform.system()
    if system == "Linux":
        return parse_ss(run(["ss", "-tulnH"]))
    if system == "Darwin":
        return parse_netstat_macos(run(["netstat", "-an"]))
    if system == "Windows":
        return parse_netstat_windows(run(["netstat", "-ano"]))
    raise SystemInspectError(f"port inspection is not supported on {system}")


def render_processes(processes: Sequence[ProcessInfo], *, total: int) -> str:
    """Human-readable process table."""

    lines = [f"{'PID':>7} {'PPID':>7} {'RSS':>9}  NAME  COMMAND"]
    for process in processes:
        rss = (
            f"{process.rss_bytes / (1024 * 1024):.1f}MB"
            if process.rss_bytes is not None
            else "-"
        )
        ppid = str(process.ppid) if process.ppid is not None else "-"
        lines.append(
            f"{process.pid:>7} {ppid:>7} {rss:>9}  {process.name}  {process.command}"
        )
    if total > len(processes):
        lines.append(f"... {total - len(processes)} more (raise --limit)")
    return "\n".join(lines)


def render_listeners(listeners: Sequence[PortListener]) -> str:
    """Human-readable listener table."""

    if not listeners:
        return "no listening ports found"
    lines = []
    for listener in listeners:
        suffix = f"  (pid {listener.pid})" if listener.pid is not None else ""
        lines.append(
            f"{listener.protocol:<4} {listener.address}:{listener.port}{suffix}"
        )
    return "\n".join(lines)


__all__ = [
    "CommandRunner",
    "PortListener",
    "ProcessInfo",
    "SystemInspectError",
    "collect_listeners",
    "collect_processes",
    "parse_netstat_macos",
    "parse_netstat_windows",
    "parse_ps",
    "parse_ss",
    "parse_tasklist",
    "render_listeners",
    "render_processes",
]
