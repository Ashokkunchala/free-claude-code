"""Lightweight entry points for installed Free Claude Code commands."""

import sys
from collections.abc import Sequence

from free_claude_code.core.version import package_version


def serve(argv: Sequence[str] | None = None) -> None:
    """Start the FastAPI server (registered as ``fcc-server``)."""
    if _print_version_if_requested(argv):
        return

    # Keep the server composition root off metadata-only command paths.
    from free_claude_code.cli.commands import serve as run_server

    run_server()


def _print_version_if_requested(argv: Sequence[str] | None) -> bool:
    args = sys.argv[1:] if argv is None else argv
    if "--version" not in args:
        return False
    print(f"free-claude-code {package_version()}")
    return True


def doctor(argv: Sequence[str] | None = None) -> None:
    """Print and copy a local diagnostic report (registered as ``fcc-doctor``)."""
    if _print_version_if_requested(argv):
        return
    from free_claude_code.cli.doctor import main

    main(argv)


def statusline(argv: Sequence[str] | None = None) -> None:
    """Render one FCC status line (registered as ``fcc-statusline``)."""
    from free_claude_code.cli.statusline import main

    main()


def status(argv: Sequence[str] | None = None) -> None:
    """Print the local FCC usage and health dashboard (registered as ``fcc-status``)."""
    from free_claude_code.cli.dashboard import main

    main(argv)


def profile(argv: Sequence[str] | None = None) -> None:
    """Manage per-project model profiles (registered as ``fcc-profile``)."""
    from free_claude_code.cli.profiles import main

    main(argv)


def approvals(argv: Sequence[str] | None = None) -> None:
    """Decide pending privileged-action approvals (``fcc-approvals``)."""
    from free_claude_code.cli.approvals import main

    main(argv)


def audit(argv: Sequence[str] | None = None) -> None:
    """Inspect the terminal command audit log (registered as ``fcc-audit``)."""
    from free_claude_code.cli.audit import main

    main(argv)


def exec_command(argv: Sequence[str] | None = None) -> None:
    """Run one command through policy enforcement (registered as ``fcc-exec``)."""
    from free_claude_code.cli.runner import main

    main(argv)


def inspect(argv: Sequence[str] | None = None) -> None:
    """Read-only repository inspection (registered as ``fcc-inspect``)."""
    from free_claude_code.cli.inspect import main

    main(argv)
