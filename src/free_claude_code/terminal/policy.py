"""R0-R3 risk classification and policy decisions for terminal commands.

The classifier maps an argument vector to a risk level using executable
tables plus argument-sensitive rules (git subcommands, recursive deletes,
cloud verbs). The engine combines the level with workspace confinement and
configured allow/deny lists into a ``PolicyVerdict``; the verdict converts to
the executor's ``PolicyDecision`` only when the command may run now.

Defaults follow the roadmap permission model: R0/R1 run automatically inside
the workspace, R2 needs explicit approval, R3 needs detailed approval, and
privilege-escalation wrappers are denied outright.
"""

import os
from dataclasses import dataclass
from enum import IntEnum

from free_claude_code.terminal.spec import CommandSpec, PolicyDecision


class RiskLevel(IntEnum):
    """Roadmap risk tiers, ordered by blast radius."""

    R0_READ_ONLY = 0
    R1_REVERSIBLE = 1
    R2_PRIVILEGED = 2
    R3_DESTRUCTIVE = 3


class Requirement(IntEnum):
    """What the policy engine requires before a command may run."""

    ALLOW = 0
    APPROVE = 1
    APPROVE_DETAILED = 2
    DENY = 3


@dataclass(frozen=True)
class PolicyVerdict:
    """The engine's assessment of one command."""

    level: RiskLevel
    requirement: Requirement
    reason: str
    policy_id: str

    def to_decision(self, *, approved: bool = False) -> PolicyDecision:
        """Convert to an executor decision; approvals gate R2/R3 verdicts."""

        if self.requirement is Requirement.ALLOW:
            return PolicyDecision(
                allowed=True, reason=self.reason, policy_id=self.policy_id
            )
        if approved and self.requirement in (
            Requirement.APPROVE,
            Requirement.APPROVE_DETAILED,
        ):
            return PolicyDecision(
                allowed=True,
                reason=f"{self.reason} (approved)",
                policy_id=self.policy_id,
            )
        return PolicyDecision(
            allowed=False, reason=self.reason, policy_id=self.policy_id
        )


@dataclass(frozen=True)
class PolicyConfig:
    """Tuning for the policy engine.

    ``auto_approve_max`` is the highest risk level allowed without approval.
    ``extra_allow`` downgrades listed executables to an automatic allow;
    ``extra_deny`` denies them outright. Both match the executable basename
    (lowercase, no ``.exe`` suffix).
    """

    workspace_root: str | None = None
    auto_approve_max: RiskLevel = RiskLevel.R1_REVERSIBLE
    extra_allow: frozenset[str] = frozenset()
    extra_deny: frozenset[str] = frozenset()


_R0_COMMANDS = frozenset(
    {
        "basename",
        "cat",
        "cut",
        "date",
        "df",
        "dir",
        "dirname",
        "du",
        "echo",
        "egrep",
        "env",
        "false",
        "fgrep",
        "file",
        "findstr",
        "free",
        "grep",
        "head",
        "hostname",
        "id",
        "ifconfig",
        "ipconfig",
        "jq",
        "less",
        "ls",
        "lsof",
        "more",
        "netstat",
        "printenv",
        "printf",
        "ps",
        "pwd",
        "readlink",
        "realpath",
        "rg",
        "sleep",
        "sort",
        "ss",
        "stat",
        "tail",
        "tasklist",
        "tr",
        "tree",
        "true",
        "type",
        "uname",
        "uniq",
        "wc",
        "where",
        "whereis",
        "which",
        "whoami",
        "yq",
    }
)

_R1_COMMANDS = frozenset(
    {
        "black",
        "chmod",
        "cmake",
        "copy",
        "coverage",
        "cp",
        "dotnet",
        "eslint",
        "flake8",
        "gradle",
        "gunzip",
        "gzip",
        "isort",
        "javac",
        "jest",
        "ln",
        "make",
        "meson",
        "mkdir",
        "mocha",
        "move",
        "msbuild",
        "mv",
        "mvn",
        "mypy",
        "ninja",
        "nose2",
        "nox",
        "patch",
        "prettier",
        "pylint",
        "pyright",
        "pytest",
        "ren",
        "rename",
        "robocopy",
        "ruff",
        "sed",
        "tee",
        "touch",
        "tox",
        "tsc",
        "ty",
        "unzip",
        "vitest",
        "xcopy",
        "zip",
    }
)

