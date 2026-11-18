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
