"""Tests for the fcc-statusline renderer used by Claude Code's statusLine hook."""

import io
import json
from typing import Any

import pytest

from free_claude_code.cli import statusline, usage_ledger


@pytest.fixture(autouse=True)
def _clean_ledger() -> None:
    usage_ledger.reset()
    yield
    usage_ledger.reset()


def _status(**overrides: Any) -> dict[str, Any]:
    status: dict[str, Any] = {
        "status": "running",
        "model": "nvidia_nim/test-model",
        "provider": "nvidia_nim",
        "admission": {
            "nvidia_nim": {
                "provider_id": "nvidia_nim",
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
                    "recovery_episodes": 0,
                },
            }
        },
        "usage": {"input_tokens": {"nvidia_nim": 5000}},
    }
    status.update(overrides)
    return status


def test_render_includes_provider_model_and_window() -> None:
    line = statusline.render(_status(), {"providers": {}})

    assert "FCC" in line
    assert "nvidia_nim" in line
    assert "test-model" in line
    assert "win 3/60" in line


def test_render_includes_calls_and_rate_limit_hits() -> None:
    line = statusline.render(_status(), {"providers": {}})

    assert "calls 12" in line
    assert "429 \u00d72" in line


def test_render_marks_active_recovery() -> None:
    status = _status()
    status["admission"]["nvidia_nim"]["recovery"] = {"active": True}

    line = statusline.render(status, {"providers": {}})

    assert "recovering" in line


def test_render_includes_today_totals_from_ledger() -> None:
    seeded = _status()
    seeded["admission"]["nvidia_nim"]["usage"]["calls_total"] = 40
    statusline.render(seeded, {})

    line = statusline.render(seeded, {})

    assert "today 40" in line


def test_render_includes_cost_when_present() -> None:
    payload = {"cost": {"total_cost_usd": 0.42}}

    line = statusline.render(_status(), payload)

    assert "$0.42" in line


def test_render_includes_workspace_when_present() -> None:
    payload = {"workspace": {"current_dir": "/home/dev/my-project"}}

    line = statusline.render(_status(), payload)

    assert "my-project" in line


def test_render_accumulates_ledger_as_side_effect() -> None:
    statusline.render(_status(), {"providers": {}})

    ledger = usage_ledger.load()
    assert ledger["providers"]["nvidia_nim"]["calls"] == 12
    assert ledger["providers"]["nvidia_nim"]["input_tokens"] == 5000


def test_offline_line_mentions_fcc_server() -> None:
    line = statusline._offline_line({})

    assert "FCC offline" in line
    assert "fcc-server" in line


def test_main_never_raises_and_prints_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(statusline, "_fetch_status", lambda: None)
    monkeypatch.setattr(statusline, "_read_payload", lambda: {})

    with pytest.raises(SystemExit) as exc:
        statusline.main()

    assert exc.value.code == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def test_main_survives_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def explode() -> dict[str, Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr(statusline, "_fetch_status", explode)
    monkeypatch.setattr(
        statusline, "_read_payload", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    with pytest.raises(SystemExit) as exc:
        statusline.main()

    assert exc.value.code == 0
    assert "FCC offline" in capsys.readouterr().out


def test_fetch_status_parses_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps({"status": "running"}).encode()

    class Response(io.BytesIO):
        status = 200

    monkeypatch.setattr(
        statusline,
        "_proxy_root_url",
        lambda: "http://127.0.0.1:8182",
    )

    from free_claude_code.cli import local_http

    monkeypatch.setattr(
        local_http._DIRECT_OPENER,
        "open",
        lambda request, *, timeout: Response(payload),
    )

    assert statusline._fetch_status() == {"status": "running"}
