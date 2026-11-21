"""Read-only process and listening-port inspection (roadmap sections 3 and 4).

``fcc-inspect processes``, ``ports``, ``services`` and ``logs`` answer
"what is running on this machine", "what is accepting connections",
"which services are healthy" and "what went wrong lately" without leaving
the terminal. Each collector shells out to the platform's own read-only tool
(``ps``, ``tasklist``, ``ss`` or ``netstat``) through an injectable runner,
so the parsers stay pure and tests stay hermetic. Process command lines
routinely carry tokens and passwords, so they pass through the default
redactor before leaving this module. A missing tool degrades to a clear
:class:`SystemInspectError` naming the tool and the platform.
"""

import csv
import os
import platform
import re
import shutil
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


@dataclass(frozen=True, slots=True)
class ServiceInfo:
    """One system service with a normalized state string."""

    name: str
    state: str
    description: str | None


@dataclass(frozen=True, slots=True)
class DiskInfo:
    """One mounted filesystem's capacity."""

    mount: str
    total_bytes: int
    used_bytes: int
    available_bytes: int


@dataclass(frozen=True, slots=True)
class MemoryInfo:
    """Physical memory total and what is currently available."""

    total_bytes: int
    available_bytes: int


@dataclass(frozen=True, slots=True)
class CpuInfo:
    """Logical core count and load averages (None where the OS hides them)."""

    logical_cores: int | None
    load_1m: float | None
    load_5m: float | None
    load_15m: float | None


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


def parse_systemctl(output: str) -> list[ServiceInfo]:
    """Parse ``systemctl list-units --type=service --all --no-legend --plain``."""

    services: list[ServiceInfo] = []
    for line in output.splitlines():
        fields = line.split(None, 4)
        if len(fields) < 4:
            continue
        name, _load, active, sub = fields[:4]
        description = fields[4] if len(fields) > 4 else None
        services.append(
            ServiceInfo(name=name, state=f"{active}/{sub}", description=description)
        )
    return services


def parse_launchctl(output: str) -> list[ServiceInfo]:
    """Parse ``launchctl list`` output (macOS): ``pid\\tstatus\\tlabel``."""

    services: list[ServiceInfo] = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) < 3 or fields[0] == "PID":
            continue
        pid_text, status_text, label = fields[0], fields[1], fields[2]
        if pid_text != "-" and pid_text.isdigit():
            state = "running"
        elif status_text.lstrip("-").isdigit():
            state = f"stopped (exit {status_text})"
        else:
            state = "stopped"
        services.append(ServiceInfo(name=label, state=state, description=None))
    return services


def parse_sc_query(output: str) -> list[ServiceInfo]:
    """Parse ``sc query type= service state= all`` output (Windows)."""

    services: list[ServiceInfo] = []
    name: str | None = None
    display: str | None = None
    state: str | None = None
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("SERVICE_NAME:"):
            if name is not None:
                services.append(
                    ServiceInfo(
                        name=name, state=state or "unknown", description=display
                    )
                )
            name = stripped.split(":", 1)[1].strip()
            display = None
            state = None
        elif stripped.startswith("DISPLAY_NAME:"):
            display = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("STATE"):
            tail = stripped.split(":", 1)[1].split()
            if len(tail) >= 2:
                state = tail[1].lower()
    if name is not None:
        services.append(
            ServiceInfo(name=name, state=state or "unknown", description=display)
        )
    return services


def collect_services(
    *,
    runner: CommandRunner | None = None,
    system: str | None = None,
) -> list[ServiceInfo]:
    """Collect system services with normalized states."""

    run = runner if runner is not None else _default_runner
    system = system if system is not None else platform.system()
    if system == "Linux":
        return parse_systemctl(
            run(
                [
                    "systemctl",
                    "list-units",
                    "--type=service",
                    "--all",
                    "--no-pager",
                    "--no-legend",
                    "--plain",
                ]
            )
        )
    if system == "Darwin":
        return parse_launchctl(run(["launchctl", "list"]))
    if system == "Windows":
        return parse_sc_query(run(["sc", "query", "type=", "service", "state=", "all"]))
    raise SystemInspectError(f"service inspection is not supported on {system}")


