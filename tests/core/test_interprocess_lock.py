"""InterprocessFileLock: contention behavior and handle hygiene."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from free_claude_code.core import interprocess_lock
from free_claude_code.core.interprocess_lock import InterprocessFileLock


def test_acquire_closes_the_handle_when_locking_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "chat.lock"
    opened: list = []
    real_open = Path.open

    def tracking_open(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        opened.append(handle)
        return handle

    def boom(_handle) -> bool:
        raise RuntimeError("locking blew up")

    monkeypatch.setattr(Path, "open", tracking_open)
    monkeypatch.setattr(interprocess_lock, "_try_lock", boom)
    lock = InterprocessFileLock(path)
    with pytest.raises(RuntimeError, match="blew up"):
        lock.acquire()

    assert opened
    assert all(handle.closed for handle in opened)

    # The failed attempt leaves the lock object reusable.
    monkeypatch.undo()
    assert lock.acquire(wait=False)
    lock.release()


def test_contended_acquisition_fails_fast_without_wait(tmp_path: Path) -> None:
    path = tmp_path / "config.lock"
    holder = InterprocessFileLock(path)
    assert holder.acquire(wait=False)
    contender = InterprocessFileLock(path)
    try:
        assert not contender.acquire(wait=False)
    finally:
        holder.release()


def test_waiting_acquisition_succeeds_after_the_holder_releases(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.lock"
    holder = InterprocessFileLock(path)
    assert holder.acquire(wait=False)
    contender = InterprocessFileLock(path)
    acquired = threading.Event()

    def contend() -> None:
        if contender.acquire(wait=True, timeout=5):
            acquired.set()

    worker = threading.Thread(target=contend)
    worker.start()
    try:
        holder.release()
        worker.join(10)
        assert acquired.is_set()
    finally:
        worker.join(10)
        contender.release()


def test_concurrent_acquirers_all_complete(tmp_path: Path) -> None:
    path = tmp_path / "config.lock"

    def round_trip(_index: int) -> bool:
        with InterprocessFileLock(path):
            return True

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(round_trip, range(8)))


@pytest.mark.skipif(os.name != "nt", reason="msvcrt locking semantics")
def test_windows_try_lock_treats_contention_as_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import msvcrt

    path = tmp_path / "chat.lock"
    path.write_bytes(b"\0")
    handle = path.open("a+b")

    def denied(_fileno, _mode, _nbytes) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(msvcrt, "locking", denied)
    try:
        assert interprocess_lock._try_lock(cast(BinaryIO, handle)) is False
    finally:
        handle.close()


@pytest.mark.skipif(os.name != "nt", reason="msvcrt locking semantics")
def test_windows_try_lock_tolerates_the_sentinel_initialization_race(
    tmp_path: Path,
) -> None:
    # Another holder locked the region before this handle wrote the sentinel
    # byte: on Windows the write itself is denied, and that contention must
    # read as "lock unavailable", not crash the caller.
    class RacyHandle:
        def __init__(self, path: Path) -> None:
            self._file = path.open("a+b")

        def fileno(self) -> int:
            return self._file.fileno()

        def seek(self, *_args) -> None:
            pass

        def write(self, *_args) -> None:
            raise PermissionError(13, "Permission denied")

        def flush(self) -> None:
            pass

        def close(self) -> None:
            self._file.close()

    handle = RacyHandle(tmp_path / "chat.lock")
    try:
        assert interprocess_lock._try_lock(cast(BinaryIO, handle)) is False
    finally:
        handle.close()
