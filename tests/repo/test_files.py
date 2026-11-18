"""Repository file operation tests: read, search, edit, rollback."""

import os
from pathlib import Path

import pytest

from free_claude_code.repo import (
    BinaryFileError,
    ConfinementError,
    IgnoreRules,
    edit_file,
    read_file,
    rollback,
    search_content,
    search_files,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    tmp_path = root
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "def main():\n    return 'hello'\n", encoding="utf-8"
    )
    (tmp_path / "src" / "util.py").write_text(
        "def helper():\n    return 'world'\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# demo\nhello readme\n", encoding="utf-8")
    (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02binary")
    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "secret.py").write_text("hello hidden\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("hello git\n", encoding="utf-8")
    return tmp_path


def test_read_file(repo: Path) -> None:
    result = read_file(repo, "src/app.py")
    assert "hello" in result.content
    assert not result.truncated
    assert result.size_bytes > 0


def test_read_file_truncates(repo: Path) -> None:
    result = read_file(repo, "src/app.py", max_bytes=5)
    assert result.truncated
    assert len(result.content.encode("utf-8", errors="replace")) >= 1
    assert result.size_bytes > 5


def test_read_binary_rejected(repo: Path) -> None:
    with pytest.raises(BinaryFileError):
        read_file(repo, "data.bin")


def test_read_outside_root_rejected(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("nope\n", encoding="utf-8")
    with pytest.raises(ConfinementError):
        read_file(repo, outside)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_read_through_symlink_escape_rejected(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("nope\n", encoding="utf-8")
    link = repo / "linked.txt"
    link.symlink_to(outside)
    with pytest.raises(ConfinementError):
        read_file(repo, "linked.txt")


def test_search_files_glob(repo: Path) -> None:
    found = search_files(repo, "**/*.py")
    assert "src/app.py" in found
    assert "src/util.py" in found
    assert "ignored/secret.py" not in found
    assert ".git/config" not in found


def test_search_files_flat_glob(repo: Path) -> None:
    found = search_files(repo, "*.md")
    assert found == ["README.md"]


def test_search_content_matches(repo: Path) -> None:
    result = search_content(repo, "hello")
    paths = {match.path for match in result.matches}
    assert "src/app.py" in paths
    assert "README.md" in paths
    assert "ignored/secret.py" not in paths
    assert ".git/config" not in paths
    # data.bin is detected binary and skipped; ignored dirs are pruned wholesale.
    assert result.files_skipped >= 1


def test_search_content_skips_binary(repo: Path) -> None:
    result = search_content(repo, "binary")
    assert all(match.path != "data.bin" for match in result.matches)


def test_search_content_glob_filter(repo: Path) -> None:
    result = search_content(repo, "hello", glob="*.md")
    assert {match.path for match in result.matches} == {"README.md"}


def test_search_content_respects_cap(repo: Path) -> None:
    result = search_content(repo, "hello", max_results=1)
    assert len(result.matches) == 1
    assert result.truncated


def test_edit_file_produces_diff_and_backup(repo: Path) -> None:
    result = edit_file(repo, "src/app.py", "def main():\n    return 'bye'\n")
    assert "-    return 'hello'" in result.diff
    assert "+    return 'bye'" in result.diff
    assert "a/src/app.py" in result.diff
    assert result.backup_content is not None
    assert "hello" in result.backup_content
    assert not result.created
    assert read_file(repo, "src/app.py").content == "def main():\n    return 'bye'\n"


def test_edit_file_rollback_restores(repo: Path) -> None:
    original = read_file(repo, "src/app.py").content
    result = edit_file(repo, "src/app.py", "changed\n")
    rollback(result)
    assert read_file(repo, "src/app.py").content == original


def test_edit_new_file_and_rollback_removes(repo: Path) -> None:
    result = edit_file(repo, "src/new.py", "fresh = True\n")
    assert result.created
    assert result.backup_content is None
    assert (repo / "src" / "new.py").exists()
    rollback(result)
    assert not (repo / "src" / "new.py").exists()


def test_edit_binary_rejected(repo: Path) -> None:
    with pytest.raises(BinaryFileError):
        edit_file(repo, "data.bin", "text\n")


def test_edit_outside_root_rejected(repo: Path) -> None:
    with pytest.raises(ConfinementError):
        edit_file(repo, "../escape.txt", "nope\n")


def test_ignore_rules_default_from_root(repo: Path) -> None:
    # search_content with no explicit rules reads the root .gitignore.
    result = search_content(repo, "hidden")
    assert result.matches == []
    # With empty rules the same file is visible.
    result = search_content(repo, "hidden", ignore=IgnoreRules())
    assert any(match.path == "ignored/secret.py" for match in result.matches)
