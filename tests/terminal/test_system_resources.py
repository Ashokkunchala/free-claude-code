"""CPU, memory and disk snapshot collectors (roadmap section 4)."""

import pytest

from free_claude_code.terminal.system_inspect import (
    CpuInfo,
    DiskInfo,
    MemoryInfo,
    SystemInspectError,
    collect_cpu,
    collect_disk,
    collect_memory,
    parse_df_kp,
    parse_meminfo,
    parse_vm_stat,
    parse_wmic_os_memory,
    render_resources,
)

DF_OUTPUT = """Filesystem                        1024-blocks      Used Available Capacity Mounted on
/dev/disk3s1s1                      488245288   9598904 308021424     4%    /
devfs                                    2283      2283         0   100%    /dev
/dev/disk3s6                        488245288  10485760 308021424     4%    /System/Volumes/Data
"""

MEMINFO = """MemTotal:       16384000 kB
MemFree:         1024000 kB
MemAvailable:    8192000 kB
Buffers:          512000 kB
"""

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               100000.
Pages active:                             200000.
Pages inactive:                           150000.
Pages speculative:                         50000.
"""

WMIC_OS = """FreePhysicalMemory  TotalVisibleMemorySize  \r
4194304             8388608                 \r
"""


def test_parse_df_kp_reads_capacity() -> None:
    disks = parse_df_kp(DF_OUTPUT)
    assert len(disks) == 3
    root = disks[0]
    assert root.mount == "/"
    assert root.total_bytes == 488245288 * 1024
    assert root.used_bytes == 9598904 * 1024
    assert root.available_bytes == 308021424 * 1024


def test_parse_df_kp_skips_malformed_lines() -> None:
    assert parse_df_kp("Filesystem\ngarbage line\n") == []


def test_parse_meminfo_prefers_memavailable() -> None:
    memory = parse_meminfo(MEMINFO)
    assert memory.total_bytes == 16384000 * 1024
    assert memory.available_bytes == 8192000 * 1024


def test_parse_meminfo_falls_back_to_memfree() -> None:
    memory = parse_meminfo("MemTotal: 1000 kB\nMemFree: 250 kB\n")
    assert memory.available_bytes == 250 * 1024


def test_parse_meminfo_requires_total() -> None:
    with pytest.raises(SystemInspectError):
        parse_meminfo("MemFree: 1 kB\n")


def test_parse_vm_stat_sums_available_pages() -> None:
    memory = parse_vm_stat(VM_STAT, total_bytes=16 * 1024**3)
    assert memory.total_bytes == 16 * 1024**3
    assert memory.available_bytes == (100000 + 150000 + 50000) * 16384


def test_parse_wmic_os_memory_reads_kb_fields() -> None:
    memory = parse_wmic_os_memory(WMIC_OS)
    assert memory.total_bytes == 8388608 * 1024
    assert memory.available_bytes == 4194304 * 1024


def test_parse_wmic_os_memory_rejects_garbage() -> None:
    with pytest.raises(SystemInspectError):
        parse_wmic_os_memory("No Instance(s) Available.\n")


def test_collect_disk_posix_uses_df() -> None:
    disks = collect_disk(runner=lambda argv: DF_OUTPUT, system="Linux")
    assert [disk.mount for disk in disks] == ["/", "/dev", "/System/Volumes/Data"]


def test_collect_disk_windows_walks_drive_letters() -> None:
    disks = collect_disk(
        system="Windows",
        exists_fn=lambda path: path in ("C:\\", "D:\\"),
        usage_fn=lambda path: (100, 40, 60) if path == "C:\\" else (200, 50, 150),
    )
    assert disks == [
        DiskInfo(mount="C:\\", total_bytes=100, used_bytes=40, available_bytes=60),
        DiskInfo(mount="D:\\", total_bytes=200, used_bytes=50, available_bytes=150),
    ]


def test_collect_disk_unknown_platform_errors() -> None:
    with pytest.raises(SystemInspectError):
        collect_disk(system="Plan9")


def test_collect_memory_linux_reads_meminfo() -> None:
    memory = collect_memory(system="Linux", meminfo_reader=lambda: MEMINFO)
    assert memory.total_bytes == 16384000 * 1024


def test_collect_memory_macos_combines_sysctl_and_vm_stat() -> None:
    outputs = {"sysctl": str(16 * 1024**3), "vm_stat": VM_STAT}
    memory = collect_memory(runner=lambda argv: outputs[argv[0]], system="Darwin")
    assert memory.available_bytes == 300000 * 16384


def test_collect_memory_windows_uses_wmic() -> None:
    memory = collect_memory(runner=lambda argv: WMIC_OS, system="Windows")
    assert memory.total_bytes == 8388608 * 1024


def test_collect_memory_missing_tool_raises() -> None:
    def runner(argv: list[str]) -> str:
        raise SystemInspectError("'vm_stat' is not installed on this system")

    with pytest.raises(SystemInspectError):
        collect_memory(runner=runner, system="Darwin")


def test_collect_cpu_reports_cores_and_loads() -> None:
    cpu = collect_cpu(cores_fn=lambda: 8, load_fn=lambda: (1.5, 1.0, 0.5))
    assert cpu == CpuInfo(logical_cores=8, load_1m=1.5, load_5m=1.0, load_15m=0.5)


def test_collect_cpu_degrades_without_loadavg() -> None:
    def no_load() -> tuple[float, float, float]:
        raise OSError("unsupported")

    cpu = collect_cpu(cores_fn=lambda: 4, load_fn=no_load)
    assert cpu.logical_cores == 4
    assert cpu.load_1m is None


def test_render_resources_lists_each_section() -> None:
    text = render_resources(
        CpuInfo(logical_cores=8, load_1m=1.0, load_5m=0.5, load_15m=0.25),
        MemoryInfo(total_bytes=16 * 1024**3, available_bytes=8 * 1024**3),
        [
            DiskInfo(
                mount="/",
                total_bytes=512 * 1024**3,
                used_bytes=256 * 1024**3,
                available_bytes=256 * 1024**3,
            )
        ],
    )
    assert "8 cores" in text
    assert "load 1.00 0.50 0.25" in text
    assert "Memory: 8.0GB available of 16.0GB" in text
    assert "Disk /: 256.0GB free of 512.0GB" in text


def test_render_resources_degrades_per_section() -> None:
    text = render_resources(
        CpuInfo(logical_cores=4, load_1m=None, load_5m=None, load_15m=None),
        None,
        [],
        errors=["'wmic' is not installed on this system"],
    )
    assert "CPU: 4 cores" in text
    assert "load" not in text
    assert "unavailable: 'wmic' is not installed" in text


def test_render_resources_empty_shows_placeholder() -> None:
    assert render_resources(None, None, []) == "no resource data"
