"""Approval token tests: binding, expiry and tamper resistance."""

import time
from pathlib import Path

import pytest

from free_claude_code.terminal import CommandSpec
from free_claude_code.terminal.approval import (
    ApprovalTokenIssuer,
    command_fingerprint,
)

SECRET = b"test-secret-key-0123456789abcdef"
OTHER_SECRET = b"other-secret-key-0123456789ab"


def make_issuer(secret: bytes = SECRET) -> ApprovalTokenIssuer:
    return ApprovalTokenIssuer(secret)


def make_spec(*argv: str, cwd: str | None = None) -> CommandSpec:
    return CommandSpec(argv=tuple(argv), cwd=cwd)


def test_roundtrip() -> None:
    issuer = make_issuer()
    spec = make_spec("git", "push", "origin", "main")
    token = issuer.mint(spec, "task-1")
    assert issuer.verify(token, spec, "task-1").ok


def test_short_secret_rejected() -> None:
    with pytest.raises(ValueError):
        ApprovalTokenIssuer(b"too-short")


def test_expired_token_rejected() -> None:
    issuer = make_issuer()
    spec = make_spec("ls")
    token = issuer.mint(spec, "task-1", ttl_seconds=1.0)
    future = time.time() + 5.0
    check = issuer.verify(token, spec, "task-1", now=future)
    assert not check.ok
    assert check.reason == "token expired"


def test_wrong_task_rejected() -> None:
    issuer = make_issuer()
    spec = make_spec("ls")
    token = issuer.mint(spec, "task-1")
    check = issuer.verify(token, spec, "task-2")
    assert not check.ok
    assert check.reason == "token bound to another task"


def test_wrong_command_rejected() -> None:
    issuer = make_issuer()
    token = issuer.mint(make_spec("ls"), "task-1")
    check = issuer.verify(token, make_spec("ls", "-la"), "task-1")
    assert not check.ok
    assert check.reason == "token not bound to this command"


def test_wrong_cwd_rejected(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    issuer = make_issuer()
    token = issuer.mint(make_spec("ls", cwd=str(first)), "task-1")
    check = issuer.verify(token, make_spec("ls", cwd=str(second)), "task-1")
    assert not check.ok


def test_tampered_payload_rejected() -> None:
    issuer = make_issuer()
    spec = make_spec("ls")
    token = issuer.mint(spec, "task-1")
    body, _ = token.split(".")
    other = issuer.mint(make_spec("ls", "-la"), "task-1")
    _, other_sig = other.split(".")
    check = issuer.verify(f"{body}.{other_sig}", spec, "task-1")
    assert not check.ok
    assert check.reason == "signature mismatch"


def test_wrong_secret_rejected() -> None:
    spec = make_spec("ls")
    token = make_issuer(OTHER_SECRET).mint(spec, "task-1")
    check = make_issuer().verify(token, spec, "task-1")
    assert not check.ok


def test_malformed_tokens_rejected() -> None:
    issuer = make_issuer()
    spec = make_spec("ls")
    for bad in ("", "no-dot", "a.b.c", ".sig", "body.", "body.!!!"):
        check = issuer.verify(bad, spec, "task-1")
        assert not check.ok, bad


def test_fingerprint_is_order_and_cwd_sensitive(tmp_path: Path) -> None:
    first = command_fingerprint(make_spec("a", "b"))
    assert first != command_fingerprint(make_spec("b", "a"))
    assert first != command_fingerprint(make_spec("a", "b", cwd=str(tmp_path)))
    assert first == command_fingerprint(make_spec("a", "b"))
