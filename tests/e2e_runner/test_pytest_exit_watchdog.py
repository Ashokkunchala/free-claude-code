"""The exit watchdog honors the suite verdict and kills a hung child."""

import io
import textwrap
import time
from pathlib import Path

import pytest

from e2e.pytest_exit_watchdog import run_pytest_with_watchdog

HANGING_CONFTEST = textwrap.dedent(
    """\
    import time

    import pytest


    @pytest.fixture(scope="session")
    def _hang_after_suite():
        yield
        time.sleep(600)
    """
)


def _write_suite(tmp_path: Path, body: str, *, hang: bool) -> None:
    (tmp_path / "test_fake.py").write_text(textwrap.dedent(body), encoding="utf-8")
    if hang:
        (tmp_path / "conftest.py").write_text(HANGING_CONFTEST, encoding="utf-8")


def _run(tmp_path: Path) -> tuple[int, str, float]:
    out = io.StringIO()
    start = time.monotonic()
    code = run_pytest_with_watchdog(
        ["test_fake.py", "-q", "-p", "no:cacheprovider"],
        grace_seconds=0.5,
        stream=out,
        cwd=str(tmp_path),
    )
    return code, out.getvalue(), time.monotonic() - start


def test_hung_child_after_green_suite_is_killed_and_reports_success(
    tmp_path: Path,
) -> None:
    _write_suite(tmp_path, "def test_ok():\n    assert True\n", hang=True)
    code, output, elapsed = _run(tmp_path)
    assert code == 0
    assert "1 passed" in output
    assert elapsed < 60


def test_hung_child_after_failed_suite_is_killed_and_reports_failure(
    tmp_path: Path,
) -> None:
    _write_suite(tmp_path, "def test_bad():\n    assert False\n", hang=True)
    code, output, elapsed = _run(tmp_path)
    assert code == 1
    assert "1 failed" in output
    assert elapsed < 60


def test_clean_green_child_exit_code_passes_through(tmp_path: Path) -> None:
    _write_suite(tmp_path, "def test_ok():\n    assert True\n", hang=False)
    code, _, elapsed = _run(tmp_path)
    assert code == 0
    assert elapsed < 10


def test_clean_failed_child_exit_code_passes_through(tmp_path: Path) -> None:
    _write_suite(tmp_path, "def test_bad():\n    assert False\n", hang=False)
    code, _, _ = _run(tmp_path)
    assert code == 1


@pytest.mark.parametrize(
    "line",
    [
        "== 1 passed in 0.01s ==",
        "=== 242 passed, 1 skipped in 2965.91s (0:49:25) ===",
        "= 1 failed, 2 passed, 3 skipped in 4.20s =",
    ],
)
def test_summary_line_detection(line: str) -> None:
    from e2e.pytest_exit_watchdog import _FAILED, _SUMMARY

    assert _SUMMARY.search(line)
    assert bool(_FAILED.search(line)) == ("failed" in line)
