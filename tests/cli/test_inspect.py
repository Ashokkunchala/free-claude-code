"""Tests for the fcc-inspect CLI."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from free_claude_code.cli.inspect import main


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (root / "README.md").write_text("# demo\nneedle line\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (root / "ignored").mkdir()
    (root / "ignored" / "hidden.py").write_text("needle hidden\n", encoding="utf-8")
    return root


def run(argv: list[str]) -> None:
    main(argv)


def test_read_plain(workspace: Path, capsys) -> None:
    run(["--workspace", str(workspace), "read", "README.md"])
    assert "needle line" in capsys.readouterr().out


def test_read_json(workspace: Path, capsys) -> None:
    run(["--workspace", str(workspace), "--json", "read", "src/app.py"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["truncated"] is False
    assert "hello" in payload["content"]


def test_read_missing_file_fails(workspace: Path, capsys) -> None:
    with pytest.raises(SystemExit):
        run(["--workspace", str(workspace), "read", "nope.txt"])
    assert "fcc-inspect" in capsys.readouterr().err


def test_read_escape_fails(workspace: Path, capsys) -> None:
    with pytest.raises(SystemExit):
        run(["--workspace", str(workspace), "read", "../outside.txt"])
    assert "outside workspace root" in capsys.readouterr().err


def test_files_glob(workspace: Path, capsys) -> None:
    run(["--workspace", str(workspace), "files", "**/*.py"])
    out = capsys.readouterr().out
    assert "src/app.py" in out
    assert "ignored/hidden.py" not in out


def test_files_json(workspace: Path, capsys) -> None:
    run(["--workspace", str(workspace), "--json", "files", "*.md"])
    assert json.loads(capsys.readouterr().out) == ["README.md"]


def test_grep_plain(workspace: Path, capsys) -> None:
    run(["--workspace", str(workspace), "grep", "needle"])
    out = capsys.readouterr().out
    assert "README.md:2: needle line" in out
    assert "hidden" not in out


def test_grep_glob_and_json(workspace: Path, capsys) -> None:
    run(
        [
            "--workspace",
            str(workspace),
            "--json",
            "grep",
            "needle",
            "--glob",
            "*.md",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["matches"]) == 1
    assert payload["matches"][0]["path"] == "README.md"


def test_status_not_a_repo(workspace: Path, capsys) -> None:
    run(["--workspace", str(workspace), "status"])
    out = capsys.readouterr().out
    assert "not a repository" in out


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_status_in_repo(workspace: Path, capsys) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=workspace, check=True, capture_output=True
    )
    run(["--workspace", str(workspace), "status"])
    out = capsys.readouterr().out
    assert "main" in out
    assert "dirty" in out


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_status_json_in_repo(workspace: Path, capsys) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=workspace, check=True, capture_output=True
    )
    run(["--workspace", str(workspace), "--json", "status"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["git"]["repository"] is True
    assert payload["git"]["branch"] == "main"
    assert payload["git"]["dirty"] is True


def test_env_plain(workspace: Path, capsys) -> None:
    run(["--workspace", str(workspace), "env"])
    out = capsys.readouterr().out
    assert "Platform:" in out


def test_env_json(workspace: Path, capsys) -> None:
    run(["--workspace", str(workspace), "--json", "env"])
    data = json.loads(capsys.readouterr().out)
    assert data["os"]
    assert data["python"]
    assert isinstance(data["top_level"], list)


def test_runs_empty(tmp_path: Path, capsys) -> None:
    run(["runs", "--runs-dir", str(tmp_path / "runs")])
    assert "no recorded runs" in capsys.readouterr().out


def _record(runs_dir: Path, task_id: str, status: str) -> None:
    from free_claude_code.terminal import append_run_record
    from free_claude_code.terminal.run import RunReport, StepReport

    append_run_record(
        runs_dir,
        RunReport(
            goal="demo goal",
            task_id=task_id,
            status=status,
            steps=(
                StepReport(
                    step_id="s1",
                    title="step",
                    status="ok",
                    argv=("git", "status"),
                    exit_code=0,
                ),
            ),
            residual=(),
        ),
    )


def test_runs_list(tmp_path: Path, capsys) -> None:
    runs_dir = tmp_path / "runs"
    _record(runs_dir, "task-a", "ok")
    _record(runs_dir, "task-a", "ok")
    _record(runs_dir, "task-b", "failed")
    run(["runs", "--runs-dir", str(runs_dir)])
    out = capsys.readouterr().out
    assert "task-a: ok (2 attempt(s)) - demo goal" in out
    assert "task-b: failed (1 attempt(s))" in out


def test_runs_one_task(tmp_path: Path, capsys) -> None:
    runs_dir = tmp_path / "runs"
    _record(runs_dir, "task-a", "ok")
    run(["runs", "task-a", "--runs-dir", str(runs_dir)])
    out = capsys.readouterr().out
    assert "demo goal" in out
    assert "git status" in out


def test_runs_one_task_json(tmp_path: Path, capsys) -> None:
    runs_dir = tmp_path / "runs"
    _record(runs_dir, "task-a", "ok")
    run(["--json", "runs", "task-a", "--runs-dir", str(runs_dir)])
    data = json.loads(capsys.readouterr().out)
    assert data["attempts"][0]["steps"][0]["argv"] == ["git", "status"]


def test_runs_unknown_task(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        run(["runs", "nope", "--runs-dir", str(tmp_path / "runs")])
    assert exc.value.code == 1


def _fake_processes() -> list:
    from free_claude_code.terminal import ProcessInfo

    return [
        ProcessInfo(
            pid=100,
            ppid=1,
            rss_bytes=10 * 1024 * 1024,
            name="small",
            command="small --run",
        ),
        ProcessInfo(
            pid=200, ppid=1, rss_bytes=99 * 1024 * 1024, name="big", command="big --run"
        ),
        ProcessInfo(
            pid=300, ppid=None, rss_bytes=None, name="kernel", command="kernel"
        ),
    ]


def _fake_listeners() -> list:
    from free_claude_code.terminal import PortListener

    return [
        PortListener(protocol="udp", address="0.0.0.0", port=53, pid=None),
        PortListener(protocol="tcp", address="127.0.0.1", port=8000, pid=200),
    ]


def test_processes_plain_sorted_by_rss(workspace: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        "free_claude_code.cli.inspect.collect_processes", lambda: _fake_processes()
    )
    run(["--workspace", str(workspace), "processes"])
    out = capsys.readouterr().out
    assert out.index("big") < out.index("small") < out.index("kernel")
    assert "99.0MB" in out


def test_processes_json_and_limit(workspace: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        "free_claude_code.cli.inspect.collect_processes", lambda: _fake_processes()
    )
    run(["--workspace", str(workspace), "--json", "processes", "--limit", "1"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 3
    assert len(payload["processes"]) == 1
    assert payload["processes"][0]["name"] == "big"


def test_processes_tool_missing_fails(workspace: Path, capsys, monkeypatch) -> None:
    from free_claude_code.terminal import SystemInspectError

    def boom():
        raise SystemInspectError("'ps' is not installed on this system")

    monkeypatch.setattr("free_claude_code.cli.inspect.collect_processes", boom)
    with pytest.raises(SystemExit):
        run(["--workspace", str(workspace), "processes"])
    assert "not installed" in capsys.readouterr().err


def test_ports_plain(workspace: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        "free_claude_code.cli.inspect.collect_listeners", lambda: _fake_listeners()
    )
    run(["--workspace", str(workspace), "ports"])
    out = capsys.readouterr().out
    assert "tcp" in out
    assert "127.0.0.1:8000" in out
    assert "pid 200" in out
    # sorted by (protocol, port): tcp line precedes udp line
    assert out.index("tcp") < out.index("udp")


def test_ports_json(workspace: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        "free_claude_code.cli.inspect.collect_listeners", lambda: _fake_listeners()
    )
    run(["--workspace", str(workspace), "--json", "ports"])
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["port"] == 8000
    assert payload[0]["pid"] == 200
    assert payload[1]["port"] == 53
