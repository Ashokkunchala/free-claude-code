"""Tests for per-project model profiles."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from free_claude_code.cli import profiles


@pytest.fixture(autouse=True)
def _clean_profiles() -> Iterator[None]:
    yield
    for directory in list(profiles.list_profiles()):
        profiles.clear(directory)


def test_model_for_directory_empty_when_unset(tmp_path: Path) -> None:
    assert profiles.model_for_directory(tmp_path) == ""


def test_set_and_read_model(tmp_path: Path) -> None:
    profiles.set_model("nvidia_nim/fast", tmp_path)

    assert profiles.model_for_directory(tmp_path) == "nvidia_nim/fast"


def test_set_model_rejects_empty(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        profiles.set_model("   ", tmp_path)


def test_profiles_are_scoped_to_directories(tmp_path: Path) -> None:
    profiles.set_model("nvidia_nim/fast", tmp_path / "a")
    profiles.set_model("open_router/slow", tmp_path / "b")

    assert profiles.model_for_directory(tmp_path / "a") == "nvidia_nim/fast"
    assert profiles.model_for_directory(tmp_path / "b") == "open_router/slow"


def test_set_model_overwrites_existing(tmp_path: Path) -> None:
    profiles.set_model("nvidia_nim/fast", tmp_path)
    profiles.set_model("nvidia_nim/slow", tmp_path)

    assert profiles.model_for_directory(tmp_path) == "nvidia_nim/slow"


def test_clear_removes_profile(tmp_path: Path) -> None:
    profiles.set_model("nvidia_nim/fast", tmp_path)

    assert profiles.clear(tmp_path) is True
    assert profiles.model_for_directory(tmp_path) == ""


def test_clear_reports_missing_profile(tmp_path: Path) -> None:
    assert profiles.clear(tmp_path) is False


def test_list_profiles_returns_all(tmp_path: Path) -> None:
    profiles.set_model("nvidia_nim/fast", tmp_path / "a")
    profiles.set_model("open_router/slow", tmp_path / "b")

    listed = profiles.list_profiles()

    assert len(listed) == 2
    assert "nvidia_nim/fast" in listed.values()
    assert "open_router/slow" in listed.values()


def test_load_tolerates_corrupt_file() -> None:
    from free_claude_code.config.paths import profiles_path

    path = profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert profiles.load()["projects"] == {}


def test_load_tolerates_wrong_version() -> None:
    from free_claude_code.config.paths import profiles_path

    path = profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"version": 99, "projects": {}}', encoding="utf-8")

    assert profiles.load()["projects"] == {}


def test_main_set_show_clear(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = str(tmp_path)

    profiles.main(["set", "nvidia_nim/fast", directory])
    assert "nvidia_nim/fast" in capsys.readouterr().out

    profiles.main(["show", directory])
    assert capsys.readouterr().out.strip() == "nvidia_nim/fast"

    profiles.main(["clear", directory])
    assert "cleared" in capsys.readouterr().out


def test_main_show_missing_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        profiles.main(["show", str(tmp_path)])

    assert exc.value.code == 1
    assert "no profile" in capsys.readouterr().err


def test_main_list(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    profiles.set_model("nvidia_nim/fast", tmp_path)

    profiles.main(["list"])

    assert "nvidia_nim/fast" in capsys.readouterr().out
