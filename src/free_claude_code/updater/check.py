"""Standalone fork preflight for the native update launchers."""

import re
import sys
from importlib.metadata import PackageNotFoundError, version

import httpx
from packaging.version import Version

COMMITS_URL = "https://api.github.com/repos/Ashokkunchala/free-claude-code/commits/main"
UPDATE_AVAILABLE = 10
_LOCAL_SHA = re.compile(r"^g([0-9a-f]{7,40})")


def _installed_commit(installed: Version) -> str | None:
    """Extract the VCS commit hash hatch-vcs embeds in git-built versions."""
    if installed.local is None:
        return None
    match = _LOCAL_SHA.match(installed.local)
    return match.group(1) if match else None


def main() -> int:
    """Return 10 to authorize installation, 0 to skip, or 1 on check failure."""
    try:
        installed = Version(version("free-claude-code"))
        if str(installed) == "0+unknown":
            raise ValueError("installed FCC version is unknown")
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                COMMITS_URL,
                headers={"Accept": "application/vnd.github+json"},
            )
            response.raise_for_status()
            metadata = response.json()
        latest = metadata["sha"]
        if not isinstance(latest, str) or not re.fullmatch(r"[0-9a-f]{40}", latest):
            raise ValueError("GitHub did not return the fork's main commit")
    except (
        PackageNotFoundError,
        httpx.HTTPError,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        print(f"Could not check for FCC updates: {exc}. Please retry.", file=sys.stderr)
        return 1

    installed_commit = _installed_commit(installed)
    if installed_commit is not None and latest.startswith(installed_commit):
        print(f"FCC {installed} is already up to date.")
        return 0
    print(
        f"FCC update available: {installed} -> fork main {latest[:7]}. "
        "Running the installer."
    )
    return UPDATE_AVAILABLE
