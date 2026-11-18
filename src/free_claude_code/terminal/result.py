"""Result of running one command through the terminal executor."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CommandResult:
    """Structured outcome of one executed (or rejected) command.

    ``exit_code`` is the child exit status; it is ``None`` when the process
    never produced one (spawn failure, cancellation before exit). ``timed_out``
    and ``cancelled`` are mutually exclusive ways the run ended early.
    """

    argv: tuple[str, ...]
    cwd: str | None
    exit_code: int | None
    duration_seconds: float
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    cancelled: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    policy_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled

    @property
    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")

    @property
    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class OutputChunk:
    """One streamed chunk of child output, delivered to callbacks."""

    stream: str  # "stdout" or "stderr"
    data: bytes
    timestamp_offset_seconds: float


@dataclass
class _Capture:
    """Mutable capture state for one stream (executor-internal)."""

    chunks: list[bytes] = field(default_factory=list)
    total_bytes: int = 0
    truncated: bool = False

    def append(self, data: bytes, cap: int) -> None:
        self.total_bytes += len(data)
        if self.truncated:
            return
        current = sum(len(chunk) for chunk in self.chunks)
        remaining = cap - current
        if remaining <= 0:
            self.truncated = True
            return
        if len(data) > remaining:
            self.chunks.append(data[:remaining])
            self.truncated = True
            return
        self.chunks.append(data)

    def joined(self) -> bytes:
        return b"".join(self.chunks)
