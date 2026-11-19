"""Enabled/disabled state for configured MCP servers.

State is one small JSON file under the user config directory. Writes
go through a temporary file plus rename so a crash never leaves a
truncated state file behind. Servers are enabled unless explicitly
disabled.
"""

import json
from pathlib import Path

from free_claude_code.config.paths import config_dir_path

STATE_FILENAME = "mcp.json"
CONFIG_FILENAME = "mcp.toml"


def default_config_path() -> Path:
    """Return the default ``mcp.toml`` location."""

    return config_dir_path() / CONFIG_FILENAME


def default_state_path() -> Path:
    """Return the default enabled/disabled state file."""

    return config_dir_path() / STATE_FILENAME


class MCPState:
    """Persisted enabled/disabled flags keyed by server name."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._disabled: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        data = json.loads(self._path.read_text(encoding="utf-8"))
        self._disabled = {str(name) for name in data.get("disabled", [])}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(".tmp")
        temp.write_text(
            json.dumps({"disabled": sorted(self._disabled)}, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(self._path)

    def is_enabled(self, name: str) -> bool:
        """Servers are enabled unless explicitly disabled."""

        return name not in self._disabled

    def set_enabled(self, name: str, enabled: bool) -> None:
        """Enable or disable one server and persist immediately."""

        if enabled:
            self._disabled.discard(name)
        else:
            self._disabled.add(name)
        self._save()
