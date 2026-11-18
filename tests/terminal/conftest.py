"""Fake-executable helpers for terminal executor tests.

All fakes are the current Python interpreter running -c snippets, so tests
behave the same on Linux, macOS and Windows without shell dependencies.
"""

import sys

import pytest


def py(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


@pytest.fixture
def echo_stdout() -> tuple[str, ...]:
    return py("print('hello-terminal')")


@pytest.fixture
def echo_stderr() -> tuple[str, ...]:
    return py("import sys; print('oops-terminal', file=sys.stderr)")


@pytest.fixture
def exit_three() -> tuple[str, ...]:
    return py("raise SystemExit(3)")


@pytest.fixture
def hang_forever() -> tuple[str, ...]:
    return py("import time; time.sleep(3600)")


@pytest.fixture
def big_output() -> tuple[str, ...]:
    return py("import sys; sys.stdout.write('x' * 300000)")


@pytest.fixture
def spawn_child_then_hang() -> tuple[str, ...]:
    return py(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3600)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(3600)\n"
    )
