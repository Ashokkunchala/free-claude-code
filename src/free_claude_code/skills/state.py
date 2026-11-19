"""Enabled/disabled state for discovered skill packs.

State is one small JSON file under the user config directory. Writes
go through a temporary file plus rename so a crash never leaves a
truncated state file behind.
"""

import json
from pathlib import Path

from free_claude_code.config.paths import config_dir_path

STATE_FILENAME = "skills.json"
PACKS_DIRNAME = "skill-packs"


def default_packs_dir() -> Path:
    """Return the default directory scanned for skill packs."""

    return config_dir_path() / PACKS_DIRNAME


def default_state_path() -> Path:
    """Return the default enabled/disabled state file."""

    return config_dir_path() / STATE_FILENAME


class SkillState:
    """Persisted enabled/disabled flags keyed by pack name."""

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
        """Packs are enabled unless explicitly disabled."""

        return name not in self._disabled

    def set_enabled(self, name: str, enabled: bool) -> None:
        """Enable or disable one pack and persist immediately."""

        if enabled:
            self._disabled.discard(name)
        else:
            self._disabled.add(name)
        self._save()
