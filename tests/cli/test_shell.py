"""fcc-shell CLI: interactive session loop, commands and exit codes."""

import io
import json
import subprocess

import pytest

from free_claude_code.cli.shell import main
from free_claude_code.terminal import RunPlan

GIT_STATUS = ("git", "status")


def invoke(args, stdin_text, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    try:
        main(args)
    except SystemExit as exc:
        return exc.code
    return 0


def base_args(tmp_path):
    return [
        "--workspace",
        str(tmp_path),
        "--approvals-file",
        str(tmp_path / "approvals.json"),
        "--secret-file",
        str(tmp_path / "secret"),
        "--audit-file",
        str(tmp_path / "audit.jsonl"),
        "--runs-dir",
        str(tmp_path / "runs"),
    ]


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.fixture
def fake_model(monkeypatch):
    from free_claude_code.cli import shell as shell_cli

    monkeypatch.setattr(shell_cli, "plan_model_from_env", lambda: object())
    return shell_cli


def plan_for(argv):
    return RunPlan.from_dict(
        {"goal": "demo", "steps": [{"id": "s1", "title": "run", "argv": list(argv)}]}
    )


def stub_propose(monkeypatch, shell_cli, plans, seen=None):
    queue = iter(plans)

    def fake(task, model, *, workspace, context, skills, attempts):
        if seen is not None:
            seen.append((task, context))
        return next(queue)

    monkeypatch.setattr(shell_cli, "propose_plan_with_feedback", fake)


def test_model_not_configured_exits_one(capsys, tmp_path, monkeypatch):
    from free_claude_code.cli import shell as shell_cli
    from free_claude_code.terminal import PlanModelError

    def boom():
        raise PlanModelError("plan model is not configured; set FCC_PLAN_BASE_URL")

    monkeypatch.setattr(shell_cli, "plan_model_from_env", boom)
    code = invoke(base_args(tmp_path), "", monkeypatch)
    assert code == 1
    assert "not configured" in capsys.readouterr().err


def test_empty_session_exits_zero(capsys, tmp_path, fake_model, monkeypatch):
    code = invoke(base_args(tmp_path), "", monkeypatch)
    assert code == 0
    assert "fcc-shell: workspace" in capsys.readouterr().err


def test_task_runs_and_reports(capsys, repo, fake_model, monkeypatch):
    seen = []
    stub_propose(monkeypatch, fake_model, [plan_for(GIT_STATUS)], seen)
    code = invoke(base_args(repo), "say hi\nexit\n", monkeypatch)
    assert code == 0
    out = capsys.readouterr().out
    assert "status: ok" in out
    assert seen and seen[0][0] == "say hi"
    assert seen[0][1] is not None and "Platform:" in seen[0][1]


def test_context_collected_once_per_session(capsys, repo, fake_model, monkeypatch):
    calls = []

    def fake_collect(workspace):
        calls.append(workspace)
        return {}

    monkeypatch.setattr(fake_model, "collect_env_context", fake_collect)
    monkeypatch.setattr(fake_model, "render_env_context", lambda ctx: "CTX")
    stub_propose(monkeypatch, fake_model, [plan_for(GIT_STATUS), plan_for(GIT_STATUS)])
    code = invoke(base_args(repo), "one\ntwo\n", monkeypatch)
    assert code == 0
    assert len(calls) == 1


def test_no_context_passes_none(capsys, repo, fake_model, monkeypatch):
    seen = []
    stub_propose(monkeypatch, fake_model, [plan_for(GIT_STATUS)], seen)
    code = invoke([*base_args(repo), "--no-context"], "go\n", monkeypatch)
    assert code == 0
    assert seen == [("go", None)]


def test_help_and_unknown_command(capsys, tmp_path, fake_model, monkeypatch):
    code = invoke(base_args(tmp_path), ":help\n:bogus\nexit\n", monkeypatch)
    assert code == 0
    captured = capsys.readouterr()
    assert ":history TASK_ID" in captured.out
    assert "unknown command" in captured.err


def test_context_command_reprints(capsys, tmp_path, fake_model, monkeypatch):
    monkeypatch.setattr(fake_model, "collect_env_context", lambda workspace: {})
    monkeypatch.setattr(fake_model, "render_env_context", lambda ctx: "CTX-HERE")
    code = invoke(base_args(tmp_path), ":context\n", monkeypatch)
    assert code == 0
    assert "CTX-HERE" in capsys.readouterr().out


def test_history_unknown_task_fails(capsys, tmp_path, fake_model, monkeypatch):
    code = invoke(base_args(tmp_path), ":history nope\n", monkeypatch)
    assert code == 1
    assert "no recorded run" in capsys.readouterr().err


def test_history_prints_recorded_run(capsys, repo, fake_model, monkeypatch):
    stub_propose(monkeypatch, fake_model, [plan_for(GIT_STATUS)])
    code = invoke(base_args(repo), "go\n", monkeypatch)
    assert code == 0
    task_id = capsys.readouterr().out.split("(task ")[1].split(")")[0]

    code = invoke(base_args(repo), f":history {task_id}\n", monkeypatch)
    assert code == 0
    out = capsys.readouterr().out
    assert task_id in out
    assert "git status" in out


def test_invalid_model_plan_exits_two(capsys, tmp_path, fake_model, monkeypatch):
    from free_claude_code.terminal import PlanError

    def junk(task, model, *, workspace, context, skills, attempts):
        raise PlanError("steps must be a non-empty array")

    monkeypatch.setattr(fake_model, "propose_plan_with_feedback", junk)
    code = invoke(base_args(tmp_path), "go\n", monkeypatch)
    assert code == 2
    assert "valid plan" in capsys.readouterr().err


def test_json_report_goes_to_stdout(capsys, repo, fake_model, monkeypatch):
    stub_propose(monkeypatch, fake_model, [plan_for(GIT_STATUS)])
    code = invoke([*base_args(repo), "--json"], "go\n", monkeypatch)
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "ok"


def test_repair_loop_runs_on_failure(capsys, repo, fake_model, monkeypatch):
    # git log on a commit-less repo exits non-zero (R0 read, so it runs).
    stub_propose(monkeypatch, fake_model, [plan_for(("git", "log"))])
    repairs = []

    def fake_repair(task, report, model, *, workspace, context, skills, attempts):
        repairs.append(report.status)
        return plan_for(GIT_STATUS)

    monkeypatch.setattr(fake_model, "propose_repair", fake_repair)
    code = invoke([*base_args(repo), "--repair-attempts", "1"], "go\n", monkeypatch)
    assert code == 0
    assert repairs == ["failed"]
    assert "status: ok" in capsys.readouterr().out


def test_failed_task_sticks_exit_code(capsys, tmp_path, fake_model, monkeypatch):
    stub_propose(monkeypatch, fake_model, [plan_for(GIT_STATUS)])
    code = invoke(base_args(tmp_path), "go\n", monkeypatch)
    assert code == 1
    assert "status: failed" in capsys.readouterr().out


_PACK_MANIFEST = """
name = "{name}"
version = "0.1.0"
purpose = "Test pack"
platforms = ["linux"]
max_risk = "r0"
executable = false
"""


def test_skill_guidance_attached_to_every_task(capsys, repo, fake_model, monkeypatch):
    packs_dir = repo / "packs"
    pack = packs_dir / "docker-triage"
    pack.mkdir(parents=True)
    (pack / "skill.toml").write_text(
        _PACK_MANIFEST.format(name="docker-triage"), encoding="utf-8"
    )
    (pack / "SKILL.md").write_text("Run docker ps first.", encoding="utf-8")
    captured = []

    def fake(task, model, *, workspace, context, skills, attempts):
        captured.append(skills)
        return plan_for(GIT_STATUS)

    monkeypatch.setattr(fake_model, "propose_plan_with_feedback", fake)
    code = invoke(
        [
            *base_args(repo),
            "--packs",
            str(packs_dir),
            "--skills-state",
            str(repo / "skills-state.json"),
        ],
        "one\ntwo\nexit\n",
        monkeypatch,
    )
    assert code == 0
    assert len(captured) == 2
    assert all(skills is not None and "docker-triage" in skills for skills in captured)


def test_no_skills_passes_none(capsys, repo, fake_model, monkeypatch):
    captured = []

    def fake(task, model, *, workspace, context, skills, attempts):
        captured.append(skills)
        return plan_for(GIT_STATUS)

    monkeypatch.setattr(fake_model, "propose_plan_with_feedback", fake)
    code = invoke([*base_args(repo), "--no-skills"], "one\nexit\n", monkeypatch)
    assert code == 0
    assert captured == [None]