_ELEVATION_COMMANDS = frozenset({"doas", "gsudo", "runas", "su", "sudo"})

_SHELL_COMMANDS = frozenset(
    {
        "bash",
        "cmd",
        "csh",
        "dash",
        "fish",
        "ksh",
        "powershell",
        "pwsh",
        "sh",
        "tcsh",
        "zsh",
    }
)

_INTERPRETER_COMMANDS = frozenset({"node", "perl", "php", "python", "python3", "ruby"})

_PACKAGE_MANAGERS = frozenset(
    {
        "apt",
        "apt-get",
        "brew",
        "bun",
        "choco",
        "dnf",
        "gem",
        "npm",
        "pip",
        "pip3",
        "pnpm",
        "uv",
        "winget",
        "yarn",
        "yum",
    }
)

_BUILD_TOOL_MANAGERS = frozenset({"cargo", "go"})

_CLOUD_CLIS = frozenset({"aws", "az", "gcloud", "gsutil"})

_REMOTE_COMMANDS = frozenset({"curl", "rsync", "scp", "sftp", "ssh", "wget"})

_PROCESS_KILLERS = frozenset({"kill", "killall", "pkill", "taskkill"})

_RM_COMMANDS = frozenset({"del", "erase", "rd", "rm", "rmdir"})

_R3_COMMANDS = frozenset(
    {
        "dd",
        "diskpart",
        "fdisk",
        "format",
        "halt",
        "mkfs",
        "mount",
        "parted",
        "poweroff",
        "reboot",
        "shutdown",
        "umount",
    }
)

_READ_VERBS = frozenset(
    {
        "api-resources",
        "api-versions",
        "cat",
        "current-context",
        "describe",
        "diff",
        "get",
        "help",
        "history",
        "images",
        "info",
        "inspect",
        "is-active",
        "is-enabled",
        "leaves",
        "list",
        "list-units",
        "logs",
        "ls",
        "outdated",
        "ps",
        "read",
        "search",
        "show",
        "stats",
        "status",
        "top",
        "validate",
        "version",
        "why",
    }
)

_VERSION_FLAGS = frozenset({"--version", "-v", "version", "--help", "-h", "help"})

_KUBECTL_CONFIG_READS = frozenset({"current-context", "get-contexts", "view"})

_COMPOSE_READS = frozenset(
    {
        "config",
        "events",
        "images",
        "logs",
        "ls",
        "ps",
        "stats",
        "top",
        "version",
    }
)

_GIT_READ = frozenset(
    {
        "blame",
        "describe",
        "diff",
        "grep",
        "log",
        "ls-files",
        "ls-remote",
        "rev-list",
        "rev-parse",
        "shortlog",
        "show",
        "status",
    }
)

_GIT_LOCAL_WRITE = frozenset(
    {
        "add",
        "apply",
        "cherry-pick",
        "checkout",
        "commit",
        "format-patch",
        "merge",
        "mv",
        "restore",
        "revert",
        "switch",
    }
)

_GIT_NETWORK = frozenset({"clone", "fetch", "pull", "remote", "submodule"})

_INTERPRETER_CODE_FLAGS = frozenset({"-c", "-e", "-E"})

_VERB_SENSITIVE = frozenset(
    {
        "docker",
        "helm",
        "journalctl",
        "kubectl",
        "podman",
        "service",
        "systemctl",
        "terraform",
        "tofu",
    }
)

# journalctl flags that delete, rotate or flush journal files.
_JOURNALCTL_WRITES = frozenset({"--rotate", "--flush", "--sync", "--relinquish-var"})


def _basename(argv0: str) -> str:
    # Normalise Windows paths so basenames resolve on any host platform.
    name = os.path.basename(argv0.replace("\\", "/")).lower()
    if name.endswith(".exe"):
        name = name[: -len(".exe")]
    return name


def _flags(args: tuple[str, ...]) -> frozenset[str]:
    return frozenset(arg for arg in args if arg.startswith("-"))


def _has_recursive_flag(flags: frozenset[str]) -> bool:
    for flag in flags:
        if flag in ("--recursive", "/s"):
            return True
        if flag.startswith("-") and not flag.startswith("--"):
            letters = flag.lstrip("-").lower()
            if "r" in letters or "s" in letters:
                return True
    return False


