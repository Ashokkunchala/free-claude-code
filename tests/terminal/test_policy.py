"""Policy engine tests: classification, confinement and verdicts."""

import os
from pathlib import Path

import pytest

from free_claude_code.terminal import (
    CommandSpec,
    PolicyConfig,
    PolicyEngine,
    Requirement,
    RiskLevel,
)
from free_claude_code.terminal.policy import classify, confinement_violation


def spec(*argv: str, cwd: str | None = None) -> CommandSpec:
    return CommandSpec(argv=tuple(argv), cwd=cwd)


def test_read_only_commands_are_r0() -> None:
    for argv in (
        ("ls", "-la"),
        ("cat", "README.md"),
        ("git", "status"),
        ("git", "log", "--oneline"),
        ("git", "diff", "HEAD~1"),
        ("grep", "-r", "TODO", "src"),
        ("ps", "aux"),
        ("df", "-h"),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R0_READ_ONLY, argv


def test_reversible_local_commands_are_r1() -> None:
    for argv in (
        ("touch", "new.txt"),
        ("mkdir", "build"),
        ("cp", "a", "b"),
        ("mv", "a", "b"),
        ("pytest", "-q"),
        ("ruff", "check", "."),
        ("make", "all"),
        ("git", "add", "src/"),
        ("git", "commit", "-m", "x"),
        ("git", "checkout", "-b", "feature"),
        ("git", "branch", "feature"),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R1_REVERSIBLE, argv


def test_privileged_commands_are_r2() -> None:
    for argv in (
        ("pip", "install", "requests"),
        ("npm", "install"),
        ("git", "push", "origin", "main"),
        ("git", "fetch"),
        ("curl", "https://example.com"),
        ("ssh", "host", "uptime"),
        ("docker", "run", "alpine"),
        ("kubectl", "apply", "-f", "deploy.yaml"),
        ("terraform", "apply"),
        ("systemctl", "restart", "nginx"),
        ("kill", "1234"),
        ("totally-unknown-tool", "--flag"),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R2_PRIVILEGED, argv


def test_destructive_commands_are_r3() -> None:
    for argv in (
        ("rm", "-rf", "build"),
        ("rm", "-r", "build"),
        ("git", "push", "--force"),
        ("git", "reset", "--hard"),
        ("git", "clean", "-fdx"),
        ("git", "branch", "-D", "old"),
        ("dd", "if=/dev/zero", "of=/dev/sda"),
        ("shutdown", "-h", "now"),
        ("bash", "-c", "echo hi"),
        ("sh", "script.sh"),
        ("python", "-c", "print(1)"),
        ("node", "-e", "console.log(1)"),
        ("kubectl", "delete", "pod", "x"),
        ("helm", "uninstall", "release"),
        ("terraform", "destroy"),
        ("docker", "system", "prune"),
        ("find", ".", "-delete"),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R3_DESTRUCTIVE, argv


def test_read_verbs_keep_cloud_and_verb_tools_r0() -> None:
    for argv in (
        ("aws", "s3", "ls"),
        ("aws", "ec2", "describe-instances"),
        ("gcloud", "projects", "list"),
        ("kubectl", "get", "pods"),
        ("kubectl", "logs", "pod-x"),
        ("docker", "ps"),
        ("docker", "inspect", "abc"),
        ("systemctl", "status", "nginx"),
        ("terraform", "plan"),
        ("helm", "list"),
        ("pip", "list"),
        ("cargo", "build"),
    ):
        level, _ = classify(argv)
        assert level in (RiskLevel.R0_READ_ONLY, RiskLevel.R1_REVERSIBLE), argv


def test_rm_single_file_is_r2() -> None:
    level, rule = classify(("rm", "file.txt"))
    assert level is RiskLevel.R2_PRIVILEGED
    assert rule == "rm:single"


def test_exe_suffix_is_stripped() -> None:
    level, _ = classify(("C:\\Windows\\System32\\cmd.exe", "/c", "dir"))
    assert level is RiskLevel.R3_DESTRUCTIVE
    level, _ = classify(("pytest.EXE",))
    assert level is RiskLevel.R1_REVERSIBLE


def test_find_exec_is_r2() -> None:
    level, _ = classify(("find", ".", "-exec", "echo", "{}", ";"))
    assert level is RiskLevel.R2_PRIVILEGED


def test_elevation_is_denied() -> None:
    engine = PolicyEngine()
    for argv in (("sudo", "ls"), ("doas", "true"), ("runas", "/user:x", "cmd")):
        verdict = engine.evaluate(spec(*argv))
        assert verdict.requirement is Requirement.DENY, argv
        assert verdict.policy_id == "deny:elevation"
        assert not verdict.to_decision().allowed


def test_unknown_defaults_to_approval() -> None:
    verdict = PolicyEngine().evaluate(spec("mystery-binary"))
    assert verdict.level is RiskLevel.R2_PRIVILEGED
    assert verdict.requirement is Requirement.APPROVE
    assert not verdict.to_decision().allowed
    assert verdict.to_decision(approved=True).allowed


def test_r0_and_r1_auto_allow() -> None:
    engine = PolicyEngine()
    assert engine.evaluate(spec("ls")).requirement is Requirement.ALLOW
    assert engine.evaluate(spec("pytest", "-q")).requirement is Requirement.ALLOW


def test_r3_needs_detailed_approval() -> None:
    verdict = PolicyEngine().evaluate(spec("rm", "-rf", "x"))
    assert verdict.requirement is Requirement.APPROVE_DETAILED
    assert not verdict.to_decision().allowed
    assert verdict.to_decision(approved=True).allowed


def test_confinement_allows_inside_workspace(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    inner = root / "pkg"
    inner.mkdir()
    assert confinement_violation(str(inner), str(root)) is None
    assert confinement_violation(str(root), str(root)) is None
    assert confinement_violation(None, str(root)) is None
    assert confinement_violation(str(inner), None) is None


def test_confinement_denies_outside_workspace(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert confinement_violation(str(outside), str(root)) is not None
    sibling = tmp_path / "repo-other"
    sibling.mkdir()
    assert confinement_violation(str(sibling), str(root)) is not None


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_confinement_catches_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "linked"
    link.symlink_to(outside, target_is_directory=True)
    assert confinement_violation(str(link), str(root)) is not None


def test_engine_denies_workspace_escape(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    engine = PolicyEngine(PolicyConfig(workspace_root=str(root)))
    verdict = engine.evaluate(spec("ls", cwd=str(tmp_path)))
    assert verdict.requirement is Requirement.DENY
    assert verdict.policy_id == "deny:workspace-escape"
    inside = engine.evaluate(spec("ls", cwd=str(root)))
    assert inside.requirement is Requirement.ALLOW


def test_configured_allow_and_deny() -> None:
    config = PolicyConfig(
        extra_allow=frozenset({"deploy-tool"}),
        extra_deny=frozenset({"legacy-tool"}),
    )
    engine = PolicyEngine(config)
    allowed = engine.evaluate(spec("deploy-tool", "--prod"))
    assert allowed.requirement is Requirement.ALLOW
    assert allowed.policy_id == "allow:configured"
    denied = engine.evaluate(spec("legacy-tool"))
    assert denied.requirement is Requirement.DENY
    assert denied.policy_id == "deny:configured"


def test_auto_approve_max_is_configurable() -> None:
    engine = PolicyEngine(PolicyConfig(auto_approve_max=RiskLevel.R0_READ_ONLY))
    assert engine.evaluate(spec("ls")).requirement is Requirement.ALLOW
    assert engine.evaluate(spec("pytest")).requirement is Requirement.APPROVE


def test_verdict_reason_mentions_rule() -> None:
    verdict = PolicyEngine().evaluate(spec("git", "push"))
    assert verdict.policy_id == "policy:git:push"
    assert "git:push" in verdict.reason


def test_executor_runs_allowed_and_rejects_denied(
    echo_stdout: tuple[str, ...],
) -> None:
    from free_claude_code.terminal import PolicyRejectedError, TerminalExecutor

    executor = TerminalExecutor()
    allowed = PolicyEngine().evaluate(spec("ls")).to_decision()
    result = executor.run(CommandSpec(argv=echo_stdout), policy=allowed)
    assert result.exit_code == 0

    denied = PolicyEngine().evaluate(spec("sudo", "ls")).to_decision()
    with pytest.raises(PolicyRejectedError):
        executor.run(CommandSpec(argv=echo_stdout), policy=denied)


def test_devops_version_probes_are_r0() -> None:
    for argv in (
        ("docker", "--version"),
        ("kubectl", "version", "--client"),
        ("helm", "version", "--short"),
        ("aws", "--version"),
        ("az", "version"),
        ("gcloud", "--version"),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R0_READ_ONLY, argv


def test_terraform_version_is_read_only() -> None:
    for argv in (("terraform", "version"), ("tofu", "version")):
        level, _ = classify(argv)
        assert level in (RiskLevel.R0_READ_ONLY, RiskLevel.R1_REVERSIBLE), argv


def test_kubectl_config_read_verbs_are_r0() -> None:
    for argv in (
        ("kubectl", "config", "current-context"),
        ("kubectl", "config", "get-contexts"),
        ("kubectl", "config", "view"),
        ("kubectl", "config", "view", "--minify"),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R0_READ_ONLY, argv


def test_kubectl_config_raw_and_writes_stay_privileged() -> None:
    for argv in (
        ("kubectl", "config", "view", "--raw"),
        ("kubectl", "config", "use-context", "prod"),
        ("kubectl", "config", "set-context", "dev"),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R2_PRIVILEGED, argv


def test_kubectl_rollout_read_verbs_are_r0() -> None:
    for argv in (
        ("kubectl", "rollout", "status", "deployment/web"),
        ("kubectl", "rollout", "history", "deployment/web"),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R0_READ_ONLY, argv


def test_kubectl_rollout_mutations_stay_privileged() -> None:
    for argv in (
        ("kubectl", "rollout", "restart", "deployment/web"),
        ("kubectl", "rollout", "undo", "deployment/web"),
        ("kubectl", "rollout", "pause", "deployment/web"),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R2_PRIVILEGED, argv


def test_docker_compose_read_verbs_are_r0() -> None:
    for argv in (
        ("docker", "compose", "ps", "-a"),
        ("docker", "compose", "logs", "web"),
        ("docker", "compose", "config"),
        ("docker", "compose", "images"),
        ("docker", "compose", "ls"),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R0_READ_ONLY, argv


def test_docker_compose_mutations_stay_privileged() -> None:
    for argv in (
        ("docker", "compose", "up", "-d"),
        ("docker", "compose", "down"),
        ("docker", "compose", "build"),
        ("docker", "compose", "restart"),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R2_PRIVILEGED, argv
