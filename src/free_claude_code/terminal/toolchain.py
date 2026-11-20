"""DevOps toolchain detection: what is installed, and guidance when it is not.

Roadmap section 4 (environment discovery): detect Docker/Podman,
kubectl/helm, Terraform/OpenTofu, cloud CLIs, Ansible and SSH without
assuming they are installed, and offer safe install guidance instead of
silent installs. Detection uses ``shutil.which``; versions come from a
bounded ``--version`` probe and never from a shell.
"""

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

_PROBE_TIMEOUT = 5.0
_VERSION = re.compile(r"\d+\.\d+(?:\.\d+)?")

WhichFn = Callable[[str], "str | None"]
ProbeFn = Callable[[list[str]], "str | None"]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """How to detect one tool and where to learn about installing it."""

    name: str
    version_argv: tuple[str, ...]
    install_hint: str


@dataclass(frozen=True, slots=True)
class ToolInfo:
    """Detection result for one tool."""

    name: str
    path: str | None
    version: str | None
    install_hint: str | None


TOOLCHAIN_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("docker", ("docker", "--version"), "https://docs.docker.com/get-docker/"),
    ToolSpec("podman", ("podman", "--version"), "https://podman.io/docs/installation"),
    ToolSpec(
        "kubectl",
        ("kubectl", "version", "--client"),
        "https://kubernetes.io/docs/tasks/tools/",
    ),
    ToolSpec("helm", ("helm", "version"), "https://helm.sh/docs/intro/install/"),
    ToolSpec(
        "terraform",
        ("terraform", "--version"),
        "https://developer.hashicorp.com/terraform/install",
    ),
    ToolSpec("tofu", ("tofu", "--version"), "https://opentofu.org/docs/intro/install/"),
    ToolSpec(
        "aws",
        ("aws", "--version"),
        "https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html",
    ),
    ToolSpec(
        "az",
        ("az", "--version"),
        "https://learn.microsoft.com/cli/azure/install-azure-cli",
    ),
    ToolSpec(
        "gcloud",
        ("gcloud", "--version"),
        "https://cloud.google.com/sdk/docs/install",
    ),
    ToolSpec(
        "ansible",
        ("ansible", "--version"),
        "https://docs.ansible.com/ansible/latest/installation_guide/",
    ),
    ToolSpec("ssh", ("ssh", "-V"), "install the OpenSSH client for your platform"),
)


def _default_which(name: str) -> str | None:
    return shutil.which(name)


def _default_probe(argv: list[str]) -> str | None:
    """Run a bounded version probe; None when unavailable or nonzero."""

    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    text = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 and not text.strip():
        return None
    return text or None


def _version_of(text: str) -> str | None:
    first_line = text.splitlines()[0] if text.splitlines() else ""
    match = _VERSION.search(first_line)
    return match.group(0) if match else None


def collect_toolchain(
    *,
    which: WhichFn | None = None,
    probe: ProbeFn | None = None,
) -> tuple[ToolInfo, ...]:
    """Detect every known tool; missing tools carry install guidance."""

    find = which if which is not None else _default_which
    run = probe if probe is not None else _default_probe
    tools: list[ToolInfo] = []
    for spec in TOOLCHAIN_SPECS:
        path = find(spec.name)
        if path is None:
            tools.append(ToolInfo(spec.name, None, None, spec.install_hint))
            continue
        output = run(list(spec.version_argv))
        version = _version_of(output) if output else None
        tools.append(ToolInfo(spec.name, path, version, None))
    return tuple(tools)


def render_toolchain(tools: tuple[ToolInfo, ...]) -> str:
    """One line per tool: name, path/version or the install hint."""

    lines: list[str] = []
    for tool in tools:
        if tool.path is not None:
            version = f" {tool.version}" if tool.version else ""
            lines.append(f"{tool.name}{version}  {tool.path}")
        else:
            lines.append(f"{tool.name}: not found - install: {tool.install_hint}")
    return "\n".join(lines)


__all__ = [
    "TOOLCHAIN_SPECS",
    "ToolInfo",
    "ToolSpec",
    "collect_toolchain",
    "render_toolchain",
]
