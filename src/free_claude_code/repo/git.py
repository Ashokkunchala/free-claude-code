"""Git workflows on top of the terminal executor and policy engine.

Every operation runs as a plain argument vector through the executor, so it
gets timeout, capture, audit and the policy verdict of the git classifier for
free. Operations whose verdict is not an automatic allow (push, rebase,
history rewrite, ...) raise ``PolicyRejectedError`` here; approving them is
the approval flow's job (fcc-exec / fcc-approvals), not this wrapper's.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

from free_claude_code.terminal import (
    CommandResult,
    CommandSpec,
    PolicyEngine,
    PolicyRejectedError,
    Requirement,
    TerminalExecutor,
    policy_config_from_env,
)


class GitError(Exception):
    """A git command exited non-zero."""

    def __init__(self, argv: tuple[str, ...], result: CommandResult) -> None:
        self.argv = argv
        self.result = result
        stderr = result.stderr_text.strip()
        super().__init__(f"git {' '.join(argv)} failed ({result.exit_code}): {stderr}")


@dataclass(frozen=True)
class GitStatus:
    branch: str
    dirty: bool
    porcelain: str


class GitRepo:
    """Policy-checked git operations confined to one repository root."""

    def __init__(
        self,
        root: Path,
        *,
        executor: TerminalExecutor | None = None,
        engine: PolicyEngine | None = None,
    ) -> None:
        self._root = Path(root)
        self._executor = executor if executor is not None else TerminalExecutor()
        self._engine = (
            engine
            if engine is not None
            else PolicyEngine(policy_config_from_env(workspace_root=str(self._root)))
        )

    @property
    def root(self) -> Path:
        return self._root

    @staticmethod
    def git_executable() -> str:
        resolved = shutil.which("git")
        if resolved is None:
            raise GitError(("git",), _missing_git_result())
        return resolved

    def run(
        self,
        *args: str,
        task_id: str | None = None,
        check: bool = True,
    ) -> CommandResult:
        """Run one git command with policy evaluation and optional checking."""

        argv = (self.git_executable(), *args)
        spec = CommandSpec(argv=argv, cwd=str(self._root))
        verdict = self._engine.evaluate(spec)
        if verdict.requirement is not Requirement.ALLOW:
            decision = verdict.to_decision()
            raise PolicyRejectedError(argv, verdict.reason or decision.reason)
        result = self._executor.run(spec, policy=verdict.to_decision(), task_id=task_id)
        if check and result.exit_code != 0:
            raise GitError(argv[1:], result)
        return result

    def status(self) -> GitStatus:
        """Branch name, dirty flag and raw porcelain output."""

        porcelain = self.run("status", "--porcelain").stdout_text
        branch = self.current_branch()
        return GitStatus(
            branch=branch, dirty=bool(porcelain.strip()), porcelain=porcelain
        )

    def current_branch(self) -> str:
        """Current branch name, or the short commit when detached."""

        result = self.run("branch", "--show-current")
        name = result.stdout_text.strip()
        if name:
            return name
        return self.run("rev-parse", "--short", "HEAD").stdout_text.strip()

    def is_dirty(self) -> bool:
        return bool(self.run("status", "--porcelain").stdout_text.strip())

    def diff(self, *args: str) -> str:
        return self.run("diff", *args).stdout_text

    def create_branch(self, name: str, *, start: str | None = None) -> None:
        args = (
            ("checkout", "-b", name)
            if start is None
            else (
                "checkout",
                "-b",
                name,
                start,
            )
        )
        self.run(*args)

    def add(self, *paths: str) -> None:
        if not paths:
            raise ValueError("git add requires at least one path")
        self.run("add", "--", *paths)

    def commit(self, message: str) -> str:
        """Commit staged changes; returns the new commit's short id."""

        self.run("commit", "-m", message)
        return self.run("rev-parse", "--short", "HEAD").stdout_text.strip()


def _missing_git_result() -> CommandResult:
    argv = ("git",)
    return CommandResult(
        argv=argv,
        cwd=None,
        exit_code=127,
        duration_seconds=0.0,
        stdout=b"",
        stderr=b"git executable not found on PATH",
        timed_out=False,
        cancelled=False,
    )


__all__ = ["GitError", "GitRepo", "GitStatus"]
