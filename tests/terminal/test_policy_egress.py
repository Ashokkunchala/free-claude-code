"""Network egress controls: open/allowlist/deny modes and env parsing."""

import pytest

from free_claude_code.terminal import (
    CommandSpec,
    EgressMode,
    PolicyConfig,
    PolicyEngine,
    Requirement,
    policy_config_from_env,
)


def spec(*argv: str) -> CommandSpec:
    return CommandSpec(argv=tuple(argv))


def engine(
    network_egress: EgressMode = EgressMode.OPEN,
    network_allow: frozenset[str] = frozenset(),
    extra_allow: frozenset[str] = frozenset(),
    extra_deny: frozenset[str] = frozenset(),
) -> PolicyEngine:
    return PolicyEngine(
        PolicyConfig(
            network_egress=network_egress,
            network_allow=network_allow,
            extra_allow=extra_allow,
            extra_deny=extra_deny,
        )
    )


def test_open_mode_keeps_risk_flow() -> None:
    verdict = engine(network_egress=EgressMode.OPEN).evaluate(
        spec("git", "push", "origin", "main")
    )
    assert verdict.requirement is Requirement.APPROVE
    assert "git:push" in verdict.reason


def test_deny_mode_blocks_every_network_command() -> None:
    e = engine(network_egress=EgressMode.DENIED)
    for argv in (
        ("curl", "https://example.com"),
        ("wget", "https://example.com/f"),
        ("ssh", "deploy@example.com"),
        ("git", "push", "origin", "main"),
        ("git", "clone", "https://github.com/a/b.git"),
        ("pip", "install", "requests"),
        ("aws", "ec2", "describe-instances"),
        ("kubectl", "get", "pods"),
        ("terraform", "plan"),
    ):
        verdict = e.evaluate(spec(*argv))
        assert verdict.requirement is Requirement.DENY, argv
        assert verdict.policy_id == "deny:egress"


def test_deny_mode_leaves_local_commands_alone() -> None:
    e = engine(network_egress=EgressMode.DENIED)
    assert e.evaluate(spec("ls", "-la")).requirement is Requirement.ALLOW
    assert e.evaluate(spec("git", "status")).requirement is Requirement.ALLOW
    assert e.evaluate(spec("git", "commit", "-m", "x")).requirement is Requirement.ALLOW
    verdict = e.evaluate(spec("git", "push", "--force", "origin", "main"))
    assert verdict.policy_id == "deny:egress"


def test_allowlist_permits_listed_hosts_through_normal_flow() -> None:
    e = engine(
        network_egress=EgressMode.ALLOWLIST,
        network_allow=frozenset({"github.com"}),
    )
    verdict = e.evaluate(spec("git", "clone", "https://github.com/a/b.git"))
    assert verdict.requirement is Requirement.APPROVE
    assert "git:clone" in verdict.reason
    verdict = e.evaluate(spec("curl", "https://github.com/releases/x"))
    assert verdict.requirement is not Requirement.DENY


def test_allowlist_matches_subdomains_with_leading_dot() -> None:
    e = engine(
        network_egress=EgressMode.ALLOWLIST,
        network_allow=frozenset({".example.com"}),
    )
    assert (
        e.evaluate(spec("curl", "https://api.example.com/v1")).requirement
        is not Requirement.DENY
    )
    assert (
        e.evaluate(spec("curl", "https://example.com/")).requirement
        is not Requirement.DENY
    )
    denied = e.evaluate(spec("curl", "https://notexample.com/"))
    assert denied.requirement is Requirement.DENY
    assert denied.policy_id == "deny:egress-allowlist"


def test_allowlist_denies_unlisted_and_scp_style_hosts() -> None:
    e = engine(
        network_egress=EgressMode.ALLOWLIST,
        network_allow=frozenset({"github.com"}),
    )
    verdict = e.evaluate(spec("git", "clone", "git@gitlab.com:a/b.git"))
    assert verdict.requirement is Requirement.DENY
    assert "gitlab.com" in verdict.reason
    verdict = e.evaluate(spec("ssh", "deploy@internal.host"))
    assert verdict.requirement is Requirement.DENY
    assert "internal.host" in verdict.reason


def test_allowlist_denies_unverifiable_targets() -> None:
    e = engine(
        network_egress=EgressMode.ALLOWLIST,
        network_allow=frozenset({"github.com"}),
    )
    # `origin` names a configured remote; the real host is unknowable.
    verdict = e.evaluate(spec("git", "push", "origin", "main"))
    assert verdict.requirement is Requirement.DENY
    assert verdict.policy_id == "deny:egress-unverifiable"
    # Cloud CLIs hide endpoints behind profiles.
    verdict = e.evaluate(spec("aws", "s3", "ls"))
    assert verdict.policy_id == "deny:egress-unverifiable"
    # A bare ssh with only flags can still smuggle a ProxyCommand.
    verdict = e.evaluate(spec("ssh", "-N"))
    assert verdict.policy_id == "deny:egress-unverifiable"


def test_allowlist_leaves_local_git_and_commands_alone() -> None:
    e = engine(network_egress=EgressMode.ALLOWLIST, network_allow=frozenset())
    assert e.evaluate(spec("git", "status")).requirement is Requirement.ALLOW
    assert e.evaluate(spec("git", "log", "--oneline")).requirement is Requirement.ALLOW
    assert e.evaluate(spec("ls")).requirement is Requirement.ALLOW


def test_egress_boundary_beats_configured_auto_allow() -> None:
    e = engine(
        network_egress=EgressMode.DENIED,
        extra_allow=frozenset({"curl"}),
    )
    verdict = e.evaluate(spec("curl", "https://example.com"))
    assert verdict.requirement is Requirement.DENY
    assert verdict.policy_id == "deny:egress"


def test_explicit_deny_still_wins_over_open_egress() -> None:
    e = engine(
        network_egress=EgressMode.OPEN,
        extra_deny=frozenset({"curl"}),
    )
    verdict = e.evaluate(spec("curl", "https://example.com"))
    assert verdict.requirement is Requirement.DENY
    assert verdict.policy_id == "deny:configured"


def test_policy_config_from_env_defaults_to_open() -> None:
    config = policy_config_from_env(workspace_root="/tmp/ws", environ={})
    assert config.network_egress is EgressMode.OPEN
    assert config.network_allow == frozenset()
    assert config.workspace_root == "/tmp/ws"


def test_policy_config_from_env_parses_allowlist() -> None:
    config = policy_config_from_env(
        environ={
            "FCC_NETWORK_EGRESS": " allowlist ",
            "FCC_NETWORK_ALLOW": "github.com, .Example.com ,,",
        }
    )
    assert config.network_egress is EgressMode.ALLOWLIST
    assert config.network_allow == frozenset({"github.com", ".example.com"})


def test_policy_config_from_env_accepts_deny_alias() -> None:
    config = policy_config_from_env(environ={"FCC_NETWORK_EGRESS": "denied"})
    assert config.network_egress is EgressMode.DENIED


def test_policy_config_from_env_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="FCC_NETWORK_EGRESS"):
        policy_config_from_env(environ={"FCC_NETWORK_EGRESS": "maybe"})
