"""DevOps toolchain detection for fcc-inspect toolchain."""

from free_claude_code.terminal.toolchain import (
    TOOLCHAIN_SPECS,
    ToolInfo,
    collect_toolchain,
    render_toolchain,
)


def fake_which(found: dict[str, str]):
    return lambda name: found.get(name)


def fake_probe(outputs: dict[str, str]):
    def probe(argv: list[str]) -> str | None:
        return outputs.get(argv[0])

    return probe


def test_found_tool_reports_path_and_version() -> None:
    tools = collect_toolchain(
        which=fake_which({"docker": "/usr/bin/docker"}),
        probe=fake_probe({"docker": "Docker version 27.1.1, build 6312585\n"}),
    )
    docker = next(tool for tool in tools if tool.name == "docker")
    assert docker.path == "/usr/bin/docker"
    assert docker.version == "27.1.1"
    assert docker.install_hint is None


def test_missing_tool_reports_install_hint_without_probing() -> None:
    probed: list[str] = []
    tools = collect_toolchain(
        which=fake_which({}),
        probe=lambda argv: probed.append(argv[0]) or None,
    )
    assert probed == []
    for tool in tools:
        assert tool.path is None
        assert tool.version is None
        assert tool.install_hint


def test_failed_version_probe_still_reports_path() -> None:
    tools = collect_toolchain(
        which=fake_which({"kubectl": "/usr/local/bin/kubectl"}),
        probe=fake_probe({}),
    )
    kubectl = next(tool for tool in tools if tool.name == "kubectl")
    assert kubectl.path == "/usr/local/bin/kubectl"
    assert kubectl.version is None


def test_specs_cover_the_roadmap_families() -> None:
    names = {spec.name for spec in TOOLCHAIN_SPECS}
    assert {
        "docker",
        "podman",
        "kubectl",
        "helm",
        "terraform",
        "tofu",
        "aws",
        "az",
        "gcloud",
        "ansible",
        "ssh",
    } <= names


def test_render_lists_found_and_missing() -> None:
    tools = (
        ToolInfo("docker", "/usr/bin/docker", "27.1.1", None),
        ToolInfo("kubectl", None, None, "https://kubernetes.io/docs/tasks/tools/"),
    )
    text = render_toolchain(tools)
    assert "docker" in text and "27.1.1" in text
    assert "kubectl" in text and "not found" in text
    assert "https://kubernetes.io/docs/tasks/tools/" in text
