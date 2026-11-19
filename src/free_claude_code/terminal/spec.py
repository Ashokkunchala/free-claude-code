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
    ``inherit_stdio`` attaches the child to the caller's terminal instead of
    capturing output (interactive flows); the result then carries empty output
    fields and streaming callbacks never fire. ``isolated=False`` keeps the
    child in the caller's process group so terminal signals reach it directly.
    ``max_cpu_seconds`` and ``max_memory_bytes`` cap the child's CPU time and
    address space; both are enforced with POSIX rlimits in the child, are a
    documented no-op on Windows, and the memory budget is enforced on Linux
    only (RLIMIT_AS is unreliable under macOS's dyld shared cache).
    """

    argv: tuple[str, ...]
    cwd: str | None = None
    env: Mapping[str, str] | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    output_cap_bytes: int = DEFAULT_OUTPUT_CAP_BYTES
    pty: bool = False
    inherit_stdio: bool = False
    isolated: bool = True
    max_cpu_seconds: float | None = None
    max_memory_bytes: int | None = None

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
        if self.max_cpu_seconds is not None and self.max_cpu_seconds <= 0:
            raise ValueError("CommandSpec.max_cpu_seconds must be > 0 when set")
        if self.max_memory_bytes is not None and self.max_memory_bytes <= 0:
            raise ValueError("CommandSpec.max_memory_bytes must be > 0 when set")
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
        inherit_stdio: bool = False,
        isolated: bool = True,
        max_cpu_seconds: float | None = None,
        max_memory_bytes: int | None = None,
    ) -> CommandSpec:
        """Build a spec from any sequence, validating eagerly."""

        return cls(
            argv=tuple(argv),
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
            output_cap_bytes=output_cap_bytes,
            pty=pty,
            inherit_stdio=inherit_stdio,
            isolated=isolated,
            max_cpu_seconds=max_cpu_seconds,
            max_memory_bytes=max_memory_bytes,
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