def collect_error_logs(
    *,
    runner: CommandRunner | None = None,
    system: str | None = None,
    lines: int = 50,
) -> list[str]:
    """Collect the newest system error/warning log lines, secret-redacted.

    Linux reads the systemd journal (warnings and above); Windows reads the
    System event log. macOS has no bounded equivalent here, so it degrades
    to a clear error.
    """

    run = runner if runner is not None else _default_runner
    system = system if system is not None else platform.system()
    if system == "Linux":
        output = run(["journalctl", "-n", str(lines), "-p", "warning", "--no-pager"])
    elif system == "Windows":
        output = run(["wevtutil", "qe", "System", f"/c:{lines}", "/rd:true", "/f:text"])
    else:
        raise SystemInspectError(f"log inspection is not supported on {system}")
    redactor = default_redactor()
    return [
        redactor.redact_text(line)
        for line in output.splitlines()
        if line.strip() and not line.startswith("-- Logs begin")
    ]


def _default_disk_usage(path: str) -> tuple[int, int, int]:
    usage = shutil.disk_usage(path)
    return (usage.total, usage.used, usage.free)


def parse_df_kp(output: str) -> list[DiskInfo]:
    """Parse ``df -kP`` output (POSIX; one line per filesystem)."""

    disks: list[DiskInfo] = []
    for line in output.splitlines()[1:]:
        fields = line.split(None, 5)
        if len(fields) < 6:
            continue
        _fs, blocks_text, used_text, avail_text, _capacity, mount = fields
        try:
            total = int(blocks_text) * 1024
            used = int(used_text) * 1024
            available = int(avail_text) * 1024
        except ValueError:
            continue
        disks.append(
            DiskInfo(
                mount=mount.strip(),
                total_bytes=total,
                used_bytes=used,
                available_bytes=available,
            )
        )
    return disks


def parse_meminfo(text: str) -> MemoryInfo:
    """Parse Linux ``/proc/meminfo``; MemAvailable is the available figure."""

    values: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.strip().split()
        if parts and parts[0].isdigit():
            values[name] = int(parts[0]) * 1024
    if "MemTotal" not in values:
        raise SystemInspectError("/proc/meminfo did not contain MemTotal")
    return MemoryInfo(
        total_bytes=values["MemTotal"],
        available_bytes=values.get("MemAvailable", values.get("MemFree", 0)),
    )


def parse_vm_stat(output: str, *, total_bytes: int) -> MemoryInfo:
    """Parse macOS ``vm_stat``; free+inactive+speculative pages are available."""

    page_size = 4096
    pages: dict[str, int] = {}
    for line in output.splitlines():
        if "page size of" in line:
            match = re.search(r"page size of (\d+) bytes", line)
            if match:
                page_size = int(match.group(1))
            continue
        name, _, rest = line.partition(":")
        number = rest.strip().rstrip(".")
        if number.isdigit():
            pages[name] = int(number)
    available_pages = (
        pages.get("Pages free", 0)
        + pages.get("Pages inactive", 0)
        + pages.get("Pages speculative", 0)
    )
    return MemoryInfo(
        total_bytes=total_bytes, available_bytes=available_pages * page_size
    )


def parse_wmic_os_memory(output: str) -> MemoryInfo:
    """Parse ``wmic OS get FreePhysicalMemory,TotalVisibleMemorySize`` (kB)."""

    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 2 and all(field.isdigit() for field in fields):
            free_kb, total_kb = int(fields[0]), int(fields[1])
            return MemoryInfo(
                total_bytes=total_kb * 1024, available_bytes=free_kb * 1024
            )
    raise SystemInspectError("could not parse wmic memory output")


def collect_disk(
    *,
    runner: CommandRunner | None = None,
    system: str | None = None,
    exists_fn: Callable[[str], bool] | None = None,
    usage_fn: Callable[[str], tuple[int, int, int]] | None = None,
) -> list[DiskInfo]:
    """Collect mounted filesystem capacity.

    POSIX reads ``df -kP``; Windows probes each fixed drive letter with
    ``shutil.disk_usage``. Both degrade to :class:`SystemInspectError` when
    the platform tool is missing.
    """

    run = runner if runner is not None else _default_runner
    system = system if system is not None else platform.system()
    if system in ("Linux", "Darwin"):
        return parse_df_kp(run(["df", "-kP"]))
    if system == "Windows":
        exists = exists_fn if exists_fn is not None else os.path.exists
        usage = usage_fn if usage_fn is not None else _default_disk_usage
        disks = []
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZAB":
            root = f"{letter}:\\"
            if not exists(root):
                continue
            total, used, free = usage(root)
            disks.append(
                DiskInfo(
                    mount=root, total_bytes=total, used_bytes=used, available_bytes=free
                )
            )
        return disks
    raise SystemInspectError(f"disk inspection is not supported on {system}")


def _read_meminfo() -> str:
    with open("/proc/meminfo", encoding="utf-8") as handle:
        return handle.read()


