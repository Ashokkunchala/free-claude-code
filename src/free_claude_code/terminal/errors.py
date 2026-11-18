"""Errors raised by the terminal executor."""


class TerminalExecutorError(Exception):
    """Base class for terminal executor errors."""


class SpawnError(TerminalExecutorError):
    """The child process could not be started."""

    def __init__(self, argv: tuple[str, ...], reason: str) -> None:
        self.argv = argv
        self.reason = reason
        super().__init__(f"could not start {argv[0]!r}: {reason}")


class PolicyRejectedError(TerminalExecutorError):
    """The policy decision did not allow the command to run."""

    def __init__(self, argv: tuple[str, ...], reason: str | None) -> None:
        self.argv = argv
        self.reason = reason
        detail = reason if reason else "no reason given"
        super().__init__(f"policy rejected {argv[0]!r}: {detail}")
