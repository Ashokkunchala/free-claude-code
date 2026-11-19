"""Tests for the fcc-config CLI."""

import json
from pathlib import Path

import pytest

from free_claude_code.cli.config import main


def write_config(path: Path, values: dict[str, str]) -> None:
    lines = ["FCC_CONFIG_SCHEMA=1"]
    lines += [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_show_masks_secret_fields(tmp_path: Path, capsys) -> None:
    path = tmp_path / ".env"
    write_config(path, {"OPENAI_API_KEY": "sk-" + "b" * 30})
    main(["--config-file", str(path), "show"])
    out = capsys.readouterr().out
    assert "sk-" + "b" * 30 not in out
    assert "openai_api_key" in out
    assert "<redacted>" in out


def test_show_marks_sources(tmp_path: Path, capsys, monkeypatch) -> None:
    path = tmp_path / ".env"
    write_config(path, {"OPENAI_API_KEY": "sk-" + "b" * 30})
    monkeypatch.setenv("MODEL", "openai/gpt-5")
    main(["--config-file", str(path), "show", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert report["fields"]["openai_api_key"]["source"] == "managed_env"
    assert report["fields"]["model"]["source"] == "process"
    assert report["fields"]["reasoning_policy"]["source"] == "default"


def test_show_json_never_leaks_process_secret(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    path = tmp_path / ".env"
    write_config(path, {})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "c" * 30)
    main(["--config-file", str(path), "show", "--json"])
    out = capsys.readouterr().out
    assert "sk-" + "c" * 30 not in out
    assert json.loads(out)["fields"]["openai_api_key"]["source"] == "process"


def test_validate_ok(tmp_path: Path, capsys) -> None:
    path = tmp_path / ".env"
    write_config(path, {"OPENAI_API_KEY": "sk-" + "b" * 30})
    main(["--config-file", str(path), "validate"])
    assert "valid" in capsys.readouterr().out.lower()


def test_validate_missing_file(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--config-file", str(tmp_path / "missing.env"), "validate"])
    assert excinfo.value.code == 1
    assert "does not exist" in capsys.readouterr().err


def test_validate_schema_mismatch(tmp_path: Path, capsys) -> None:
    path = tmp_path / ".env"
    path.write_text("FCC_CONFIG_SCHEMA=99\n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        main(["--config-file", str(path), "validate"])
    assert excinfo.value.code == 1
    assert "schema" in capsys.readouterr().err.lower()


def test_validate_bad_value(tmp_path: Path, capsys) -> None:
    path = tmp_path / ".env"
    write_config(path, {"MODEL": "no-provider-prefix"})
    with pytest.raises(SystemExit) as excinfo:
        main(["--config-file", str(path), "validate"])
    assert excinfo.value.code == 1
    assert "invalid" in capsys.readouterr().err.lower()


def test_precedence_explains_order(tmp_path: Path, capsys) -> None:
    path = tmp_path / ".env"
    write_config(path, {})
    main(["--config-file", str(path), "precedence"])
    out = capsys.readouterr().out.lower()
    assert "process" in out
    assert "managed" in out
    assert "default" in out
    assert str(tmp_path).lower() in out or ".env" in out
