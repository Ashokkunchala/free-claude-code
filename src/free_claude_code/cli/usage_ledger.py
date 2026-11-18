"""Best-effort daily per-provider usage accumulation for FCC client surfaces.

The proxy exposes live, run-scoped counters through ``/admin/api/status``. This
module folds those cumulative counters into a persisted, per-day ledger so free
tier consumption is visible across restarts. It is intentionally forgiving: a
missing or unreadable ledger never blocks a caller, and a counter that moves
backwards (for example after a server restart) simply re-baselines.
"""

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from free_claude_code.config.paths import usage_daily_path

_COUNTER_FIELDS = (
    ("calls_total", "calls"),
    ("rate_limit_hits", "rate_limit_hits"),
    ("recovery_episodes", "recovery_episodes"),
)


def load() -> dict[str, Any]:
    """Return the persisted ledger, or an empty ledger when unavailable."""

    path = usage_daily_path()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return _empty()
    if not isinstance(document, dict) or document.get("date") != _today():
        return _empty()
    return document


def accumulate(status: Mapping[str, Any]) -> dict[str, Any]:
    """Fold a live status snapshot into today's ledger and persist any change."""

    ledger = load()
    providers = ledger.setdefault("providers", {})
    seen = ledger.setdefault("seen", {})
    changed = False
    admission = status.get("admission")
    if isinstance(admission, Mapping):
        for provider_id, snapshot in admission.items():
            usage = snapshot.get("usage") if isinstance(snapshot, Mapping) else None
            if not isinstance(usage, Mapping):
                continue
            current = {name: _as_int(usage.get(name)) for name, _ in _COUNTER_FIELDS}
            previous = seen.get(provider_id)
            previous = previous if isinstance(previous, Mapping) else {}
            totals = providers.setdefault(
                provider_id, {"calls": 0, "rate_limit_hits": 0, "recovery_episodes": 0}
            )
            for name, key in _COUNTER_FIELDS:
                delta = current[name] - _as_int(previous.get(name))
                if delta > 0:
                    totals[key] = _as_int(totals.get(key)) + delta
                    changed = True
            if dict(previous) != current:
                seen[provider_id] = current
                changed = True
    changed = _accumulate_input_tokens(ledger, status) or changed
    if changed:
        _write(ledger)
    return ledger


def reset() -> None:
    """Discard the accumulated ledger."""

    _write(_empty())


def _accumulate_input_tokens(ledger: dict[str, Any], status: Mapping[str, Any]) -> bool:
    usage = status.get("usage")
    tokens = usage.get("input_tokens") if isinstance(usage, Mapping) else None
    if not isinstance(tokens, Mapping):
        return False
    providers = ledger.setdefault("providers", {})
    seen_tokens = ledger.setdefault("seen_tokens", {})
    changed = False
    for provider_id, current_value in tokens.items():
        if not isinstance(provider_id, str):
            continue
        current = _as_int(current_value)
        previous = _as_int(seen_tokens.get(provider_id))
        if current < previous:
            previous = 0
        totals = providers.setdefault(
            provider_id,
            {
                "calls": 0,
                "rate_limit_hits": 0,
                "recovery_episodes": 0,
                "input_tokens": 0,
            },
        )
        delta = current - previous
        if delta > 0:
            totals["input_tokens"] = _as_int(totals.get("input_tokens")) + delta
            changed = True
        if seen_tokens.get(provider_id) != current:
            seen_tokens[provider_id] = current
            changed = True
    return changed


def _empty() -> dict[str, Any]:
    return {"date": _today(), "providers": {}, "seen": {}, "seen_tokens": {}}


def _today() -> str:
    return date.today().isoformat()


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _write(payload: dict[str, Any]) -> None:
    path = usage_daily_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=".usage-", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=True, indent=2)
                output.write("\n")
            os.replace(temp_name, path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
    except OSError:
        pass
