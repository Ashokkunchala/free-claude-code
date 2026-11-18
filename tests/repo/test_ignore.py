"""IgnoreRules tests: gitignore subset semantics."""

from pathlib import Path

from free_claude_code.repo.ignore import IgnoreRules


def rules(*lines: str) -> IgnoreRules:
    return IgnoreRules.from_lines(list(lines))


def test_comments_and_blanks_skipped() -> None:
    parsed = rules("# comment", "", "   ")
    assert not parsed.is_ignored("anything")


def test_basename_pattern_matches_any_depth() -> None:
    parsed = rules("*.log")
    assert parsed.is_ignored("debug.log")
    assert parsed.is_ignored("logs/debug.log")
    assert not parsed.is_ignored("debug.logs")


def test_anchored_pattern_matches_root_only() -> None:
    parsed = rules("/build")
    assert parsed.is_ignored("build", is_dir=True)
    assert not parsed.is_ignored("src/build", is_dir=True)


def test_dir_only_pattern() -> None:
    parsed = rules("node_modules/")
    assert parsed.is_ignored("node_modules", is_dir=True)
    assert not parsed.is_ignored("node_modules", is_dir=False)


def test_dir_only_pattern_matches_children_via_parent() -> None:
    parsed = rules("node_modules/")
    assert parsed.is_ignored("node_modules/pkg/index.js")


def test_negation_reinstates() -> None:
    parsed = rules("*.log", "!keep.log")
    assert parsed.is_ignored("debug.log")
    assert not parsed.is_ignored("keep.log")


def test_last_matching_rule_wins() -> None:
    parsed = rules("!keep.log", "*.log")
    assert parsed.is_ignored("keep.log")


def test_double_star_crosses_directories() -> None:
    parsed = rules("**/tmp/**")
    assert parsed.is_ignored("a/tmp/b/file.txt")
    assert parsed.is_ignored("tmp/file.txt")
    assert not parsed.is_ignored("a/tmpx/b")


def test_pattern_with_slash_matches_from_root() -> None:
    parsed = rules("docs/*.md")
    assert parsed.is_ignored("docs/readme.md")
    assert not parsed.is_ignored("src/docs/readme.md")
    assert not parsed.is_ignored("docs/sub/nested.md")


def test_question_mark_matches_one_char() -> None:
    parsed = rules("file?.txt")
    assert parsed.is_ignored("file1.txt")
    assert not parsed.is_ignored("file12.txt")


def test_git_dir_always_ignored() -> None:
    parsed = IgnoreRules()
    assert parsed.is_ignored(".git", is_dir=True)
    assert parsed.is_ignored(".git/objects/aa/bb")


def test_from_root_reads_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    parsed = IgnoreRules.from_root(tmp_path)
    assert parsed.is_ignored("mod.pyc")
    assert not parsed.is_ignored("mod.py")


def test_from_root_missing_file_means_no_rules(tmp_path: Path) -> None:
    parsed = IgnoreRules.from_root(tmp_path)
    assert not parsed.is_ignored("mod.pyc")


def test_windows_separators_normalised() -> None:
    parsed = rules("build/")
    assert parsed.is_ignored("build\\out\\file.o")
