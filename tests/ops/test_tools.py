"""Tool registry: probes are fixed, read-only and cover the roadmap tools."""

from free_claude_code.ops import DEVOPS_TOOLS, ToolKind


def test_registry_covers_the_devops_set() -> None:
    kinds = {probe.kind for probe in DEVOPS_TOOLS}
    assert kinds == {
        ToolKind.DOCKER,
        ToolKind.KUBECTL,
        ToolKind.HELM,
        ToolKind.TERRAFORM,
        ToolKind.AWS,
        ToolKind.AZ,
        ToolKind.GCLOUD,
        ToolKind.JOURNALCTL,
    }


def test_probes_use_the_tool_as_argv0() -> None:
    for probe in DEVOPS_TOOLS:
        assert probe.argv[0] == probe.kind.value
        assert len(probe.argv) >= 2


def test_probe_timeouts_are_short() -> None:
    for probe in DEVOPS_TOOLS:
        assert probe.timeout_seconds <= 30.0
