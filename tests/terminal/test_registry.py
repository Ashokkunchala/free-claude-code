"""Registry tests: the PID net migrated from cli.process_registry."""

import os
import subprocess
import sys

import pytest

from free_claude_code.terminal import processes


def test_register_unregister_roundtrip() -> None:
    before = len(processes._registry_pids)
    processes.register_pid(0)
    assert len(processes._registry_pids) == before
    processes.register_pid(424242)
    assert 424242 in processes._registry_pids
    processes.unregister_pid(424242)
    assert 424242 not in processes._registry_pids
    processes.unregister_pid(0)  # no-op, no raise


def test_ensure_atexit_idempotent() -> None:
    processes.ensure_atexit_registered()
    processes.ensure_atexit_registered()


def test_kill_all_empty_is_noop() -> None:
    processes.kill_all_best_effort()


@pytest.mark.skipif(
    os.name == "nt", reason="os.getpgid is POSIX-only; Windows uses taskkill"
)
def test_kill_all_survives_os_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(processes, "_registry_pids", {99999})

    def _kill_raises(pid: int, sig: int) -> None:
        raise ProcessLookupError("no such process")

    monkeypatch.setattr(os, "kill", _kill_raises)
    monkeypatch.setattr(
        os, "getpgid", lambda pid: (_ for _ in ()).throw(ProcessLookupError())
    )
    processes.kill_all_best_effort()  # logs, does not raise


def test_shim_aliases_live_set() -> None:
    from free_claude_code.cli import process_registry as pr

    assert pr._pids is processes._registry_pids
    pr.register_pid(31337)
    assert 31337 in processes._registry_pids
    pr.unregister_pid(31337)


@pytest.mark.skipif(os.name == "nt", reason="process-group semantics are POSIX-only")
def test_kill_pid_tree_kills_group_leader_tree() -> None:
    # A group leader whose child ignores SIGTERM to the single pid: only the
    # group signal reaches it.
    leader = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print(os.getpid(), flush=True)\n"
            "time.sleep(60)\n",
        ],
        stdout=subprocess.PIPE,
        start_new_session=True,
    )
    assert leader.stdout is not None
    pid = int(leader.stdout.readline().strip())
    processes.register_pid(pid)
    processes.kill_pid_tree_best_effort(pid)
    try:
        leader.wait(timeout=10)
    except subprocess.TimeoutExpired:
        raise AssertionError(f"group leader {pid} survived tree kill") from None
    finally:
        leader.stdout.close()
    processes.unregister_pid(pid)
