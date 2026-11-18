"""Read-only infrastructure diagnostics.

Every diagnostic probe is a fixed argument vector the terminal policy
must allow (R0/R1) before it runs - the runner raises
``PolicyRejectedError`` for anything the policy gates, so diagnostics
cannot become a write path. Plan builders only emit read-only probes:
inspect, list, get, describe, logs, status. Mutating operations
(apply, destroy, rollout restart, prune, ...) are never part of a
diagnostic plan; they stay behind the approval flow like any other
command.
"""

from dataclasses import dataclass
from pathlib import Path

from free_claude_code.ops.tools import DEVOPS_TOOLS, ToolProbe, ToolStatus
from free_claude_code.terminal import (
    CommandResult,
    CommandSpec,
    PolicyConfig,
    PolicyEngine,
    PolicyRejectedError,
    Requirement,
    TerminalExecutor,
)
from free_claude_code.terminal.spec import DEFAULT_TIMEOUT_SECONDS

_TAIL_CHARS = 4000


@dataclass(frozen=True)
class Probe:
    """One read-only diagnostic command."""

    name: str
    argv: tuple[str, ...]
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class ProbeResult:
    """The outcome of one probe: output tail plus success flags."""

    name: str
    argv: tuple[str, ...]
    ok: bool
    exit_code: int | None
    output_tail: str
    timed_out: bool = False


@dataclass(frozen=True)
class DiagnosticReport:
    """The results of one diagnostic plan, in run order."""

    subject: str
    results: tuple[ProbeResult, ...]

    @property
    def ok(self) -> bool:
        return bool(self.results) and all(result.ok for result in self.results)

    @property
    def failed(self) -> tuple[ProbeResult, ...]:
        return tuple(result for result in self.results if not result.ok)

    def summary(self) -> str:
        lines = []
        for result in self.results:
            status = "ok" if result.ok else f"FAIL (exit {result.exit_code})"
            lines.append(f"{result.name}: {status} {' '.join(result.argv)}")
        return "\n".join(lines)


def docker_diagnostics() -> tuple[Probe, ...]:
    """Docker daemon, containers and images (read-only)."""

    return (
        Probe("docker-info", ("docker", "info")),
        Probe("docker-containers", ("docker", "ps", "-a")),
        Probe("docker-images", ("docker", "images")),
    )


def compose_diagnostics() -> tuple[Probe, ...]:
    """Compose project state in the current directory (read-only)."""

    return (
        Probe("compose-config", ("docker", "compose", "config", "--quiet")),
        Probe("compose-services", ("docker", "compose", "ps", "-a")),
    )


def kubernetes_diagnostics(namespace: str | None = None) -> tuple[Probe, ...]:
    """Cluster context, workloads, services and recent events (read-only)."""

    scope = ("-n", namespace) if namespace else ("-A",)
    return (
        Probe("k8s-context", ("kubectl", "config", "current-context")),
        Probe("k8s-namespaces", ("kubectl", "get", "namespaces")),
        Probe("k8s-pods", ("kubectl", "get", "pods", *scope, "-o", "wide")),
        Probe("k8s-deployments", ("kubectl", "get", "deployments", *scope)),
        Probe("k8s-services", ("kubectl", "get", "services", *scope)),
        Probe("k8s-events", ("kubectl", "get", "events", *scope)),
    )


def rollout_status(deployment: str, namespace: str | None = None) -> Probe:
    """One deployment's rollout status (read-only)."""

    argv = ("kubectl", "rollout", "status", f"deployment/{deployment}")
    if namespace:
        argv = (*argv, "-n", namespace)
    return Probe("k8s-rollout-status", argv, timeout_seconds=60.0)


def terraform_diagnostics() -> tuple[Probe, ...]:
    """Terraform version and configuration validity (read-only)."""

    return (
        Probe("terraform-version", ("terraform", "version")),
        Probe("terraform-validate", ("terraform", "validate", "-no-color")),
    )


def aws_diagnostics() -> tuple[Probe, ...]:
    """AWS identity and region inventory (read-only)."""

    return (
        Probe("aws-identity", ("aws", "sts", "get-caller-identity")),
        Probe("aws-regions", ("aws", "ec2", "describe-regions")),
    )


def azure_diagnostics() -> tuple[Probe, ...]:
    """Azure account and subscription context (read-only)."""

    return (Probe("az-account", ("az", "account", "show")),)


def gcloud_diagnostics() -> tuple[Probe, ...]:
    """Google Cloud active configuration (read-only)."""

    return (Probe("gcloud-config", ("gcloud", "config", "list")),)


class DiagnosticsRunner:
    """Runs read-only diagnostic probes under the terminal policy."""

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
            else PolicyEngine(PolicyConfig(workspace_root=str(self._root)))
        )

    @property
    def root(self) -> Path:
        return self._root

    def detect_tool(
        self, probe: ToolProbe, *, task_id: str | None = None
    ) -> ToolStatus:
        """Probe one tool's version; unavailable tools are not errors."""

        spec = CommandSpec(
            argv=probe.argv,
            cwd=str(self._root),
            timeout_seconds=probe.timeout_seconds,
        )
        verdict = self._engine.evaluate(spec)
        if verdict.requirement is not Requirement.ALLOW:
            decision = verdict.to_decision()
            raise PolicyRejectedError(probe.argv, verdict.reason or decision.reason)
        result = self._executor.run(spec, policy=verdict.to_decision(), task_id=task_id)
        output = (result.stdout + result.stderr).decode("utf-8", "replace")
        first_line = output.strip().splitlines()[0] if output.strip() else ""
        return ToolStatus(
            kind=probe.kind,
            argv=probe.argv,
            available=result.exit_code == 0,
            version_line=first_line,
            exit_code=result.exit_code,
        )

    def detect_all(
        self,
        probes: tuple[ToolProbe, ...] = DEVOPS_TOOLS,
        *,
        task_id: str | None = None,
    ) -> tuple[ToolStatus, ...]:
        """Detect every known DevOps tool, in registry order."""

        return tuple(self.detect_tool(probe, task_id=task_id) for probe in probes)

    def run_probe(self, probe: Probe, *, task_id: str | None = None) -> ProbeResult:
        """Run one probe after policy evaluation."""

        spec = CommandSpec(
            argv=probe.argv,
            cwd=str(self._root),
            timeout_seconds=probe.timeout_seconds,
        )
        verdict = self._engine.evaluate(spec)
        if verdict.requirement is not Requirement.ALLOW:
            decision = verdict.to_decision()
            raise PolicyRejectedError(probe.argv, verdict.reason or decision.reason)
        result = self._executor.run(spec, policy=verdict.to_decision(), task_id=task_id)
        return _result_from_command(probe, result)

    def run_plan(
        self,
        subject: str,
        probes: tuple[Probe, ...] | list[Probe],
        *,
        task_id: str | None = None,
    ) -> DiagnosticReport:
        """Run every probe in the plan; a failed probe never stops the plan."""

        results = [self.run_probe(probe, task_id=task_id) for probe in probes]
        return DiagnosticReport(subject, tuple(results))


def _result_from_command(probe: Probe, result: CommandResult) -> ProbeResult:
    output = (result.stdout + result.stderr).decode("utf-8", "replace")
    timed_out = result.exit_code is None
    return ProbeResult(
        name=probe.name,
        argv=probe.argv,
        ok=result.exit_code == 0,
        exit_code=result.exit_code,
        output_tail=output[-_TAIL_CHARS:],
        timed_out=timed_out,
    )
