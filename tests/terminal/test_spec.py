"""CommandSpec validation tests."""

from pathlib import Path

import pytest

from free_claude_code.terminal.spec import CommandSpec, PolicyDecision


def test_spec_rejects_empty_argv() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        CommandSpec(argv=())


def test_spec_rejects_empty_entries() -> None:
    with pytest.raises(ValueError, match="non-empty strings"):
        CommandSpec(argv=("echo", ""))


def test_spec_rejects_tiny_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        CommandSpec(argv=("echo",), timeout_seconds=0.001)


def test_spec_rejects_negative_cap() -> None:
    with pytest.raises(ValueError, match="output_cap_bytes"):
        CommandSpec(argv=("echo",), output_cap_bytes=-1)


def test_spec_rejects_missing_cwd(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a directory"):
        CommandSpec(argv=("echo",), cwd=str(tmp_path / "missing"))


def test_spec_of_builds_from_list() -> None:
    spec = CommandSpec.of(["echo", "hi"], timeout_seconds=5)
    assert spec.argv == ("echo", "hi")
    assert spec.timeout_seconds == 5


def test_policy_defaults_to_allow() -> None:
    assert PolicyDecision().allowed
