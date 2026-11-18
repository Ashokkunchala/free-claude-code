"""gitignore-style ignore rules for repository traversal.

Supported subset of gitignore semantics: comments and blank lines, ``!``
negation, trailing ``/`` directory-only patterns, leading ``/`` anchoring,
``*``/``?`` within a component, and ``**`` across components. Patterns
without a slash match any path component; patterns with a slash match from
the repository root. Rules come from the root ``.gitignore`` (nested
``.gitignore`` files are not read yet); ``.git`` itself is always ignored.
"""

import re
from dataclasses import dataclass
from pathlib import Path

_ALWAYS_IGNORED = (".git",)


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """Translate one gitignore pattern body into an anchored regex."""

    anchored = pattern.startswith("/")
    if anchored:
        pattern = pattern[1:]
    out: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern[i : i + 2] == "**":
                # `**/` also matches zero directories; bare `**` matches all.
                if pattern[i : i + 3] == "**/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(re.escape(char))
            else:
                out.append(pattern[i : end + 1])
                i = end
        else:
            out.append(re.escape(char))
        i += 1
    body = "".join(out)
    if anchored or "/" in pattern:
        return re.compile(f"^{body}$")
    # No slash: match any single component of the path.
    return re.compile(f"(^|/){body}$")


@dataclass(frozen=True)
class _Rule:
    regex: re.Pattern[str]
    negated: bool
    dir_only: bool


class IgnoreRules:
    """Ordered gitignore rules; the last matching rule wins."""

    def __init__(self, rules: list[_Rule] | None = None) -> None:
        self._rules = rules if rules is not None else []

    @staticmethod
    def from_lines(lines: list[str]) -> IgnoreRules:
        rules: list[_Rule] = []
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:]
            dir_only = line.endswith("/")
            if dir_only:
                line = line[:-1]
            if not line:
                continue
            rules.append(
                _Rule(regex=_pattern_regex(line), negated=negated, dir_only=dir_only)
            )
        return IgnoreRules(rules)

    @staticmethod
    def from_root(root: Path) -> IgnoreRules:
        """Load rules from the root ``.gitignore``; missing file means none."""

        try:
            text = (root / ".gitignore").read_text(encoding="utf-8")
        except OSError:
            return IgnoreRules()
        return IgnoreRules.from_lines(text.splitlines())

    def is_ignored(self, rel_path: str, *, is_dir: bool = False) -> bool:
        rel = rel_path.replace("\\", "/").strip("/")
        if not rel:
            return False
        top = rel.split("/", 1)[0]
        if top in _ALWAYS_IGNORED:
            return True
        ignored = False
        for rule in self._rules:
            if rule.dir_only and not is_dir:
                # A dir-only pattern matches children via a parent directory.
                if any(rule.regex.search(part) for part in _parent_paths(rel)):
                    ignored = not rule.negated
                continue
            if rule.regex.search(rel):
                ignored = not rule.negated
        return ignored


def _parent_paths(rel: str) -> list[str]:
    parts = rel.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


__all__ = ["IgnoreRules"]
