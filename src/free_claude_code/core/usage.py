"""Process-wide per-provider token totals for quota and status surfaces.

Free tiers are metered per provider, so token consumption has to survive
provider retries, model fallbacks, and streaming delivery. The registry only
accumulates numbers and never performs I/O, which keeps it safe to call from
request execution paths.
"""

import threading

_TOTALS: dict[str, int] = {}
_LOCK = threading.Lock()


def record_provider_input_tokens(provider_id: str, input_tokens: int) -> None:
    """Accumulate one attempt's estimated input tokens for its provider."""

    if not provider_id or input_tokens <= 0:
        return
    with _LOCK:
        _TOTALS[provider_id] = _TOTALS.get(provider_id, 0) + input_tokens


def provider_input_tokens() -> dict[str, int]:
    """Return a snapshot of every provider's accumulated input tokens."""

    with _LOCK:
        return dict(_TOTALS)


def reset_provider_input_tokens() -> None:
    """Discard accumulated totals; used by isolated tests."""

    with _LOCK:
        _TOTALS.clear()
