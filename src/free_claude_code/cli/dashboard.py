"""Local usage and health dashboard for the FCC proxy (registered as ``fcc-status``).

Answers "is my proxy up, what is it serving, and how much of the free tier have
I used today?" from one command. It reads the same ``/admin/api/status``
document the Admin UI uses and folds it into the persisted daily ledger, so the
numbers survive server restarts.
"""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.request import Request

from free_claude_code.cli import usage_ledger
from free_claude_code.cli.local_http import open_local_request

STATUS_PATH = "/admin/api/status"
STATUS_TIMEOUT_SECONDS = 3.0


def fetch_status(proxy_root_url: str) -> dict[str, Any] | None:
    """Return the live status document, or None when the proxy is unreachable."""

    url = f"{proxy_root_url.rstrip('/')}{STATUS_PATH}"
    try:
        with open_local_request(
            Request(url, method="GET"), timeout=STATUS_TIMEOUT_SECONDS
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except OSError, ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def render(status: Mapping[str, Any], ledger: Mapping[str, Any]) -> str:
    """Render a human-readable summary of live and daily usage."""

    lines: list[str] = []
    state = _text(status.get("status")) or "unknown"
    host = _text(status.get("host")) or "127.0.0.1"
    port = _as_int(status.get("port"))
    lines.append(f"Proxy: {state} at http://{host}:{port}")
    lines.append(f"Model: {_text(status.get('model')) or '(unset)'}")
    lines.append("")

    lines.append("Live this session")
    admission = status.get("admission")
    if isinstance(admission, Mapping) and admission:
        for provider_id in sorted(admission):
            entry = admission[provider_id]
            if not isinstance(entry, Mapping):
                continue
            window = (
                f"{_as_int(entry.get('admitted_in_window'))}"
                f"/{_as_int(entry.get('rate_limit'))}"
                f" in {_as_int(entry.get('rate_window'))}s"
            )
            in_flight = (
                f"{_as_int(entry.get('active_attempts'))}"
                f"/{_as_int(entry.get('max_concurrency'))} in flight"
            )
            usage = entry.get("usage") if isinstance(entry, Mapping) else None
            calls = (
                _as_int(usage.get("calls_total")) if isinstance(usage, Mapping) else 0
            )
            errors = (
                _as_int(usage.get("errors_total")) if isinstance(usage, Mapping) else 0
            )
            rate_limited = (
                _as_int(usage.get("rate_limit_hits"))
                if isinstance(usage, Mapping)
                else 0
            )
            recovery = entry.get("recovery") if isinstance(entry, Mapping) else None
            recovering = (
                isinstance(recovery, Mapping) and recovery.get("active") is True
            )
            parts = [
                f"{provider_id}: {window}",
                in_flight,
                f"{calls} calls",
                f"{errors} errors",
                f"{rate_limited} rate-limit hits",
            ]
            if recovering:
                parts.append("recovering")
            lines.append("  " + ", ".join(parts))
    else:
        lines.append("  (no admission data yet)")

    lines.append("")
    lines.append("Today (persisted)")
    providers = ledger.get("providers") if isinstance(ledger, Mapping) else None
    if isinstance(providers, Mapping) and providers:
        for provider_id in sorted(providers):
            entry = providers[provider_id]
            if not isinstance(entry, Mapping):
                continue
            lines.append(
                "  " + f"{provider_id}: {_as_int(entry.get('calls'))} calls, "
                f"{_as_int(entry.get('rate_limit_hits'))} rate-limit hits, "
                f"{_as_int(entry.get('input_tokens'))} input tokens"
            )
    else:
        lines.append("  (no usage recorded yet)")

    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point for the ``fcc-status`` command."""

    from free_claude_code.config.loader import get_settings
    from free_claude_code.config.server_urls import (
        local_admin_url,
        local_proxy_root_url,
    )

    parser = argparse.ArgumentParser(
        prog="fcc-status",
        description="Show proxy health and per-provider usage.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the raw status document as JSON."
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    proxy_root_url = local_proxy_root_url(settings)
    status = fetch_status(proxy_root_url)
    if status is None:
        print(
            f"Proxy is not reachable at {proxy_root_url}.\nStart it with: fcc-server",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
        return
    ledger = usage_ledger.accumulate(status)
    print(render(status, ledger))
    print("")
    print(f"Admin UI: {local_admin_url(settings)}")


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


__all__ = ["fetch_status", "main", "render"]