def _classify_rm(args: tuple[str, ...]) -> tuple[RiskLevel, str]:
    if _has_recursive_flag(_flags(args)):
        return RiskLevel.R3_DESTRUCTIVE, "rm:recursive"
    return RiskLevel.R2_PRIVILEGED, "rm:single"


def _classify_find(args: tuple[str, ...]) -> tuple[RiskLevel, str]:
    if "-delete" in args:
        return RiskLevel.R3_DESTRUCTIVE, "find:delete"
    if "-exec" in args or "-execdir" in args:
        return RiskLevel.R2_PRIVILEGED, "find:exec"
    return RiskLevel.R0_READ_ONLY, "find:read"


def _classify_git(args: tuple[str, ...]) -> tuple[RiskLevel, str]:
    if not args:
        return RiskLevel.R0_READ_ONLY, "git:help"
    sub = args[0]
    rest = args[1:]
    flags = _flags(rest)
    if sub in _GIT_READ:
        return RiskLevel.R0_READ_ONLY, f"git:{sub}"
    if sub == "push":
        if flags & frozenset({"-f", "--force", "--force-with-lease", "--delete"}):
            return RiskLevel.R3_DESTRUCTIVE, "git:push-force"
        return RiskLevel.R2_PRIVILEGED, "git:push"
    if sub == "reset":
        if "--hard" in flags:
            return RiskLevel.R3_DESTRUCTIVE, "git:reset-hard"
        return RiskLevel.R1_REVERSIBLE, "git:reset"
    if sub == "clean":
        return RiskLevel.R3_DESTRUCTIVE, "git:clean"
    if sub in ("branch", "tag"):
        if "-D" in flags:
            return RiskLevel.R3_DESTRUCTIVE, f"git:{sub}-force-delete"
        if "-d" in flags or "--delete" in flags:
            return RiskLevel.R2_PRIVILEGED, f"git:{sub}-delete"
        if any(not arg.startswith("-") for arg in rest):
            return RiskLevel.R1_REVERSIBLE, f"git:{sub}-write"
        return RiskLevel.R0_READ_ONLY, f"git:{sub}-list"
    if sub == "stash":
        if rest and rest[0] in ("pop", "drop", "clear"):
            return RiskLevel.R2_PRIVILEGED, "git:stash-drop"
        if rest and rest[0] in ("list", "show"):
            return RiskLevel.R0_READ_ONLY, "git:stash-read"
        return RiskLevel.R1_REVERSIBLE, "git:stash-save"
    if sub == "rm":
        return RiskLevel.R2_PRIVILEGED, "git:rm"
    if sub == "rebase":
        return RiskLevel.R2_PRIVILEGED, "git:rebase"
    if sub in _GIT_LOCAL_WRITE:
        return RiskLevel.R1_REVERSIBLE, f"git:{sub}"
    if sub in _GIT_NETWORK:
        return RiskLevel.R2_PRIVILEGED, f"git:{sub}"
    return RiskLevel.R2_PRIVILEGED, "git:unknown"


