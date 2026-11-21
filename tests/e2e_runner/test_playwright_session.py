"""stop_with_timeout and session_playwright: bounded teardown semantics."""

import time
from collections.abc import Generator

import pytest

import e2e.playwright_session as playwright_session


class _FakePlaywright:
    def __init__(self, behavior: str) -> None:
        self.behavior = behavior
        self.stopped = False

    def stop(self) -> None:
        if self.behavior == "hang":
            time.sleep(30)
        elif self.behavior == "raise":
            raise RuntimeError("driver already gone")
        self.stopped = True


class _FakeSyncPlaywright:
    def __init__(self, pw: _FakePlaywright) -> None:
        self._pw = pw

    def start(self) -> _FakePlaywright:
        return self._pw


def test_stop_within_timeout_returns_true() -> None:
    pw = _FakePlaywright("fast")
    assert playwright_session.stop_with_timeout(pw, timeout=5.0) is True
    assert pw.stopped


def test_hung_stop_returns_false_without_blocking() -> None:
    pw = _FakePlaywright("hang")
    started = time.monotonic()
    assert playwright_session.stop_with_timeout(pw, timeout=0.05) is False
    assert time.monotonic() - started < 5.0
    assert not pw.stopped


def test_raising_stop_reports_and_returns_true(
    capsys: pytest.CaptureFixture[str],
) -> None:
    pw = _FakePlaywright("raise")
    assert playwright_session.stop_with_timeout(pw, timeout=5.0) is True
    assert "stop raised" in capsys.readouterr().out


def _drive(monkeypatch: pytest.MonkeyPatch, pw: _FakePlaywright) -> Generator[object]:
    monkeypatch.setattr(
        playwright_session, "sync_playwright", lambda: _FakeSyncPlaywright(pw)
    )
    return playwright_session.session_playwright()


def test_session_playwright_stops_on_teardown(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pw = _FakePlaywright("fast")
    it = _drive(monkeypatch, pw)
    assert next(it) is pw
    it.close()
    assert pw.stopped
    assert capsys.readouterr().out == ""


def test_session_playwright_warns_and_continues_on_hung_stop(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pw = _FakePlaywright("hang")
    monkeypatch.setattr(playwright_session, "STOP_TIMEOUT_SECONDS", 0.05)
    it = _drive(monkeypatch, pw)
    assert next(it) is pw
    started = time.monotonic()
    it.close()
    assert time.monotonic() - started < 5.0
    assert "abandoning the hung proactor" in capsys.readouterr().out
