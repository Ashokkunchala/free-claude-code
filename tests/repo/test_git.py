"""GitRepo tests against real temporary git repositories."""

import shutil
import subprocess
from pathlib import Path

import pytest

from free_claude_code.repo.git import GitError, GitRepo
from free_claude_code.terminal import PolicyRejectedError

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git executable not available"
)


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> GitRepo:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "config", "commit.gpgsign", "false")
    (root / "file.txt").write_text("one\n", encoding="utf-8")
    git(root, "add", "file.txt")
    git(root, "commit", "-m", "initial")
    return GitRepo(root)


def test_status_clean(repo: GitRepo) -> None:
    status = repo.status()
    assert status.branch == "main"
    assert not status.dirty
    assert status.porcelain == ""


def test_status_dirty(repo: GitRepo) -> None:
    (repo.root / "file.txt").write_text("two\n", encoding="utf-8")
    assert repo.is_dirty()
    assert "file.txt" in repo.status().porcelain


def test_create_branch_and_switch(repo: GitRepo) -> None:
    repo.create_branch("feature")
    assert repo.current_branch() == "feature"


def test_add_and_commit(repo: GitRepo) -> None:
    (repo.root / "new.txt").write_text("new\n", encoding="utf-8")
    repo.add("new.txt")
    short = repo.commit("add new file")
    assert short
    assert not repo.is_dirty()
    log = repo.run("log", "--oneline").stdout_text
    assert "add new file" in log


def test_diff_shows_changes(repo: GitRepo) -> None:
    (repo.root / "file.txt").write_text("two\n", encoding="utf-8")
    diff = repo.diff()
    assert "-one" in diff
    assert "+two" in diff


def test_failed_command_raises_git_error(repo: GitRepo) -> None:
    with pytest.raises(GitError):
        repo.run("checkout", "no-such-branch-ever")


def test_unchecked_run_returns_result(repo: GitRepo) -> None:
    result = repo.run("checkout", "no-such-branch-ever", check=False)
    assert result.exit_code != 0


def test_privileged_operation_policy_rejected(repo: GitRepo) -> None:
    with pytest.raises(PolicyRejectedError):
        repo.run("push", "origin", "main")


def test_add_requires_paths(repo: GitRepo) -> None:
    with pytest.raises(ValueError):
        repo.add()


def test_run_emits_audit_events(repo: GitRepo) -> None:
    from free_claude_code.terminal import InMemoryAuditSink, TerminalExecutor

    sink = InMemoryAuditSink()
    instrumented = GitRepo(repo.root, executor=TerminalExecutor(sink=sink))
    instrumented.status()
    kinds = [event.kind for event in sink.events()]
    assert "planned" in kinds
    assert "finished" in kinds
