"""Declarative skill-pack manifests: parsing and validation.

A skill pack is a directory with a ``skill.toml`` manifest and an
optional ``SKILL.md`` instruction file. Manifests are declarative:
they declare what the skill is for, which platforms and tools it
needs, the highest risk tier it may request and the approval
requirements around it. Packs containing executable code must say so
and are only loadable from trusted sources (roadmap section 8).
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from free_claude_code.terminal.policy import RiskLevel

MANIFEST_NAME = "skill.toml"
INSTRUCTIONS_NAME = "SKILL.md"

_NAME = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")
_VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
PLATFORMS = ("windows", "linux", "macos")
MAX_TIMEOUT_SECONDS = 3600
MAX_OUTPUT_BYTES = 1_048_576

_RISK_NAMES = {level.name.lower(): level for level in RiskLevel}
_RISK_ALIASES = {f"r{level.value}": level for level in RiskLevel}


@dataclass(frozen=True)
class ToolDef:
    """One typed tool definition a skill pack contributes."""

    name: str
    description: str
    timeout_seconds: int
    max_output_bytes: int


@dataclass(frozen=True)
class SkillManifest:
    """Validated declarations of one skill pack."""

    name: str
    version: str
    purpose: str
    platforms: tuple[str, ...]
    required_tools: tuple[str, ...]
    max_risk: RiskLevel
    approval_from: RiskLevel
    timeout_seconds: int
    rollback: str
    verification: tuple[str, ...]
    executable: bool
    tools: tuple[ToolDef, ...]


def parse_risk(value: str) -> RiskLevel:
    """Parse a risk tier written as ``r2`` or ``r2_privileged``."""

    key = value.strip().lower()
    level = _RISK_ALIASES.get(key)
    if level is None:
        level = _RISK_NAMES.get(key)
    if level is None:
        raise ValueError(f"Unknown risk tier: {value!r}")
    return level


def _want(condition: bool, issues: list[str], message: str) -> None:
    if not condition:
        issues.append(message)


def _tool_def(raw: object, issues: list[str]) -> ToolDef | None:
    if not isinstance(raw, dict):
        issues.append("tool definitions must be tables")
        return None
    name = raw.get("name", "")
    description = raw.get("description", "")
    timeout = raw.get("timeout_seconds", 60)
    limit = raw.get("max_output_bytes", MAX_OUTPUT_BYTES)
    _want(
        isinstance(name, str) and bool(_NAME.fullmatch(name)),
        issues,
        f"tool name must be kebab-case, got {name!r}",
    )
    _want(
        isinstance(description, str) and bool(description.strip()),
        issues,
        f"tool {name!r} needs a description",
    )
    _want(
        isinstance(timeout, int) and 0 < timeout <= MAX_TIMEOUT_SECONDS,
        issues,
        f"tool {name!r} timeout_seconds must be 1..{MAX_TIMEOUT_SECONDS}",
    )
    _want(
        isinstance(limit, int) and 0 < limit <= MAX_OUTPUT_BYTES,
        issues,
        f"tool {name!r} max_output_bytes must be 1..{MAX_OUTPUT_BYTES}",
    )
    if not (
        isinstance(name, str)
        and _NAME.fullmatch(name)
        and isinstance(description, str)
        and description.strip()
        and isinstance(timeout, int)
        and 0 < timeout <= MAX_TIMEOUT_SECONDS
        and isinstance(limit, int)
        and 0 < limit <= MAX_OUTPUT_BYTES
    ):
        return None
    return ToolDef(
        name=name,
        description=description.strip(),
        timeout_seconds=timeout,
        max_output_bytes=limit,
    )


def parse_manifest(data: dict) -> SkillManifest:
    """Validate raw ``skill.toml`` data into a manifest.

    Raises ValueError listing every issue found, so pack authors fix
    their manifest in one pass instead of one error at a time.
    """

    issues: list[str] = []
    name = data.get("name", "")
    version = data.get("version", "")
    purpose = data.get("purpose", "")
    platforms = data.get("platforms", [])
    required_tools = data.get("required_tools", [])
    max_risk_raw = data.get("max_risk", "r0")
    approval_raw = data.get("approval_from", "r2")
    timeout = data.get("timeout_seconds", 300)
    rollback = data.get("rollback", "")
    verification = data.get("verification", [])
    executable = data.get("executable", False)
    tools_raw = data.get("tools", [])

    _want(
        isinstance(name, str) and bool(_NAME.fullmatch(name)),
        issues,
        f"name must be kebab-case, got {name!r}",
    )
    _want(
        isinstance(version, str) and bool(_VERSION.fullmatch(version)),
        issues,
        f"version must be semver X.Y.Z, got {version!r}",
    )
    _want(
        isinstance(purpose, str) and bool(purpose.strip()),
        issues,
        "purpose must be a non-empty string",
    )
    _want(
        isinstance(platforms, list)
        and bool(platforms)
        and all(p in PLATFORMS for p in platforms),
        issues,
        f"platforms must be a non-empty subset of {sorted(PLATFORMS)}",
    )
    _want(
        isinstance(required_tools, list)
        and all(isinstance(t, str) and t for t in required_tools),
        issues,
        "required_tools must be a list of executable names",
    )
    _want(
        isinstance(timeout, int) and 0 < timeout <= MAX_TIMEOUT_SECONDS,
        issues,
        f"timeout_seconds must be 1..{MAX_TIMEOUT_SECONDS}",
    )
    _want(
        isinstance(rollback, str),
        issues,
        "rollback must be a string describing the rollback strategy",
    )
    _want(
        isinstance(verification, list)
        and all(isinstance(v, str) and v for v in verification),
        issues,
        "verification must be a list of verification step descriptions",
    )
    _want(
        isinstance(executable, bool),
        issues,
        "executable must be true or false",
    )
    _want(
        isinstance(tools_raw, list),
        issues,
        "tools must be a list of tool definition tables",
    )

    max_risk = approval_from = None
    for label, raw, which in (
        ("max_risk", max_risk_raw, "max"),
        ("approval_from", approval_raw, "approval"),
    ):
        try:
            level = parse_risk(raw) if isinstance(raw, str) else None
        except ValueError:
            level = None
        if level is None:
            issues.append(f"{label} must be one of {sorted(_RISK_ALIASES)}")
        elif which == "max":
            max_risk = level
        else:
            approval_from = level

    tools: list[ToolDef] = []
    seen: set[str] = set()
    if isinstance(tools_raw, list):
        for raw in tools_raw:
            tool = _tool_def(raw, issues)
            if tool is None:
                continue
            _want(
                tool.name not in seen,
                issues,
                f"duplicate tool definition {tool.name!r}",
            )
            seen.add(tool.name)
            tools.append(tool)

    if issues:
        raise ValueError("Invalid skill manifest: " + "; ".join(issues))
    assert max_risk is not None and approval_from is not None
    return SkillManifest(
        name=name,
        version=version,
        purpose=purpose.strip(),
        platforms=tuple(sorted(platforms)),
        required_tools=tuple(required_tools),
        max_risk=max_risk,
        approval_from=approval_from,
        timeout_seconds=timeout,
        rollback=rollback.strip() if isinstance(rollback, str) else "",
        verification=tuple(verification),
        executable=executable,
        tools=tuple(tools),
    )


def load_manifest(path: Path) -> SkillManifest:
    """Parse and validate the manifest file at ``path``."""

    return parse_manifest(tomllib.loads(path.read_text(encoding="utf-8")))
