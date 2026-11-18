"""Command specification for the terminal executor."""

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_OUTPUT_CAP_BYTES = 1_000_000
_MIN_TIMEOUT_SECONDS = 0.05


@dataclass(frozen=True)
class CommandSpec:
    """Everything the executor needs to run one command deterministically.

    ``argv`` is always an argument vector; the executor never invokes a shell.
    ``env`` replaces the inherited environment when given (allowlist semantics);
    ``None`` inherits ``os.environ``. ``pty`` requests pseudo-terminal
    allocation and is only honored for explicitly interactive call sites.
    """

    argv: tuple[str, ...]
    cwd: str | None = None
    env: Mapping[str, str] | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    output_cap_bytes: int = DEFAULT_OUTPUT_CAP_BYTES
    pty: bool = False

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("CommandSpec.argv must not be empty")
        if any(not isinstance(part, str) or part == "" for part in self.argv):
            raise ValueError("CommandSpec.argv entries must be non-empty strings")
        if self.timeout_seconds < _MIN_TIMEOUT_SECONDS:
            raise ValueError(
                f"CommandSpec.timeout_seconds must be >= {_MIN_TIMEOUT_SECONDS}"
            )
        if self.output_cap_bytes < 0:
            raise ValueError("CommandSpec.output_cap_bytes must be >= 0")
        if self.cwd is not None and not os.path.isdir(self.cwd):
            raise ValueError(f"CommandSpec.cwd is not a directory: {self.cwd}")

    @classmethod
    def of(
        cls,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        output_cap_bytes: int = DEFAULT_OUTPUT_CAP_BYTES,
        pty: bool = False,
    ) -> CommandSpec:
        """Build a spec from any sequence, validating eagerly."""

        return cls(
            argv=tuple(argv),
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
            output_cap_bytes=output_cap_bytes,
            pty=pty,
        )


@dataclass(frozen=True)
class PolicyDecision:
    """The policy layer's verdict for one command.

    The executor runs only when ``allowed`` is True. T2's permission layer
    produces these; until then call sites pass the default allow.
    """

    allowed: bool = True
    reason: str | None = None
    policy_id: str | None = None


DEFAULT_POLICY_DECISION = PolicyDecision()
