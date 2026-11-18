"""Redaction tests: shapes, settings-derived secrets, chunk-boundary splits."""

import sys
from dataclasses import dataclass

from free_claude_code.terminal import CommandSpec, Redactor, TerminalExecutor


def test_redactor_masks_common_token_shapes() -> None:
    redactor = Redactor()
    assert redactor.redact(b"key: sk-abcdefghij0123456789abcd") == b"key: <redacted>"
    assert redactor.redact(b"gsk_abcdefghij0123456789abcd") == b"<redacted>"
    assert redactor.redact(b"nvapi-abcdefghij0123456789abcd") == b"<redacted>"
    assert redactor.redact(b"ghp_abcdefghij0123456789abcd") == b"<redacted>"
    assert b"<redacted>" in redactor.redact(
        b"Authorization: Bearer abcdefghij0123456789abcd"
    )


def test_redactor_keeps_ordinary_text() -> None:
    text = b"diff --git a/file b/file +++ b/file"
    assert Redactor().redact(text) == text


def test_redactor_ignores_short_secret_values() -> None:
    redactor = Redactor(["tiny"])
    assert redactor.redact(b"tiny") == b"tiny"


@dataclass
class _StubSettings:
    openai_api_key: str | None = "sk-live-secret-value-0123456789"
    azure_openai_api_key: str | None = None
    open_router_api_key: str | None = None


def test_from_settings_redacts_configured_keys() -> None:
    redactor = Redactor.from_settings(_StubSettings())
    assert (
        redactor.redact(b"token was sk-live-secret-value-0123456789 ok")
        == b"token was <redacted> ok"
    )


def test_from_settings_none_is_shape_only() -> None:
    redactor = Redactor.from_settings(None)
    assert redactor.redact(b"plain output") == b"plain output"


def test_result_redacts_secret_split_across_reads() -> None:
    # The fake writes the secret in two flushed halves; only the joined
    # buffer can be redacted reliably.
    code = (
        "import sys, time\n"
        "sys.stdout.write('sk-splitsecret-0123'); sys.stdout.flush()\n"
        "time.sleep(0.2)\n"
        "sys.stdout.write('456tail'); sys.stdout.flush()\n"
    )
    result = TerminalExecutor().run(CommandSpec(argv=(sys.executable, "-c", code)))
    assert b"sk-splitsecret-0123456tail" not in result.stdout
    assert b"<redacted>" in result.stdout