def collect_memory(
    *,
    runner: CommandRunner | None = None,
    system: str | None = None,
    meminfo_reader: Callable[[], str] | None = None,
) -> MemoryInfo:
    """Collect physical memory total/available.

    Linux reads ``/proc/meminfo`` directly; macOS combines ``sysctl
    hw.memsize`` with ``vm_stat``; Windows asks ``wmic OS``.
    """

    run = runner if runner is not None else _default_runner
    system = system if system is not None else platform.system()
    if system == "Linux":
        reader = meminfo_reader if meminfo_reader is not None else _read_meminfo
        return parse_meminfo(reader())
    if system == "Darwin":
        total_text = run(["sysctl", "-n", "hw.memsize"]).strip()
        if not total_text.isdigit():
            raise SystemInspectError("sysctl hw.memsize returned no number")
        return parse_vm_stat(run(["vm_stat"]), total_bytes=int(total_text))
    if system == "Windows":
        return parse_wmic_os_memory(
            run(["wmic", "OS", "get", "FreePhysicalMemory,TotalVisibleMemorySize"])
        )
    raise SystemInspectError(f"memory inspection is not supported on {system}")


def collect_cpu(
    *,
    cores_fn: Callable[[], int | None] | None = None,
    load_fn: Callable[[], tuple[float, float, float]] | None = None,
) -> CpuInfo:
    """Collect logical core count and load averages via the stdlib.

    ``os.getloadavg`` raises OSError on Windows, so loads degrade to None
    there instead of failing the whole snapshot.
    """

    cores = cores_fn if cores_fn is not None else os.cpu_count
    load = load_fn if load_fn is not None else os.getloadavg
    try:
        load_1m, load_5m, load_15m = load()
    except OSError:
        load_1m = load_5m = load_15m = None
    return CpuInfo(
        logical_cores=cores(),
        load_1m=load_1m,
        load_5m=load_5m,
        load_15m=load_15m,
    )


def _render_bytes(count: int) -> str:
    gib = count / (1024**3)
    if gib >= 1:
        return f"{gib:.1f}GB"
    return f"{count / (1024**2):.0f}MB"


def render_resources(
    cpu: CpuInfo | None,
    memory: MemoryInfo | None,
    disks: Sequence[DiskInfo],
    *,
    errors: Sequence[str] = (),
) -> str:
    """Human-readable resource snapshot; sections degrade individually."""

    lines: list[str] = []
    if cpu is not None:
        cores = str(cpu.logical_cores) if cpu.logical_cores is not None else "?"
        if cpu.load_1m is not None:
            lines.append(
                f"CPU: {cores} cores, load {cpu.load_1m:.2f} {cpu.load_5m:.2f} {cpu.load_15m:.2f}"
            )
        else:
            lines.append(f"CPU: {cores} cores")
    if memory is not None:
        lines.append(
            f"Memory: {_render_bytes(memory.available_bytes)} available of {_render_bytes(memory.total_bytes)}"
        )
    lines.extend(
        f"Disk {disk.mount}: {_render_bytes(disk.available_bytes)} free of {_render_bytes(disk.total_bytes)}"
        for disk in disks
    )
    lines.extend(f"unavailable: {error}" for error in errors)
    return "\n".join(lines) if lines else "no resource data"


def render_services(services: Sequence[ServiceInfo], *, total: int) -> str:
    """Human-readable service table, failures first."""

    def rank(service: ServiceInfo) -> tuple[int, str]:
        if service.state.startswith("failed"):
            return (0, service.name)
        if service.state in ("running", "active/running", "active/exited"):
            return (1, service.name)
        return (2, service.name)

    ordered = sorted(services, key=rank)
    lines = []
    for service in ordered:
        description = f"  {service.description}" if service.description else ""
        lines.append(f"{service.state:<16} {service.name}{description}")
    if total > len(services):
        lines.append(f"... {total - len(services)} more (raise --limit)")
    return "\n".join(lines)


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
    "CpuInfo",
    "DiskInfo",
    "MemoryInfo",
    "PortListener",
    "ProcessInfo",
    "ServiceInfo",
    "SystemInspectError",
    "collect_cpu",
    "collect_disk",
    "collect_error_logs",
    "collect_listeners",
    "collect_memory",
    "collect_processes",
    "collect_services",
    "parse_df_kp",
    "parse_launchctl",
    "parse_meminfo",
    "parse_netstat_macos",
    "parse_netstat_windows",
    "parse_ps",
    "parse_sc_query",
    "parse_ss",
    "parse_systemctl",
    "parse_tasklist",
    "parse_vm_stat",
    "parse_wmic_os_memory",
    "render_listeners",
    "render_processes",
    "render_resources",
    "render_services",
]
