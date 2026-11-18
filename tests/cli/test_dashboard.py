"""Tests for the fcc-status dashboard command."""

import io
import json
from collections.abc import Iterator
from typing import Any

import pytest

from free_claude_code.cli import dashboard, usage_ledger


@pytest.fixture(autouse=True)
def _clean_ledger() -> Iterator[None]:
    usage_ledger.reset()
    yield
    usage_ledger.reset()


def _status() -> dict[str, Any]:
    return {
        "status": "running",
        "host": "127.0.0.1",
        "port": 8182,
        "model": "nvidia_nim/test-model",
        "admission": {
            "nvidia_nim": {
                "rate_limit": 60,
                "rate_window": 60,
                "max_concurrency": 4,
                "active_attempts": 1,
                "admitted_in_window": 3,
                "recovery": {"active": False},
                "usage": {
                    "calls_total": 12,
                    "errors_total": 1,
                    "rate_limit_hits": 2,
                },
            }
        },
        "usage": {"input_tokens": {"nvidia_nim": 5000}},
    }


def test_render_shows_state_model_and_admission() -> None:
    text = dashboard.render(_status(), {"providers": {}})

    assert "Proxy: running at http://127.0.0.1:8182" in text
    assert "Model: nvidia_nim/test-model" in text
    assert "nvidia_nim: 3/60 in 60s" in text
    assert "1/4 in flight" in text
    assert "12 calls" in text
    assert "2 rate-limit hits" in text


def test_render_shows_recovery_state() -> None:
    status = _status()
    status["admission"]["nvidia_nim"]["recovery"] = {"active": True}

    text = dashboard.render(status, {"providers": {}})

    assert "recovering" in text


def test_render_shows_today_ledger() -> None:
    ledger = {
        "providers": {
            "nvidia_nim": {"calls": 40, "rate_limit_hits": 3, "input_tokens": 900}
        }
    }

    text = dashboard.render(_status(), ledger)

    assert "Today (persisted)" in text
    assert "40 calls" in text
    assert "3 rate-limit hits" in text
    assert "900 input tokens" in text


def test_render_tolerates_empty_status() -> None:
    text = dashboard.render({}, {"providers": {}})

    assert "Proxy: unknown" in text
    assert "(no admission data yet)" in text
    assert "(no usage recorded yet)" in text


def test_fetch_status_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"status": "running"}).encode()

    class Response(io.BytesIO):
        status = 200

    from free_claude_code.cli import local_http

    monkeypatch.setattr(
        local_http._DIRECT_OPENER,
        "open",
        lambda request, *, timeout: Response(payload),
    )

    assert dashboard.fetch_status("http://127.0.0.1:8182") == {"status": "running"}


def test_fetch_status_returns_none_when_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from urllib.error import URLError

    from free_claude_code.cli import local_http

    def refuse(request, *, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr(local_http._DIRECT_OPENER, "open", refuse)

    assert dashboard.fetch_status("http://127.0.0.1:8182") is None


def _patch_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "free_claude_code.config.server_urls.local_proxy_root_url",
        lambda _settings: "http://127.0.0.1:8182",
    )
    monkeypatch.setattr(
        "free_claude_code.config.server_urls.local_admin_url",
        lambda _settings: "http://127.0.0.1:8182/admin",
    )


def test_main_json_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(dashboard, "fetch_status", lambda _url: _status())
    _patch_urls(monkeypatch)

    dashboard.main(["--json"])

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "running"


def test_main_human_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(dashboard, "fetch_status", lambda _url: _status())
    _patch_urls(monkeypatch)

    dashboard.main([])

    out = capsys.readouterr().out
    assert "Proxy: running" in out
    assert "Admin UI: http://127.0.0.1:8182/admin" in out


def test_main_offline_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(dashboard, "fetch_status", lambda _url: None)
    _patch_urls(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        dashboard.main([])

    assert exc.value.code == 1
    assert "fcc-server" in capsys.readouterr().err
