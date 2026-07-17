from pathlib import Path

from typer.testing import CliRunner

import forge_companion.cli as cli
from forge_companion.cli import app

runner = CliRunner()


def test_doctor_requires_token_without_printing_secrets() -> None:
    result = runner.invoke(app, ["doctor"], env={"BREWFORGE_API_TOKEN": ""})

    assert result.exit_code == 2
    assert "BREWFORGE_API_TOKEN is not set" in result.output
    assert "bfk_" not in result.output


def test_backup_command_writes_file_and_reports_destination(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {"data": [], "pagination": {"hasMore": False, "total": 0}}

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    destination = tmp_path / "brewforge.json"

    result = runner.invoke(
        app,
        ["snapshot", "--output", str(destination)],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0
    assert destination.exists()
    assert str(destination) in result.output
    assert "test-token" not in destination.read_text(encoding="utf-8")


def test_backup_command_reports_api_error_without_traceback(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    class BrokenClient:
        def __init__(self, token: str) -> None:
            pass

        def get(self, path: str, params: object = None) -> dict[str, object]:
            raise ValueError("unexpected response")

    monkeypatch.setattr(cli, "BrewForgeClient", BrokenClient)
    destination = tmp_path / "must-not-exist.json"

    result = runner.invoke(
        app,
        ["snapshot", "--output", str(destination)],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 1
    assert "Snapshot failed: unexpected response" in result.output
    assert "Traceback" not in result.output
    assert not destination.exists()
