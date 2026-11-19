"""The fcc-skills command line interface."""

import json

import pytest

from free_claude_code.cli.skills import main

MANIFEST = """
name = "docker-triage"
version = "0.1.0"
purpose = "Diagnose container failures"
platforms = ["linux"]
max_risk = "r0"
executable = false
"""


def make_pack(root, name, *, body=None):
    pack = root / name
    pack.mkdir(parents=True)
    (pack / "skill.toml").write_text(
        body if body is not None else MANIFEST, encoding="utf-8"
    )
    return pack


def run_cli(capsys, *argv):
    main([str(arg) for arg in argv])
    return capsys.readouterr()


def test_list_shows_packs_and_state(tmp_path, capsys):
    make_pack(tmp_path, "docker-triage")
    out = run_cli(capsys, "--packs", tmp_path, "--state", tmp_path / "s.json", "list")
    assert "docker-triage 0.1.0 [enabled]" in out.out
    assert "Diagnose container failures" in out.out


def test_list_json(tmp_path, capsys):
    make_pack(tmp_path, "docker-triage")
    out = run_cli(
        capsys,
        "--packs",
        tmp_path,
        "--no-bundled",
        "--state",
        tmp_path / "s.json",
        "--json",
        "list",
    )
    packs = json.loads(out.out)["packs"]
    assert packs[0]["name"] == "docker-triage"
    assert packs[0]["enabled"] is True
    assert packs[0]["usable"] is True


def test_list_empty(tmp_path, capsys):
    out = run_cli(
        capsys,
        "--packs",
        tmp_path,
        "--no-bundled",
        "--state",
        tmp_path / "s.json",
        "list",
    )
    assert "No skill packs found." in out.out


def test_inspect_shows_manifest_details(tmp_path, capsys):
    make_pack(tmp_path, "docker-triage")
    out = run_cli(
        capsys,
        "--packs",
        tmp_path,
        "--state",
        tmp_path / "s.json",
        "inspect",
        "docker-triage",
    )
    assert "max risk: r0_read_only" in out.out
    assert "executable code: no" in out.out


def test_inspect_unknown_pack_exits_2(tmp_path, capsys):
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--packs",
                str(tmp_path),
                "--state",
                str(tmp_path / "s.json"),
                "inspect",
                "missing",
            ]
        )
    assert caught.value.code == 2


def test_disable_then_enable_round_trip(tmp_path, capsys):
    make_pack(tmp_path, "docker-triage")
    base = ["--packs", str(tmp_path), "--state", str(tmp_path / "s.json")]
    main([*base, "disable", "docker-triage"])
    out = run_cli(capsys, *base, "list")
    assert "[disabled]" in out.out
    main([*base, "enable", "docker-triage"])
    capsys.readouterr()
    out = run_cli(capsys, *base, "--json", "list")
    assert json.loads(out.out)["packs"][0]["enabled"] is True


def test_validate_reports_issues_and_exit_1(tmp_path, capsys):
    bad = make_pack(tmp_path, "broken", body="name = [")
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--packs",
                str(tmp_path),
                "--state",
                str(tmp_path / "s.json"),
                "validate",
                str(bad),
            ]
        )
    assert caught.value.code == 1
    assert "INVALID" in capsys.readouterr().out


def test_validate_all_under_packs_root(tmp_path, capsys):
    make_pack(tmp_path, "docker-triage")
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--packs",
                str(tmp_path),
                "--state",
                str(tmp_path / "s.json"),
                "validate",
            ]
        )
    assert caught.value.code == 0
    assert "docker-triage: ok" in capsys.readouterr().out


def test_default_list_includes_bundled_packs(tmp_path, capsys):
    out = run_cli(capsys, "--state", tmp_path / "s.json", "list")
    assert "ci-triage" in out.out
    assert "docker-triage" in out.out
    assert "k8s-inspect" in out.out


def test_user_pack_shadows_bundled_pack(tmp_path, capsys):
    user = tmp_path / "packs"
    make_pack(
        user,
        "ci-triage",
        body=MANIFEST.replace("docker-triage", "ci-triage").replace("0.1.0", "9.9.9"),
    )
    out = run_cli(capsys, "--packs", user, "--state", tmp_path / "s.json", "list")
    assert "ci-triage 9.9.9" in out.out
    assert "ci-triage 0.1.0" not in out.out