def _classify_verb_command(name: str, args: tuple[str, ...]) -> tuple[RiskLevel, str]:
    verbs = [arg for arg in args if not arg.startswith("-")]
    joined = " ".join(verbs)
    if not verbs and args and all(arg in _VERSION_FLAGS for arg in args):
        return RiskLevel.R0_READ_ONLY, f"{name}:version"
    if name in ("terraform", "tofu"):
        if verbs and verbs[0] == "destroy":
            return RiskLevel.R3_DESTRUCTIVE, f"{name}:destroy"
        if verbs and verbs[0] == "apply":
            return RiskLevel.R2_PRIVILEGED, f"{name}:apply"
        if verbs and verbs[0] == "version":
            return RiskLevel.R0_READ_ONLY, f"{name}:version"
        if verbs and verbs[0] in ("plan", "validate", "show", "output"):
            return RiskLevel.R1_REVERSIBLE, f"{name}:read"
        if verbs[:1] == ["state"]:
            # `state pull` prints the raw state, secrets included.
            if len(verbs) >= 2 and verbs[1] == "list":
                return RiskLevel.R1_REVERSIBLE, f"{name}:state-read"
            return RiskLevel.R2_PRIVILEGED, f"{name}:state-write"
        return RiskLevel.R2_PRIVILEGED, f"{name}:other"
    if name in ("kubectl", "helm"):
        destructive = {"delete", "drain", "uninstall"}
        if verbs and verbs[0] in destructive:
            return RiskLevel.R3_DESTRUCTIVE, f"{name}:{verbs[0]}"
        if name == "kubectl" and verbs[:1] == ["config"]:
            # `config view --raw` prints credentials; keep it privileged.
            if len(verbs) >= 2 and verbs[1] in _KUBECTL_CONFIG_READS:
                if "--raw" in args:
                    return RiskLevel.R2_PRIVILEGED, "kubectl:config-raw"
                return RiskLevel.R0_READ_ONLY, "kubectl:config-read"
            return RiskLevel.R2_PRIVILEGED, "kubectl:config-write"
        if name == "kubectl" and verbs[:1] == ["rollout"]:
            if len(verbs) >= 2 and verbs[1] in ("status", "history"):
                return RiskLevel.R0_READ_ONLY, "kubectl:rollout-read"
            return RiskLevel.R2_PRIVILEGED, "kubectl:rollout-write"
        if verbs and verbs[0] in _READ_VERBS:
            return RiskLevel.R0_READ_ONLY, f"{name}:read"
        return RiskLevel.R2_PRIVILEGED, f"{name}:write"
    if name in ("docker", "podman"):
        # Covers `docker rm` as well as `docker system/volume/image prune`.
        if "prune" in verbs:
            return RiskLevel.R3_DESTRUCTIVE, f"{name}:prune"
        if verbs and (verbs[0] in ("rm", "rmi") or "rm" in verbs):
            return RiskLevel.R3_DESTRUCTIVE, f"{name}:remove"
        if verbs[:1] == ["compose"]:
            if len(verbs) >= 2 and verbs[1] in _COMPOSE_READS:
                return RiskLevel.R0_READ_ONLY, f"{name}:compose-read"
            return RiskLevel.R2_PRIVILEGED, f"{name}:compose-write"
        if verbs and verbs[0] in _READ_VERBS:
            return RiskLevel.R0_READ_ONLY, f"{name}:read"
        return RiskLevel.R2_PRIVILEGED, f"{name}:write"
    if name in ("systemctl", "service"):
        if verbs and any(verb in _READ_VERBS for verb in verbs):
            return RiskLevel.R0_READ_ONLY, f"{name}:read"
        return RiskLevel.R2_PRIVILEGED, f"{name}:write"
    if name == "journalctl":
        flags = _flags(args)
        if any(flag.startswith("--vacuum") for flag in flags):
            return RiskLevel.R2_PRIVILEGED, "journalctl:vacuum"
        if flags & _JOURNALCTL_WRITES:
            return RiskLevel.R2_PRIVILEGED, "journalctl:write"
        return RiskLevel.R0_READ_ONLY, "journalctl:read"
    # Cloud CLIs use compound verbs like `describe-instances`; match stems.
    if verbs and any(
        verb in _READ_VERBS or verb.split("-")[0] in _READ_VERBS for verb in verbs[:4]
    ):
        return RiskLevel.R0_READ_ONLY, f"{name}:read"
    if not verbs or joined == "":
        return RiskLevel.R2_PRIVILEGED, f"{name}:bare"
    return RiskLevel.R2_PRIVILEGED, f"{name}:write"


def _classify_package_manager(
    name: str, args: tuple[str, ...]
) -> tuple[RiskLevel, str]:
    verbs = [arg for arg in args if not arg.startswith("-")]
    if verbs and verbs[0] in _READ_VERBS:
        return RiskLevel.R0_READ_ONLY, f"{name}:read"
    if (
        name in _BUILD_TOOL_MANAGERS
        and verbs
        and verbs[0]
        in (
            "build",
            "check",
            "doc",
            "fmt",
            "test",
            "vet",
        )
    ):
        return RiskLevel.R1_REVERSIBLE, f"{name}:build"
    return RiskLevel.R2_PRIVILEGED, f"{name}:mutate"


