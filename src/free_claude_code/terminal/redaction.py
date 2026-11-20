"""Secret redaction for captured command output.

Covers two classes of secrets: values loaded from the FCC settings document
(provider API keys) and common token shapes seen in command output. Redaction
is byte-oriented so it works on partial UTF-8 in captured streams.
"""

import re
from collections.abc import Iterable

REDACTED = b"<redacted>"

# Common token shapes: provider keys and bearer-style tokens. Kept narrow to
# avoid false positives in ordinary diffs and logs.
_TOKEN_PATTERNS: tuple[re.Pattern[bytes], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        rb"sk-[A-Za-z0-9_\-]{20,}",  # OpenAI-style
        rb"gsk_[A-Za-z0-9_\-]{20,}",  # Groq
        rb"nvapi-[A-Za-z0-9_\-]{20,}",  # NVIDIA NIM
        rb"gh[pousr]_[A-Za-z0-9]{20,}",  # GitHub tokens
        rb"Bearer\s+[A-Za-z0-9_\-.]{20,}",
    )
)

# Settings field names whose values must never appear in captured output.
SECRET_SETTING_FIELDS: tuple[str, ...] = (
    "openai_api_key",
    "azure_openai_api_key",
    "open_router_api_key",
)

_MIN_SECRET_LENGTH = 8


def _secret_values(extra_secrets: Iterable[str]) -> list[bytes]:
    values: list[bytes] = []
    for secret in extra_secrets:
        encoded = secret.encode("utf-8", errors="ignore")
        if len(encoded) >= _MIN_SECRET_LENGTH:
            values.append(encoded)
    return values


class Redactor:
    """Redacts known secret values and token shapes from byte streams."""

    def __init__(self, extra_secrets: Iterable[str] = ()) -> None:
        self._secrets = _secret_values(extra_secrets)

    @classmethod
    def from_settings(cls, settings: object | None) -> Redactor:
        """Build a redactor from a Settings-like object.

        Fields named in ``SECRET_SETTING_FIELDS`` that hold non-empty strings
        become redaction values. ``None`` yields a shape-only redactor.
        """

        secrets: list[str] = []
        if settings is not None:
            for field_name in SECRET_SETTING_FIELDS:
                value = getattr(settings, field_name, None)
                if isinstance(value, str) and value:
                    secrets.append(value)
        return cls(secrets)

    def redact(self, data: bytes) -> bytes:
        for secret in self._secrets:
            if secret in data:
                data = data.replace(secret, REDACTED)
        for pattern in _TOKEN_PATTERNS:
            data = pattern.sub(REDACTED, data)
        return data

    def redact_text(self, text: str) -> str:
        return self.redact(text.encode("utf-8", errors="replace")).decode(
            "utf-8", errors="replace"
        )


def default_redactor() -> Redactor:
    """Shape-only redactor for call sites without settings access."""

    return Redactor()


#: Longest token anchor plus the minimum trailing run a token pattern needs
#: before it can match ("Bearer " + 19 chars is the worst case below).
_ANCHOR_PREFIXES: tuple[bytes, ...] = (
    b"sk-",
    b"gsk_",
    b"nvapi-",
    b"ghp_",
    b"gho_",
    b"ghu_",
    b"ghs_",
    b"ghr_",
    b"Bearer ",
)
_TOKEN_MIN_TAIL = 20


def _carry_length(secret_lengths: Iterable[int]) -> int:
    """Bytes a streaming redactor must hold back to catch split matches.

    A configured secret split across a chunk boundary leaves a partial of at
    most ``len(secret) - 1`` bytes in the earlier chunk. A token shape leaves
    a non-matching partial of at most ``len(anchor) + _TOKEN_MIN_TAIL - 1``
    bytes (one more character and the pattern would already match, so the
    whole-buffer redaction would have caught it).
    """

    carry = max(len(anchor) + _TOKEN_MIN_TAIL - 1 for anchor in _ANCHOR_PREFIXES)
    for length in secret_lengths:
        carry = max(carry, length - 1)
    return carry


class StreamRedactor:
    """Stateful redactor for chunked output streams.

    Holds back a tail that could be the start of a secret or token and
    redacts it together with the next chunk, so a secret split across read
    chunks never reaches the consumer in full. ``flush`` emits the final
    held-back tail at end of stream.
    """

    def __init__(self, redactor: Redactor) -> None:
        self._redactor = redactor
        self._carry_len = _carry_length(len(s) for s in redactor._secrets)
        self._carry = b""

    def redact_chunk(self, data: bytes) -> bytes:
        """Redact one chunk; may return fewer bytes than given (tail held)."""

        # Redact first, then hold back the tail: a partial secret or token
        # that only the next chunk completes can only sit at the end of the
        # buffer, and complete matches are already replaced, so the emitted
        # prefix never contains secret bytes. The held tail is already
        # redacted; re-redacting it with the next chunk is harmless.
        buf = self._redactor.redact(self._carry + data)
        if len(buf) <= self._carry_len:
            self._carry = buf
            return b""
        self._carry = buf[-self._carry_len :]
        return buf[: -self._carry_len]

    def flush(self) -> bytes:
        """Emit the held-back tail at end of stream."""

        out = self._redactor.redact(self._carry)
        self._carry = b""
        return out
