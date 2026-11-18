"""Render a compact FCC status line for Claude Code's ``statusLine`` hook.

Claude Code invokes the configured command with a JSON status payload on stdin
and renders its stdout as one line. This command answers the question "which
free provider is serving me, and how much of its window have I used?" without
touching the user's global configuration.
"""

import json
import os
import sys
from collections.abc import Mapping
from urllib.request import Request

from free_claude_code.cli import usage_ledger
from free_claude_code.cli.local_http import open_local_request
from free_claude_code.core.json_types import JsonObject

STATUS_PATH = "/admin/api/status"
STATUS_TIMEOUT_SECONDS = 1.2

_DIM = "\033[2m"
_RESET = "\033[0m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"


def main() -> None:
    """Print one status line; never raise into Claude Code's render loop."""

    try:
        payload = _read_payload()
        status = _fetch_status()
        line = render(status, payload) if status is not None else _offline_line(payload)
    except BaseException:
        line = f"{_DIM}FCC offline{_RESET}"
    print(line)
    raise SystemExit(0)


def render(status: Mapping[str, object], payload: Mapping[str, object]) -> str:
    """Project the proxy status and Claude payload into one ANSI line."""

    ledger = usage_ledger.accumulate(status)
    provider = _text(status.get("provider")) or "fcc"
    model = _short_model(_text(status.get("model")))
    parts = [f"{_GREEN}FCC{_RESET} {provider} \u203a {model}"]

    entry = _admission_entry(status, provider)
    if entry is not None:
        window = f"{_as_int(entry.get('admitted_in_window'))}/{_as_int(entry.get('rate_limit'))}"
        parts.append(f"{_DIM}win {window}{_RESET}")
        usage = _mapping(entry.get("usage"))
        if usage is not None:
            parts.append(f"{_DIM}calls {_as_int(usage.get('calls_total'))}{_RESET}")
            rate_limited = _as_int(usage.get("rate_limit_hits"))
            if rate_limited:
                parts.append(f"{_YELLOW}429 \u00d7{rate_limited}{_RESET}")
        recovery = _mapping(entry.get("recovery"))
        if recovery is not None and recovery.get("active") is True:
            parts.append(f"{_RED}recovering{_RESET}")

    today = _today_calls(ledger, provider)
    if today:
        parts.append(f"{_DIM}today {today}{_RESET}")

    cost = _cost_from_payload(payload)
    if cost is not None and cost > 0:
        parts.append(f"{_DIM}${cost:.2f}{_RESET}")

    workspace = _workspace_from_payload(payload)
    if workspace:
        parts.append(f"{_DIM}{workspace}{_RESET}")

    return " \u00b7 ".join(parts)


def _fetch_status() -> JsonObject | None:
    base = _proxy_root_url()
    if base is None:
        return None
    url = f"{base.rstrip('/')}{STATUS_PATH}"
    try:
        with open_local_request(
            Request(url, method="GET"), timeout=STATUS_TIMEOUT_SECONDS
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except OSError, ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _proxy_root_url() -> str | None:
    base = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    if base:
        return base
    try:
        from free_claude_code.config.loader import get_settings
        from free_claude_code.config.server_urls import local_proxy_root_url

        return local_proxy_root_url(get_settings())
    except Exception:
        return None


def _offline_line(payload: Mapping[str, object]) -> str:
    model = _short_model(_model_from_payload(payload))
    return f"{_RED}FCC offline{_RESET} {_DIM}(run fcc-server){_RESET} {_DIM}{model}{_RESET}"


def _read_payload() -> JsonObject:
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _admission_entry(
    status: Mapping[str, object], provider: str
) -> Mapping[str, object] | None:
    admission = _mapping(status.get("admission"))
    if admission is None:
        return None
    return _mapping(admission.get(provider))


def _model_from_payload(payload: Mapping[str, object]) -> str:
    model = payload.get("model")
    if isinstance(model, Mapping):
        return _text(model.get("display_name")) or _text(model.get("id"))
    return _text(model)


def _short_model(model: str) -> str:
    return model.rsplit("/", 1)[-1] if model else "no model"


def _today_calls(ledger: Mapping[str, object], provider: str) -> int:
    providers = _mapping(ledger.get("providers"))
    if providers is None:
        return 0
    entry = _mapping(providers.get(provider))
    return _as_int(entry.get("calls")) if entry is not None else 0


def _cost_from_payload(payload: Mapping[str, object]) -> float | None:
    cost = _mapping(payload.get("cost"))
    if cost is None:
        return None
    value = cost.get("total_cost_usd")
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _workspace_from_payload(payload: Mapping[str, object]) -> str:
    workspace = _mapping(payload.get("workspace"))
    if workspace is None:
        return ""
    directory = _text(workspace.get("current_dir")) or _text(
        workspace.get("project_dir")
    )
    return directory.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


__all__ = ["main", "render"]
