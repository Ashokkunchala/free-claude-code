"""Read-only environment context for plan generation (roadmap: Understand, Inspect).

A plan written against the real workspace beats one written blind. This
collector gathers the small, safe facts a planner needs - platform,
shell, git state and a bounded top-level listing - using argument-vector
subprocess calls with timeouts. It reads nothing secret: no environment
variable values, no file contents, no command output beyond the bounded
fields below. Every probe degrades to None when the tool is absent.
"""

import os
import platform
import subprocess
from dataclasses import dataclass

_MAX_LISTING = 50
_MAX_GIT_LINES = 20
_PROBE_TIMEOUT = 5.0


@dataclass(frozen=True, slots=True)
class EnvContext:
    """Bounded, read-only facts about the workspace environment."""

    os_name: str
    arch: str
    python_version: str
    shell: str | None
    git_branch: str | None
    git_dirty: tuple[str, ...] = ()
    top_level: tuple[str, ...] = ()


def _probe(argv: list[str], cwd: str) -> str | None:
    """Run a read-only probe; None when the tool or command is unavailable."""

    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _git_branch(workspace: str) -> str | None:
    out = _probe(["git", "branch", "--show-current"], workspace)
    if out is None:
        return None
    return out.strip() or None


def _git_dirty(workspace: str) -> tuple[str, ...]:
    out = _probe(["git", "status", "--porcelain"], workspace)
    if out is None:
        return ()
    return tuple(out.splitlines()[:_MAX_GIT_LINES])


def _top_level(workspace: str) -> tuple[str, ...]:
    try:
        entries = sorted(os.listdir(workspace))[:_MAX_LISTING]
    except OSError:
        return ()
    return tuple(entries)


def collect_env_context(workspace: str) -> EnvContext:
    """Collect the bounded context; never raises on missing tools."""

    return EnvContext(
        os_name=platform.system(),
        arch=platform.machine(),
        python_version=platform.python_version(),
        shell=os.environ.get("SHELL") or os.environ.get("COMSPEC"),
        git_branch=_git_branch(workspace),
        git_dirty=_git_dirty(workspace),
        top_level=_top_level(workspace),
    )


def render_env_context(context: EnvContext) -> str:
    """The prompt block describing the workspace to the planning model."""

    lines = [
        f"Platform: {context.os_name} {context.arch}",
        f"Python: {context.python_version}",
    ]
    if context.shell:
        lines.append(f"Shell: {context.shell}")
    if context.git_branch:
        lines.append(f"Git branch: {context.git_branch}")
    if context.git_dirty:
        lines.append("Uncommitted paths:")
        lines.extend(f"  {entry}" for entry in context.git_dirty)
    if context.top_level:
        lines.append("Workspace top level:")
        lines.extend(f"  {entry}" for entry in context.top_level)
    return "\n".join(lines)