def classify(argv: tuple[str, ...]) -> tuple[RiskLevel, str]:
    """Classify one argument vector into a risk level and rule id."""

    name = _basename(argv[0])
    args = argv[1:]
    if name in _SHELL_COMMANDS:
        return RiskLevel.R3_DESTRUCTIVE, "shell:arbitrary"
    if name in _INTERPRETER_COMMANDS:
        if _flags(args) & _INTERPRETER_CODE_FLAGS:
            return RiskLevel.R3_DESTRUCTIVE, "interpreter:inline-code"
        return RiskLevel.R2_PRIVILEGED, "interpreter:script"
    if name == "git":
        return _classify_git(args)
    if name in _RM_COMMANDS:
        return _classify_rm(args)
    if name == "find":
        return _classify_find(args)
    if name in _VERB_SENSITIVE or name in _CLOUD_CLIS:
        return _classify_verb_command(name, args)
    if name in _PACKAGE_MANAGERS or name in _BUILD_TOOL_MANAGERS:
        return _classify_package_manager(name, args)
    if name in _R3_COMMANDS:
        return RiskLevel.R3_DESTRUCTIVE, f"r3:{name}"
    if name in _REMOTE_COMMANDS:
        return RiskLevel.R2_PRIVILEGED, f"remote:{name}"
    if name in _PROCESS_KILLERS:
        return RiskLevel.R2_PRIVILEGED, f"kill:{name}"
    if name in _R0_COMMANDS:
        return RiskLevel.R0_READ_ONLY, f"r0:{name}"
    if name in _R1_COMMANDS:
        return RiskLevel.R1_REVERSIBLE, f"r1:{name}"
    return RiskLevel.R2_PRIVILEGED, "unknown:command"


def confinement_violation(cwd: str | None, workspace_root: str | None) -> str | None:
    """Return why ``cwd`` escapes ``workspace_root``, or None when confined.

    ``os.path.realpath`` resolves symlinks on both sides, so a symlink inside
    the workspace pointing outside it still trips the check.
    """

    if cwd is None or workspace_root is None:
        return None
    real_cwd = os.path.normcase(os.path.realpath(cwd))
    real_root = os.path.normcase(os.path.realpath(workspace_root))
    if real_cwd == real_root or real_cwd.startswith(real_root + os.sep):
        return None
    return f"cwd {real_cwd} is outside workspace root {real_root}"


class PolicyEngine:
    """Combines classification, confinement and config into verdicts."""

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self._config = config if config is not None else PolicyConfig()

    @property
    def config(self) -> PolicyConfig:
        return self._config

    def evaluate(self, spec: CommandSpec) -> PolicyVerdict:
        name = _basename(spec.argv[0])
        if name in self._config.extra_deny:
            return PolicyVerdict(
                level=RiskLevel.R3_DESTRUCTIVE,
                requirement=Requirement.DENY,
                reason=f"{name} is denied by configuration",
                policy_id="deny:configured",
            )
        if name in _ELEVATION_COMMANDS:
            return PolicyVerdict(
                level=RiskLevel.R3_DESTRUCTIVE,
                requirement=Requirement.DENY,
                reason="privilege escalation is never permitted",
                policy_id="deny:elevation",
            )
        violation = confinement_violation(spec.cwd, self._config.workspace_root)
        if violation is not None:
            return PolicyVerdict(
                level=RiskLevel.R3_DESTRUCTIVE,
                requirement=Requirement.DENY,
                reason=violation,
                policy_id="deny:workspace-escape",
            )
        level, rule = classify(spec.argv)
        if name in self._config.extra_allow:
            return PolicyVerdict(
                level=level,
                requirement=Requirement.ALLOW,
                reason=f"{name} is allowed by configuration ({rule})",
                policy_id="allow:configured",
            )
        if level <= self._config.auto_approve_max:
            return PolicyVerdict(
                level=level,
                requirement=Requirement.ALLOW,
                reason=f"{rule} runs automatically at {level.name}",
                policy_id=f"policy:{rule}",
            )
        if level <= RiskLevel.R2_PRIVILEGED:
            return PolicyVerdict(
                level=level,
                requirement=Requirement.APPROVE,
                reason=f"{rule} requires approval at {level.name}",
                policy_id=f"policy:{rule}",
            )
        return PolicyVerdict(
            level=level,
            requirement=Requirement.APPROVE_DETAILED,
            reason=f"{rule} requires detailed approval at {level.name}",
            policy_id=f"policy:{rule}",
        )
